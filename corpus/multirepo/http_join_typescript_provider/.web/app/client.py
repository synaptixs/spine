"""A Python consumer of a Node service."""

import httpx


def place_order() -> object:
    return httpx.post("/v1/orders")


def check_health() -> object:
    # CONTROL: nothing serves this. A joiner that joins everything emits an edge here.
    return httpx.get("/health")
