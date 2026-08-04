"""Python ORM models → ``Entity`` / ``Field`` / ``REFERENCES``.

The negative tests come first on purpose. At field level a Pydantic model, a dataclass, a
TypedDict and an attrs class look exactly like an ORM model, and turning them into tables
would fill the data layer with rows, columns and relationships that exist in no database.
A missing entity is a gap; an invented one is a lie the graph presents as grounded.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.extractor import PythonExtractor, RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, NodeKind


def _extract(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for name, source in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _entities(batch: FactBatch) -> set[str]:
    return {n.name for n in batch.nodes if n.kind is NodeKind.ENTITY}


def _fields(batch: FactBatch) -> set[str]:
    """Columns of a *table*, not attributes of a class.

    The front-end already emits a ``Field`` per class attribute, and it still does — the
    table's columns are a separate set of nodes hanging off the ``Entity``.
    """
    return {n.name for n in batch.nodes if n.kind is NodeKind.FIELD and n.id.startswith("py:entity:")}


def _references(batch: FactBatch) -> list[Edge]:
    return [e for e in batch.edges if e.kind is EdgeKind.REFERENCES]


# ---- the shapes that must NEVER become tables ------------------------------


def test_pydantic_dataclass_typeddict_and_attrs_are_not_entities(tmp_path: Path) -> None:
    """None of these has a table. All of them look like one if you gate on shape."""
    batch = _extract(
        tmp_path,
        {
            "shapes.py": (
                "import attrs\n"
                "from dataclasses import dataclass\n"
                "from typing import TypedDict\n\n"
                "from pydantic import BaseModel\n\n\n"
                "class OrderIn(BaseModel):\n"
                "    id: int\n"
                "    total: float\n\n\n"
                "@dataclass\n"
                "class OrderRow:\n"
                "    id: int\n"
                "    total: float\n\n\n"
                "class OrderDict(TypedDict):\n"
                "    id: int\n\n\n"
                "@attrs.define\n"
                "class OrderAttrs:\n"
                "    id: int\n"
            )
        },
    )
    assert _entities(batch) == set()
    assert _references(batch) == []


def test_a_computed_tablename_yields_no_entity(tmp_path: Path) -> None:
    """A table named by a guess is worse than a table that is missing."""
    batch = _extract(
        tmp_path,
        {
            "m.py": (
                "from sqlalchemy.orm import DeclarativeBase\n\n\n"
                "def _table_for(name):\n    return name\n\n\n"
                "class Base(DeclarativeBase):\n    pass\n\n\n"
                "class Row(Base):\n"
                '    __tablename__ = _table_for("rows")\n'
            )
        },
    )
    assert _entities(batch) == set()


# ---- SQLAlchemy ------------------------------------------------------------


def test_sqlalchemy_2_style_entity_columns_and_provenance(tmp_path: Path) -> None:
    batch = _extract(
        tmp_path,
        {
            "models.py": (
                "import uuid\n\n"
                "from sqlalchemy import String\n"
                "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n\n\n"
                "class Base(DeclarativeBase):\n    pass\n\n\n"
                "class OrderRow(Base):\n"
                '    __tablename__ = "orders"\n\n'
                "    pk: Mapped[uuid.UUID] = mapped_column(primary_key=True)\n"
                "    total: Mapped[str] = mapped_column(String(64))\n"
            )
        },
    )
    assert _entities(batch) == {"orders"}
    assert _fields(batch) == {"orders.pk", "orders.total"}
    entity = next(n for n in batch.nodes if n.kind is NodeKind.ENTITY)
    # Provenance at the class line — where a reader goes to change the table.
    assert entity.provenance is not None
    class_line = next(n for n in batch.nodes if n.kind is NodeKind.TYPE and n.name == "OrderRow")
    assert class_line.provenance is not None
    assert entity.provenance.line == class_line.provenance.line
    # The class keeps its Type node; the Entity is added alongside, as C# does.
    assert any(n.kind is NodeKind.TYPE and n.name == "OrderRow" for n in batch.nodes)


def test_classic_column_style_is_read_too(tmp_path: Path) -> None:
    batch = _extract(
        tmp_path,
        {
            "m.py": (
                "from sqlalchemy import Column, Integer\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n\n"
                "class Row(Base):\n"
                '    __tablename__ = "rows"\n'
                "    id = Column(Integer, primary_key=True)\n"
            )
        },
    )
    assert _entities(batch) == {"rows"}
    assert _fields(batch) == {"rows.id"}


def test_a_literal_foreign_key_links_two_entities(tmp_path: Path) -> None:
    batch = _extract(
        tmp_path,
        {
            "m.py": (
                "from sqlalchemy import Column, ForeignKey, Integer\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n\n"
                "class Customer(Base):\n"
                '    __tablename__ = "customers"\n'
                "    id = Column(Integer, primary_key=True)\n\n\n"
                "class Order(Base):\n"
                '    __tablename__ = "orders"\n'
                '    customer_id = Column(Integer, ForeignKey("customers.id"))\n'
            )
        },
    )
    assert _entities(batch) == {"customers", "orders"}
    (edge,) = _references(batch)
    assert (edge.src, edge.dst) == ("py:entity:orders", "py:entity:customers")


def test_a_foreign_key_to_an_unknown_table_still_emits(tmp_path: Path) -> None:
    """Same rule the SQL front-end follows: the edge is real even when the table is elsewhere."""
    batch = _extract(
        tmp_path,
        {
            "m.py": (
                "from sqlalchemy import Column, ForeignKey, Integer\n"
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n\n"
                "class Order(Base):\n"
                '    __tablename__ = "orders"\n'
                '    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))\n'
            )
        },
    )
    (edge,) = _references(batch)
    assert edge.dst == "py:entity:warehouses"
    target = next(n for n in batch.nodes if n.id == "py:entity:warehouses")
    assert target.external


def test_entity_ids_are_not_sql_prefixed(tmp_path: Path) -> None:
    """``link_data_layer`` keys on the prefix to decide which side is authoritative —
    a ``sql:`` id here would make the ORM's inferred model outrank the real schema."""
    batch = _extract(
        tmp_path,
        {
            "m.py": (
                "from sqlalchemy.orm import declarative_base\n\n"
                "Base = declarative_base()\n\n\n"
                "class Row(Base):\n"
                '    __tablename__ = "rows"\n'
            )
        },
    )
    entity = next(n for n in batch.nodes if n.kind is NodeKind.ENTITY)
    assert entity.id == "py:entity:rows" and not entity.id.startswith("sql:")


# ---- Django ----------------------------------------------------------------


def test_django_meta_db_table_wins(tmp_path: Path) -> None:
    batch = _extract(
        tmp_path,
        {
            "shop/models.py": (
                "from django.db import models\n\n\n"
                "class Order(models.Model):\n"
                "    total = models.IntegerField()\n\n"
                "    class Meta:\n"
                '        db_table = "legacy_orders"\n'
            )
        },
    )
    assert _entities(batch) == {"legacy_orders"}
    assert _fields(batch) == {"legacy_orders.total"}


def test_django_default_table_name_is_app_model(tmp_path: Path) -> None:
    """Django's own convention when ``Meta.db_table`` is absent."""
    batch = _extract(
        tmp_path,
        {"shop/models.py": ("from django.db import models\n\n\nclass Order(models.Model):\n    pass\n")},
    )
    assert _entities(batch) == {"shop_order"}


def test_django_foreign_key_resolves_across_files(tmp_path: Path) -> None:
    """The reason emission is deferred: the target class lives in another module, and its
    table name is only known once every model has been read."""
    batch = _extract(
        tmp_path,
        {
            "shop/models.py": (
                "from django.db import models\n\n\n"
                "class Customer(models.Model):\n"
                "    class Meta:\n"
                '        db_table = "customers"\n'
            ),
            "billing/models.py": (
                "from django.db import models\n\n"
                "from shop.models import Customer\n\n\n"
                "class Invoice(models.Model):\n"
                "    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)\n\n"
                "    class Meta:\n"
                '        db_table = "invoices"\n'
            ),
        },
    )
    assert _entities(batch) == {"customers", "invoices"}
    (edge,) = _references(batch)
    assert (edge.src, edge.dst) == ("py:entity:invoices", "py:entity:customers")


def test_django_lazy_string_reference_resolves(tmp_path: Path) -> None:
    batch = _extract(
        tmp_path,
        {
            "shop/models.py": (
                "from django.db import models\n\n\n"
                "class Customer(models.Model):\n"
                "    class Meta:\n"
                '        db_table = "customers"\n\n\n'
                "class Invoice(models.Model):\n"
                '    customer = models.ForeignKey("shop.Customer", on_delete=models.CASCADE)\n\n'
                "    class Meta:\n"
                '        db_table = "invoices"\n'
            )
        },
    )
    (edge,) = _references(batch)
    assert (edge.src, edge.dst) == ("py:entity:invoices", "py:entity:customers")


# ---- state hygiene ---------------------------------------------------------


def test_finalize_clears_state_between_walks(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "from sqlalchemy.orm import declarative_base\n\nBase = declarative_base()\n\n\n"
        'class Row(Base):\n    __tablename__ = "rows"\n',
        encoding="utf-8",
    )
    repo = RepoCodeExtractor([PythonExtractor()])
    first = repo.extract(tmp_path)
    second = repo.extract(tmp_path)

    def count(batch: FactBatch) -> int:
        return len([n for n in batch.nodes if n.kind is NodeKind.ENTITY])

    assert count(first) == count(second) == 1


# ---- this repo -------------------------------------------------------------


def test_this_repo_has_one_entity_per_declared_tablename() -> None:
    """Self-adjusting rather than a fixed count, and it doubles as the false-positive
    guard: this repo is full of Pydantic models, so any shape-based rule would blow past
    the number of real ``__tablename__`` declarations immediately."""
    root = Path(__file__).resolve().parents[2]
    declared = set()
    for path in (root / "src").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("__tablename__ =") and stripped.rstrip().endswith(('"', "'")):
                declared.add(stripped.split("=", 1)[1].strip().strip("\"'"))

    batch = RepoCodeExtractor().extract(root / "src")
    assert _entities(batch) == declared
    assert declared, "no __tablename__ declarations found — the probe stopped working"
