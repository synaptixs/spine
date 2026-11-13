//! Rust marks its tests inside the file, where no path rule can see them.

pub fn total(x: u32) -> u32 {
    x
}

#[test]
fn total_is_identity() {
    let _ = total(1);
}

#[cfg(test)]
mod tests {
    use super::total;

    fn helper() -> u32 {
        1
    }

    #[test]
    fn uses_helper() {
        let _ = total(helper());
    }
}

#[cfg(test)]
mod outlined;

#[cfg(not(test))]
pub mod real {
    pub fn run() {}
}
