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
from fractions import Fraction
from pathlib import Path
from typing import Any

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.verify import ParityCount, source_parity_counts

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


@dataclass(frozen=True)
class ParityReport:
    """Declared-vs-emitted counts for routes and tables, per file.

    Deliberately **not** a single ratio. This repo's routes score 68 declared against 70
    emitted — a naive ratio reads 1.03, which is not a recall figure and not a bug: a router
    mounted twice yields two ``Endpoint`` nodes from one decorator. Shortfall and surplus are
    different phenomena and are reported apart, because averaging them hides both.
    """

    counts: tuple[ParityCount, ...]

    @property
    def declared(self) -> int:
        return sum(c.declared for c in self.counts)

    @property
    def in_graph(self) -> int:
        return sum(c.in_graph for c in self.counts)

    @property
    def shortfall(self) -> int:
        """Constructs the source declares and the graph does not hold. The number that matters."""
        return sum(c.shortfall for c in self.counts)

    @property
    def surplus(self) -> int:
        """Nodes beyond what is declared — expected where a router is mounted more than once."""
        return sum(max(0, c.in_graph - c.declared) for c in self.counts)

    @property
    def short_files(self) -> tuple[ParityCount, ...]:
        return tuple(c for c in self.counts if c.shortfall)


def score_parity(repo: Path | str, *, sql_dialect: str | None = None) -> ParityReport:
    """Per-construct parity for ``repo`` — needs no corpus, no test suite, no execution.

    The third oracle. ``corpus`` needs hand-labelled fixtures and ``runtime`` needs a test
    suite to run; this needs only the source, so it works on a repository that has neither.
    """
    root = Path(repo)
    if not root.is_dir():
        raise CorpusError(f"{root}: not a directory")
    batch = RepoCodeExtractor(sql_dialect=sql_dialect).extract(root)
    return ParityReport(tuple(source_parity_counts(batch, root)))


# ---- the scoreboard --------------------------------------------------------

SCOREBOARD_VERSION = 1

# The baseline lives *inside the package* so it ships in the wheel. `pyproject.toml` builds
# `src/orchestrator` only, so a copy at the repo root would be invisible to a pip-installed
# Spine — and the build document, which quotes this number, runs there. The corpus fixtures
# stay outside: they are needed to regenerate the baseline, never to read it.
BASELINE = Path(__file__).with_name("scoreboard.json")

# What each metric is gated on, and it is recorded in the file so the artefact explains its
# own contract rather than leaving a reader to infer it from CI behaviour.
#
#   strict  — any drop fails. Only safe for metrics measured against COMMITTED FIXTURES,
#             which repo churn cannot move.
#   ratchet — an increase fails. For a metric that rises only when the graph falls behind.
#   false   — recorded, never fails.
#
# Invention is deliberately ungated. It is measured against the repository itself, so it
# moves whenever anyone writes ordinary code: adding `def handler(cb): return cb()` moved it
# from 496 to 497 and shifted the rate. A metric measured against a moving population cannot
# be gated on equality, and a tolerance band would be an arbitrary number that eventually
# fires on something legitimate and gets widened until it means nothing.
#
# The cost is stated rather than hidden: nothing here would catch a front-end change that
# adds thousands of phantom edges. Corpus precision catches it only if the corpus happens to
# contain that shape.
GATES = {"corpus": "strict", "parity": "ratchet", "invention": False, "runtime": False}


@dataclass(frozen=True)
class Regression:
    """A gated metric that moved the wrong way."""

    metric: str
    detail: str
    was: str
    now: str

    def __str__(self) -> str:
        return f"{self.metric}: {self.detail} — was {self.was}, now {self.now}"


def _ratio(matched: int, total: int) -> Fraction | None:
    """Exact, from the integer counts. Floats are for display only (§9)."""
    return Fraction(matched, total) if total else None


def _score_entry(s: KindScore) -> dict[str, int]:
    return {"expected": s.expected, "emitted": s.emitted, "matched": s.matched}


def build_scoreboard(
    corpus_root: Path | str = "corpus", repo: Path | str = ".", *, runtime: bool = False
) -> dict[str, Any]:
    """The committed baseline: every oracle's numbers, and whether each one is gated.

    Deterministic — no timestamps, no paths, no ordering that depends on the filesystem — so
    the same tree produces a byte-identical file.

    ``runtime`` is opt-in because the runtime oracle *executes the repository's test suite*,
    and a command CI runs by default must not do that.
    """
    from orchestrator.pkg.invention import score_invention

    corpus = score_corpus(corpus_root)
    parity = score_parity(repo)
    invention = score_invention(repo)

    languages: dict[str, Any] = {}
    for lang, groups in corpus.totals().items():
        languages[lang] = {
            group: {s.kind: _score_entry(s) for s in scores} for group, scores in groups.items()
        }

    board: dict[str, Any] = {
        "version": SCOREBOARD_VERSION,
        "metrics": {
            "corpus": {"gated": GATES["corpus"], "languages": languages},
            "parity": {
                "gated": GATES["parity"],
                "shortfall": parity.shortfall,
                "surplus": parity.surplus,
                "declared": parity.declared,
                "in_graph": parity.in_graph,
            },
            "invention": {
                "gated": GATES["invention"],
                "count": len(invention.invented),
                "total_calls": invention.total_calls,
                "note": "moves with ordinary commits — recorded as a trend, never gated",
            },
        },
    }
    if runtime:
        from orchestrator.pkg.runtime_oracle import score_runtime

        report = score_runtime(repo)
        board["metrics"]["runtime"] = {
            "gated": GATES["runtime"],
            "observed": report.observed,
            "matched": report.matched,
            "note": "non-deterministic: moved 0.61 -> 0.70 purely because the suite grew",
        }
    return board


def measured_recall(language: str, kind: str = "CALLS", *, group: str = "edges") -> float | None:
    """The committed corpus recall for one language and kind, or ``None`` if unmeasured.

    ``None`` is not zero. Six of the eight front-ends have no corpus, and a language nobody
    measured has not scored badly — it has not been scored. Callers must render the difference.

    This is deliberately the **corpus** number, not the runtime one. Corpus recall is measured
    against committed fixtures, so it is a property of this extractor version and travels with
    it. Runtime recall describes one repository's test suite, is non-deterministic, and has no
    place in anything labelled deterministic.
    """
    try:
        board = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = (
        board.get("metrics", {})
        .get("corpus", {})
        .get("languages", {})
        .get(language, {})
        .get(group, {})
        .get(kind)
    )
    if not entry or not entry.get("expected"):
        return None
    return int(entry["matched"]) / int(entry["expected"])


def compare_scoreboard(baseline: dict[str, Any], current: dict[str, Any]) -> list[Regression]:
    """Gated metrics that moved the wrong way. Empty means the build passes."""
    out: list[Regression] = []

    base_langs = baseline.get("metrics", {}).get("corpus", {}).get("languages", {})
    cur_langs = current.get("metrics", {}).get("corpus", {}).get("languages", {})
    for lang, groups in base_langs.items():
        for group, kinds in groups.items():
            for kind, was in kinds.items():
                now = cur_langs.get(lang, {}).get(group, {}).get(kind)
                if now is None:
                    # The whole kind vanished. Not "unchanged", and not zero — it is a
                    # population that used to exist and does not (§9).
                    out.append(
                        Regression("corpus", f"{lang}/{group}/{kind} disappeared", "present", "absent")
                    )
                    continue
                for label, total_key in (("precision", "emitted"), ("recall", "expected")):
                    before = _ratio(was["matched"], was[total_key])
                    after = _ratio(now["matched"], now[total_key])
                    if before is None or after is None:
                        continue  # undefined is not a drop from undefined
                    if after < before:
                        out.append(
                            Regression(
                                "corpus",
                                f"{lang}/{group}/{kind} {label}",
                                f"{float(before):.4f}",
                                f"{float(after):.4f}",
                            )
                        )

    was_short = baseline.get("metrics", {}).get("parity", {}).get("shortfall")
    now_short = current.get("metrics", {}).get("parity", {}).get("shortfall")
    if was_short is not None and now_short is not None and now_short > was_short:
        out.append(Regression("parity", "shortfall increased", str(was_short), str(now_short)))

    return out


def scoreboard_improvements(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Gated metrics that moved the *right* way — the baseline is stale and should be rewritten."""
    out: list[str] = []
    base_langs = baseline.get("metrics", {}).get("corpus", {}).get("languages", {})
    cur_langs = current.get("metrics", {}).get("corpus", {}).get("languages", {})
    for lang, groups in base_langs.items():
        for group, kinds in groups.items():
            for kind, was in kinds.items():
                now = cur_langs.get(lang, {}).get(group, {}).get(kind)
                if now is None:
                    continue
                for label, total_key in (("precision", "emitted"), ("recall", "expected")):
                    before, after = (
                        _ratio(was["matched"], was[total_key]),
                        _ratio(now["matched"], now[total_key]),
                    )
                    if before is not None and after is not None and after > before:
                        out.append(
                            f"corpus {lang}/{group}/{kind} {label}: {float(before):.4f} -> {float(after):.4f}"
                        )
    b, c = baseline.get("metrics", {}).get("parity", {}), current.get("metrics", {}).get("parity", {})
    if b.get("shortfall") is not None and c.get("shortfall") is not None and c["shortfall"] < b["shortfall"]:
        out.append(f"parity shortfall: {b['shortfall']} -> {c['shortfall']}")
    return out


__all__ = [
    "CASE_FILE",
    "BASELINE",
    "GATES",
    "SCOREBOARD_VERSION",
    "Regression",
    "build_scoreboard",
    "compare_scoreboard",
    "measured_recall",
    "scoreboard_improvements",
    "AccuracyReport",
    "CaseReport",
    "CorpusError",
    "KindScore",
    "ParityReport",
    "score_case",
    "score_parity",
    "score_corpus",
]
