use crate::constants::{OUTPUT_DRAIN_GRACE, POLL_INTERVAL, READ_CHUNK_BYTES, TERMINATION_GRACE};
use crate::output::HeadTailBytes;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Debug)]
pub struct ProcessOutput {
    pub exit_code: i32,
    pub signal: Option<i32>,
    pub timed_out: bool,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub stdout_total_bytes: usize,
    pub stderr_total_bytes: usize,
    pub stdout_omitted_bytes: usize,
    pub stderr_omitted_bytes: usize,
    pub duration_ms: u128,
}

pub struct ProcessSpec {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: Option<String>,
    pub env: HashMap<String, String>,
    pub stdin: Option<Vec<u8>>,
    pub timeout: Option<Duration>,
    pub max_output_bytes: usize,
}

type TimeoutCallback<'a> = dyn Fn(&[u8]) + 'a;

fn spawn_reader<R>(
    mut stream: R,
    target: Arc<Mutex<HeadTailBytes>>,
    finished: mpsc::Sender<()>,
) -> thread::JoinHandle<()>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut chunk = vec![0_u8; READ_CHUNK_BYTES];
        loop {
            match stream.read(&mut chunk) {
                Ok(0) => break,
                Ok(read) => {
                    if let Ok(mut buffer) = target.lock() {
                        buffer.append(&chunk[..read]);
                    } else {
                        break;
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        let _ = finished.send(());
    })
}

#[cfg(unix)]
fn configure_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(not(unix))]
fn configure_process_group(_command: &mut Command) {}

#[cfg(unix)]
fn terminate_process_tree(child: &mut std::process::Child) {
    let pid = child.id() as i32;
    unsafe {
        libc::kill(-pid, libc::SIGTERM);
    }
    let deadline = Instant::now() + TERMINATION_GRACE;
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) => thread::sleep(POLL_INTERVAL),
            Err(_) => break,
        }
    }
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    let _ = child.wait();
}

#[cfg(not(unix))]
fn terminate_process_tree(child: &mut std::process::Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn exit_parts(status: ExitStatus) -> (i32, Option<i32>) {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(code) = status.code() {
            (code, None)
        } else {
            let signal = status.signal();
            (-signal.unwrap_or(1), signal)
        }
    }
    #[cfg(not(unix))]
    {
        (status.code().unwrap_or(1), None)
    }
}

fn wait_for_readers(readers: Vec<thread::JoinHandle<()>>, finished: mpsc::Receiver<()>) {
    let deadline = Instant::now() + OUTPUT_DRAIN_GRACE;
    let mut remaining = readers.len();
    while remaining > 0 {
        let Some(wait) = deadline.checked_duration_since(Instant::now()) else {
            break;
        };
        match finished.recv_timeout(wait) {
            Ok(()) => remaining -= 1,
            Err(mpsc::RecvTimeoutError::Timeout | mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }

    // Completed readers are joined to release their thread resources. A
    // descendant may intentionally keep a pipe open; those readers stay
    // detached after the bounded drain grace rather than blocking forever.
    for reader in readers {
        if remaining == 0 || reader.is_finished() {
            let _ = reader.join();
        }
    }
}

pub fn run_process(
    spec: ProcessSpec,
    on_timeout: Option<&TimeoutCallback<'_>>,
) -> Result<ProcessOutput, String> {
    let started = Instant::now();
    let mut command = Command::new(&spec.program);
    command
        .args(&spec.args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(if spec.stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        });
    if let Some(cwd) = &spec.cwd {
        command.current_dir(cwd);
    }
    if !spec.env.is_empty() {
        command.envs(&spec.env);
    }
    configure_process_group(&mut command);

    let mut child = command
        .spawn()
        .map_err(|error| format!("command failed to start: {error}"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "command stdout pipe was not created".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "command stderr pipe was not created".to_string())?;

    let stdout_buffer = Arc::new(Mutex::new(HeadTailBytes::new(spec.max_output_bytes)));
    let stderr_buffer = Arc::new(Mutex::new(HeadTailBytes::new(spec.max_output_bytes)));
    let (reader_finished_tx, reader_finished_rx) = mpsc::channel();
    let readers = vec![
        spawn_reader(
            stdout,
            Arc::clone(&stdout_buffer),
            reader_finished_tx.clone(),
        ),
        spawn_reader(
            stderr,
            Arc::clone(&stderr_buffer),
            reader_finished_tx.clone(),
        ),
    ];
    drop(reader_finished_tx);

    if let Some(input) = spec.stdin {
        if let Some(mut stream) = child.stdin.take() {
            thread::spawn(move || {
                let _ = stream.write_all(&input);
                let _ = stream.flush();
            });
        }
    }

    let deadline = spec.timeout.map(|timeout| started + timeout);
    let (status, timed_out) = loop {
        match child.try_wait() {
            Ok(Some(status)) => break (Some(status), false),
            Ok(None) => {}
            Err(error) => return Err(format!("command wait failed: {error}")),
        }

        if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            if let Some(callback) = on_timeout {
                // The child can emit a process-control marker immediately
                // before the deadline while the reader thread has not yet
                // published it. Give that bounded control data one scheduler
                // turn before attempting remote cleanup.
                thread::sleep(Duration::from_millis(30));
                let snapshot = stderr_buffer
                    .lock()
                    .map(|buffer| buffer.raw_bytes())
                    .unwrap_or_default();
                callback(&snapshot);
            }
            terminate_process_tree(&mut child);
            break (None, true);
        }
        thread::sleep(POLL_INTERVAL);
    };

    wait_for_readers(readers, reader_finished_rx);

    let stdout = stdout_buffer
        .lock()
        .map_err(|_| "stdout buffer lock was poisoned".to_string())?;
    let stderr = stderr_buffer
        .lock()
        .map_err(|_| "stderr buffer lock was poisoned".to_string())?;
    let (exit_code, signal) = if timed_out {
        (124, None)
    } else {
        exit_parts(status.expect("non-timeout command has an exit status"))
    };

    Ok(ProcessOutput {
        exit_code,
        signal,
        timed_out,
        stdout: stdout.display_bytes(),
        stderr: stderr.display_bytes(),
        stdout_total_bytes: stdout.total_bytes(),
        stderr_total_bytes: stderr.total_bytes(),
        stdout_omitted_bytes: stdout.omitted_bytes(),
        stderr_omitted_bytes: stderr.omitted_bytes(),
        duration_ms: started.elapsed().as_millis(),
    })
}
