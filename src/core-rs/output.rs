use std::cmp;

#[derive(Debug)]
pub struct HeadTailBytes {
    max_bytes: usize,
    head: Vec<u8>,
    tail: Vec<u8>,
    total_bytes: usize,
}

impl HeadTailBytes {
    pub fn new(max_bytes: usize) -> Self {
        Self {
            max_bytes,
            head: Vec::new(),
            tail: Vec::new(),
            total_bytes: 0,
        }
    }

    pub fn append(&mut self, chunk: &[u8]) {
        if chunk.is_empty() {
            return;
        }
        self.total_bytes = self.total_bytes.saturating_add(chunk.len());

        let head_budget = self.max_bytes / 2;
        let head_remaining = head_budget.saturating_sub(self.head.len());
        let head_take = cmp::min(head_remaining, chunk.len());
        self.head.extend_from_slice(&chunk[..head_take]);

        let remainder = &chunk[head_take..];
        if remainder.is_empty() {
            return;
        }
        let tail_budget = self.max_bytes - head_budget;
        self.tail.extend_from_slice(remainder);
        if self.tail.len() > tail_budget {
            let overflow = self.tail.len() - tail_budget;
            self.tail.drain(..overflow);
        }
    }

    pub fn total_bytes(&self) -> usize {
        self.total_bytes
    }

    pub fn retained_bytes(&self) -> usize {
        self.head.len() + self.tail.len()
    }

    pub fn omitted_bytes(&self) -> usize {
        self.total_bytes.saturating_sub(self.retained_bytes())
    }

    pub fn raw_bytes(&self) -> Vec<u8> {
        let mut result = Vec::with_capacity(self.retained_bytes());
        result.extend_from_slice(&self.head);
        result.extend_from_slice(&self.tail);
        result
    }

    pub fn display_bytes(&self) -> Vec<u8> {
        let omitted = self.omitted_bytes();
        if omitted == 0 {
            return self.raw_bytes();
        }
        let marker = format!("\n... [truncated, {omitted} bytes omitted] ...\n");
        let mut result = Vec::with_capacity(self.retained_bytes().saturating_add(marker.len()));
        result.extend_from_slice(&self.head);
        result.extend_from_slice(marker.as_bytes());
        result.extend_from_slice(&self.tail);
        result
    }
}
