use tokio::sync::mpsc;

pub async fn fetch() -> usize {
    helper();
    let _channel = mpsc::channel::<usize>(1);
    1
}

fn helper() {}

pub struct Worker;

impl Worker {
    pub async fn run(&self) {
        self.local();
        tokio::select! { _ = fetch() => {} }
    }

    fn local(&self) {}
}

pub async fn inferred(worker: &Worker) {
    worker.run().await;
}
