//! Line slicing and numbered formatting for bounded reads.
//!
//! Positive offsets are 1-based. Negative offsets select lines from the end.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::fmt::Write;

/// Count logical lines in text.
///
/// - Empty text → 0
/// - No trailing newline: last segment counts
/// - Trailing newline: does not add an extra empty line
#[pyfunction]
pub fn count_lines(text: &str) -> usize {
    count_lines_inner(text)
}

fn count_lines_inner(text: &str) -> usize {
    if text.is_empty() {
        return 0;
    }
    let mut count = text.bytes().filter(|&b| b == b'\n').count();
    if !text.ends_with('\n') {
        count += 1;
    }
    count
}

/// Split text into lines while retaining line endings.
fn lines_with_endings(text: &str) -> Vec<&str> {
    if text.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut start = 0;
    for (i, b) in text.bytes().enumerate() {
        if b == b'\n' {
            out.push(&text[start..=i]);
            start = i + 1;
        }
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// Resolve an offset to a zero-based start index and line count.
///
/// - `offset >= 1`: start at that 1-based line
/// - `offset == 0`: start at line 1
/// - `offset < 0`: tail `|offset|` lines (`limit` ignored for window size)
fn resolve_window(total_lines: usize, offset: i64, limit: usize) -> PyResult<(usize, usize, bool)> {
    if limit == 0 {
        return Err(PyValueError::new_err("limit must be > 0"));
    }
    if total_lines == 0 {
        return Ok((0, 0, false));
    }

    if offset < 0 {
        let tail = usize::try_from(offset.unsigned_abs()).unwrap_or(usize::MAX);
        let effective = tail.min(total_lines);
        let start = total_lines - effective;
        return Ok((start, effective, false));
    }

    let start_line = if offset == 0 { 1 } else { offset as usize };
    let start_index = start_line.saturating_sub(1);
    if start_index >= total_lines {
        return Ok((total_lines, 0, false));
    }
    let available = total_lines - start_index;
    let take = limit.min(available);
    let truncated = take < available;
    Ok((start_index, take, truncated))
}

/// Slice lines (0-based `[start, start+take)`) keeping original endings.
#[pyfunction]
pub fn slice_lines(text: &str, start: usize, take: usize) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    text.split_inclusive('\n')
        .skip(start)
        .take(take)
        .map(str::to_owned)
        .collect()
}

/// Format lines as `cat -n` (right-aligned line numbers + tab).
#[pyfunction]
pub fn format_cat_n(
    lines: Vec<String>,
    start_line: usize,
    total_lines: usize,
    truncated: bool,
) -> String {
    format_cat_n_inner(&lines, start_line, total_lines, truncated)
}

fn format_cat_n_inner(
    lines: &[String],
    start_line: usize,
    total_lines: usize,
    truncated: bool,
) -> String {
    if lines.is_empty() {
        return String::new();
    }
    let width = total_lines.to_string().len().max(4);
    let body_len: usize = lines.iter().map(String::len).sum();
    let mut out = String::with_capacity(body_len + lines.len() * (width + 1));
    for (i, line) in lines.iter().enumerate() {
        let n = start_line + i;
        let body = line.as_str();
        write!(&mut out, "{n:>width$}\t").expect("writing to String cannot fail");
        out.push_str(body);
    }
    if truncated {
        let shown_end = start_line + lines.len() - 1;
        let remaining = total_lines.saturating_sub(shown_end);
        if remaining > 0 {
            if !out.ends_with('\n') {
                out.push('\n');
            }
            out.push_str(&format!("... [truncated, {remaining} lines not shown]"));
        }
    }
    out
}

/// Prepare a bounded line window.
///
/// Returns `(content, total_lines, start_line, end_line, truncated, raw_lines)`.
///
/// - `offset`: 1-based start line, or negative for tail
/// - `limit`: max lines (ignored when offset < 0 for window size)
/// - `show_line_numbers`: if true, content is cat -n format
#[pyfunction]
pub fn prepare_read(
    text: &str,
    offset: i64,
    limit: usize,
    show_line_numbers: bool,
) -> PyResult<(String, usize, usize, usize, bool, Vec<String>)> {
    let all_lines = lines_with_endings(text);
    let total = all_lines.len();
    let (start, take, truncated) = resolve_window(total, offset, limit)?;
    if take == 0 {
        let start_line = if total == 0 { 1 } else { total + 1 };
        return Ok((String::new(), total, start_line, total, false, Vec::new()));
    }
    let lines: Vec<String> = all_lines[start..start + take]
        .iter()
        .map(|line| (*line).to_owned())
        .collect();
    let start_line = start + 1;
    let end_line = start + lines.len();
    let content = if show_line_numbers {
        format_cat_n_inner(&lines, start_line, total, truncated)
    } else {
        lines.concat()
    };
    Ok((content, total, start_line, end_line, truncated, lines))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn count_basic() {
        assert_eq!(count_lines_inner(""), 0);
        assert_eq!(count_lines_inner("a"), 1);
        assert_eq!(count_lines_inner("a\n"), 1);
        assert_eq!(count_lines_inner("a\nb"), 2);
        assert_eq!(count_lines_inner("a\nb\n"), 2);
    }

    #[test]
    fn tail_window() {
        let total = count_lines_inner("1\n2\n3\n4\n5\n");
        let (start, take, truncated) = resolve_window(total, -2, 2000).unwrap();
        assert_eq!((start, take, truncated), (3, 2, false));
        let lines = slice_lines("1\n2\n3\n4\n5\n", start, take);
        assert_eq!(lines.concat(), "4\n5\n");
    }

    #[test]
    fn one_based_window() {
        let total = count_lines_inner("a\nb\nc\n");
        let (start, take, _) = resolve_window(total, 2, 1).unwrap();
        assert_eq!((start, take), (1, 1));
        assert_eq!(slice_lines("a\nb\nc\n", start, take).concat(), "b\n");
    }

    #[test]
    fn slice_lines_handles_empty_and_unterminated_text() {
        assert!(slice_lines("", 0, 1).is_empty());
        assert_eq!(slice_lines("a\nb", 1, 1), vec!["b"]);
        assert!(slice_lines("a\nb", 2, 1).is_empty());
    }

    #[test]
    fn minimum_offset_tails_without_overflow() {
        let (start, take, truncated) = resolve_window(2, i64::MIN, 1).unwrap();
        assert_eq!((start, take, truncated), (0, 2, false));
    }
}
