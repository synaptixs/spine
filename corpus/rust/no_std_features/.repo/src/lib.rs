#![no_std]
extern crate alloc;
use core::fmt;

pub struct Device;

impl fmt::Display for Device {
    fn fmt(&self, _formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        core::fmt::Result::Ok(())
    }
}

#[cfg(feature = "fast")]
pub fn mode() -> u8 { 1 }
#[cfg(not(feature = "fast"))]
pub fn mode() -> u8 { 0 }

pub fn selected() -> u8 { mode() }
