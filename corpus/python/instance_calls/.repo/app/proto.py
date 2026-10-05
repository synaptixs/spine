"""A structural type: a call through it lands on the protocol's declared member (D4)."""

from typing import Protocol


class Reader(Protocol):
    def read(self) -> str: ...
