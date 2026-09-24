"""PKG: Room maps onto ``Entity`` + ``READS``/``WRITES``/``REFERENCES``.

P3 of kotlin-support-roadmap.md, §3.3. The behaviours pinned here are the ones
where a plausible shortcut gives a wrong answer:

* an entity's **name is its table** while its **id is its class** — the id keeps
  it unique, the name is what ``data_layer_link`` reconciles against a schema;
* a ``@Query`` is classified by *parsing* it, so ``DELETE FROM topics`` is a
  ``WRITES`` even though the annotation is called Query;
* a write method's entity comes from its **parameter type**, peeled through the
  collection and coroutine wrappers Room methods are written against;
* a table no class declares still gets an edge, to an honest external node.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.data_layer_link import link_data_layer
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.kotlin_extractor import KotlinExtractor

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")
pytest.importorskip("sqlglot", reason="install the 'sql' extra")

DATA_KT = """\
package shop.db

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert

@Entity(tableName = "topics")
data class TopicEntity(
    @PrimaryKey val id: String,
    @ColumnInfo(name = "display_name") val name: String,
    notAProperty: String,
)

@Entity
data class Plain(val id: String)

@Entity(
    tableName = "news_topics",
    foreignKeys = [ForeignKey(entity = TopicEntity::class, parentColumns = ["id"])],
)
data class CrossRef(@PrimaryKey val topicId: String)

@Dao
interface TopicDao {
    @Query(value = "SELECT * FROM topics")
    fun all(): List<TopicEntity>

    @Query(value = \"\"\"
        DELETE FROM topics
        WHERE id = :id
    \"\"\")
    suspend fun remove(id: String)

    @Query(value = "SELECT * FROM unmapped_audit")
    fun audit(): List<String>

    @Query(value = "this is not sql at all ((")
    fun broken(): List<String>

    @Upsert
    suspend fun upsert(entities: List<TopicEntity>)
}
"""


def _facts(tmp_path: Path, src: str = DATA_KT, name: str = "Data.kt") -> FactBatch:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src, encoding="utf-8")
    ex = KotlinExtractor()
    return ex.finalize(ex.extract(path=f, module=ex.module_name(f, tmp_path), rel=name))


def _entities(batch: FactBatch) -> dict[str, str]:
    """Entity id → its name (the table)."""
    return {n.id: n.name for n in batch.nodes if n.kind is NodeKind.ENTITY}


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


# ---- Entity nodes -----------------------------------------------------------


def test_entity_id_follows_the_class_and_name_follows_the_table(tmp_path: Path) -> None:
    """The split that lets one node be both a Kotlin class and a SQL table."""
    entities = _entities(_facts(tmp_path))
    assert entities["java:entity:shop.db.TopicEntity"] == "topics"


def test_entity_without_a_table_name_falls_back_to_the_class(tmp_path: Path) -> None:
    assert _entities(_facts(tmp_path))["java:entity:shop.db.Plain"] == "Plain"


def test_constructor_properties_become_entity_fields(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    fields = {n.id for n in batch.nodes if n.kind is NodeKind.FIELD}
    assert "java:entity:shop.db.TopicEntity.id" in fields
    # `@ColumnInfo(name = ...)` renames the column, and the graph records columns.
    assert "java:entity:shop.db.TopicEntity.display_name" in fields
    # A bare constructor parameter declares no property, so no column (D6).
    assert "java:entity:shop.db.TopicEntity.notAProperty" not in fields


def test_the_class_keeps_its_own_type_and_fields(tmp_path: Path) -> None:
    """The Entity is a parallel view, not a replacement — the C#/PHP precedent."""
    batch = _facts(tmp_path)
    ids = {n.id for n in batch.nodes}
    assert "java:shop.db.TopicEntity" in ids
    assert "java:shop.db.TopicEntity.name" in ids


# ---- REFERENCES -------------------------------------------------------------


def test_foreign_key_inside_the_entity_annotation_is_a_reference(tmp_path: Path) -> None:
    """Room writes `ForeignKey(...)` inside `@Entity(foreignKeys = [...])`, with no `@`."""
    references = _edges(_facts(tmp_path), EdgeKind.REFERENCES)
    assert (
        "java:entity:shop.db.CrossRef",
        "java:entity:shop.db.TopicEntity",
    ) in references


def test_relation_view_references_through_embedded_and_junction(tmp_path: Path) -> None:
    """A Room view is not an entity; its edges hang off the entity it embeds."""
    src = """\
package shop.db

import androidx.room.Embedded
import androidx.room.Entity
import androidx.room.Junction
import androidx.room.Relation

@Entity(tableName = "topics")
data class TopicEntity(val id: String)

@Entity(tableName = "news")
data class NewsEntity(val id: String)

@Entity(tableName = "news_topics")
data class CrossRef(val id: String)

data class PopulatedNews(
    @Embedded val entity: NewsEntity,
    @Relation(
        parentColumn = "id",
        entityColumn = "id",
        associateBy = Junction(value = CrossRef::class),
    )
    val topics: List<TopicEntity>,
)
"""
    references = _edges(_facts(tmp_path, src, "View.kt"), EdgeKind.REFERENCES)
    news = "java:entity:shop.db.NewsEntity"
    assert (news, "java:entity:shop.db.TopicEntity") in references  # child, from the element type
    assert (news, "java:entity:shop.db.CrossRef") in references  # the junction table
    # The view class itself is a query result shape, not a table.
    assert "java:entity:shop.db.PopulatedNews" not in _entities(_facts(tmp_path, src, "View.kt"))


# ---- READS / WRITES ---------------------------------------------------------


def test_a_select_query_reads_its_table(tmp_path: Path) -> None:
    reads = _edges(_facts(tmp_path), EdgeKind.READS)
    assert ("java:shop.db.TopicDao.all", "java:entity:shop.db.TopicEntity") in reads


def test_a_delete_query_writes_rather_than_reads(tmp_path: Path) -> None:
    """The reason the SQL is parsed: `@Query` says nothing about direction."""
    batch = _facts(tmp_path)
    caller = "java:shop.db.TopicDao.remove"
    target = "java:entity:shop.db.TopicEntity"
    assert (caller, target) in _edges(batch, EdgeKind.WRITES)
    assert (caller, target) not in _edges(batch, EdgeKind.READS)


def test_an_upsert_resolves_its_entity_through_the_parameter_type(tmp_path: Path) -> None:
    """`upsert(entities: List<TopicEntity>)` — the wrapper must not hide the entity."""
    writes = _edges(_facts(tmp_path), EdgeKind.WRITES)
    assert ("java:shop.db.TopicDao.upsert", "java:entity:shop.db.TopicEntity") in writes


def test_a_table_no_class_declares_still_gets_an_honest_edge(tmp_path: Path) -> None:
    """The DAO really does read `unmapped_audit`; pretending otherwise loses a fact."""
    batch = _facts(tmp_path)
    reads = _edges(batch, EdgeKind.READS)
    assert ("java:shop.db.TopicDao.audit", "java:entity:unmapped_audit") in reads
    external = {n.id for n in batch.nodes if n.external and n.kind is NodeKind.ENTITY}
    assert "java:entity:unmapped_audit" in external


def test_unparseable_sql_emits_nothing(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    touched = {src for src, _ in _edges(batch, EdgeKind.READS) | _edges(batch, EdgeKind.WRITES)}
    assert "java:shop.db.TopicDao.broken" not in touched


def test_a_plain_class_produces_no_entity(tmp_path: Path) -> None:
    src = "package shop.db\n\nclass NotAnEntity(val id: String)\n"
    assert _entities(_facts(tmp_path, src, "Plain.kt")) == {}


# ---- the payoff: reconciliation with a real schema ---------------------------


def test_room_entities_reconcile_against_a_sql_schema(tmp_path: Path) -> None:
    """P3's exit criterion: the DAO layer and its migrations describe one table.

    ``data_layer_link`` pairs an ORM entity with a schema table **by name**, which
    is the whole reason a Room entity is named after its table rather than its
    class. The schema wins, and the DAO's READS/WRITES follow it across.
    """
    pytest.importorskip("sqlglot", reason="install the 'sql' extra")
    (tmp_path / "Data.kt").write_text(DATA_KT, encoding="utf-8")
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE topics (id TEXT PRIMARY KEY, display_name TEXT);\n", encoding="utf-8"
    )
    batch = link_data_layer(RepoCodeExtractor().extract(tmp_path))

    ids = {n.id for n in batch.nodes}
    assert "sql:topics" in ids, "the schema table is the authoritative node"
    assert "java:entity:shop.db.TopicEntity" not in ids, "the ORM entity merged onto it"
    reads = _edges(batch, EdgeKind.READS)
    assert ("java:shop.db.TopicDao.all", "sql:topics") in reads


# ---- §11 finding 6: an entity's table and an entity's class are different claims ----


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for name, src in files.items():
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def test_a_table_name_held_in_a_constant_is_resolved_not_assumed(tmp_path: Path) -> None:
    """`@Entity(tableName = TOPICS)` names a table; the class name is not that table.

    An unreadable `tableName` used to be indistinguishable from an absent one, so the
    class name was claimed — and then one real table produced *two* Entity nodes, the
    grounded class and an external node for the table its own `@Query` names, which
    `data_layer_link` matches by name and so never reconciles with the migration.
    """
    batch = _repo(
        tmp_path,
        {
            "E.kt": """\
package app.data

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Query

const val TOPICS = "topics"

@Entity(tableName = TOPICS)
class TopicEntity(val id: String)

@Dao
interface TopicDao {
    @Query("SELECT * FROM topics")
    fun all(): List<TopicEntity>
}
"""
        },
    )
    entities = [n for n in batch.nodes if n.kind is NodeKind.ENTITY]
    assert [(n.id, n.name) for n in entities] == [("java:entity:app.data.TopicEntity", "topics")]
    assert ("java:app.data.TopicDao.all", "java:entity:app.data.TopicEntity") in {
        (e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.READS
    }


def test_an_unreadable_table_name_yields_no_entity_at_all(tmp_path: Path) -> None:
    """When the constant is not in the file, the table is unknown — and a table name that
    is *wrong* is worse here than one that is missing."""
    batch = _repo(
        tmp_path,
        {
            "E.kt": """\
package app.data

import androidx.room.Entity

@Entity(tableName = Tables.TOPICS)
class TopicEntity(val id: String)
"""
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENTITY]


def test_a_write_parameter_that_is_not_an_entity_mints_nothing(tmp_path: Path) -> None:
    """`@Insert fun insert(dto: SomeDto)` on a plain data class.

    The id was built from the *class*, so it minted `java:entity:app.data.SomeDto` — an
    Entity whose name is a dotted FQN, for a class carrying no `@Entity`. Unlike an
    unknown *table*, there is nothing here an external placeholder could honestly stand
    for.
    """
    batch = _repo(
        tmp_path,
        {
            "D.kt": """\
package app.data

import androidx.room.Dao
import androidx.room.Insert

class SomeDto(val id: String)

@Dao
interface Writer {
    @Insert
    fun insert(dto: SomeDto)
}
"""
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENTITY]
    assert not [e for e in batch.edges if e.kind is EdgeKind.WRITES]


def test_a_write_parameter_declared_nowhere_still_mints_nothing(tmp_path: Path) -> None:
    """#394. `@Insert fun insert(dto: MysteryThing)` where `MysteryThing` is declared

    *nowhere* in the scanned tree — not even as a plain class. The previous guard
    only refused when the parameter type happened to be declared somewhere in the
    tree (the `SomeDto` case above); an undeclared type fell through to the
    unknown-*table* placeholder path and minted `Entity java:entity:app.data.MysteryThing`,
    a node named with a dotted class FQN rather than a table name. Provenance — this
    id came from a parameter type, not a `@Query` — settles it regardless of whether
    the class is declared anywhere at all.
    """
    batch = _repo(
        tmp_path,
        {
            "D.kt": """\
package app.data

import androidx.room.Dao
import androidx.room.Insert

@Dao
interface Writer {
    @Insert
    fun insert(dto: MysteryThing)
}
"""
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENTITY]
    assert not [e for e in batch.edges if e.kind is EdgeKind.WRITES]


def test_a_write_parameter_that_is_a_genuine_entity_still_resolves(tmp_path: Path) -> None:
    """The provenance check must not cost the ordinary case: a real `@Entity` parameter
    still grounds its `WRITES` edge exactly as before."""
    batch = _repo(
        tmp_path,
        {
            "E.kt": """\
package app.data

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert

@Entity
class TopicEntity(val id: String)

@Dao
interface Writer {
    @Insert
    fun insert(topic: TopicEntity)
}
"""
        },
    )
    assert ("java:app.data.Writer.insert", "java:entity:app.data.TopicEntity") in {
        (e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.WRITES
    }


def test_a_write_parameter_reachable_only_through_a_wildcard_import_resolves(tmp_path: Path) -> None:
    """#397. A genuine `@Entity` reachable only via `import app.data.*` used to be

    guessed into the *caller's* own package instead — `resolve` never tried a
    wildcard prefix — and then silently refused there, a recall miss rather than a
    fabrication. The entity itself is real and unambiguous, so it must resolve.
    """
    batch = _repo(
        tmp_path,
        {
            "data/Topic.kt": """\
package app.data

import androidx.room.Entity

@Entity
class TopicEntity(val id: String)
""",
            "db/Dao.kt": """\
package app.db

import androidx.room.Dao
import androidx.room.Insert
import app.data.*

@Dao
interface Writer {
    @Insert
    fun insert(topic: TopicEntity)
}
""",
        },
    )
    assert ("java:app.db.Writer.insert", "java:entity:app.data.TopicEntity") in {
        (e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.WRITES
    }


def test_a_precise_import_outranks_an_unrelated_wildcard_hit(tmp_path: Path) -> None:
    """A review of the #397 fix above found a regression it introduced: treating

    `resolve`'s precise-import answer as merely one candidate among wildcard
    guesses meant an unrelated `import app.other.*` that happens to also declare a
    same-named `@Entity` made an otherwise unambiguous `import app.data.TopicEntity`
    look ambiguous, and the edge was dropped — worse than the bug being fixed.
    Kotlin's own resolution order never considers this ambiguous: an explicit
    import always wins over a wildcard, full stop.
    """
    batch = _repo(
        tmp_path,
        {
            "data/Topic.kt": """\
package app.data

import androidx.room.Entity

@Entity
class TopicEntity(val id: String)
""",
            "other/Topic.kt": """\
package app.other

import androidx.room.Entity

@Entity
class TopicEntity(val id: String)
""",
            "db/Dao.kt": """\
package app.db

import androidx.room.Dao
import androidx.room.Insert
import app.data.TopicEntity
import app.other.*

@Dao
interface Writer {
    @Insert
    fun insert(topic: TopicEntity)
}
""",
        },
    )
    assert ("java:app.db.Writer.insert", "java:entity:app.data.TopicEntity") in {
        (e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.WRITES
    }


def test_a_write_parameter_ambiguous_across_two_wildcard_imports_mints_nothing(
    tmp_path: Path,
) -> None:
    """Two wildcard-imported packages each declaring a genuine `@Entity` of the same

    simple name is a real ambiguity — resolved the same way an ambiguous call
    resolution is refused elsewhere in this front-end, rather than guessed.
    """
    batch = _repo(
        tmp_path,
        {
            "a/Topic.kt": """\
package app.a

import androidx.room.Entity

@Entity
class TopicEntity(val id: String)
""",
            "b/Topic.kt": """\
package app.b

import androidx.room.Entity

@Entity
class TopicEntity(val id: String)
""",
            "db/Dao.kt": """\
package app.db

import androidx.room.Dao
import androidx.room.Insert
import app.a.*
import app.b.*

@Dao
interface Writer {
    @Insert
    fun insert(topic: TopicEntity)
}
""",
        },
    )
    assert not [e for e in batch.edges if e.kind is EdgeKind.WRITES]


def _writes(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.WRITES}


_ENTITY_IN = """\
package {pkg}

import androidx.room.Entity

@Entity
class {name}(val id: String)
"""

_DAO = """\
package app.db

import androidx.room.Dao
import androidx.room.Insert
{imports}

{decls}

@Dao
interface Writer {{
{methods}
}}
"""


def test_a_same_package_plain_class_hides_a_wildcard_imported_entity(tmp_path: Path) -> None:
    """Kotlin resolves a simple name through the same package before any star
    import, so `t: Topic` is the plain `app.db.Topic` — not the `@Entity` that
    `import app.w.*` also offers. Treating the two as peers invented a `WRITES`."""
    batch = _repo(
        tmp_path,
        {
            "w/Topic.kt": _ENTITY_IN.format(pkg="app.w", name="Topic"),
            "db/Dao.kt": _DAO.format(
                imports="import app.w.*",
                decls="data class Topic(val id: String)",
                methods="    @Insert\n    fun insert(t: Topic)",
            ),
        },
    )
    assert not _writes(batch)


def test_a_same_package_entity_wins_over_a_wildcard_imported_one(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "w/Topic.kt": _ENTITY_IN.format(pkg="app.w", name="TopicEntity"),
            "db/Entity.kt": _ENTITY_IN.format(pkg="app.db", name="TopicEntity"),
            "db/Dao.kt": _DAO.format(
                imports="import app.w.*", decls="", methods="    @Insert\n    fun insert(t: TopicEntity)"
            ),
        },
    )
    assert _writes(batch) == {("java:app.db.Writer.insert", "java:entity:app.db.TopicEntity")}


def test_overloaded_write_methods_each_keep_their_entity(tmp_path: Path) -> None:
    """Overloads share one function id; settling them together made two grounded
    entities look like one ambiguous parameter, and both edges were dropped."""
    batch = _repo(
        tmp_path,
        {
            "db/Entities.kt": _ENTITY_IN.format(pkg="app.db", name="TopicEntity")
            + "\n@Entity\nclass NewsEntity(val id: String)\n",
            "db/Dao.kt": _DAO.format(
                imports="",
                decls="",
                methods=(
                    "    @Insert\n    fun insert(t: TopicEntity)\n    @Insert\n    fun insert(n: NewsEntity)"
                ),
            ),
        },
    )
    assert _writes(batch) == {
        ("java:app.db.Writer.insert", "java:entity:app.db.TopicEntity"),
        ("java:app.db.Writer.insert", "java:entity:app.db.NewsEntity"),
    }


def test_a_qualified_parameter_type_resolves_as_written(tmp_path: Path) -> None:
    """`app.data.TopicEntity` names that class, even in a package declaring its own
    `TopicEntity` — resolving the simple name wrote to the wrong entity."""
    batch = _repo(
        tmp_path,
        {
            "data/Topic.kt": _ENTITY_IN.format(pkg="app.data", name="TopicEntity"),
            "db/Entity.kt": _ENTITY_IN.format(pkg="app.db", name="TopicEntity"),
            "db/Dao.kt": _DAO.format(
                imports="", decls="", methods="    @Insert\n    fun insert(t: app.data.TopicEntity)"
            ),
        },
    )
    assert _writes(batch) == {("java:app.db.Writer.insert", "java:entity:app.data.TopicEntity")}
