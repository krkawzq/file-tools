//! Pure text transformations for structured patches.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Search for a line sequence with progressively relaxed normalization.
#[pyfunction]
pub fn seek_sequence(
    py: Python<'_>,
    lines: Vec<String>,
    pattern: Vec<String>,
    start: usize,
    eof: bool,
) -> Option<usize> {
    py.allow_threads(|| seek_sequence_inner(&lines, &pattern, start, eof))
}

fn seek_sequence_inner(
    lines: &[String],
    pattern: &[String],
    start: usize,
    eof: bool,
) -> Option<usize> {
    if pattern.is_empty() {
        return Some(start);
    }
    if pattern.len() > lines.len() {
        return None;
    }
    let search_start = if eof && lines.len() >= pattern.len() {
        lines.len() - pattern.len()
    } else {
        start
    };
    let search_end = lines.len() - pattern.len();
    if search_start > search_end {
        return None;
    }

    for i in search_start..=search_end {
        if lines[i..i + pattern.len()] == *pattern {
            return Some(i);
        }
    }

    let pattern_rstripped: Vec<&str> = pattern.iter().map(|line| line.trim_end()).collect();

    for i in search_start..=search_end {
        let ok = pattern_rstripped
            .iter()
            .enumerate()
            .all(|(p_idx, pat)| lines[i + p_idx].trim_end() == *pat);
        if ok {
            return Some(i);
        }
    }

    let pattern_trimmed: Vec<&str> = pattern.iter().map(|line| line.trim()).collect();

    for i in search_start..=search_end {
        let ok = pattern_trimmed
            .iter()
            .enumerate()
            .all(|(p_idx, pat)| lines[i + p_idx].trim() == *pat);
        if ok {
            return Some(i);
        }
    }

    for i in search_start..=search_end {
        let ok = pattern
            .iter()
            .enumerate()
            .all(|(p_idx, pat)| normalised_eq(&lines[i + p_idx], pat));
        if ok {
            return Some(i);
        }
    }
    None
}

fn normalised_eq(left: &str, right: &str) -> bool {
    left.trim()
        .chars()
        .map(normalise_char)
        .eq(right.trim().chars().map(normalise_char))
}

fn normalise_char(c: char) -> char {
    match c {
        '\u{2010}' | '\u{2011}' | '\u{2012}' | '\u{2013}' | '\u{2014}' | '\u{2015}'
        | '\u{2212}' => '-',
        '\u{2018}' | '\u{2019}' | '\u{201A}' | '\u{201B}' => '\'',
        '\u{201C}' | '\u{201D}' | '\u{201E}' | '\u{201F}' => '"',
        '\u{00A0}' | '\u{2002}' | '\u{2003}' | '\u{2004}' | '\u{2005}' | '\u{2006}'
        | '\u{2007}' | '\u{2008}' | '\u{2009}' | '\u{200A}' | '\u{202F}' | '\u{205F}'
        | '\u{3000}' => ' ',
        other => other,
    }
}

/// Apply sorted, non-overlapping line replacements in one linear rebuild.
#[pyfunction]
pub fn apply_line_replacements(
    py: Python<'_>,
    lines: Vec<String>,
    replacements: Vec<(usize, usize, Vec<String>)>,
) -> PyResult<Vec<String>> {
    py.allow_threads(|| apply_line_replacements_inner(lines, replacements))
        .map_err(PyValueError::new_err)
}

fn apply_line_replacements_inner(
    lines: Vec<String>,
    mut replacements: Vec<(usize, usize, Vec<String>)>,
) -> Result<Vec<String>, String> {
    replacements.sort_by_key(|(idx, _, _)| *idx);

    let mut previous_end = 0usize;
    let mut added = 0usize;
    let mut removed = 0usize;
    for (start_idx, old_len, new_segment) in &replacements {
        let end = start_idx
            .checked_add(*old_len)
            .ok_or_else(|| format!("replacement span overflow: start={start_idx} len={old_len}"))?;
        if *start_idx > lines.len() || end > lines.len() {
            return Err(format!(
                "replacement span out of range: start={start_idx} len={old_len} lines={}",
                lines.len()
            ));
        }
        if *start_idx < previous_end {
            return Err("overlapping line replacements".to_owned());
        }
        previous_end = end;
        added = added
            .checked_add(new_segment.len())
            .ok_or_else(|| "replacement output line count overflow".to_owned())?;
        removed = removed
            .checked_add(*old_len)
            .ok_or_else(|| "replacement input line count overflow".to_owned())?;
    }

    // Rebuild once instead of repeatedly removing/inserting into the middle of
    // a Vec (which is quadratic for many hunks).
    let output_len = lines
        .len()
        .checked_sub(removed)
        .and_then(|remaining| remaining.checked_add(added))
        .ok_or_else(|| "replacement output line count overflow".to_owned())?;
    let mut output = Vec::with_capacity(output_len);
    let mut original = lines.into_iter();
    let mut cursor = 0usize;
    for (start_idx, old_len, new_segment) in replacements {
        output.extend(original.by_ref().take(start_idx - cursor));
        for _ in 0..old_len {
            original.next();
        }
        output.extend(new_segment);
        cursor = start_idx + old_len;
    }
    output.extend(original);
    Ok(output)
}

/// One update chunk: `(change_context, old_lines, new_lines, is_end_of_file)`.
type Chunk = (Option<String>, Vec<String>, Vec<String>, bool);

fn compute_replacements(
    original_lines: &[String],
    path: &str,
    chunks: &[Chunk],
) -> Result<Vec<(usize, usize, Vec<String>)>, String> {
    let mut replacements: Vec<(usize, usize, Vec<String>)> = Vec::new();
    let mut line_index: usize = 0;

    for chunk in chunks {
        let (change_context, old_lines, new_lines, is_eof) = chunk;

        if let Some(ctx) = change_context {
            match seek_sequence_inner(original_lines, std::slice::from_ref(ctx), line_index, false)
            {
                Some(idx) => line_index = idx + 1,
                None => {
                    return Err(format!("Failed to find context '{ctx}' in {path}"));
                }
            }
        }

        if old_lines.is_empty() {
            let insertion_idx = if change_context.is_some() && !*is_eof {
                line_index
            } else {
                original_lines.len()
            };
            replacements.push((insertion_idx, 0, new_lines.clone()));
            continue;
        }

        let mut pattern: &[String] = old_lines;
        let mut new_slice: &[String] = new_lines;
        let mut found = seek_sequence_inner(original_lines, pattern, line_index, *is_eof);

        if found.is_none() && pattern.last().is_some_and(|s| s.is_empty()) {
            pattern = &pattern[..pattern.len() - 1];
            if new_slice.last().is_some_and(|s| s.is_empty()) {
                new_slice = &new_slice[..new_slice.len() - 1];
            }
            found = seek_sequence_inner(original_lines, pattern, line_index, *is_eof);
        }

        if let Some(start_idx) = found {
            replacements.push((start_idx, pattern.len(), new_slice.to_vec()));
            line_index = start_idx + pattern.len();
        } else {
            return Err(format!(
                "Failed to find expected lines in {path}:\n{}",
                old_lines.join("\n")
            ));
        }
    }

    Ok(replacements)
}

/// Derive new file contents from original text + update chunks.
///
/// `chunks`: list of `(change_context | None, old_lines, new_lines, is_end_of_file)`.
#[pyfunction]
pub fn derive_new_contents(
    py: Python<'_>,
    original_content: &str,
    path: &str,
    chunks: Vec<Chunk>,
) -> PyResult<String> {
    py.allow_threads(|| derive_new_contents_inner(original_content, path, chunks))
        .map_err(PyValueError::new_err)
}

fn derive_new_contents_inner(
    original_content: &str,
    path: &str,
    chunks: Vec<Chunk>,
) -> Result<String, String> {
    // Move-only hunks preserve the decoded source text and newline shape.
    if chunks.is_empty() {
        return Ok(original_content.to_owned());
    }

    let crlf = original_content
        .as_bytes()
        .windows(2)
        .any(|window| window == b"\r\n");
    // Remove the newline sentinel while retaining real trailing blank lines.
    let original_lines: Vec<String> = original_content
        .split_terminator('\n')
        .map(|line| {
            if crlf {
                line.strip_suffix('\r').unwrap_or(line).to_owned()
            } else {
                line.to_owned()
            }
        })
        .collect();

    let replacements = compute_replacements(&original_lines, path, &chunks)?;
    let new_lines = apply_line_replacements_inner(original_lines, replacements)?;

    if new_lines.is_empty() {
        return Ok(String::new());
    }
    let newline = if crlf { "\r\n" } else { "\n" };
    let mut new_content = new_lines.join(newline);
    new_content.push_str(newline);
    Ok(new_content)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eof_prefers_last() {
        let lines = vec!["a", "b", "c", "b", "c"]
            .into_iter()
            .map(String::from)
            .collect::<Vec<_>>();
        let pat = vec!["b".into(), "c".into()];
        assert_eq!(seek_sequence_inner(&lines, &pat, 0, false), Some(1));
        assert_eq!(seek_sequence_inner(&lines, &pat, 0, true), Some(3));
    }

    #[test]
    fn unicode_dash() {
        let lines = vec![format!("hello \u{2013} world")];
        let pat = vec!["hello - world".into()];
        assert_eq!(seek_sequence_inner(&lines, &pat, 0, false), Some(0));
    }

    #[test]
    fn line_replacements_are_linear_and_preserve_equal_position_order() {
        let lines = vec!["a".into(), "b".into()];
        let replacements = vec![(1, 0, vec!["first".into()]), (1, 0, vec!["second".into()])];
        let result = apply_line_replacements_inner(lines, replacements).unwrap();
        assert_eq!(result, vec!["a", "first", "second", "b"]);
    }

    #[test]
    fn invalid_line_replacement_returns_error() {
        let result =
            apply_line_replacements_inner(vec!["a".into()], vec![(3, 0, vec!["x".into()])]);
        assert!(result.unwrap_err().contains("out of range"));
    }

    #[test]
    fn derive_preserves_trailing_blank_lines() {
        let chunks = vec![(None, vec!["a".into()], vec!["A".into()], false)];
        let result = derive_new_contents_inner("a\n\n", "f.txt", chunks).unwrap();
        assert_eq!(result, "A\n\n");
    }

    #[test]
    fn derive_without_chunks_does_not_modify_newline_shape() {
        for original in ["", "\n", "\n\n", "a", "a\n", "a\n\n", "a\r\n"] {
            assert_eq!(
                derive_new_contents_inner(original, "f.txt", Vec::new()).unwrap(),
                original
            );
        }
    }

    #[test]
    fn context_only_addition_inserts_after_context() {
        let chunks = vec![(
            Some("middle".into()),
            Vec::new(),
            vec!["inserted".into()],
            false,
        )];
        let result = derive_new_contents_inner("start\nmiddle\nend\n", "f.txt", chunks).unwrap();
        assert_eq!(result, "start\nmiddle\ninserted\nend\n");
    }

    #[test]
    fn derive_preserves_crlf() {
        let chunks = vec![(None, vec!["middle".into()], vec!["changed".into()], false)];
        let result =
            derive_new_contents_inner("start\r\nmiddle\r\nend\r\n", "f.txt", chunks).unwrap();
        assert_eq!(result, "start\r\nchanged\r\nend\r\n");
    }
}
