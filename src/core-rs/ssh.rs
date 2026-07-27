use crate::client::{
    command_result, decode_text, encode_text, inject_command_flag, normalize_env,
    normalize_flags_object, normalize_timeout, validate_max_output_bytes, CommandResult, CoreError,
    CoreResult,
};
use crate::command::{run_process, ProcessOutput, ProcessSpec};
use crate::constants::{
    FILE_TRANSFER_OUTPUT_LIMIT, REMOTE_KILL_TIMEOUT, SSH_CLIENT_KIND, TOKEN_COUNTER,
};
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
        ssh_flags: Vec<String>,
        allow_password_prompt: bool,
        accept_unknown_host_key: bool,
    ) -> CoreResult<Self> {
        let host = host.trim();
        let username = username.trim();
        if host.is_empty() {
            return Err(CoreError::Value("ssh host is required".to_string()));
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
            home: ".".to_string(),
            cwd: ".".to_string(),
        };
        let probe = client.run_ssh(
            "pwd",
            None,
            Some(Duration::from_secs_f64(connect_timeout)),
            8192,
        )?;
        if probe.exit_code != 0 {
            let message = String::from_utf8_lossy(&probe.stderr).trim().to_string();
            if message.to_ascii_lowercase().contains("permission denied") {
                return Err(CoreError::IncorrectPassword(format!(
                    "SSH authentication failed for {}@{}{}",
                    client.username,
                    client.host,
                    if client.password.is_some() {
                        " (incorrect password)"
                    } else {
                        ""
                    }
                )));
            }
            return Err(CoreError::Client(format!("SSH connect failed: {message}")));
        }
        let home = String::from_utf8_lossy(&probe.stdout).trim().to_string();
        if !home.starts_with('/') {
            return Err(CoreError::Client(format!(
                "SSH connect failed: remote pwd returned an invalid path: {home:?}"
            )));
        }
        client.home = normalize_posix(&home);
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
        if self.ssh_flags.iter().any(|flag| flag == "-a") {
            args.extend([
                "-o".to_string(),
                "IdentitiesOnly=yes".to_string(),
                "-o".to_string(),
                "IdentityAgent=none".to_string(),
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
        let output = self.run_ssh(remote_command, stdin, None, FILE_TRANSFER_OUTPUT_LIMIT)?;
        if output.exit_code == 0 {
            return Ok(output);
        }
        let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
        Err(CoreError::Client(format!(
            "SSH {action} failed: {path}: {message}"
        )))
    }

    fn read_bytes_native(&self, path: &str) -> CoreResult<Vec<u8>> {
        let resolved = self.resolve_native(path);
        self.checked_remote(
            "read_bytes",
            path,
            &format!("cat -- {}", shell_quote(&resolved)),
            None,
        )
        .map(|output| output.stdout)
    }

    fn write_bytes_native(&self, path: &str, data: Vec<u8>) -> CoreResult<()> {
        let resolved = self.resolve_native(path);
        let parent = Path::new(&resolved)
            .parent()
            .and_then(Path::to_str)
            .unwrap_or("/");
        let command = format!(
            "mkdir -p -- {} && cat > {}",
            shell_quote(parent),
            shell_quote(&resolved)
        );
        self.checked_remote("write_bytes", path, &command, Some(data))
            .map(|_| ())
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

    fn resolve(&self, path: &str) -> String {
        self.resolve_native(path)
    }

    #[pyo3(signature = (*parts))]
    fn join(&self, parts: &Bound<'_, PyTuple>) -> String {
        let parts = parts
            .iter()
            .filter_map(|part| part.extract::<String>().ok())
            .collect::<Vec<_>>();
        join_posix(&parts)
    }

    fn exists(&self, py: Python<'_>, path: &str) -> bool {
        let command = format!("test -e {}", shell_quote(&self.resolve_native(path)));
        py.allow_threads(|| self.run_ssh(&command, None, None, 8192))
            .is_ok_and(|output| output.exit_code == 0)
    }

    fn is_file(&self, py: Python<'_>, path: &str) -> bool {
        let command = format!("test -f {}", shell_quote(&self.resolve_native(path)));
        py.allow_threads(|| self.run_ssh(&command, None, None, 8192))
            .is_ok_and(|output| output.exit_code == 0)
    }

    fn is_dir(&self, py: Python<'_>, path: &str) -> bool {
        let command = format!("test -d {}", shell_quote(&self.resolve_native(path)));
        py.allow_threads(|| self.run_ssh(&command, None, None, 8192))
            .is_ok_and(|output| output.exit_code == 0)
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

    #[pyo3(signature = (path, *, parents = true, exist_ok = true))]
    fn mkdir(&self, py: Python<'_>, path: &str, parents: bool, exist_ok: bool) -> PyResult<()> {
        let resolved = self.resolve_native(path);
        let command = if parents && exist_ok {
            format!("mkdir -p -- {}", shell_quote(&resolved))
        } else if parents {
            let parent = Path::new(&resolved)
                .parent()
                .and_then(Path::to_str)
                .unwrap_or("/");
            format!(
                "mkdir -p -- {} && mkdir -- {}",
                shell_quote(parent),
                shell_quote(&resolved)
            )
        } else if exist_ok {
            format!(
                "mkdir -- {0} 2>/dev/null || test -d {0}",
                shell_quote(&resolved)
            )
        } else {
            format!("mkdir -- {}", shell_quote(&resolved))
        };
        py.allow_threads(|| self.checked_remote("mkdir", path, &command, None))
            .map(|_| ())
            .map_err(CoreError::into_pyerr)
    }

    fn delete(&self, py: Python<'_>, path: &str) -> PyResult<()> {
        let resolved = self.resolve_native(path);
        let command = format!("rm -- {}", shell_quote(&resolved));
        py.allow_threads(|| self.checked_remote("delete", path, &command, None))
            .map(|_| ())
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

        let mut parts = vec![format!("cd {}", shell_quote(&workdir))];
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
