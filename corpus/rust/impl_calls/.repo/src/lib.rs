struct A;
trait T { fn f(&self); }
impl A { fn f(&self) {} fn new(&self) { Self::new(); self.f(); } }
impl T for A { fn f(&self) {} }
fn helper() {}
fn run(helper: fn()) { helper(); }
fn direct() { helper(); A::new(); }
