"""Shapes B35 leaves out on purpose (D5): `super().m()` and `cls.m()` (B53 records them)."""

from app.store import Store


class Child(Store):
    def get(self, key: int) -> int:
        return super().get(key)

    @classmethod
    def build(cls) -> "Child":
        return cls.make()
