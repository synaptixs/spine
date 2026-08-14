"""Mounts the router under a prefix — the other half of the EXPOSES join."""

from api.routes import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router, prefix="/v1")
