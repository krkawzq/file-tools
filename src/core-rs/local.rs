use crate::client::{
    command_result, decode_text, encode_text, inject_command_flag, normalize_env,
    normalize_flags_object, normalize_timeout, validate_max_output_bytes, CommandResult, CoreError,
    CoreResult,
};
use crate::command::{run_process, ProcessSpec};
use crate::constants::{DEFAULT_MAX_TRANSFER_BYTES, LOCAL_CLIENT_KIND, MAX_TRANSFER_BYTES};
use crate::fs::{local_file_info, read_local_window, FileInfo};
use pyo3::prelude::*;
use pyo3::types::PyAny;
use std::collections::HashMap;
use std::hash::{DefaultHasher, Hash, Hasher};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, OnceLock};

const PATH_LOCK_SHARDS: usize = 128;

fn path_locks() -> &'static Vec<Mutex<()>> {
    static LOCKS: OnceLock<Vec<Mutex<()>>> = OnceLock::new();
    LOCKS.get_or_init(|| (0..PATH_LOCK_SHARDS).map(|_| Mutex::new(())).collect())
}

fn path_lock_index(path: &Path) -> usize {
    let mut hasher = DefaultHasher::new();
    path.hash(&mut hasher);
    hasher.finish() as usize % PATH_LOCK_SHARDS
}

fn expand_home(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if text == "~" {
        return std::env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| path.to_path_buf());
    }
    if let Some(rest) = text.strip_prefix("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    path.to_path_buf()
}

fn lexical_normalize(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if !result.pop() {
                    result.push(component.as_os_str());
                }
            }
            _ => result.push(component.as_os_str()),
        }
    }
    result
}

fn absolute_path(path: &Path) -> CoreResult<PathBuf> {
    let expanded = expand_home(path);
    let absolute = if expanded.is_absolute() {
        expanded
    } else {
        std::env::current_dir()
            .map_err(|error| {
                CoreError::Client(format!("cannot resolve current directory: {error}"))
            })?
            .join(expanded)
    };
    Ok(absolute
        .canonicalize()
        .unwrap_or_else(|_| lexical_normalize(&absolute)))
}

#[pyclass(module = "file_tools._core")]
pub struct LocalClient {
    cwd: PathBuf,
    max_transfer_bytes: usize,
}

impl LocalClient {
    pub fn new_native(cwd: Option<&str>, max_transfer_bytes: usize) -> CoreResult<Self> {
        if max_transfer_bytes == 0 || max_transfer_bytes > MAX_TRANSFER_BYTES {
            return Err(CoreError::Value(format!(
                "max_transfer_bytes must be between 1 and {MAX_TRANSFER_BYTES}"
            )));
        }
        let path = cwd
            .filter(|cwd| !cwd.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        Ok(Self {
            cwd: absolute_path(&path)?,
            max_transfer_bytes,
        })
    }

    fn resolve_native(&self, path: &str) -> String {
        let expanded = expand_home(Path::new(path));
        let candidate = if expanded.is_absolute() {
            expanded
        } else {
            self.cwd.join(expanded)
        };
        candidate
            .canonicalize()
            .unwrap_or_else(|_| lexical_normalize(&candidate))
            .to_string_lossy()
            .into_owned()
    }

    fn read_bytes_native(&self, path: &str) -> CoreResult<Vec<u8>> {
        let resolved = PathBuf::from(self.resolve_native(path));
        let info = local_file_info(&resolved, path)?;
        if !info.exists {
            return Err(CoreError::NotFound(format!(
                "read_bytes failed: {path}: file does not exist"
            )));
        }
        if info.size > self.max_transfer_bytes as u64 {
            return Err(CoreError::TransferLimit(format!(
                "read_bytes failed: {path}: file size {} exceeds the configured transfer limit of {} bytes",
                info.size, self.max_transfer_bytes
            )));
        }
        std::fs::read(resolved).map_err(|error| CoreError::from_io("read_bytes", path, error))
    }

    fn write_bytes_atomic_native(
        &self,
        path: &str,
        data: &[u8],
        expected_version: Option<&str>,
        create_only: bool,
    ) -> CoreResult<FileInfo> {
        if data.len() > self.max_transfer_bytes {
            return Err(CoreError::TransferLimit(format!(
                "write_bytes failed: {path}: content size {} exceeds the configured transfer limit of {} bytes",
                data.len(), self.max_transfer_bytes
            )));
        }
        let resolved = PathBuf::from(self.resolve_native(path));
        if let Some(parent) = resolved.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| CoreError::from_io("write_bytes", path, error))?;
        }
        let lock = &path_locks()[path_lock_index(&resolved)];
        let _guard = lock.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let before = local_file_info(&resolved, path)?;
        if create_only && before.exists {
            return Err(CoreError::Conflict(format!(
                "write conflict: {path}: destination already exists"
            )));
        }
        if let Some(expected) = expected_version {
            if before.version.as_deref() != Some(expected) {
                return Err(CoreError::Conflict(format!(
                    "write conflict: {path}: expected version {expected:?}, found {:?}",
                    before.version
                )));
            }
        }

        let parent = resolved.parent().unwrap_or_else(|| Path::new("."));
        let mut temporary = tempfile::Builder::new()
            .prefix(".file-tools-")
            .suffix(".tmp")
            .tempfile_in(parent)
            .map_err(|error| CoreError::from_io("write_bytes", path, error))?;
        temporary
            .write_all(data)
            .map_err(|error| CoreError::from_io("write_bytes", path, error))?;
        temporary
            .flush()
            .map_err(|error| CoreError::from_io("write_bytes", path, error))?;
        if before.exists {
            let permissions = std::fs::symlink_metadata(&resolved)
                .map_err(|error| CoreError::from_io("write_bytes", path, error))?
                .permissions();
            temporary
                .as_file()
                .set_permissions(permissions)
                .map_err(|error| CoreError::from_io("write_bytes", path, error))?;
        }
        temporary
            .persist(&resolved)
            .map_err(|error| CoreError::from_io("write_bytes", path, error.error))?;
        local_file_info(&resolved, path)
    }

    fn write_bytes_native(&self, path: &str, data: &[u8]) -> CoreResult<()> {
        self.write_bytes_atomic_native(path, data, None, false)
            .map(|_| ())
    }

    fn delete_if_version_native(
        &self,
        path: &str,
        expected_version: Option<&str>,
    ) -> CoreResult<()> {
        let resolved = PathBuf::from(self.resolve_native(path));
        let lock = &path_locks()[path_lock_index(&resolved)];
        let _guard = lock.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let before = local_file_info(&resolved, path)?;
        if !before.exists {
            return Err(CoreError::NotFound(format!(
                "delete failed: {path}: file does not exist"
            )));
        }
        if let Some(expected) = expected_version {
            if before.version.as_deref() != Some(expected) {
                return Err(CoreError::Conflict(format!(
                    "delete conflict: {path}: expected version {expected:?}, found {:?}",
                    before.version
                )));
            }
        }
        std::fs::remove_file(resolved).map_err(|error| CoreError::from_io("delete", path, error))
    }
}

#[pymethods]
impl LocalClient {
    #[new]
    #[pyo3(signature = (cwd = None, *, max_transfer_bytes = DEFAULT_MAX_TRANSFER_BYTES))]
    fn py_new(py: Python<'_>, cwd: Option<PathBuf>, max_transfer_bytes: usize) -> PyResult<Self> {
        let cwd = cwd.as_ref().map(|path| path.to_string_lossy());
        py.allow_threads(|| Self::new_native(cwd.as_deref(), max_transfer_bytes))
            .map_err(CoreError::into_pyerr)
    }

    #[classattr]
    fn kind() -> &'static str {
        LOCAL_CLIENT_KIND
    }

    #[getter]
    fn cwd(&self) -> String {
        self.cwd.to_string_lossy().into_owned()
    }

    fn resolve(&self, py: Python<'_>, path: &str) -> String {
        py.allow_threads(|| self.resolve_native(path))
    }

    #[pyo3(signature = (*parts))]
    fn join(&self, py: Python<'_>, parts: &Bound<'_, pyo3::types::PyTuple>) -> String {
        let mut result = PathBuf::new();
        for part in parts.iter() {
            if let Ok(value) = part.extract::<String>() {
                result.push(value);
            }
        }
        py.allow_threads(|| result.to_string_lossy().into_owned())
    }

    fn stat(&self, py: Python<'_>, path: &str) -> PyResult<FileInfo> {
        py.allow_threads(|| {
            let resolved = PathBuf::from(self.resolve_native(path));
            local_file_info(&resolved, path)
        })
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

    #[pyo3(signature = (path, *, encoding = "utf-8"))]
    fn read_text(&self, py: Python<'_>, path: &str, encoding: &str) -> PyResult<String> {
        let bytes = py
            .allow_threads(|| self.read_bytes_native(path))
            .map_err(|error| {
                match error {
                    CoreError::Client(message) => CoreError::Client(message.replacen(
                        "read_bytes failed",
                        "read_text failed",
                        1,
                    )),
                    other => other,
                }
                .into_pyerr()
            })?;
        decode_text(py, &bytes, encoding, &format!("read_text failed: {path}"))
            .map_err(CoreError::into_pyerr)
    }

    fn read_bytes(&self, py: Python<'_>, path: &str) -> PyResult<Vec<u8>> {
        py.allow_threads(|| self.read_bytes_native(path))
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
        let resolved = PathBuf::from(self.resolve_native(path));
        let window = py
            .allow_threads(|| {
                read_local_window(&resolved, path, offset, limit, self.max_transfer_bytes)
            })
            .map_err(CoreError::into_pyerr)?;
        let text = decode_text(
            py,
            &window.bytes,
            encoding,
            &format!("read_text failed: {path}"),
        )
        .map_err(CoreError::into_pyerr)?;
        Ok((
            text,
            window.total_lines,
            window.start_line,
            window.end_line,
            window.truncated,
        ))
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
            &format!("write_text failed: {path}"),
        )
        .map_err(CoreError::into_pyerr)?;
        py.allow_threads(|| self.write_bytes_native(path, &bytes))
            .map_err(|error| match error {
                CoreError::Client(message) => CoreError::Client(message.replacen(
                    "write_bytes failed",
                    "write_text failed",
                    1,
                )),
                other => other,
            })
            .map_err(CoreError::into_pyerr)
    }

    fn write_bytes(&self, py: Python<'_>, path: &str, data: Vec<u8>) -> PyResult<()> {
        py.allow_threads(|| self.write_bytes_native(path, &data))
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
            &format!("write_text_atomic failed: {path}"),
        )
        .map_err(CoreError::into_pyerr)?;
        py.allow_threads(|| {
            self.write_bytes_atomic_native(path, &bytes, expected_version, create_only)
        })
        .map_err(CoreError::into_pyerr)
    }

    #[pyo3(signature = (path, *, parents = true, exist_ok = true))]
    fn mkdir(&self, py: Python<'_>, path: &str, parents: bool, exist_ok: bool) -> PyResult<()> {
        py.allow_threads(|| {
            let resolved = self.resolve_native(path);
            let result = if parents {
                if !exist_ok && Path::new(&resolved).exists() {
                    Err(std::io::Error::new(
                        std::io::ErrorKind::AlreadyExists,
                        "already exists",
                    ))
                } else {
                    std::fs::create_dir_all(&resolved)
                }
            } else if exist_ok {
                match std::fs::create_dir(&resolved) {
                    Ok(()) => Ok(()),
                    Err(error)
                        if error.kind() == std::io::ErrorKind::AlreadyExists
                            && Path::new(&resolved).is_dir() =>
                    {
                        Ok(())
                    }
                    Err(error) => Err(error),
                }
            } else {
                std::fs::create_dir(&resolved)
            };
            result.map_err(|error| format!("mkdir failed: {path}: {error}"))
        })
        .map_err(crate::client::ClientError::new_err)
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
            .unwrap_or_else(|| self.cwd());
        let timeout = normalize_timeout(py, timeout.as_ref()).map_err(CoreError::into_pyerr)?;
        let output_limit = validate_max_output_bytes(py, max_output_bytes.as_ref())
            .map_err(CoreError::into_pyerr)?;
        let env = normalize_env(py, env.as_ref()).map_err(CoreError::into_pyerr)?;
        let input = stdin
            .as_ref()
            .map(|value| encode_text(py, value, "utf-8", "stdin must be valid UTF-8 text"))
            .transpose()
            .map_err(CoreError::into_pyerr)?;
        let flag_list = normalize_flags_object(py, flags.as_ref(), !cfg!(windows))
            .map_err(CoreError::into_pyerr)?;

        let mut extras = HashMap::new();
        let (program, args) =
            if let Some(interpreter) = interpreter.filter(|value| !value.trim().is_empty()) {
                let interpreter = interpreter.trim().to_string();
                let effective = inject_command_flag(&interpreter, &flag_list);
                extras.insert("interpreter".to_string(), interpreter.clone());
                if !effective.is_empty() {
                    extras.insert("flags".to_string(), effective.join(" "));
                }
                let mut args = effective;
                args.push(command.clone());
                (interpreter, args)
            } else if cfg!(windows) {
                ("cmd".to_string(), vec!["/c".to_string(), command.clone()])
            } else {
                (
                    "/bin/sh".to_string(),
                    vec!["-c".to_string(), command.clone()],
                )
            };

        let output = py
            .allow_threads(|| {
                run_process(
                    ProcessSpec {
                        program,
                        args,
                        cwd: Some(workdir.clone()),
                        env,
                        stdin: input,
                        timeout,
                        max_output_bytes: output_limit,
                    },
                    None,
                )
            })
            .map_err(crate::client::ClientError::new_err)?;
        Ok(command_result(output, command, workdir, extras))
    }

    fn __repr__(&self) -> String {
        format!("LocalClient(cwd={:?})", self.cwd())
    }
}
