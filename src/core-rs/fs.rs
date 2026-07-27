//! Shared filesystem metadata and bounded line-window primitives.

use crate::client::{CoreError, CoreResult};
use pyo3::prelude::*;
use std::collections::VecDeque;
use std::fs::{File, Metadata};
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::time::UNIX_EPOCH;

#[pyclass(module = "file_tools._core", frozen)]
#[derive(Clone, Debug)]
pub struct FileInfo {
    #[pyo3(get)]
    pub exists: bool,
    #[pyo3(get)]
    pub kind: String,
    #[pyo3(get)]
    pub size: u64,
    #[pyo3(get)]
    pub modified_ns: Option<u128>,
    #[pyo3(get)]
    pub is_symlink: bool,
    #[pyo3(get)]
    pub version: Option<String>,
}

impl FileInfo {
    pub fn missing() -> Self {
        Self {
            exists: false,
            kind: "missing".to_string(),
            size: 0,
            modified_ns: None,
            is_symlink: false,
            version: None,
        }
    }

    pub fn from_local_metadata(metadata: &Metadata, is_symlink: bool) -> Self {
        let kind = if is_symlink {
            "symlink"
        } else if metadata.is_file() {
            "file"
        } else if metadata.is_dir() {
            "directory"
        } else {
            "other"
        };
        let modified_ns = metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
            .map(|value| value.as_nanos());
        let mut version = format!("local:{}:{}", metadata.len(), modified_ns.unwrap_or(0));
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            version.push_str(&format!(":{}:{}", metadata.dev(), metadata.ino()));
        }
        Self {
            exists: true,
            kind: kind.to_string(),
            size: metadata.len(),
            modified_ns,
            is_symlink,
            version: Some(version),
        }
    }

    pub fn is_file(&self) -> bool {
        self.kind == "file"
    }

    pub fn is_dir(&self) -> bool {
        self.kind == "directory"
    }
}

#[pymethods]
impl FileInfo {
    fn __repr__(&self) -> String {
        format!(
            "FileInfo(exists={}, kind={:?}, size={}, version={:?})",
            self.exists, self.kind, self.size, self.version
        )
    }
}

pub fn local_file_info(path: &Path, display_path: &str) -> CoreResult<FileInfo> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) => Ok(FileInfo::from_local_metadata(
            &metadata,
            metadata.file_type().is_symlink(),
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(FileInfo::missing()),
        Err(error) => Err(CoreError::from_io("stat", display_path, error)),
    }
}

#[derive(Debug)]
pub struct TextWindow {
    pub bytes: Vec<u8>,
    pub total_lines: usize,
    pub start_line: usize,
    pub end_line: usize,
    pub truncated: bool,
}

fn append_bounded(target: &mut Vec<u8>, line: &[u8], max_bytes: usize) -> CoreResult<()> {
    if target.len().saturating_add(line.len()) > max_bytes {
        return Err(CoreError::TransferLimit(format!(
            "read window exceeds the configured transfer limit of {max_bytes} bytes"
        )));
    }
    target.extend_from_slice(line);
    Ok(())
}

pub fn read_local_window(
    path: &Path,
    display_path: &str,
    offset: i64,
    limit: usize,
    max_bytes: usize,
) -> CoreResult<TextWindow> {
    let info = local_file_info(path, display_path)?;
    if !info.exists {
        return Err(CoreError::NotFound(format!(
            "read_window failed: {display_path}: file does not exist"
        )));
    }
    if info.is_dir() {
        return Err(CoreError::Client(format!(
            "read_window failed: {display_path}: path is a directory"
        )));
    }
    if !info.is_file() {
        return Err(CoreError::Client(format!(
            "read_window failed: {display_path}: path is not a regular file"
        )));
    }
    let file =
        File::open(path).map_err(|error| CoreError::from_io("read_window", display_path, error))?;
    let mut reader = BufReader::new(file);
    let mut line = Vec::new();
    let mut total_lines = 0usize;

    if offset < 0 {
        let requested = usize::try_from(offset.unsigned_abs()).unwrap_or(usize::MAX);
        let mut tail = VecDeque::<Vec<u8>>::new();
        let mut retained_bytes = 0usize;
        loop {
            line.clear();
            let read = reader
                .read_until(b'\n', &mut line)
                .map_err(|error| CoreError::from_io("read_window", display_path, error))?;
            if read == 0 {
                break;
            }
            total_lines += 1;
            retained_bytes = retained_bytes.saturating_add(line.len());
            tail.push_back(line.clone());
            while tail.len() > requested {
                if let Some(removed) = tail.pop_front() {
                    retained_bytes = retained_bytes.saturating_sub(removed.len());
                }
            }
            if retained_bytes > max_bytes {
                return Err(CoreError::TransferLimit(format!(
                    "read window exceeds the configured transfer limit of {max_bytes} bytes"
                )));
            }
        }
        let start_line = total_lines.saturating_sub(tail.len()).saturating_add(1);
        let mut bytes = Vec::with_capacity(retained_bytes);
        for retained in tail {
            bytes.extend_from_slice(&retained);
        }
        return Ok(TextWindow {
            bytes,
            total_lines,
            start_line,
            end_line: total_lines,
            truncated: false,
        });
    }

    let requested_start = if offset == 0 { 1 } else { offset as usize };
    let requested_end = requested_start.saturating_add(limit.saturating_sub(1));
    let mut bytes = Vec::new();
    loop {
        line.clear();
        let read = reader
            .read_until(b'\n', &mut line)
            .map_err(|error| CoreError::from_io("read_window", display_path, error))?;
        if read == 0 {
            break;
        }
        total_lines += 1;
        if (requested_start..=requested_end).contains(&total_lines) {
            append_bounded(&mut bytes, &line, max_bytes)?;
        }
    }
    let start_line = if requested_start > total_lines {
        total_lines.saturating_add(1)
    } else {
        requested_start
    };
    let selected = total_lines
        .saturating_sub(start_line.saturating_sub(1))
        .min(limit);
    let end_line = if selected == 0 {
        total_lines
    } else {
        start_line + selected - 1
    };
    Ok(TextWindow {
        bytes,
        total_lines,
        start_line,
        end_line,
        truncated: end_line < total_lines,
    })
}
