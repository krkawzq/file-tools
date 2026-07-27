use crate::client::{
    command_result, decode_text, encode_text, inject_command_flag, normalize_env,
    normalize_flags_object, normalize_timeout, validate_max_output_bytes, CommandResult, CoreError,
    CoreResult,
};
use crate::command::{run_process, ProcessOutput, ProcessSpec};
use crate::constants::{
    DEFAULT_FILE_OPERATION_TIMEOUT, DEFAULT_MAX_TRANSFER_BYTES, MAX_TRANSFER_BYTES,
    REMOTE_KILL_TIMEOUT, SSH_CLIENT_KIND, TOKEN_COUNTER,
};
use crate::fs::FileInfo;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyTuple};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tempfile::TempDir;

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

fn shell_quote_path(value: &str) -> String {
    if value == "~" {
        return "\"$HOME\"".to_string();
    }
    if let Some(rest) = value.strip_prefix("~/") {
        return format!("\"$HOME\"/{}", shell_quote(rest));
    }
    shell_quote(value)
}

fn control_token() -> String {
    let sequence = TOKEN_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    format!("{:x}{:x}{:x}", std::process::id(), nanos, sequence)
}

fn normalize_posix(path: &str) -> String {
    let absolute = path.starts_with('/');
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if parts.last().is_some_and(|part| *part != "..") {
                    parts.pop();
                } else if !absolute {
                    parts.push("..");
                }
            }
            part => parts.push(part),
        }
    }
    let joined = parts.join("/");
    if absolute {
        if joined.is_empty() {
            "/".to_string()
        } else {
            format!("/{joined}")
        }
    } else if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

fn join_posix(parts: &[String]) -> String {
    if parts.is_empty() {
        return String::new();
    }
    let mut result = parts[0].clone();
    for part in &parts[1..] {
        if result.ends_with('/') {
            result.push_str(part.trim_start_matches('/'));
        } else {
            result.push('/');
            result.push_str(part.trim_start_matches('/'));
        }
    }
    result
}

fn expand_local_key(path: &str) -> String {
    if path == "~" {
        return std::env::var("HOME").unwrap_or_else(|_| path.to_string());
    }
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home)
                .join(rest)
                .to_string_lossy()
                .into_owned();
        }
    }
    path.to_string()
}

struct SshInvocation {
    spec: ProcessSpec,
    _askpass: Option<TempDir>,
}

#[derive(Clone, Copy, Debug)]
enum RemoteProcess {
    ProcessGroup(i32),
    Process(i32),
}

fn find_remote_process(stderr: &[u8], prefix: &str) -> Option<RemoteProcess> {
    let text = String::from_utf8_lossy(stderr);
    for line in text.lines() {
        let mut fields = line.split_whitespace();
        if fields.next()? != prefix {
            continue;
        }
        let mode = fields.next()?;
        let pid = fields.next()?.parse::<i32>().ok()?;
        return match mode {
            "PGID" => Some(RemoteProcess::ProcessGroup(pid)),
            "PID" => Some(RemoteProcess::Process(pid)),
            _ => None,
        };
    }
    None
}

fn strip_control_marker(stderr: &[u8], prefix: &str) -> (Vec<u8>, usize) {
    let needle = prefix.as_bytes();
    let Some(start) = stderr
        .windows(needle.len())
        .position(|window| window == needle)
    else {
        return (stderr.to_vec(), 0);
    };
    let end = stderr[start..]
        .iter()
        .position(|byte| *byte == b'\n')
        .map(|offset| start + offset + 1)
        .unwrap_or(stderr.len());
    let line = &stderr[start..end];
    if find_remote_process(line, prefix).is_none() {
        return (stderr.to_vec(), 0);
    }
    let mut result = Vec::with_capacity(stderr.len() - (end - start));
    result.extend_from_slice(&stderr[..start]);
    result.extend_from_slice(&stderr[end..]);
    (result, end - start)
}

#[pyclass(module = "file_tools._core")]
pub struct SshClient {
    host: String,
    port: u16,
    username: String,
    password: Option<String>,
    key_filename: Option<String>,
    ssh_flags: Vec<String>,
    allow_password_prompt: bool,
    accept_unknown_host_key: bool,
    connect_timeout: f64,
    operation_timeout: Duration,
    max_transfer_bytes: usize,
    control_dir: Option<TempDir>,
    home: String,
    cwd: String,
}

impl SshClient {
    #[allow(clippy::too_many_arguments)]
    pub fn connect(
        host: &str,
        port: u16,
        username: &str,
        password: Option<&str>,
        key_filename: Option<&str>,
        cwd: &str,
        connect_timeout: f64,
        operation_timeout: f64,
        max_transfer_bytes: usize,
        multiplexing: bool,
        ssh_flags: Vec<String>,
        allow_password_prompt: bool,
        accept_unknown_host_key: bool,
    ) -> CoreResult<Self> {
        let host = host.trim();
        let username = username.trim();
        if host.is_empty() {
            return Err(CoreError::Value("ssh host is required".to_string()));
        }
        if host.starts_with('-') {
            return Err(CoreError::Value(
                "ssh host must not start with '-'".to_string(),
            ));
        }
        if username.is_empty() {
            return Err(CoreError::Value("ssh user is required".to_string()));
        }
        if port == 0 {
            return Err(CoreError::Value(
                "ssh port is required and must be a positive integer".to_string(),
            ));
        }
        if !connect_timeout.is_finite() || connect_timeout <= 0.0 {
            return Err(CoreError::Value(
                "connect_timeout must be a positive finite number".to_string(),
            ));
        }
        if !operation_timeout.is_finite() || operation_timeout <= 0.0 {
            return Err(CoreError::Value(
                "operation_timeout must be a positive finite number".to_string(),
            ));
        }
        if max_transfer_bytes == 0 || max_transfer_bytes > MAX_TRANSFER_BYTES {
            return Err(CoreError::Value(format!(
                "max_transfer_bytes must be between 1 and {MAX_TRANSFER_BYTES}"
            )));
        }
        #[cfg(unix)]
        let control_dir = if multiplexing {
            Some(
                tempfile::Builder::new()
                    .prefix("file-tools-ssh-")
                    .tempdir()
                    .map_err(|error| {
                        CoreError::Client(format!("cannot create SSH control directory: {error}"))
                    })?,
            )
        } else {
            None
        };
        #[cfg(not(unix))]
        let control_dir = {
            let _ = multiplexing;
            None
        };
        let mut client = Self {
            host: host.to_string(),
            port,
            username: username.to_string(),
            password: password
                .filter(|value| !value.is_empty())
                .map(str::to_string),
            key_filename: key_filename
                .filter(|value| !value.is_empty())
                .map(expand_local_key),
            ssh_flags: ssh_flags
                .into_iter()
                .filter(|flag| matches!(flag.as_str(), "-X" | "-Y" | "-A" | "-a" | "-C"))
                .collect(),
            allow_password_prompt,
            accept_unknown_host_key,
            connect_timeout,
            operation_timeout: Duration::from_secs_f64(operation_timeout),
            max_transfer_bytes,
            control_dir,
            home: "~".to_string(),
            cwd: "~".to_string(),
        };
        client.cwd = client.resolve_from_home(cwd);
        Ok(client)
    }

    fn resolve_from_home(&self, path: &str) -> String {
        if path == "." || path.is_empty() || path == "~" {
            return self.home.clone();
        }
        if let Some(rest) = path.strip_prefix("~/") {
            return normalize_posix(&format!("{}/{}", self.home, rest));
        }
        if path.starts_with('/') {
            return normalize_posix(path);
        }
        normalize_posix(&format!("{}/{}", self.home, path))
    }

    fn resolve_native(&self, path: &str) -> String {
        if path == "~" {
            return self.home.clone();
        }
        if let Some(rest) = path.strip_prefix("~/") {
            return normalize_posix(&format!("{}/{}", self.home, rest));
        }
        if path.starts_with('/') {
            return normalize_posix(path);
        }
        normalize_posix(&format!("{}/{}", self.cwd, path))
    }

    fn ssh_invocation(
        &self,
        remote_command: &str,
        stdin: Option<Vec<u8>>,
        timeout: Option<Duration>,
        max_output_bytes: usize,
    ) -> CoreResult<SshInvocation> {
        let mut args = vec![
            "-T".to_string(),
            "-p".to_string(),
            self.port.to_string(),
            "-l".to_string(),
            self.username.clone(),
            "-o".to_string(),
            format!(
                "ConnectTimeout={}",
                self.connect_timeout.ceil().max(1.0) as u64
            ),
            "-o".to_string(),
            if self.accept_unknown_host_key {
                "StrictHostKeyChecking=accept-new".to_string()
            } else {
                "StrictHostKeyChecking=yes".to_string()
            },
        ];
        if let Some(key) = &self.key_filename {
            args.extend(["-i".to_string(), key.clone()]);
        }
        if let Some(directory) = &self.control_dir {
            let control_path = directory.path().join("mux-%C");
            args.extend([
                "-o".to_string(),
                "ControlMaster=auto".to_string(),
                "-o".to_string(),
                "ControlPersist=60".to_string(),
                "-o".to_string(),
                format!("ControlPath={}", control_path.to_string_lossy()),
            ]);
        }
        args.extend(self.ssh_flags.iter().cloned());

        let mut env = HashMap::new();
        let mut askpass = None;
        if let Some(password) = &self.password {
            let directory = tempfile::Builder::new()
                .prefix("file-tools-askpass-")
                .tempdir()
                .map_err(|error| {
                    CoreError::Client(format!("cannot create SSH askpass helper: {error}"))
                })?;
            #[cfg(windows)]
            let helper = directory.path().join("askpass.cmd");
            #[cfg(not(windows))]
            let helper = directory.path().join("askpass.sh");
            #[cfg(windows)]
            let script = "@echo off\r\necho %FILE_TOOLS_SSH_PASSWORD%\r\n";
            #[cfg(not(windows))]
            let script = "#!/bin/sh\nprintf '%s\\n' \"$FILE_TOOLS_SSH_PASSWORD\"\n";
            fs::write(&helper, script).map_err(|error| {
                CoreError::Client(format!("cannot write SSH askpass helper: {error}"))
            })?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&helper, fs::Permissions::from_mode(0o700)).map_err(
                    |error| {
                        CoreError::Client(format!(
                            "cannot secure SSH askpass helper permissions: {error}"
                        ))
                    },
                )?;
            }
            env.insert(
                "SSH_ASKPASS".to_string(),
                helper.to_string_lossy().into_owned(),
            );
            env.insert("SSH_ASKPASS_REQUIRE".to_string(), "force".to_string());
            env.insert("DISPLAY".to_string(), "file-tools:0".to_string());
            env.insert("FILE_TOOLS_SSH_PASSWORD".to_string(), password.clone());
            args.extend([
                "-o".to_string(),
                "BatchMode=no".to_string(),
                "-o".to_string(),
                "NumberOfPasswordPrompts=1".to_string(),
            ]);
            askpass = Some(directory);
        } else if !self.allow_password_prompt {
            args.extend(["-o".to_string(), "BatchMode=yes".to_string()]);
        }

        // OpenSSH has no universally portable option terminator in every
        // supported release. connect() rejects option-shaped hosts before the
        // destination is appended.
        args.push(self.host.clone());
        args.push(remote_command.to_string());
        Ok(SshInvocation {
            spec: ProcessSpec {
                program: "ssh".to_string(),
                args,
                cwd: None,
                env,
                stdin,
                timeout,
                max_output_bytes,
            },
            _askpass: askpass,
        })
    }

    fn run_ssh(
        &self,
        remote_command: &str,
        stdin: Option<Vec<u8>>,
        timeout: Option<Duration>,
        max_output_bytes: usize,
    ) -> CoreResult<ProcessOutput> {
        let invocation = self.ssh_invocation(remote_command, stdin, timeout, max_output_bytes)?;
        run_process(invocation.spec, None).map_err(CoreError::Client)
    }

    fn terminate_remote(&self, marker: RemoteProcess) {
        let target = match marker {
            RemoteProcess::ProcessGroup(pid) => format!("-{pid}"),
            RemoteProcess::Process(pid) => pid.to_string(),
        };
        let command = format!(
            "kill -TERM {target} 2>/dev/null || true; \
             i=0; while kill -0 {target} 2>/dev/null && [ \"$i\" -lt 10 ]; do \
             sleep 0.1; i=$((i + 1)); done; \
             kill -KILL {target} 2>/dev/null || true"
        );
        let _ = self.run_ssh(&command, None, Some(REMOTE_KILL_TIMEOUT), 8192);
    }

    fn checked_remote(
        &self,
        action: &str,
        path: &str,
        remote_command: &str,
        stdin: Option<Vec<u8>>,
    ) -> CoreResult<ProcessOutput> {
        let output = self.run_ssh(
            remote_command,
            stdin,
            Some(self.operation_timeout),
            self.max_transfer_bytes,
        )?;
        if output.timed_out {
            return Err(CoreError::Timeout(format!(
                "SSH {action} timed out after {:.3}s: {path}",
                self.operation_timeout.as_secs_f64()
            )));
        }
        if output.stdout_omitted_bytes > 0 || output.stderr_omitted_bytes > 0 {
            return Err(CoreError::TransferLimit(format!(
                "SSH {action} exceeded the configured transfer limit of {} bytes: {path}",
                self.max_transfer_bytes
            )));
        }
        if output.exit_code == 0 {
            return Ok(output);
        }
        let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let rendered = format!(
            "SSH {action} failed with exit code {}: {path}: {message}",
            output.exit_code
        );
        let lower = message.to_ascii_lowercase();
        if lower.contains("permission denied")
            && (lower.contains("publickey") || lower.contains("password"))
        {
            Err(CoreError::Authentication(rendered))
        } else if lower.contains("permission denied") {
            Err(CoreError::PermissionDenied(rendered))
        } else {
            Err(CoreError::Client(rendered))
        }
    }

    fn read_bytes_native(&self, path: &str) -> CoreResult<Vec<u8>> {
        let resolved = self.resolve_native(path);
        self.checked_remote(
            "read_bytes",
            path,
            &format!("cat -- {}", shell_quote_path(&resolved)),
            None,
        )
        .map(|output| output.stdout)
    }

    fn stat_native(&self, path: &str) -> CoreResult<FileInfo> {
        let resolved = shell_quote_path(&self.resolve_native(path));
        let command = format!(
            "p={resolved}; \
             if ! test -e \"$p\" && ! test -L \"$p\"; then \
               printf 'missing\\t0\\t-\\t0\\t-'; exit 0; \
             fi; \
             if test -L \"$p\"; then kind=symlink; link=1; \
             elif test -f \"$p\"; then kind=file; link=0; \
             elif test -d \"$p\"; then kind=directory; link=0; \
             else kind=other; link=0; fi; \
             if test \"$kind\" = file; then size=$(wc -c < \"$p\") || exit 1; \
             else size=0; fi; \
             mtime=$(stat -c '%Y' -- \"$p\" 2>/dev/null || stat -f '%m' \"$p\") || exit 1; \
             signature=$(stat -c '%s:%Y:%i:%y' -- \"$p\" 2>/dev/null || \
                         stat -f '%z:%m:%i' \"$p\") || exit 1; \
             printf '%s\\t%s\\t%s\\t%s\\tremote:%s:%s' \
               \"$kind\" \"$size\" \"$mtime\" \"$link\" \"$kind\" \"$signature\""
        );
        let output = self.checked_remote("stat", path, &command, None)?;
        let text = String::from_utf8_lossy(&output.stdout);
        let fields = text.split('\t').collect::<Vec<_>>();
        if fields.first() == Some(&"missing") {
            return Ok(FileInfo::missing());
        }
        if fields.len() != 5 {
            return Err(CoreError::Client(format!(
                "SSH stat failed: {path}: invalid metadata response"
            )));
        }
        let size = fields[1].parse::<u64>().map_err(|_| {
            CoreError::Client(format!("SSH stat failed: {path}: invalid file size"))
        })?;
        let modified_ns = fields[2]
            .parse::<u128>()
            .ok()
            .map(|seconds| seconds.saturating_mul(1_000_000_000));
        Ok(FileInfo {
            exists: true,
            kind: fields[0].to_string(),
            size,
            modified_ns,
            is_symlink: fields[3] == "1",
            version: Some(fields[4].to_string()),
        })
    }

    fn read_text_window_native(
        &self,
        path: &str,
        offset: i64,
        limit: usize,
    ) -> CoreResult<(Vec<u8>, usize, usize, usize, bool)> {
        let resolved = shell_quote_path(&self.resolve_native(path));
        let token = control_token();
        let marker = format!("__FILE_TOOLS_WINDOW_{token}__");
        let selection = if offset < 0 {
            let tail = offset.unsigned_abs().min(i64::MAX as u64);
            format!(
                "if test \"$total\" -gt {tail}; then start=$((total - {tail} + 1)); \
                 else start=1; fi; end=$total; truncated=0"
            )
        } else {
            let start = if offset == 0 { 1 } else { offset as u64 };
            let span = u64::try_from(limit.saturating_sub(1)).unwrap_or(u64::MAX);
            let requested_end = start.saturating_add(span).min(i64::MAX as u64);
            format!(
                "start={start}; end={requested_end}; \
                 if test \"$end\" -gt \"$total\"; then end=$total; fi; \
                 if test \"$start\" -gt \"$total\"; then start=$((total + 1)); fi; \
                 if test \"$end\" -lt \"$total\"; then truncated=1; else truncated=0; fi"
            )
        };
        let command = format!(
            "p={resolved}; \
             if ! test -e \"$p\" && ! test -L \"$p\"; then exit 44; fi; \
             if test -d \"$p\"; then exit 45; fi; \
             if ! test -f \"$p\"; then exit 46; fi; \
             total=$(awk 'END {{ print NR }}' \"$p\") || exit 1; \
             {selection}; \
             if test \"$start\" -le \"$end\"; then \
               sed -n \"${{start}},${{end}}p\" \"$p\" || exit 1; \
             fi; \
             printf '%s\\t%s\\t%s\\t%s\\t%s\\n' {} \
               \"$total\" \"$start\" \"$end\" \"$truncated\" >&2",
            shell_quote(&marker)
        );
        let output = self.run_ssh(
            &command,
            None,
            Some(self.operation_timeout),
            self.max_transfer_bytes,
        )?;
        if output.timed_out {
            return Err(CoreError::Timeout(format!(
                "SSH read_text_window timed out after {:.3}s: {path}",
                self.operation_timeout.as_secs_f64()
            )));
        }
        if output.stdout_omitted_bytes > 0 || output.stderr_omitted_bytes > 0 {
            return Err(CoreError::TransferLimit(format!(
                "SSH read_text_window exceeded the configured transfer limit of {} bytes: {path}",
                self.max_transfer_bytes
            )));
        }
        match output.exit_code {
            0 => {}
            44 => {
                return Err(CoreError::NotFound(format!(
                    "SSH read_text_window failed: {path}: file does not exist"
                )))
            }
            45 => {
                return Err(CoreError::Client(format!(
                    "SSH read_text_window failed: {path}: path is a directory"
                )))
            }
            46 => {
                return Err(CoreError::Client(format!(
                    "SSH read_text_window failed: {path}: path is not a regular file"
                )))
            }
            _ => {
                let message = String::from_utf8_lossy(&output.stderr);
                if message.to_ascii_lowercase().contains("permission denied") {
                    return Err(CoreError::PermissionDenied(format!(
                        "SSH read_text_window failed: {path}: {}",
                        message.trim()
                    )));
                }
                return Err(CoreError::Client(format!(
                    "SSH read_text_window failed: {path}: {}",
                    message.trim()
                )));
            }
        }
        let stderr = String::from_utf8_lossy(&output.stderr);
        let metadata = stderr
            .lines()
            .find(|line| line.starts_with(&marker))
            .ok_or_else(|| {
                CoreError::Client(format!(
                    "SSH read_text_window failed: {path}: missing window metadata"
                ))
            })?;
        let fields = metadata.split('\t').collect::<Vec<_>>();
        if fields.len() != 5 {
            return Err(CoreError::Client(format!(
                "SSH read_text_window failed: {path}: invalid window metadata"
            )));
        }
        let parse = |value: &str, field: &str| {
            value.parse::<usize>().map_err(|_| {
                CoreError::Client(format!(
                    "SSH read_text_window failed: {path}: invalid {field}"
                ))
            })
        };
        Ok((
            output.stdout,
            parse(fields[1], "total line count")?,
            parse(fields[2], "start line")?,
            parse(fields[3], "end line")?,
            fields[4] == "1",
        ))
    }

    fn write_bytes_atomic_native(
        &self,
        path: &str,
        data: Vec<u8>,
        expected_version: Option<&str>,
        create_only: bool,
    ) -> CoreResult<FileInfo> {
        if data.len() > self.max_transfer_bytes {
            return Err(CoreError::TransferLimit(format!(
                "SSH write_bytes failed: {path}: content size {} exceeds the configured transfer limit of {} bytes",
                data.len(), self.max_transfer_bytes
            )));
        }
        let resolved = self.resolve_native(path);
        let parent = Path::new(&resolved)
            .parent()
            .and_then(Path::to_str)
            .unwrap_or("/");
        let target = shell_quote_path(&resolved);
        let token = control_token();
        let expected_check = expected_version.map_or_else(
            || ":".to_string(),
            |version| format!("test \"$before\" = {} || exit 73", shell_quote(version)),
        );
        let create_check = if create_only {
            "test \"$before\" = missing || exit 73"
        } else {
            ":"
        };
        let command = format!(
            "target={target}; \
             parent={}; mkdir -p -- \"$parent\" || exit 1; \
             lock=\"${{target}}.file-tools.lock\"; \
             acquired=0; i=0; \
             while test \"$i\" -lt 200; do \
               if mkdir -- \"$lock\" 2>/dev/null; then acquired=1; break; fi; \
               sleep 0.05; i=$((i + 1)); \
             done; \
             test \"$acquired\" = 1 || exit 74; \
             tmp=\"${{target}}.file-tools-{token}.tmp\"; \
             cleanup() {{ rm -f -- \"$tmp\"; rmdir -- \"$lock\" 2>/dev/null || true; }}; \
             trap cleanup EXIT HUP INT TERM; \
             ft_version() {{ \
               if ! test -e \"$1\" && ! test -L \"$1\"; then printf missing; return; fi; \
               if test -L \"$1\"; then kind=symlink; \
               elif test -f \"$1\"; then kind=file; \
               elif test -d \"$1\"; then kind=directory; else kind=other; fi; \
               signature=$(stat -c '%s:%Y:%i:%y' -- \"$1\" 2>/dev/null || \
                           stat -f '%z:%m:%i' \"$1\") || return 1; \
               printf 'remote:%s:%s' \"$kind\" \"$signature\"; \
             }}; \
             before=$(ft_version \"$target\") || exit 1; \
             {create_check}; {expected_check}; \
             umask 077; cat > \"$tmp\" || exit 1; \
             if test -e \"$target\" && ! test -L \"$target\"; then \
               chmod --reference=\"$target\" \"$tmp\" 2>/dev/null || \
               chmod \"$(stat -c '%a' -- \"$target\" 2>/dev/null || \
                        stat -f '%Lp' \"$target\")\" \"$tmp\" 2>/dev/null || true; \
             fi; \
             mv -f -- \"$tmp\" \"$target\" || exit 1; \
             trap - EXIT HUP INT TERM; rmdir -- \"$lock\" 2>/dev/null || true",
            shell_quote_path(parent),
        );
        match self.checked_remote("write_bytes", path, &command, Some(data)) {
            Ok(_) => self.stat_native(path),
            Err(CoreError::Client(message)) if message.contains("exit code 73") => {
                Err(CoreError::Conflict(format!("write conflict: {path}")))
            }
            Err(CoreError::Client(message)) if message.contains("exit code 74") => {
                Err(CoreError::Timeout(format!("write lock timed out: {path}")))
            }
            Err(error) => Err(error),
        }
    }

    fn write_bytes_native(&self, path: &str, data: Vec<u8>) -> CoreResult<()> {
        self.write_bytes_atomic_native(path, data, None, false)
            .map(|_| ())
    }

    fn delete_if_version_native(
        &self,
        path: &str,
        expected_version: Option<&str>,
    ) -> CoreResult<()> {
        let resolved = self.resolve_native(path);
        let target = shell_quote_path(&resolved);
        let expected_check = expected_version.map_or_else(
            || ":".to_string(),
            |version| format!("test \"$before\" = {} || exit 73", shell_quote(version)),
        );
        let command = format!(
            "target={target}; lock=\"${{target}}.file-tools.lock\"; \
             acquired=0; i=0; \
             while test \"$i\" -lt 200; do \
               if mkdir -- \"$lock\" 2>/dev/null; then acquired=1; break; fi; \
               sleep 0.05; i=$((i + 1)); \
             done; \
             test \"$acquired\" = 1 || exit 74; \
             cleanup() {{ rmdir -- \"$lock\" 2>/dev/null || true; }}; \
             trap cleanup EXIT HUP INT TERM; \
             if ! test -e \"$target\" && ! test -L \"$target\"; then exit 44; fi; \
             if test -L \"$target\"; then kind=symlink; \
             elif test -f \"$target\"; then kind=file; \
             elif test -d \"$target\"; then kind=directory; else kind=other; fi; \
             signature=$(stat -c '%s:%Y:%i:%y' -- \"$target\" 2>/dev/null || \
                         stat -f '%z:%m:%i' \"$target\") || exit 1; \
             before=\"remote:${{kind}}:${{signature}}\"; \
             {expected_check}; rm -f -- \"$target\" || exit 1; \
             trap - EXIT HUP INT TERM; rmdir -- \"$lock\" 2>/dev/null || true"
        );
        match self.checked_remote("delete", path, &command, None) {
            Ok(_) => Ok(()),
            Err(CoreError::Client(message)) if message.contains("exit code 44") => Err(
                CoreError::NotFound(format!("SSH delete failed: {path}: file does not exist")),
            ),
            Err(CoreError::Client(message)) if message.contains("exit code 73") => {
                Err(CoreError::Conflict(format!("delete conflict: {path}")))
            }
            Err(CoreError::Client(message)) if message.contains("exit code 74") => {
                Err(CoreError::Timeout(format!("delete lock timed out: {path}")))
            }
            Err(error) => Err(error),
        }
    }
}

#[pymethods]
impl SshClient {
    #[new]
    #[pyo3(signature = (
        host,
        *,
        port,
        username,
        password = None,
        key_filename = None,
        cwd = ".",
        connect_timeout = 30.0,
        operation_timeout = DEFAULT_FILE_OPERATION_TIMEOUT.as_secs_f64(),
        max_transfer_bytes = DEFAULT_MAX_TRANSFER_BYTES,
        multiplexing = true,
        ssh_flags = None,
        allow_password_prompt = true,
        accept_unknown_host_key = false
    ))]
    #[allow(clippy::too_many_arguments)]
    fn py_new(
        py: Python<'_>,
        host: &str,
        port: i64,
        username: &str,
        password: Option<&str>,
        key_filename: Option<&str>,
        cwd: &str,
        connect_timeout: f64,
        operation_timeout: f64,
        max_transfer_bytes: usize,
        multiplexing: bool,
        ssh_flags: Option<Py<PyAny>>,
        allow_password_prompt: bool,
        accept_unknown_host_key: bool,
    ) -> PyResult<Self> {
        if port <= 0 || port > u16::MAX as i64 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "ssh port is required and must be a positive integer",
            ));
        }
        let flags =
            normalize_flags_object(py, ssh_flags.as_ref(), true).map_err(CoreError::into_pyerr)?;
        py.allow_threads(|| {
            Self::connect(
                host,
                port as u16,
                username,
                password,
                key_filename,
                cwd,
                connect_timeout,
                operation_timeout,
                max_transfer_bytes,
                multiplexing,
                flags,
                allow_password_prompt,
                accept_unknown_host_key,
            )
        })
        .map_err(CoreError::into_pyerr)
    }

    #[classattr]
    fn kind() -> &'static str {
        SSH_CLIENT_KIND
    }

    #[getter]
    fn cwd(&self) -> String {
        self.cwd.clone()
    }

    fn resolve(&self, py: Python<'_>, path: &str) -> String {
        py.allow_threads(|| self.resolve_native(path))
    }

    #[pyo3(signature = (*parts))]
    fn join(&self, py: Python<'_>, parts: &Bound<'_, PyTuple>) -> String {
        let parts = parts
            .iter()
            .filter_map(|part| part.extract::<String>().ok())
            .collect::<Vec<_>>();
        py.allow_threads(|| join_posix(&parts))
    }

    fn stat(&self, py: Python<'_>, path: &str) -> PyResult<FileInfo> {
        py.allow_threads(|| self.stat_native(path))
            .map_err(CoreError::into_pyerr)
    }

    fn exists(&self, py: Python<'_>, path: &str) -> PyResult<bool> {
        self.stat(py, path).map(|info| info.exists)
    }

    fn is_file(&self, py: Python<'_>, path: &str) -> PyResult<bool> {
        self.stat(py, path).map(|info| info.is_file())
    }

    fn is_dir(&self, py: Python<'_>, path: &str) -> PyResult<bool> {
        self.stat(py, path).map(|info| info.is_dir())
    }

    fn path_info(&self, py: Python<'_>, path: &str) -> PyResult<(bool, bool, bool)> {
        self.stat(py, path)
            .map(|info| (info.exists, info.is_file(), info.is_dir()))
    }

    fn read_bytes(&self, py: Python<'_>, path: &str) -> PyResult<Vec<u8>> {
        py.allow_threads(|| self.read_bytes_native(path))
            .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (path, *, encoding = "utf-8"))]
    fn read_text(&self, py: Python<'_>, path: &str, encoding: &str) -> PyResult<String> {
        let bytes = py
            .allow_threads(|| self.read_bytes_native(path))
            .map_err(|error| {
                match error {
                    CoreError::Client(message) => CoreError::Client(message.replacen(
                        "SSH read_bytes failed",
                        "SSH read_text failed",
                        1,
                    )),
                    other => other,
                }
                .into_pyerr()
            })?;
        decode_text(
            py,
            &bytes,
            encoding,
            &format!("SSH read_text failed: {path}"),
        )
        .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (path, offset, limit, *, encoding = "utf-8"))]
    fn read_text_window(
        &self,
        py: Python<'_>,
        path: &str,
        offset: i64,
        limit: usize,
        encoding: &str,
    ) -> PyResult<(String, usize, usize, usize, bool)> {
        if limit == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err("limit must be > 0"));
        }
        let (bytes, total, start, end, truncated) = py
            .allow_threads(|| self.read_text_window_native(path, offset, limit))
            .map_err(CoreError::into_pyerr)?;
        let text = decode_text(
            py,
            &bytes,
            encoding,
            &format!("SSH read_text failed: {path}"),
        )
        .map_err(CoreError::into_pyerr)?;
        Ok((text, total, start, end, truncated))
    }

    fn write_bytes(&self, py: Python<'_>, path: &str, data: Vec<u8>) -> PyResult<()> {
        py.allow_threads(|| self.write_bytes_native(path, data))
            .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (path, content, *, encoding = "utf-8"))]
    fn write_text(
        &self,
        py: Python<'_>,
        path: &str,
        content: Py<PyAny>,
        encoding: &str,
    ) -> PyResult<()> {
        let bytes = encode_text(
            py,
            &content,
            encoding,
            &format!("SSH write_text failed: {path}"),
        )
        .map_err(CoreError::into_pyerr)?;
        py.allow_threads(|| self.write_bytes_native(path, bytes))
            .map_err(|error| match error {
                CoreError::Client(message) => CoreError::Client(message.replacen(
                    "SSH write_bytes failed",
                    "SSH write_text failed",
                    1,
                )),
                other => other,
            })
            .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (
        path,
        content,
        *,
        encoding = "utf-8",
        expected_version = None,
        create_only = false
    ))]
    fn write_text_atomic(
        &self,
        py: Python<'_>,
        path: &str,
        content: Py<PyAny>,
        encoding: &str,
        expected_version: Option<&str>,
        create_only: bool,
    ) -> PyResult<FileInfo> {
        let bytes = encode_text(
            py,
            &content,
            encoding,
            &format!("SSH write_text_atomic failed: {path}"),
        )
        .map_err(CoreError::into_pyerr)?;
        py.allow_threads(|| {
            self.write_bytes_atomic_native(path, bytes, expected_version, create_only)
        })
        .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (path, *, parents = true, exist_ok = true))]
    fn mkdir(&self, py: Python<'_>, path: &str, parents: bool, exist_ok: bool) -> PyResult<()> {
        let resolved = self.resolve_native(path);
        let command = if parents && exist_ok {
            format!("mkdir -p -- {}", shell_quote_path(&resolved))
        } else if parents {
            let parent = Path::new(&resolved)
                .parent()
                .and_then(Path::to_str)
                .unwrap_or("/");
            format!(
                "mkdir -p -- {} && mkdir -- {}",
                shell_quote_path(parent),
                shell_quote_path(&resolved)
            )
        } else if exist_ok {
            format!(
                "mkdir -- {0} 2>/dev/null || test -d {0}",
                shell_quote_path(&resolved)
            )
        } else {
            format!("mkdir -- {}", shell_quote_path(&resolved))
        };
        py.allow_threads(|| self.checked_remote("mkdir", path, &command, None))
            .map(|_| ())
            .map_err(CoreError::into_pyerr)
    }

    fn delete(&self, py: Python<'_>, path: &str) -> PyResult<()> {
        py.allow_threads(|| self.delete_if_version_native(path, None))
            .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (path, *, expected_version = None))]
    fn delete_if_version(
        &self,
        py: Python<'_>,
        path: &str,
        expected_version: Option<&str>,
    ) -> PyResult<()> {
        py.allow_threads(|| self.delete_if_version_native(path, expected_version))
            .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (
        command,
        *,
        cwd = None,
        timeout = None,
        env = None,
        stdin = None,
        interpreter = None,
        flags = None,
        max_output_bytes = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn exec_command(
        &self,
        py: Python<'_>,
        command: String,
        cwd: Option<String>,
        timeout: Option<Py<PyAny>>,
        env: Option<Py<PyAny>>,
        stdin: Option<Py<PyAny>>,
        interpreter: Option<String>,
        flags: Option<Py<PyAny>>,
        max_output_bytes: Option<Py<PyAny>>,
    ) -> PyResult<CommandResult> {
        let workdir = cwd
            .as_deref()
            .filter(|cwd| !cwd.is_empty())
            .map(|cwd| self.resolve_native(cwd))
            .unwrap_or_else(|| self.cwd.clone());
        let timeout = normalize_timeout(py, timeout.as_ref()).map_err(CoreError::into_pyerr)?;
        let output_limit = validate_max_output_bytes(py, max_output_bytes.as_ref())
            .map_err(CoreError::into_pyerr)?;
        let env = normalize_env(py, env.as_ref()).map_err(CoreError::into_pyerr)?;
        let input = stdin
            .as_ref()
            .map(|value| encode_text(py, value, "utf-8", "stdin must be valid UTF-8 text"))
            .transpose()
            .map_err(CoreError::into_pyerr)?;
        let flag_list =
            normalize_flags_object(py, flags.as_ref(), true).map_err(CoreError::into_pyerr)?;

        let mut parts = vec![format!("cd {}", shell_quote_path(&workdir))];
        for (key, value) in env {
            parts.push(format!(
                "export {}={}",
                shell_quote(&key),
                shell_quote(&value)
            ));
        }
        let mut extras = HashMap::new();
        if let Some(interpreter) = interpreter.filter(|value| !value.trim().is_empty()) {
            let interpreter = interpreter.trim().to_string();
            let effective = inject_command_flag(&interpreter, &flag_list);
            let mut invocation = vec![shell_quote(&interpreter)];
            invocation.extend(effective.iter().map(|flag| shell_quote(flag)));
            invocation.push(shell_quote(&command));
            parts.push(invocation.join(" "));
            extras.insert("interpreter".to_string(), interpreter);
            if !effective.is_empty() {
                extras.insert("flags".to_string(), effective.join(" "));
            }
        } else {
            parts.push(command.clone());
        }
        let actual = parts.join(" && ");
        let token = control_token();
        let prefix = format!("__FILE_TOOLS_{token}__");
        let flush_function = format!("__file_tools_flush_{token}");
        let status_variable = format!("__file_tools_status_{token}");
        let actual = format!(
            "{flush_function}() {{ {status_variable}=\"$1\"; trap - EXIT; \
             sleep 0.01; exit \"${status_variable}\"; }}; \
             trap '{flush_function} \"$?\"' EXIT; {actual}"
        );
        let pgid_inner = format!(
            "printf '%s PGID %s\\n' {} \"$$\" >&2; exec sh -c {}",
            shell_quote(&prefix),
            shell_quote(&actual)
        );
        let pid_inner = format!(
            "printf '%s PID %s\\n' {} \"$$\" >&2; exec sh -c {}",
            shell_quote(&prefix),
            shell_quote(&actual)
        );
        let remote = format!(
            "if command -v setsid >/dev/null 2>&1 && \
             setsid --wait true >/dev/null 2>&1; then \
             exec setsid --wait sh -c {}; \
             elif command -v setsid >/dev/null 2>&1 && \
             setsid -w true >/dev/null 2>&1; then \
             exec setsid -w sh -c {}; \
             else exec sh -c {}; fi",
            shell_quote(&pgid_inner),
            shell_quote(&pgid_inner),
            shell_quote(&pid_inner)
        );
        let invocation = self
            .ssh_invocation(&remote, input, timeout, output_limit)
            .map_err(CoreError::into_pyerr)?;
        let terminate = |stderr: &[u8]| {
            if let Some(marker) = find_remote_process(stderr, &prefix) {
                self.terminate_remote(marker);
            }
        };
        let mut output = py
            .allow_threads(|| run_process(invocation.spec, Some(&terminate)))
            .map_err(crate::client::ClientError::new_err)?;
        let (cleaned, marker_bytes) = strip_control_marker(&output.stderr, &prefix);
        output.stderr = cleaned;
        output.stderr_total_bytes = output.stderr_total_bytes.saturating_sub(marker_bytes);
        Ok(command_result(output, command, workdir, extras))
    }

    fn __repr__(&self) -> String {
        format!(
            "SshClient(host={:?}, port={}, username={:?}, cwd={:?})",
            self.host, self.port, self.username, self.cwd
        )
    }
}
