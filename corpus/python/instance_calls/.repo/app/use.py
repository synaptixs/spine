"""Every receiver shape B35 types, and the ones it must refuse."""

from typing import Optional

from somelib import Client

from app import Store, Tool
from app.mixins import Both, Wrapped
from app.proto import Reader
from app.store import Rocket


class Service:
    cache: Store

    def __init__(self, store: Store) -> None:
        self._store = store
        self._rocket = Rocket()
        self.later = None
        self.later = Store()
        self.mixed = Store()
        self.mixed = Rocket()
        self.client = Client()

    def via_param_field(self) -> int:
        return self._store.get(1)

    def via_ctor_field(self) -> int:
        return self._rocket.get(1)

    def via_class_annotation(self) -> int:
        return self.cache.get(1)

    def via_none_then_type(self) -> int:
        return self.later.get(1)

    def via_mixed_field(self) -> int:
        return self.mixed.get(1)

    def via_external_field(self) -> int:
        return self.client.get(1)


class Special(Service):
    def via_inherited_field(self) -> int:
        return self._store.get(2)


def via_param(store: Store) -> int:
    return store.get(1)


def via_optional(store: Optional[Store]) -> int:
    return store.get(1)


def via_union_none(store: Store | None) -> int:
    return store.get(1)


def via_union_two(thing: Store | Rocket) -> int:
    return thing.get(1)


def via_local() -> int:
    s = Store()
    return s.get(1)


def via_reassigned() -> int:
    s = Store()
    s = Rocket()
    return s.get(1)


def via_inherited(store: Store) -> int:
    return store.shared()


def via_classmethod() -> Store:
    return Store.make()


def via_protocol(reader: Reader) -> str:
    return reader.read()


def via_mro(both: Both) -> str:
    return both.ping()


def via_open_base(w: Wrapped) -> str:
    w.close()
    return w.own()


def via_external_param(c: Client) -> int:
    return c.get(1)


def via_closure() -> int:
    s = Store()

    def inner() -> int:
        return s.get(1)

    return inner()


def via_chain() -> int:
    return Service(Store()).cache.get(1)


def via_class_attribute() -> int:
    return Tool.run(1)
