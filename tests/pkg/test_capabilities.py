"""The capability matrix is a reading of the front-ends, and must stay one.

The point of these tests is that the committed table in ``KNOWLEDGE_GRAPH.md`` cannot drift
from the code it describes: add ``Endpoint`` to the Python front-end and the doc test fails
until the table is regenerated. A hand-maintained version of this table was measured 22%
wrong, and nothing failed — that is the failure mode being closed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator.pkg.capabilities import (
    FRONT_ENDS,
    Capability,
    _kinds_in,
    front_end_capabilities,
    render_markdown,
)
from orchestrator.pkg.extractor import RepoCodeExtractor, default_extractors
from orchestrator.pkg.facts import EdgeKind, NodeKind

_DOC = Path(__file__).resolve().parents[2] / "KNOWLEDGE_GRAPH.md"
_BLOCK = re.compile(
    r"<!-- BEGIN capability-matrix[^>]*-->\n(?P<body>.*?)\n<!-- END capability-matrix -->",
    re.DOTALL,
)


def _by_language() -> dict[str, Capability]:
    return {c.language: c for c in front_end_capabilities()}


def test_the_committed_matrix_matches_the_front_ends() -> None:
    """The doc block is byte-equal to what the generator produces, or the build fails."""
    match = _BLOCK.search(_DOC.read_text(encoding="utf-8"))
    assert match is not None, "capability-matrix markers missing from KNOWLEDGE_GRAPH.md"
    assert match.group("body") == render_markdown(), (
        "KNOWLEDGE_GRAPH.md's capability matrix is stale — regenerate with `orchestrator pkg capabilities`."
    )


def test_every_front_end_has_a_row() -> None:
    """A new language must appear in the matrix, not silently sit outside it."""
    caps = _by_language()
    assert set(caps) == {fe.language for fe in FRONT_ENDS}
    # Every front-end emits *something*; an empty row means the derivation broke.
    assert all(c.node_kinds and c.edge_kinds for c in caps.values())


def test_reported_kinds_are_real_vocabulary() -> None:
    """Guards the ``NodeKind.X`` scan against reporting a member that no longer exists."""
    node_values = {k.value for k in NodeKind}
    edge_values = {k.value for k in EdgeKind}
    for cap in front_end_capabilities():
        assert set(cap.node_kinds) <= node_values
        assert set(cap.edge_kinds) <= edge_values


def test_only_the_named_front_end_class_counts() -> None:
    """``extractor.py`` holds ``RepoCodeExtractor`` beside the Python front-end, so the scan
    must ignore other ``*Extractor`` classes — otherwise Python inherits the whole-repo
    import join. Module-level helpers *do* count: Java builds its endpoints in one.

    Tested on synthetic source rather than on Python's real column, because that column is
    now a moving target — SSPN-2 gave it ``Endpoint``/``EXPOSES``, and SSPN-3 will add
    ``Entity``. A test keyed to today's kinds would fail for the wrong reason.
    """
    source = (
        "class PythonExtractor:\n"
        "    def extract(self):\n        return NodeKind.MODULE, EdgeKind.CALLS\n\n"
        "class RepoCodeExtractor:\n"
        "    def extract(self):\n        return NodeKind.ENTITY, EdgeKind.READS\n\n"
        "def _helper():\n    return NodeKind.ENDPOINT, EdgeKind.EXPOSES\n"
    )
    nodes, edges = _kinds_in(source, keep_class="PythonExtractor")
    assert nodes == {"MODULE", "ENDPOINT"}
    assert edges == {"CALLS", "EXPOSES"}


def test_python_claims_the_node_kinds_it_now_emits() -> None:
    """The parity track, asserted: Endpoint (SSPN-2), Entity (SSPN-3), READS/WRITES (SSPN-4).

    This is the assertion that fails when a front-end grows a kind and nobody regenerates the
    committed matrix — the drift that made a hand-authored version 22% wrong.
    """
    python = _by_language()["python"]
    assert {"Endpoint", "Entity", "Field", "Module", "Type", "Function"} <= set(python.node_kinds)
    assert {"EXPOSES", "REFERENCES", "READS", "WRITES"} <= set(python.edge_kinds)
    # Doc / MENTIONS belong to doc ingestion, which is no language's column.
    assert "Doc" not in python.node_kinds
    assert "MENTIONS" not in python.edge_kinds


# One real file per language, so the cross-check below extracts something rather than
# asserting containment against an empty set. A language whose extra isn't installed skips
# — locally that is everything but Python and SQL; CI installs the rest.
_FIXTURES: dict[str, tuple[str, str]] = {
    "python": (
        "sample.py",
        "import os\n\n\nclass Base:\n    pass\n\n\nclass Thing(Base):\n"
        "    def go(self) -> None:\n        self.name = os.getcwd()\n",
    ),
    "sql": (
        "schema.sql",
        "CREATE TABLE customer (id INT PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE invoice (id INT PRIMARY KEY, customer_id INT REFERENCES customer(id));\n"
        "INSERT INTO invoice (id, customer_id) SELECT 1, id FROM customer;\n",
    ),
    "typescript": (
        "sample.ts",
        "import { readFile } from 'fs';\n\nexport interface Named { name: string }\n\n"
        "export class Thing implements Named {\n  name = 'x';\n  go() { readFile('p', () => {}); }\n}\n",
    ),
    "kotlin": (
        "Sample.kt",
        "package demo\n\nimport kotlin.math.abs\n\n"
        "class Sample(private val name: String) {\n"
        "    fun go(): Int = abs(name.length)\n}\n",
    ),
    "java": (
        "Sample.java",
        "package demo;\n\nimport java.util.List;\n\npublic class Sample {\n"
        "  private String name;\n  public void go() { System.out.println(name); }\n}\n",
    ),
    "csharp": (
        "Sample.cs",
        "using System;\n\nnamespace Demo {\n  public class Sample {\n"
        '    private string name = "x";\n    public void Go() { Console.WriteLine(name); }\n  }\n}\n',
    ),
    "go": (
        "sample.go",
        'package demo\n\nimport "fmt"\n\ntype Thing struct{ Name string }\n\n'
        "func (t Thing) Go() { fmt.Println(t.Name) }\n",
    ),
    "c": ("sample.c", '#include <stdio.h>\n\nstruct Thing { int n; };\n\nvoid go(void) { printf("x"); }\n'),
    "cpp": (
        "sample.cpp",
        '#include <cstdio>\n\nclass Thing { public: int n; void go() { printf("x"); } };\n',
    ),
    "php": (
        "sample.php",
        "<?php\nnamespace Demo;\n\nuse Demo\\Base;\n\nclass Thing extends Base {\n"
        "    public function go(): void {}\n}\n",
    ),
    "perl": (
        "sample.pm",
        "package Demo::Thing;\nuse parent -norequire, 'Demo::Base';\n\nsub go {\n    return 1;\n}\n",
    ),
    # CommonJS on purpose: `require` and `exports.` are the paths the JavaScript front-end adds
    # over the TypeScript one it subclasses, so an ESM fixture would exercise only the parent.
    "javascript": (
        "sample.js",
        "const { readFile } = require('fs');\nconst express = require('express');\n"
        "const { DataTypes } = require('sequelize');\n\nclass Base {}\n\n"
        "class Thing extends Base {\n  name = 'x';\n  go() { readFile('p', () => {}); }\n}\n\n"
        "exports.run = function () { return new Thing(); };\n"
        "function health(req, res) {}\nconst app = express();\napp.get('/health', health);\n"
        "module.exports.model = (s) => {\n"
        "  s.define('user', { id: DataTypes.INTEGER });\n"
        "  s.define('post', { id: DataTypes.INTEGER });\n"
        "  s.models.post.belongsTo(s.models.user);\n};\n",
    ),
}

#: Front-ends with no fixture above, each with the reason. Absent from both, a new front-end
#: would register, claim a column, and never have that claim checked against a real run — the
#: superset test skips what it has no fixture for, silently. That is how JavaScript arrived
#: uncovered, and how Gradle had been for its whole life.
_NO_FIXTURE: dict[str, str] = {
    "gradle": "pre-existing gap, not an exemption on the merits: no fixture was written when the "
    "Gradle reader landed (kotlin-support-roadmap D11). Recorded here so it is visible, not excused",
}


def test_every_front_end_is_cross_checked_or_says_why_not() -> None:
    registered = {fe.language for fe in FRONT_ENDS}
    assert not set(_FIXTURES) & set(_NO_FIXTURE), "a front-end cannot be both fixtured and excused"
    uncovered = sorted(registered - set(_FIXTURES) - set(_NO_FIXTURE))
    assert not uncovered, f"no superset fixture and no stated reason for: {uncovered}"
    stale = sorted((set(_FIXTURES) | set(_NO_FIXTURE)) - registered)
    assert not stale, f"fixtured or excused, but no longer a registered front-end: {stale}"


@pytest.mark.parametrize("language", sorted(_FIXTURES))
def test_the_matrix_claims_a_superset_of_what_a_run_emits(language: str, tmp_path: Path) -> None:
    """Cross-check the static reading against a real extraction.

    Capability is a *superset* of any one repo's coverage — a front-end that can emit
    ``Endpoint`` emits none for a fixture with no routes — so the assertion is containment,
    not equality. It still catches the derivation missing a kind the code actually produces,
    which is the direction that would make the matrix a lie.
    """
    name, source = _FIXTURES[language]
    suffix = Path(name).suffix
    if suffix not in {s for ex in default_extractors() for s in ex.suffixes}:
        pytest.skip(f"install the {language!r} extra — no front-end registered for {suffix}")
    (tmp_path / name).write_text(source, encoding="utf-8")

    batch = RepoCodeExtractor().extract(tmp_path)
    language_of = {n.id: n.language for n in batch.nodes}
    emitted_nodes = {n.kind.value for n in batch.nodes if n.language == language}
    emitted_edges = {e.kind.value for e in batch.edges if language_of.get(e.src) == language}
    assert emitted_nodes, f"fixture produced no {language} nodes — it stopped exercising the front-end"

    cap = _by_language()[language]
    assert emitted_nodes <= set(cap.node_kinds), f"{language} emitted an unclaimed node kind"
    assert emitted_edges <= set(cap.edge_kinds), f"{language} emitted an unclaimed edge kind"
