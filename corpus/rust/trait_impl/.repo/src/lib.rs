trait Base { fn base(&self) {} }
trait Show: Base { fn show(&self); fn fallback(&self) {} }
struct Item;
impl Base for Item {}
impl Show for Item { fn show(&self) {} }
impl Item { fn own(&self) {} }
