mod outer { pub fn f() {} mod inner { fn run() { super::f(); } } }
fn entry() { crate::outer::f(); }
