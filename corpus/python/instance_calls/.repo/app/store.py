"""A small hierarchy plus a decoy class that shares a method name."""


class Base:
    def shared(self) -> int:
        return 1

    @classmethod
    def make(cls) -> "Base":
        return cls()


class Store(Base):
    def get(self, key: int) -> int:
        return key

    def put(self, key: int) -> int:
        return self.shared()


class Rocket:
    """The decoy: `get` here must never catch a call made through a `Store`."""

    def get(self, key: int) -> int:
        return key
