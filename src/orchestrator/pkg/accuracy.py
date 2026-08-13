"""Tier-2 graph accuracy — precision and recall against hand-labelled ground truth.

``verify.py`` answers *is the graph self-consistent*, which it can do on any repo with
nothing to compare against. This module answers *is the graph right*, which it cannot do
without an oracle — so it takes one: a corpus of small fixture repositories whose facts a
human has written down by hand (see ``corpus/README.md``).

**This reports; it does not gate.** No threshold, no non-zero exit on a low score. A
regression gate is a later phase and building it before there is anything true to put in it
is the mistake this whole measurement exists to avoid.

The scoring rules, each of which was decided against a real case rather than in the
abstract:

- **Node match key is ``(id, kind)``; edge match key is ``(src, dst, kind)``.** Provenance is
  deliberately *not* in the key — a fact that moved down a line is not a wrong fact, and
  folding that into precision makes the headline number move for reasons nobody cares about.
  Drift is reported separately, for the expected facts that carry an ``at``.
- **External nodes are excluded from the node ratio. Edges pointing at them are not.** An
  ``external=True`` node is a placeholder for something outside the scanned tree, so counting
  it would mean labelling every stdlib symbol in every case. An edge *to* one is a different
  thing: it is a claim that the call happens, and that claim can be false. Excluding both is
  how ``build -CALLS-> py:cls`` — where ``cls`` is a local variable — scored 1.0 precision on
  the first corpus run.
- **A kind with no expected facts scores ``None``, never ``1.0``.** Vacuous perfection is the
  easiest way to publish a misleading number. The raw counts are reported alongside, so
  "emitted 7, labelled 0" stays visible instead of averaging into a score.
- **``known_gaps`` and ``false_positives`` never change a score.** They annotate a miss or an
  invention with a reason so a low number is legible; they do not suppress it. Both are
  validated as consistent with ``edges`` on load, because an exemption that quietly moved the
  number would be worse than no annotation at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind

CASE_FILE = "expected.json"

_NODE_KINDS = {k.value for k in NodeKind}
_EDGE_KINDS = {k.value for k in EdgeKind}
_MAX_EXAMPLES = 10


class CorpusError(Exception):
    """A corpus case is malformed. The only condition that fails ``pkg accuracy``."""


@dataclass(frozen=True)
class KindScore:
    """Precision and recall for one node or edge kind.

    ``None`` means *undefined*, not *zero*: precision needs something emitted to be right or
    wrong about, and recall needs something expected to have found.
    """

    kind: str
    expected: int
    emitted: int
    matched: int

    @property
    def precision(self) -> float | None:
        return self.matched / self.emitted if self.emitted else None

    @property
    def recall(self) -> float | None:
        return self.matched / self.expected if self.expected else None


@dataclass(frozen=True)
class CaseReport:
    """One corpus case, scored."""

    language: str
    case: str
    nodes: tuple[KindScore, ...]
    edges: tuple[KindScore, ...]
    missing: tuple[str, ...]
    unlabelled: tuple[str, ...]
    known_gaps: int
    declared_false_positives: int
    provenance_checked: int
    provenance_drift: tuple[str, ...]


@dataclass(frozen=True)
class AccuracyReport:
    """Every case, plus the per-language totals."""

    cases: tuple[CaseReport, ...]

    def totals(self) -> dict[str, dict[str, tuple[KindScore, ...]]]:
        """``{language: {"nodes": (...), "edges": (...)}}`` — summed across cases."""
        out: dict[str, dict[str, tuple[KindScore, ...]]] = {}
        for lang in sorted({c.language for c in self.cases}):
            cases = [c for c in self.cases if c.language == lang]
            out[lang] = {
                "nodes": _sum_scores([s for c in cases for s in c.nodes]),
                "edges": _sum_scores([s for c in cases for s in c.edges]),
            }
        return out


def _sum_scores(scores: list[KindScore]) -> tuple[KindScore, ...]:
    merged: dict[str, list[int]] = {}
    for s in scores:
        acc = merged.setdefault(s.kind, [0, 0, 0])
        acc[0] += s.expected
        acc[1] += s.emitted
        acc[2] += s.matched
    return tuple(KindScore(k, *v) for k, v in sorted(merged.items()))


# ---- loading & validation -------------------------------------------------


def _require(spec: dict[str, Any], field: str, where: Path) -> Any:
    if field not in spec:
        raise CorpusError(f"{where}: missing required field {field!r}")
    return spec[field]


def _node_key(entry: dict[str, Any], where: Path) -> tuple[str, ...]:
    kind = str(entry.get("kind", ""))
    if kind not in _NODE_KINDS:
        raise CorpusError(f"{where}: {kind!r} is not a NodeKind (expected one of {sorted(_NODE_KINDS)})")
    if not entry.get("id"):
        raise CorpusError(f"{where}: a node entry has no id")
    return (str(entry["id"]), kind)


def _edge_key(entry: dict[str, Any], where: Path) -> tuple[str, ...]:
    kind = str(entry.get("kind", ""))
    if kind not in _EDGE_KINDS:
        raise CorpusError(f"{where}: {kind!r} is not an EdgeKind (expected one of {sorted(_EDGE_KINDS)})")
    if not entry.get("src") or not entry.get("dst"):
        raise CorpusError(f"{where}: an edge entry is missing src or dst")
    return (str(entry["src"]), str(entry["dst"]), kind)


def _load_case(case_dir: Path) -> tuple[dict[str, Any], Path]:
    path = case_dir / CASE_FILE
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"{case_dir}: no {CASE_FILE}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{path}: invalid JSON — {exc}") from exc
    if not isinstance(spec, dict):
        raise CorpusError(f"{path}: expected a JSON object")

    for field in ("language", "case", "root", "nodes", "edges"):
        _require(spec, field, path)

    root = (case_dir / str(spec["root"])).resolve()
    if not root.is_dir():
        raise CorpusError(f"{path}: root {spec['root']!r} is not a directory")

    expected_edges = {_edge_key(e, path) for e in spec["edges"]}

    # known_gaps annotates a labelled edge; it must never introduce or remove one, or the
    # annotation would silently move the score it exists to explain.
    for gap in spec.get("known_gaps", []):
        key = _edge_key(gap.get("edge", {}), path)
        if key not in expected_edges:
            raise CorpusError(f"{path}: known_gaps names an edge absent from 'edges': {key}")

    # A declared false positive is by definition not a true fact, so it must NOT be labelled.
    for fp in spec.get("false_positives", []):
        key = _edge_key(fp.get("edge", {}), path)
        if key in expected_edges:
            raise CorpusError(f"{path}: false_positives names an edge that is also in 'edges': {key}")

    return spec, root


# ---- scoring --------------------------------------------------------------


def _score_kinds(
    expected: set[tuple[str, ...]], emitted: set[tuple[str, ...]], kinds: set[str]
) -> tuple[KindScore, ...]:
    scores = []
    for kind in sorted(kinds):
        exp = {t for t in expected if t[-1] == kind}
        got = {t for t in emitted if t[-1] == kind}
        scores.append(KindScore(kind, len(exp), len(got), len(exp & got)))
    return tuple(scores)


def _describe(items: set[tuple[str, ...]]) -> tuple[str, ...]:
    out = []
    for t in sorted(items):
        out.append(f"{t[0]} -{t[2]}-> {t[1]}" if len(t) == 3 else f"{t[0]} ({t[1]})")
    return tuple(out)


def score_case(case_dir: Path, *, sql_dialect: str | None = None) -> CaseReport:
    """Score one corpus case against a fresh extraction of its fixture."""
    spec, root = _load_case(case_dir)
    batch = RepoCodeExtractor(sql_dialect=sql_dialect).extract(root)
    path = case_dir / CASE_FILE

    # Nodes: external ones are placeholders for things outside the tree, not claims.
    emitted_nodes: set[tuple[str, ...]] = {(n.id, n.kind.value) for n in batch.nodes if not n.external}
    expected_nodes: set[tuple[str, ...]] = {_node_key(n, path) for n in spec["nodes"]}

    # Edges: every emitted edge counts, including edges to external targets.
    emitted_edges: set[tuple[str, ...]] = {(e.src, e.dst, e.kind.value) for e in batch.edges}
    expected_edges: set[tuple[str, ...]] = {_edge_key(e, path) for e in spec["edges"]}

    node_kinds = {t[1] for t in expected_nodes | emitted_nodes}
    edge_kinds = {t[2] for t in expected_edges | emitted_edges}

    missing = (expected_nodes - emitted_nodes) | (expected_edges - emitted_edges)
    unlabelled = (emitted_nodes - expected_nodes) | (emitted_edges - expected_edges)

    # Provenance is compared only where a label opted in with an "at". No expected fact
    # carries one today, so this reports 0 checked rather than a silent 1.0.
    by_id = {n.id: n for n in batch.nodes}
    checked = 0
    drift = []
    for entry in spec["nodes"]:
        at = entry.get("at")
        if not at:
            continue
        checked += 1
        node = by_id.get(str(entry["id"]))
        actual = str(node.provenance) if node is not None and node.provenance else "—"
        if actual != at:
            drift.append(f"{entry['id']}: labelled {at}, emitted {actual}")

    return CaseReport(
        language=str(spec["language"]),
        case=str(spec["case"]),
        nodes=_score_kinds(expected_nodes, emitted_nodes, node_kinds),
        edges=_score_kinds(expected_edges, emitted_edges, edge_kinds),
        missing=_describe(missing)[:_MAX_EXAMPLES],
        unlabelled=_describe(unlabelled)[:_MAX_EXAMPLES],
        known_gaps=len(spec.get("known_gaps", [])),
        declared_false_positives=len(spec.get("false_positives", [])),
        provenance_checked=checked,
        provenance_drift=tuple(drift),
    )


def score_corpus(
    corpus_root: Path | str, *, language: str | None = None, sql_dialect: str | None = None
) -> AccuracyReport:
    """Score every case under ``corpus_root``, optionally filtered to one language.

    A case is any directory containing an ``expected.json``. Order is deterministic.
    """
    root = Path(corpus_root)
    if not root.is_dir():
        raise CorpusError(f"{root}: not a directory")

    case_dirs = sorted(p.parent for p in root.rglob(CASE_FILE))
    if not case_dirs:
        raise CorpusError(f"{root}: no {CASE_FILE} found under this path")

    reports = []
    for case_dir in case_dirs:
        report = score_case(case_dir, sql_dialect=sql_dialect)
        if language is None or report.language == language:
            reports.append(report)
    return AccuracyReport(tuple(reports))


__all__ = [
    "CASE_FILE",
    "AccuracyReport",
    "CaseReport",
    "CorpusError",
    "KindScore",
    "score_case",
    "score_corpus",
]
