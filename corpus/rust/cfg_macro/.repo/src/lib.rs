fn f() { platform(); }
#[cfg(unix)] fn platform() {}
#[cfg(windows)] fn platform() {}
macro_rules! generated { () => { fn made() {} }; }
generated!();
