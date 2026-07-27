use std::cmp;
use std::collections::VecDeque;

#[derive(Debug)]
pub struct HeadTailBytes {
    max_bytes: usize,
    head: Vec<u8>,
    tail: VecDeque<u8>,
    total_bytes: usize,
}

impl HeadTailBytes {
    pub fn new(max_bytes: usize) -> Self {
        Self {
            max_bytes,
            head: Vec::new(),
            tail: VecDeque::new(),
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
        if tail_budget == 0 {
            return;
        }
        if remainder.len() >= tail_budget {
            self.tail.clear();
            self.tail
                .extend(remainder[remainder.len() - tail_budget..].iter().copied());
            return;
        }

        let overflow = self
            .tail
            .len()
            .saturating_add(remainder.len())
            .saturating_sub(tail_budget);
        if overflow > 0 {
            // VecDeque drops this prefix by advancing its head instead of
            // shifting the entire retained tail on every output chunk.
            self.tail.drain(..overflow);
        }
        self.tail.extend(remainder.iter().copied());
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
        let (first, second) = self.tail.as_slices();
        result.extend_from_slice(first);
        result.extend_from_slice(second);
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
        let (first, second) = self.tail.as_slices();
        result.extend_from_slice(first);
        result.extend_from_slice(second);
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retains_head_and_latest_tail_across_chunks() {
        let mut buffer = HeadTailBytes::new(7);
        buffer.append(b"abc");
        buffer.append(b"def");
        buffer.append(b"ghijk");

        assert_eq!(buffer.raw_bytes(), b"abchijk");
        assert_eq!(buffer.total_bytes(), 11);
        assert_eq!(buffer.retained_bytes(), 7);
        assert_eq!(buffer.omitted_bytes(), 4);
    }

    #[test]
    fn one_large_chunk_keeps_exact_budget() {
        let mut buffer = HeadTailBytes::new(5);
        buffer.append(b"abcdef");

        assert_eq!(buffer.raw_bytes(), b"abdef");
        assert_eq!(buffer.retained_bytes(), 5);
        assert_eq!(buffer.omitted_bytes(), 1);
    }

    #[test]
    fn zero_budget_tracks_total_without_retaining_bytes() {
        let mut buffer = HeadTailBytes::new(0);
        buffer.append(b"abc");

        assert!(buffer.raw_bytes().is_empty());
        assert_eq!(buffer.total_bytes(), 3);
        assert_eq!(buffer.omitted_bytes(), 3);
    }

    #[test]
    fn chunk_boundaries_do_not_change_retained_output() {
        let input = (0_u8..100).collect::<Vec<_>>();
        for max_bytes in 0..=20 {
            let head_budget = max_bytes / 2;
            let tail_budget = max_bytes - head_budget;
            let expected = if input.len() <= max_bytes {
                input.clone()
            } else {
                [&input[..head_budget], &input[input.len() - tail_budget..]].concat()
            };

            for chunk_size in 1..=17 {
                let mut buffer = HeadTailBytes::new(max_bytes);
                for chunk in input.chunks(chunk_size) {
                    buffer.append(chunk);
                }
                assert_eq!(
                    buffer.raw_bytes(),
                    expected,
                    "max_bytes={max_bytes} chunk_size={chunk_size}"
                );
            }
        }
    }
}
