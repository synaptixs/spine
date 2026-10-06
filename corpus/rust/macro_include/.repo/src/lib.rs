macro_rules! generated { () => { fn made() {} }; }
generated!();
include!("generated.rs");
fn visible() {}
