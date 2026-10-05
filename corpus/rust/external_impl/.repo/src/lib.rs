trait Local { fn run(&self); }
impl Local for std::path::PathBuf { fn run(&self) {} }
