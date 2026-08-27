from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/orders")
def create_order():
    return {"ok": True}


@app.get("/v1/orders/{order_id}")
def read_order(order_id: str):
    return {"id": order_id}
