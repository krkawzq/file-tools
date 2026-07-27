use crate::client::{
    command_result, decode_text, encode_text, inject_command_flag, normalize_env,
    normalize_flags_object, normalize_timeout, validate_max_output_bytes, CommandResult, CoreError,
    CoreResult,
};
use crate::command::{run_process, ProcessSpec};
use crate::constants::LOCAL_CLIENT_KIND;
use pyo3::prelude::*;
use pyo3::types::PyAny;
use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};

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
}

impl LocalClient {
    pub fn new_native(cwd: Option<&str>) -> CoreResult<Self> {
        let path = cwd
            .filter(|cwd| !cwd.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        Ok(Self {
            cwd: absolute_path(&path)?,
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
        std::fs::read(self.resolve_native(path))
            .map_err(|error| CoreError::Client(format!("read_bytes failed: {path}: {error}")))
    }

    fn write_bytes_native(&self, path: &str, data: &[u8]) -> CoreResult<()> {
        let resolved = PathBuf::from(self.resolve_native(path));
        if let Some(parent) = resolved.parent() {
            std::fs::create_dir_all(parent).map_err(|error| {
                CoreError::Client(format!("write_bytes failed: {path}: {error}"))
            })?;
        }
        std::fs::write(&resolved, data)
            .map_err(|error| CoreError::Client(format!("write_bytes failed: {path}: {error}")))
    }
}

#[pymethods]
impl LocalClient {
    #[new]
    #[pyo3(signature = (cwd = None))]
    fn py_new(py: Python<'_>, cwd: Option<PathBuf>) -> PyResult<Self> {
        let cwd = cwd.as_ref().map(|path| path.to_string_lossy());
        py.allow_threads(|| Self::new_native(cwd.as_deref()))
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

    fn exists(&self, py: Python<'_>, path: &str) -> bool {
        py.allow_threads(|| Path::new(&self.resolve_native(path)).exists())
    }

    fn is_file(&self, py: Python<'_>, path: &str) -> bool {
        py.allow_threads(|| Path::new(&self.resolve_native(path)).is_file())
    }

    fn is_dir(&self, py: Python<'_>, path: &str) -> bool {
        py.allow_threads(|| Path::new(&self.resolve_native(path)).is_dir())
    }

    fn path_info(&self, py: Python<'_>, path: &str) -> (bool, bool, bool) {
        py.allow_threads(|| match std::fs::metadata(self.resolve_native(path)) {
            Ok(metadata) => (true, metadata.is_file(), metadata.is_dir()),
            Err(_) => (false, false, false),
        })
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
        py.allow_threads(|| {
            std::fs::remove_file(self.resolve_native(path))
                .map_err(|error| format!("delete failed: {path}: {error}"))
        })
        .map_err(crate::client::ClientError::new_err)
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
