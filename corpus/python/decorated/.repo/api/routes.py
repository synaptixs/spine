"""Routes declared by decorator, a wrapping decorator, and a decorated class."""

from dataclasses import dataclass
from functools import wraps

from fastapi import APIRouter

router = APIRouter()


@dataclass
class Item:
    """A decorated class — the decorator must not hide the Type."""

    name: str


def audited(fn):
    """Pass-through decorator that preserves the wrapped function."""

    @wraps(fn)
    def inner(*args, **kwargs):
        return fn(*args, **kwargs)

    return inner


@router.get("/items")
def list_items() -> list[Item]:
    """Reached only through the route — nothing in Python calls it."""
    return []


@router.post("/items")
@audited
def create_item(name: str) -> Item:
    """A handler sitting behind a second decorator."""
    return Item(name)
