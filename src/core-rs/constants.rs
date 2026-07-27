use std::sync::atomic::AtomicU64;
use std::time::Duration;

pub const DEFAULT_MAX_OUTPUT_BYTES: usize = 1024 * 1024;
pub const FILE_TRANSFER_OUTPUT_LIMIT: usize = usize::MAX / 4;
pub const LOCAL_CLIENT_KIND: &str = "local";
pub const MAX_CONFIGURABLE_OUTPUT_BYTES: usize = 16 * 1024 * 1024;
pub const OUTPUT_DRAIN_GRACE: Duration = Duration::from_secs(2);
pub const POLL_INTERVAL: Duration = Duration::from_millis(10);
pub const READ_CHUNK_BYTES: usize = 64 * 1024;
pub const REMOTE_KILL_TIMEOUT: Duration = Duration::from_secs(3);
pub const SSH_CLIENT_KIND: &str = "ssh";
pub const TERMINATION_GRACE: Duration = Duration::from_secs(1);

pub static TOKEN_COUNTER: AtomicU64 = AtomicU64::new(0);
