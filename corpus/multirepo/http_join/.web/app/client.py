import httpx


def place_order(payload):
    return httpx.post("/v1/orders", json=payload)


def fetch_order():
    return httpx.get("/v1/orders/42")


def fetch_by_var(order_id):
    return httpx.get(f"/v1/orders/{order_id}")


def check_health():
    return httpx.get("/health")
