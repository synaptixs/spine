"""N18: a consumer importing a provider's *re-export* lands on the symbol, not the provider's package.

Inside one repository a name a package re-binds (`from .money import to_cents` in
`shared/__init__.py`) resolves to the defining symbol. Across a declared `package` join it used to
resolve to the provider's *module*: the consumer's placeholder `py:shared.to_cents` matched no
provider symbol, and the longest-prefix fallback then picked `shared`. Same code, two answers.

The provider's re-export tables are built while its files are read and are not part of the cached
facts, so these tests go through the real multi-repo path, cold and warm, with git repositories on
disk — a unit test that hands the joiner a table would pass while a warm cache silently dropped it.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from orchestrator.pkg.facts import EdgeKind
from orchestrator.pkg.persistence import MergedFacts, load_or_extract_repos
from orchestrator.pkg.python_reexport import (
    ModuleExports,
    collect_exports,
    exports_from_dict,
    exports_to_dict,
)
from orchestrator.pkg.repos import RepoSet, from_mapping

CHARGE = """\
from shared import {name}


def charge(amount):
    return {name}(amount)
"""

MONEY = """\
def to_cents(amount):
    return int(amount * 100)


class Money:
    @classmethod
    def of(cls, amount):
        return cls()
"""


def _repo(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "i"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True)


def _system(tmp_path: Path, shared: dict[str, str], consumer: str, name: str = "to_cents") -> RepoSet:
    _repo(tmp_path / "shared", {"shared/money.py": MONEY, **shared})
    _repo(tmp_path / "billing", {"app/__init__.py": "", "app/charge.py": consumer.format(name=name)})
    return from_mapping(
        {"billing": "billing", "shared": "shared"},
        base=tmp_path,
        joins=[{"kind": "package", "consumer": "billing", "provider": "shared"}],
    )


def _crossing(merged: MergedFacts, kind: EdgeKind, src: str = "py:billing@app.charge") -> set[str]:
    return {
        e.dst for e in merged.batch.edges if e.kind is kind and e.src.startswith(src) and "shared@" in e.dst
    }


def _both(tmp_path: Path, repo_set: RepoSet) -> tuple[MergedFacts, MergedFacts]:
    cache = tmp_path / "cache"
    cold = load_or_extract_repos(repo_set, cache_dir=cache)
    warm = load_or_extract_repos(repo_set, cache_dir=cache)
    assert [r.cached for r in warm.repos] == [True, True], "precondition: the facts came from cache"
    return cold, warm


def _assert_lands(tmp_path: Path, repo_set: RepoSet, target: str) -> None:
    for merged in _both(tmp_path, repo_set):
        assert _crossing(merged, EdgeKind.CALLS) == {target}
        assert _crossing(merged, EdgeKind.IMPORTS) == {target}


SYMBOL = "py:shared@shared.money.to_cents"
PACKAGE = "py:shared@shared"


def test_a_package_reexport_lands_on_the_defining_symbol(tmp_path: Path) -> None:
    system = _system(tmp_path, {"shared/__init__.py": "from .money import to_cents\n"}, CHARGE)
    _assert_lands(tmp_path, system, SYMBOL)


def test_a_reexport_chain_is_followed(tmp_path: Path) -> None:
    shared = {
        "shared/__init__.py": "from .api import to_cents\n",
        "shared/api.py": "from .money import to_cents\n",
    }
    _assert_lands(tmp_path, _system(tmp_path, shared, CHARGE), SYMBOL)


def test_an_aliased_reexport_is_followed(tmp_path: Path) -> None:
    shared = {"shared/__init__.py": "from .money import to_cents as cents\n"}
    _assert_lands(tmp_path, _system(tmp_path, shared, CHARGE, name="cents"), SYMBOL)


def test_a_star_reexport_is_followed(tmp_path: Path) -> None:
    shared = {"shared/__init__.py": "from .money import *\n"}
    _assert_lands(tmp_path, _system(tmp_path, shared, CHARGE), SYMBOL)


def test_a_member_of_a_reexported_class_lands_on_the_member(tmp_path: Path) -> None:
    consumer = "from shared import Money\n\n\ndef charge(amount):\n    return Money.of(amount)\n"
    system = _system(tmp_path, {"shared/__init__.py": "from .money import Money\n"}, consumer)
    for merged in _both(tmp_path, system):
        assert _crossing(merged, EdgeKind.CALLS) == {"py:shared@shared.money.Money.of"}


def test_a_name_the_provider_cannot_pin_down_keeps_the_package(tmp_path: Path) -> None:
    """The control for refusals: two imports of one name that disagree bind nothing we can follow."""
    shared = {
        "shared/__init__.py": (
            "try:\n    from .money import to_cents\nexcept ImportError:\n    from .slow import to_cents\n"
        ),
        "shared/slow.py": "def to_cents(amount):\n    return amount\n",
    }
    _assert_lands(tmp_path, _system(tmp_path, shared, CHARGE), PACKAGE)


def test_a_direct_import_is_unchanged(tmp_path: Path) -> None:
    consumer = "from shared.money import to_cents\n\n\ndef charge(amount):\n    return to_cents(amount)\n"
    system = _system(tmp_path, {"shared/__init__.py": ""}, consumer)
    _assert_lands(tmp_path, system, SYMBOL)


def test_a_third_party_import_stays_external(tmp_path: Path) -> None:
    consumer = "from json import dumps\n\n\ndef charge(amount):\n    return dumps(amount)\n"
    system = _system(tmp_path, {"shared/__init__.py": "from .money import to_cents\n"}, consumer)
    for merged in _both(tmp_path, system):
        assert _crossing(merged, EdgeKind.CALLS) == set()
        assert "py:json.dumps" in {n.id for n in merged.batch.nodes}


# ---- the cache format --------------------------------------------------------


_SHAPES = """\
import os
from .a import one
from .b import two as deux
from .c import *
from .d import *

try:
    from .fast import pick
except ImportError:
    from .slow import pick

__all__ = ["one", "deux"]
three = os.sep


def made():
    return 1


class Thing:
    pass
"""

_UNKNOWABLE_ALL = "__all__ = [name for name in dir()]\n"


def _exports(source: str, module: str = "pkg") -> dict[str, ModuleExports]:
    import ast

    tree = ast.parse(source)
    return {f"py:{module}": collect_exports(tree, lambda stmt: f"{module}.{stmt.module or ''}".rstrip("."))}


def test_the_tables_survive_a_round_trip_through_json() -> None:
    import json

    for source in (_SHAPES, _UNKNOWABLE_ALL, ""):
        original = _exports(source)
        restored = exports_from_dict(json.loads(json.dumps(exports_to_dict(original), sort_keys=True)))
        assert restored == original


def test_an_unreadable_all_stays_the_unknowable_sentinel() -> None:
    """`_star_answer` compares it by identity: a copy that merely equals it would read as a literal."""
    import json

    original = _exports(_UNKNOWABLE_ALL)
    restored = exports_from_dict(json.loads(json.dumps(exports_to_dict(original))))
    assert restored is not None
    assert restored["py:pkg"].all_names is original["py:pkg"].all_names


def test_a_damaged_table_is_refused_not_guessed() -> None:
    assert exports_from_dict({"py:pkg": {"bindings": 3}}) is None
    assert exports_from_dict("nope") is None
    assert exports_from_dict(None) is None
