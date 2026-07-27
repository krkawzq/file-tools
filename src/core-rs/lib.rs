//! Native filesystem, SSH, command, and text-processing primitives.

mod client;
mod command;
mod constants;
mod edit;
mod local;
mod output;
mod patch;
mod read;
mod ssh;

use pyo3::prelude::*;

#[pymodule]
fn _core(py: Python<'_>, m: &Bound<PyModule>) -> PyResult<()> {
    client::register(py, m)?;

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
