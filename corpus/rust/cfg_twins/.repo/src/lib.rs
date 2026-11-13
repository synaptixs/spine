//! One module declared twice: once for builds, once for tests. Both declarations share one id.

#[cfg(not(test))]
mod clock {
    pub fn now() -> u64 {
        42
    }
}

#[cfg(test)]
mod clock {
    pub fn now() -> u64 {
        0
    }
}

pub fn stamp() -> u64 {
    clock::now()
}

#[cfg(test)]
mod tests {
    use super::stamp;

    #[test]
    fn stamps() {
        let _ = stamp();
    }
}
