#[derive(Clone, GeneratedTrait)]
pub struct Visible;

#[async_trait::async_trait]
pub trait Handler {
    async fn handle(&self);
}

make_generated! { fn invisible() {} }

impl Visible {
    pub fn source_method(&self) {}
}
