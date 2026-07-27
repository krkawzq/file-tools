//! Exact + rstrip string matching / replacement.
//!
//! Match strategy (decreasing priority):
//! 1. Exact substring match
//! 2. Per-line rstrip match (tolerate trailing whitespace drift)
//!
//! Match positions are byte offsets into UTF-8 text.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn uses_crlf(text: &str) -> bool {
    text.as_bytes().windows(2).any(|window| window == b"\r\n")
}

fn with_crlf(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\n', "\r\n")
}

/// Find all occurrences of `pattern` in `text`.
///
/// Returns a list of `(byte_start, byte_len)` pairs.
#[pyfunction]
pub fn find_matches(text: &str, pattern: &str) -> Vec<(usize, usize)> {
    find_matches_inner(text, pattern)
}

fn find_matches_inner(text: &str, pattern: &str) -> Vec<(usize, usize)> {
    if text.is_empty() && pattern.is_empty() {
        return vec![(0, 0)];
    }
    if pattern.is_empty() {
        return vec![];
    }

    let exact = exact_find_all(text, pattern);
    if !exact.is_empty() {
        return exact;
    }

    // Patch/edit inputs conventionally use LF even when the target file came
    // from Windows. Match that input against CRLF without changing byte
    // offsets in the original text.
    if uses_crlf(text) && pattern.contains('\n') {
        let crlf_pattern = with_crlf(pattern);
        if crlf_pattern != pattern {
            let exact = exact_find_all(text, &crlf_pattern);
            if !exact.is_empty() {
                return exact;
            }
            return rstrip_lines_find_all(text, &crlf_pattern);
        }
    }

    rstrip_lines_find_all(text, pattern)
}

fn exact_find_all(text: &str, pattern: &str) -> Vec<(usize, usize)> {
    // `match_indices` advances on UTF-8 character boundaries and yields
    // non-overlapping matches, matching replacement semantics. Advancing a
    // byte at a time can slice through a multibyte character; collecting
    // overlapping spans also makes `replace_all` impossible to apply.
    text.match_indices(pattern)
        .map(|(idx, matched)| (idx, matched.len()))
        .collect()
}

fn rstrip_lines_find_all(text: &str, pattern: &str) -> Vec<(usize, usize)> {
    // Preserve the trailing segment so newline state remains observable.
    let text_lines: Vec<&str> = text.split('\n').collect();
    let pattern_lines: Vec<&str> = pattern.split('\n').collect();
    let pattern_len = pattern_lines.len();
    if pattern_len == 0 || pattern_len > text_lines.len() {
        return Vec::new();
    }

    let pattern_rstrip: Vec<&str> = pattern_lines.iter().map(|l| l.trim_end()).collect();
    let mut matches = Vec::new();

    let mut line_starts = Vec::with_capacity(text_lines.len());
    let mut off = 0usize;
    for (i, line) in text_lines.iter().enumerate() {
        line_starts.push(off);
        off += line.len();
        if i + 1 < text_lines.len() {
            off += 1; // '\n'
        }
    }

    let mut i = 0;
    while i <= text_lines.len() - pattern_len {
        let window = &text_lines[i..i + pattern_len];
        let ok = window
            .iter()
            .zip(pattern_rstrip.iter())
            .all(|(a, b)| a.trim_end() == *b);
        if !ok {
            i += 1;
            continue;
        }
        let byte_offset = line_starts[i];
        let mut match_length: usize =
            window.iter().map(|l| l.len()).sum::<usize>() + (pattern_len - 1);
        // In CRLF text, split('\n') leaves the final '\r' attached to each
        // logical line. Keep the last one outside the replacement span so a
        // whitespace-tolerant match cannot turn its following CRLF into LF.
        if window.last().is_some_and(|line| line.ends_with('\r')) {
            match_length -= 1;
        }
        matches.push((byte_offset, match_length));
        i += pattern_len;
    }
    matches
}

/// Apply replacements at given `(byte_start, byte_len)` spans.
///
/// Spans may arrive in any order; they are sorted, validated, and rebuilt in
/// one forward pass.
#[pyfunction]
pub fn apply_replacements_text(
    text: &str,
    matches: Vec<(usize, usize)>,
    new_string: &str,
) -> PyResult<String> {
    let mut ordered = matches;
    ordered.sort_by_key(|(pos, _)| *pos);

    let mut previous_end = 0usize;
    let mut removed_bytes = 0usize;
    for &(pos, len) in &ordered {
        let Some(end) = pos.checked_add(len) else {
            return Err(PyValueError::new_err(format!(
                "match span overflow: pos={pos} len={len}"
            )));
        };
        if end > text.len() {
            return Err(PyValueError::new_err(format!(
                "match span out of range: pos={pos} len={len} text_len={}",
                text.len()
            )));
        }
        if !text.is_char_boundary(pos) || !text.is_char_boundary(end) {
            return Err(PyValueError::new_err(
                "match span is not on a UTF-8 char boundary",
            ));
        }
        if pos < previous_end {
            return Err(PyValueError::new_err("overlapping matches"));
        }
        previous_end = end;
        removed_bytes = removed_bytes
            .checked_add(len)
            .ok_or_else(|| PyValueError::new_err("total match length overflow"))?;
    }

    let inserted_bytes = new_string
        .len()
        .checked_mul(ordered.len())
        .ok_or_else(|| PyValueError::new_err("replacement size overflow"))?;
    let result_capacity = text
        .len()
        .checked_sub(removed_bytes)
        .and_then(|remaining| remaining.checked_add(inserted_bytes))
        .ok_or_else(|| PyValueError::new_err("result size overflow"))?;

    let mut result = String::with_capacity(result_capacity);
    let mut last = 0usize;
    for (pos, len) in &ordered {
        result.push_str(&text[last..*pos]);
        result.push_str(new_string);
        last = pos + len;
    }
    result.push_str(&text[last..]);
    Ok(result)
}

/// Find matches and replace. Returns `(new_text, replacement_count)`.
///
/// When `replace_all` is false and more than one match exists, raises ValueError
/// with code prefix `AMBIGUOUS:`. When no match, raises `NOT_FOUND:`.
#[pyfunction]
pub fn edit_text(
    text: &str,
    old_string: &str,
    new_string: &str,
    replace_all: bool,
) -> PyResult<(String, usize)> {
    let crlf = uses_crlf(text);
    let matches = find_matches_inner(text, old_string);
    if matches.is_empty() {
        return Err(PyValueError::new_err("NOT_FOUND: old_string not found"));
    }
    if matches.len() > 1 && !replace_all {
        return Err(PyValueError::new_err(format!(
            "AMBIGUOUS: old_string found {} times",
            matches.len()
        )));
    }
    let selected = if replace_all {
        matches
    } else {
        vec![matches[0]]
    };
    let count = selected.len();
    let replacement = if crlf {
        with_crlf(new_string)
    } else {
        new_string.to_owned()
    };
    let new_text = apply_replacements_text(text, selected, &replacement)?;
    Ok((new_text, count))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_match() {
        let m = find_matches_inner("hello world\n", "hello");
        assert_eq!(m, vec![(0, 5)]);
    }

    #[test]
    fn exact_unicode_matches_are_safe_and_non_overlapping() {
        let m = find_matches_inner("éé", "é");
        assert_eq!(m, vec![(0, 2), (2, 2)]);

        let m = find_matches_inner("aaaa", "aaa");
        assert_eq!(m, vec![(0, 3)]);
    }

    #[test]
    fn rstrip_match() {
        let m = find_matches_inner("hello   \nworld\t\n", "hello\nworld");
        assert_eq!(m.len(), 1);
        assert_eq!(m[0].0, 0);
    }

    #[test]
    fn replace_via_apply() {
        let text = "dup\nmiddle\ndup\n";
        let matches = find_matches_inner(text, "dup");
        assert_eq!(matches.len(), 2);
        let mut out = String::new();
        let mut last = 0usize;
        for (pos, len) in &matches {
            out.push_str(&text[last..*pos]);
            out.push_str("NEW");
            last = pos + len;
        }
        out.push_str(&text[last..]);
        assert_eq!(out, "NEW\nmiddle\nNEW\n");
    }

    #[test]
    fn rstrip_matches_do_not_overlap() {
        let m = find_matches_inner("a \na \na \n", "a\na");
        assert_eq!(m.len(), 1);
        assert_eq!(m[0].0, 0);
    }

    #[test]
    fn replacement_accepts_unsorted_spans_and_reserves_expansion() {
        let result = apply_replacements_text("a-b-c", vec![(4, 1), (0, 1)], "expanded").unwrap();
        assert_eq!(result, "expanded-b-expanded");
    }

    #[test]
    fn edit_preserves_crlf_and_accepts_lf_patterns() {
        let (result, count) =
            edit_text("hello  \r\nworld\r\n", "hello\nworld", "hi\nthere", false).unwrap();
        assert_eq!(count, 1);
        assert_eq!(result, "hi\r\nthere\r\n");
        assert_eq!(
            find_matches_inner("hello\r\nworld\r\n", "hello\nworld"),
            vec![(0, 12)]
        );
    }

    #[test]
    fn edit_does_not_add_a_trailing_newline() {
        let (result, count) = edit_text("before", "before", "after", false).unwrap();
        assert_eq!(count, 1);
        assert_eq!(result, "after");
    }

    #[test]
    fn edit_can_remove_a_trailing_newline() {
        let (result, count) = edit_text("before\n", "before\n", "after", false).unwrap();
        assert_eq!(count, 1);
        assert_eq!(result, "after");
    }
}
