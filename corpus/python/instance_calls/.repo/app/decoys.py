"""Receiver shapes that must NOT get a method edge (B53 / SSPN-113).

Each function is written so a wrong claim would land on `Store.get`, `Base.shared` or
`Rocket.get`; the extractor refuses every one. `corpus/python/instance_calls/expected.json`
lists each as a refusal, so an over-eager rule shows up as a gated regression here and not only
in a unit test.
"""

from typing import Dict, List

from somelib import Widget, register

from app.store import Base, Rocket, Store

try:
    from app.fast import Pick
except ImportError:
    from app.slow import Pick


class Outer:
    class Store:
        pass

    field: Store

    def via_class_frame(self, s: Store) -> int:
        return s.get(1)


class Wide(Base, Widget):
    """Base first, external second: the external base may override `shared`."""


class WideFirst(Widget, Base):
    """The external base first."""


def helper(key: int) -> int:
    return key


class Tool:
    """`run` is a class attribute holding a function: a callable, not a method."""

    run = helper


class Shadow(Base):
    """An instance attribute and a class attribute both replace `Base.shared`."""

    shared = None

    def __init__(self) -> None:
        self.shared = lambda: 2


class Impostor(Base):
    """`self` is the instance only while it is the first parameter and nothing rebinds it."""

    def rebound(self, other: Rocket) -> int:
        self = other
        return self.shared()

    @staticmethod
    def static(self) -> int:
        return self.shared()


class Cell:
    def __init__(self) -> None:
        self.store = Store()

    def rebind_in_with(self) -> int:
        with open("f") as self.store:
            pass
        return self.store.get(1)

    def rebind_in_closure(self) -> int:
        def reset() -> None:
            self.store = Rocket()

        register(reset)
        return self.store.get(1)


class Holder:
    def __init__(self) -> None:
        self.store = Store()


class Prop(Holder):
    @property
    def store(self) -> object:
        return self._raw

    def via_property(self) -> int:
        return self.store.get(1)


def via_loop(xs: list) -> int:
    s = Store()
    for s in xs:
        pass
    return s.get(1)


def via_comprehension(xs: list) -> list:
    s = Store()
    return [s.get(1) for s in xs]


def via_lambda() -> int:
    s = Store()
    return (lambda s: s.get(1))(0)


def via_walrus(xs: list) -> int:
    s = Store()
    if (s := xs):
        pass
    return s.get(1)


def via_except_star() -> int:
    s = Store()
    try:
        pass
    except* ValueError as s:
        pass
    return s.get(1)


def via_match(xs: dict) -> int:
    s = Store()
    match xs:
        case {**s}:
            pass
    return s.get(1)


def via_type_alias() -> int:
    s = Store()
    type s = int
    return s.get(1)


def via_type_param[Store](s: Store) -> int:
    return s.get(1)


def via_nonlocal(s: Store) -> int:
    def reset() -> None:
        nonlocal s
        s = Rocket()

    register(reset)
    return s.get(1)


def via_list(xs: List[Store]) -> int:
    return xs.get(1)


def via_generic(d: Dict[str, Store]) -> int:
    return d.get(1)


def via_annotation_before_local(s: Store) -> int:
    class Store:
        pass

    return s.get(1)


def via_disagreeing_imports(p: Pick) -> int:
    return p.run()


def via_wide(w: Wide) -> int:
    return w.shared()


def via_wide_first(w: WideFirst) -> int:
    return w.shared()


def via_shadow(s: Shadow) -> int:
    return s.shared()


def outer_with_match(v: int) -> int:
    match v:
        case 1:

            def hidden(s: Store) -> int:
                return s.get(1)

    return v
