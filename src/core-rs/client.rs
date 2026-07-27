use crate::constants::{DEFAULT_MAX_OUTPUT_BYTES, MAX_CONFIGURABLE_OUTPUT_BYTES};
use crate::local::LocalClient;
use crate::ssh::SshClient;
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyBytes, PyMapping, PyModule, PyString, PyTuple};
use std::collections::HashMap;
use std::path::Path;
use std::time::Duration;

create_exception!(_core, ClientError, PyException);

#[derive(Debug)]
pub enum CoreError {
    Client(String),
    Value(String),
}

impl CoreError {
    pub fn into_pyerr(self) -> PyErr {
        match self {
            Self::Client(message) => ClientError::new_err(message),
            Self::Value(message) => PyValueError::new_err(message),
        }
    }
}

pub type CoreResult<T> = Result<T, CoreError>;

#[pyclass(module = "file_tools._core", frozen)]
#[derive(Clone, Debug)]
pub struct CommandResult {
    #[pyo3(get)]
    pub exit_code: i32,
    #[pyo3(get)]
    pub stdout: String,
    #[pyo3(get)]
    pub stderr: String,
    #[pyo3(get)]
    pub command: String,
    #[pyo3(get)]
    pub cwd: String,
    #[pyo3(get)]
    pub duration_ms: u128,
    #[pyo3(get)]
    pub timed_out: bool,
    #[pyo3(get)]
    pub signal: Option<i32>,
    #[pyo3(get)]
    pub stdout_total_bytes: usize,
    #[pyo3(get)]
    pub stderr_total_bytes: usize,
    #[pyo3(get)]
    pub stdout_omitted_bytes: usize,
    #[pyo3(get)]
    pub stderr_omitted_bytes: usize,
    extras: HashMap<String, String>,
}

#[pymethods]
impl CommandResult {
    #[getter]
    fn ok(&self) -> bool {
        self.exit_code == 0 && !self.timed_out
    }

    #[getter]
    fn truncated(&self) -> bool {
        self.stdout_omitted_bytes > 0 || self.stderr_omitted_bytes > 0
    }

    #[getter]
    fn extras(&self) -> HashMap<String, String> {
        self.extras.clone()
    }

    fn __bool__(&self) -> bool {
        self.ok()
    }

    fn __repr__(&self) -> String {
        format!(
            "CommandResult(exit_code={}, timed_out={}, stdout_bytes={}, stderr_bytes={})",
            self.exit_code, self.timed_out, self.stdout_total_bytes, self.stderr_total_bytes
        )
    }
}

pub fn command_result(
    output: crate::command::ProcessOutput,
    command: String,
    cwd: String,
    extras: HashMap<String, String>,
) -> CommandResult {
    CommandResult {
        exit_code: output.exit_code,
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        command,
        cwd,
        duration_ms: output.duration_ms,
        timed_out: output.timed_out,
        signal: output.signal,
        stdout_total_bytes: output.stdout_total_bytes,
        stderr_total_bytes: output.stderr_total_bytes,
        stdout_omitted_bytes: output.stdout_omitted_bytes,
        stderr_omitted_bytes: output.stderr_omitted_bytes,
        extras,
    }
}

fn split_windows_flags(value: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let mut quote: Option<char> = None;
    for character in value.chars() {
        match (quote, character) {
            (Some(active), current) if current == active => quote = None,
            (None, '"' | '\'') => quote = Some(character),
            (None, current) if current.is_whitespace() => {
                if !token.is_empty() {
                    tokens.push(std::mem::take(&mut token));
                }
            }
            (_, current) => token.push(current),
        }
    }
    if !token.is_empty() {
        tokens.push(token);
    }
    tokens
}

pub fn normalize_flags_object(
    py: Python<'_>,
    flags: Option<&Py<PyAny>>,
    posix: bool,
) -> CoreResult<Vec<String>> {
    let Some(flags) = flags else {
        return Ok(Vec::new());
    };
    let value = flags.bind(py);
    if value.is_none() {
        return Ok(Vec::new());
    }
    if value.is_instance_of::<PyString>() {
        let text = value
            .extract::<String>()
            .map_err(|error| CoreError::Value(error.to_string()))?;
        let text = text.trim();
        if text.is_empty() {
            return Ok(Vec::new());
        }
        return if posix {
            shell_words::split(text)
                .map_err(|error| CoreError::Value(format!("invalid flags: {error}")))
        } else {
            Ok(split_windows_flags(text))
        };
    }

    let iterator = value
        .try_iter()
        .map_err(|_| CoreError::Value("flags must be a string or sequence".to_string()))?;
    let mut result = Vec::new();
    for item in iterator {
        let item = item.map_err(|error| CoreError::Value(error.to_string()))?;
        result.push(
            item.str()
                .map_err(|error| CoreError::Value(error.to_string()))?
                .to_string_lossy()
                .into_owned(),
        );
    }
    Ok(result)
}

#[pyfunction]
#[pyo3(signature = (flags = None, *, posix = None))]
fn normalize_flags(
    py: Python<'_>,
    flags: Option<Py<PyAny>>,
    posix: Option<bool>,
) -> PyResult<Vec<String>> {
    normalize_flags_object(py, flags.as_ref(), posix.unwrap_or(!cfg!(windows)))
        .map_err(CoreError::into_pyerr)
}

pub fn inject_command_flag(interpreter: &str, flags: &[String]) -> Vec<String> {
    let basename = Path::new(interpreter)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(interpreter)
        .to_ascii_lowercase();
    let name = basename.strip_suffix(".exe").unwrap_or(&basename);
    let expected = match name {
        "bash" | "sh" | "zsh" | "dash" | "ksh" | "fish" | "python" | "python3" | "ruby"
        | "perl" => Some("-c"),
        "pwsh" | "powershell" => Some("-Command"),
        "cmd" => Some("/c"),
        _ if name.starts_with("python3.") => Some("-c"),
        _ => None,
    };
    let Some(expected) = expected else {
        return flags.to_vec();
    };

    if flags.iter().any(|flag| {
        flag.eq_ignore_ascii_case(expected)
            || (expected == "-c"
                && flag.starts_with('-')
                && !flag.starts_with("--")
                && flag[1..].to_ascii_lowercase().contains('c'))
    }) {
        return flags.to_vec();
    }
    let mut result = flags.to_vec();
    result.push(expected.to_string());
    result
}

#[pyfunction]
fn inject_cmd_flag(interpreter: &str, flags: Vec<String>) -> Vec<String> {
    inject_command_flag(interpreter, &flags)
}

pub fn normalize_timeout(
    py: Python<'_>,
    timeout: Option<&Py<PyAny>>,
) -> CoreResult<Option<Duration>> {
    let Some(timeout) = timeout else {
        return Ok(None);
    };
    let value = timeout.bind(py);
    if value.is_none() {
        return Ok(None);
    }
    if value.is_instance_of::<PyBool>() {
        return Err(CoreError::Client(
            "timeout must be a non-negative finite number or None".to_string(),
        ));
    }
    let seconds = value.extract::<f64>().map_err(|_| {
        CoreError::Client("timeout must be a non-negative finite number or None".to_string())
    })?;
    if !seconds.is_finite() || seconds < 0.0 {
        return Err(CoreError::Client(
            "timeout must be a non-negative finite number or None".to_string(),
        ));
    }
    Ok(if seconds == 0.0 {
        None
    } else {
        Some(Duration::from_secs_f64(seconds))
    })
}

pub fn validate_max_output_bytes(py: Python<'_>, value: Option<&Py<PyAny>>) -> CoreResult<usize> {
    let Some(value) = value else {
        return Ok(DEFAULT_MAX_OUTPUT_BYTES);
    };
    let value = value.bind(py);
    if value.is_none() {
        return Ok(DEFAULT_MAX_OUTPUT_BYTES);
    }
    if value.is_instance_of::<PyBool>() {
        return Err(CoreError::Client(
            "max_output_bytes must be an integer".to_string(),
        ));
    }
    let parsed = value
        .extract::<isize>()
        .map_err(|_| CoreError::Client("max_output_bytes must be an integer".to_string()))?;
    if parsed <= 0 {
        return Err(CoreError::Client(
            "max_output_bytes must be greater than zero".to_string(),
        ));
    }
    let parsed = parsed as usize;
    if parsed > MAX_CONFIGURABLE_OUTPUT_BYTES {
        return Err(CoreError::Client(format!(
            "max_output_bytes must not exceed {MAX_CONFIGURABLE_OUTPUT_BYTES} bytes"
        )));
    }
    Ok(parsed)
}

fn valid_env_name(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some('_' | 'A'..='Z' | 'a'..='z'))
        && chars.all(|character| matches!(character, '_' | 'A'..='Z' | 'a'..='z' | '0'..='9'))
}

pub fn normalize_env(
    py: Python<'_>,
    env: Option<&Py<PyAny>>,
) -> CoreResult<HashMap<String, String>> {
    let Some(env) = env else {
        return Ok(HashMap::new());
    };
    let value = env.bind(py);
    if value.is_none() {
        return Ok(HashMap::new());
    }
    let mapping = value.downcast::<PyMapping>().map_err(|_| {
        CoreError::Client("env must be a mapping of variable names to values".into())
    })?;
    let items = mapping
        .items()
        .map_err(|error| CoreError::Client(error.to_string()))?;
    let mut result = HashMap::new();
    for item in items
        .try_iter()
        .map_err(|error| CoreError::Client(error.to_string()))?
    {
        let item = item.map_err(|error| CoreError::Client(error.to_string()))?;
        let tuple = item
            .downcast::<PyTuple>()
            .map_err(|_| CoreError::Client("env mapping item is not a pair".into()))?;
        let key = tuple
            .get_item(0)
            .map_err(|error| CoreError::Client(error.to_string()))?
            .str()
            .map_err(|error| CoreError::Client(error.to_string()))?
            .to_string_lossy()
            .into_owned();
        let value = tuple
            .get_item(1)
            .map_err(|error| CoreError::Client(error.to_string()))?
            .str()
            .map_err(|error| CoreError::Client(error.to_string()))?
            .to_string_lossy()
            .into_owned();
        if !valid_env_name(&key) {
            return Err(CoreError::Client(format!(
                "invalid environment variable name: {key:?}"
            )));
        }
        if value.contains('\0') {
            return Err(CoreError::Client(format!(
                "environment variable {key:?} contains a NUL byte"
            )));
        }
        result.insert(key, value);
    }
    Ok(result)
}

pub fn encode_text(
    py: Python<'_>,
    value: &Py<PyAny>,
    encoding: &str,
    context: &str,
) -> CoreResult<Vec<u8>> {
    let encoded = value
        .bind(py)
        .call_method1("encode", (encoding, "strict"))
        .map_err(|error| CoreError::Client(format!("{context}: {error}")))?;
    encoded
        .downcast::<PyBytes>()
        .map_err(|error| CoreError::Client(format!("{context}: {error}")))
        .map(|bytes| bytes.as_bytes().to_vec())
}

pub fn decode_text(
    py: Python<'_>,
    bytes: &[u8],
    encoding: &str,
    context: &str,
) -> CoreResult<String> {
    PyBytes::new(py, bytes)
        .call_method1("decode", (encoding, "replace"))
        .and_then(|value| value.extract::<String>())
        .map_err(|error| CoreError::Client(format!("{context}: {error}")))
}

pub fn register(py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("ClientError", py.get_type::<ClientError>())?;
    module.add_class::<CommandResult>()?;
    module.add_class::<LocalClient>()?;
    module.add_class::<SshClient>()?;
    let client_alias = module
        .getattr("LocalClient")?
        .call_method1("__or__", (module.getattr("SshClient")?,))?;
    module.add("Client", client_alias)?;
    module.add_function(wrap_pyfunction!(normalize_flags, module)?)?;
    module.add_function(wrap_pyfunction!(inject_cmd_flag, module)?)?;
    Ok(())
}
