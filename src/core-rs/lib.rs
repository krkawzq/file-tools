//! High-speed string operators for `file-tools`.
//!
//! Algorithms ported/adapted from:
//! - codex-rs `apply-patch` (seek_sequence, replacements)
//! - grok-build `codex/apply_patch` + `search_replace` helpers
//! - Claude Code style exact + rstrip edit matching

mod edit;
mod patch;
mod read;

use pyo3::prelude::*;

#[pymodule]
fn _core(m: &Bound<PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(edit::find_matches, m)?)?;
    m.add_function(wrap_pyfunction!(edit::apply_replacements_text, m)?)?;
    m.add_function(wrap_pyfunction!(edit::edit_text, m)?)?;

    m.add_function(wrap_pyfunction!(read::count_lines, m)?)?;
    m.add_function(wrap_pyfunction!(read::slice_lines, m)?)?;
    m.add_function(wrap_pyfunction!(read::format_cat_n, m)?)?;
    m.add_function(wrap_pyfunction!(read::prepare_read, m)?)?;

    m.add_function(wrap_pyfunction!(patch::seek_sequence, m)?)?;
    m.add_function(wrap_pyfunction!(patch::derive_new_contents, m)?)?;
    m.add_function(wrap_pyfunction!(patch::apply_line_replacements, m)?)?;
    Ok(())
}
