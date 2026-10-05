trait Store<T> { fn save(&self); }
struct Sql<T>(T);
impl<T> Store<T> for Sql<T> { fn save(&self) {} }
impl<T> Store<T> for T { fn save(&self) {} }
