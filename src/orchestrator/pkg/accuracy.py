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

from orchestrator.pkg.extractor import RepoCodeExtractor, default_extractors
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
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
    """Every case, plus the per-language totals.

    ``skipped`` names cases whose language front-end is not installed. They are **not** scored
    zero: an optional extra being absent is not a regression, and conflating the two is how a
    green build turns red for a reason nobody changed.
    """

    cases: tuple[CaseReport, ...]
    skipped: tuple[str, ...] = ()

    @property
    def skipped_languages(self) -> tuple[str, ...]:
        return tuple(sorted({c.split("/")[0] for c in self.skipped}))

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

    for field in ("language", "case", "nodes", "edges"):
        _require(spec, field, path)
    if "root" not in spec and "roots" not in spec:
        raise CorpusError(f"{path}: expected 'root' (one fixture) or 'roots' (a multi-repo case)")

    # A cross-repo case names several fixtures, keyed the way `.spine/repos.yaml` keys them —
    # so the ids a label must use are the scoped ids the product actually emits, not a
    # corpus-only spelling that could agree with nothing.
    if "roots" in spec:
        roots = spec["roots"]
        if not isinstance(roots, dict) or not roots:
            raise CorpusError(f"{path}: 'roots' must be a non-empty mapping of repo key to path")
        for key, rel in roots.items():
            resolved = (case_dir / str(rel)).resolve()
            if not resolved.is_dir():
                raise CorpusError(f"{path}: root {rel!r} for repo {key!r} is not a directory")
        root = case_dir
    else:
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


def _case_batch(spec: dict[str, Any], case_dir: Path, root: Path, sql_dialect: str | None) -> FactBatch:
    """One fixture, or several merged and joined the way the product would merge them.

    A cross-repo case must be scored through the *real* multi-repo path — scoping, merging and
    the declared joins — or it would measure a corpus-only assembly that nothing ships. The
    joins come from the case's own `joins:` block, which is the same shape a repo declares in
    `.spine/repos.yaml`.
    """
    if "roots" not in spec:
        return RepoCodeExtractor(sql_dialect=sql_dialect).extract(root)

    from orchestrator.pkg.join_link import link_joins
    from orchestrator.pkg.repos import joins_from_list
    from orchestrator.pkg.scoping import merge_repos

    batches: dict[str, FactBatch] = {}
    unresolved: dict[str, list[Any]] = {}
    for key, rel in sorted(spec["roots"].items()):
        extractor = RepoCodeExtractor(sql_dialect=sql_dialect)
        batches[key] = extractor.extract((case_dir / str(rel)).resolve())
        unresolved[key] = list(extractor.unresolved_calls)
    merged = merge_repos(batches)
    joins = joins_from_list(spec.get("joins", []), where=case_dir / CASE_FILE)
    return link_joins(merged, joins, unresolved)[0]


def score_case(case_dir: Path, *, sql_dialect: str | None = None) -> CaseReport:
    """Score one corpus case against a fresh extraction of its fixture."""
    spec, root = _load_case(case_dir)
    batch = _case_batch(spec, case_dir, root, sql_dialect)
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

    # A front-end whose optional extra is not installed emits nothing, which would score its
    # cases at 0.00 and read as a total regression. `default_extractors()` returns only the
    # front-ends that actually imported, so an unavailable language is skipped and said so.
    available = {ex.language for ex in default_extractors(sql_dialect=sql_dialect)}

    reports = []
    skipped: list[str] = []
    for case_dir in case_dirs:
        spec, _ = _load_case(case_dir)
        case_language = str(spec["language"])
        if language is not None and case_language != language:
            continue
        # `language` names what a case *measures*; `requires` names the front-ends it needs
        # installed. They are the same for every single-language case and differ for a
        # cross-repo one, whose fixtures may be Python while what it measures is the join.
        # Conflating them either skipped the case forever or filed its scores under a language
        # whose front-end it is not testing.
        needs = {str(x) for x in spec.get("requires", [case_language])}
        if missing := sorted(needs - available):
            skipped.append(f"{case_language}/{spec['case']} (needs {', '.join(missing)})")
            continue
        reports.append(score_case(case_dir, sql_dialect=sql_dialect))
    return AccuracyReport(tuple(reports), tuple(skipped))


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


@dataclass(frozen=True)
class DriftReport:
    """Documentation drift for a repository: prose the graph cannot support.

    ``mentions`` is the denominator — every code-intent claim the docs make, bound or not.
    **It was ``docs`` (sections) until 2026-08-31, and that was wrong twice over:** a section
    count does not move when prose inside an existing section is edited, which is what a
    documentation change usually is, so any added claim raised the "rate" with nothing able to
    dilute it; and the resulting figure was not bounded by 1, so it was not a rate at all.

    ``docs`` is still carried, because a zero has to be readable. A repository with no
    documentation scores zero drift and so does one whose documentation is perfectly accurate —
    not the same result, and the same reason ``corpus`` records ``skipped_languages`` and
    ``invention`` records a per-language ``status``.
    """

    count: int
    docs: int
    mentions: int = 0

    @property
    def measured(self) -> bool:
        """False when there was nothing to measure — no docs, so no claim to check."""
        return self.docs > 0

    @property
    def rate(self) -> Fraction | None:
        """Unbound claims over claims made. Exact; floats are for display only."""
        return Fraction(self.count, self.mentions) if self.mentions else None


def score_drift(repo: Path | str, *, sql_dialect: str | None = None) -> DriftReport:
    """Symbol-shaped doc claims the graph cannot support, and how many docs were read.

    **Symbol-shaped only**, via :func:`doc_link.symbolish_drift` — the same filter the
    ``state`` surfaces and the review path already apply. Gating a noisier population than
    the one reported would be a gate nobody trusts: a reader who sees 12 in the report and
    41 in the gate cannot act on either.

    Deterministic and no-LLM, like everything else on the scoreboard.
    """
    from orchestrator.pkg.doc_link import symbolish_drift
    from orchestrator.pkg.doc_source import read_doc_pages
    from orchestrator.pkg.docs import DocReconciler

    root = Path(repo)
    if not root.is_dir():
        raise CorpusError(f"{root}: not a directory")
    # Sections, not files — `read_doc_pages` splits on headings. Kept for the zero case.
    pages = read_doc_pages(root)
    if not pages:
        return DriftReport(count=0, docs=0, mentions=0)
    batch = RepoCodeExtractor(sql_dialect=sql_dialect).extract(root)
    # Reconcile once and keep both halves. `doc_drift` throws the bindings away, and the
    # bindings are the denominator: every claim the docs make, which is what an added
    # paragraph moves.
    bindings, drift = DocReconciler(batch, repo_root=root).reconcile(pages)
    symbolish = [f for f in drift if symbolish_drift(f.mention)]
    return DriftReport(count=len(symbolish), docs=len(pages), mentions=len(bindings))


def score_comprehension(repo: Path | str, *, sql_dialect: str | None = None) -> Any:
    """Provenance validity for ``repo`` — do facts open to a line that names them?

    The fifth oracle, and the first that asks about the *answer* rather than the shape. Lives
    in ``evals`` (G6 owns that package); this is the seam that puts it on the one scoreboard
    rather than starting a second.

    **Measured on the repository under test, offline.** The pinned five-repository corpus is
    reached by ``evals.corpus_fetch`` and run explicitly, for the same reason ``runtime`` is
    opt-in: a gate CI runs by default must not depend on the network, and a metric that fails
    because a clone timed out teaches people to ignore it.
    """
    from orchestrator.evals.comprehension import score_provenance

    root = Path(repo)
    if not root.is_dir():
        raise CorpusError(f"{root}: not a directory")
    batch = RepoCodeExtractor(sql_dialect=sql_dialect).extract(root)
    return score_provenance(batch, root)


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
# Invention is `strict` — the only metric here gated on an absolute value rather than on the
# baseline, and the reason is that it is the only one with a *correct* value. A `CALLS` edge to
# a name the calling function bound itself is a defect, not a score: there is no repository and
# no commit where one is acceptable, so there is nothing for a ratchet to ratchet.
#
# It was ungated until 2026-08-24, for a good reason that no longer holds. The *rate* does move
# with ordinary code — adding `def handler(cb): return cb()` took it from 496 to 497 — and a
# tolerance band would be an arbitrary number that eventually fires on something legitimate and
# gets widened until it means nothing. But the rate was never what needed gating. The count was,
# and every front-end that can express the defect now refuses it: Python (3.18.0), C, and
# TypeScript/Go/C++/C# with the port that came with this gate.
#
# Strict here means **zero per language, not zero-versus-baseline**. Comparing to a stored
# number would let a non-zero baseline become the thing everyone agrees to live with, which is
# how a defect count turns into a metric.
#
# What it does NOT catch is stated rather than hidden: the oracle detects shadowing, so a
# front-end that fabricates some other way passes this gate. Corpus precision catches that only
# if the corpus happens to carry the shape — which is why `corpus/*/shadowed_calls` exists, and
# why a new invention class needs a fixture as well as a detector.
# Drift is RECORDED AND NEVER GATED — `False`, like `runtime`. It shipped as a `ratchet` on
# 2026-08-31 and the gate was withdrawn the same day, one pull request later, because that pull
# request was a documentation change and the gate failed it. The argument for the gate said "a
# gate that fires on the work it is meant to protect gets switched off, and then it protects
# nothing." It then did exactly that, which is the clearest evidence available that it was not
# ready.
#
# Two things were wrong, and the second is why fixing the first is not enough:
#
#   1. The denominator was `docs` (sections). A section count does not move when prose inside an
#      existing section is edited — which is what a documentation change usually is — so any
#      added claim raised the figure with nothing able to dilute it. It was also not bounded by
#      1, so it was not a rate. Fixed: the denominator is now `mentions`, every code-intent
#      claim the docs make. 893/8,885 = 0.1005 here, against the old 893/1,532 = 0.5829.
#
#   2. **About a tenth of the population cannot bind by construction.** The four claims that
#      failed that pull request were `impact_source` (a constructor parameter), `not_measured`
#      (a string literal), `_TEMPERATURE_REFUSED` (a module-level constant) and
#      `llm.temperature_skipped` (a log event name). The graph has no node kind for any of them.
#      So a design record naming implementation detail — which is what design records do — sits
#      at or above the average and trips a strict comparison. With the corrected denominator
#      that pull request *still* failed, 0.100506 -> 0.100517.
#
# A tolerance band is not the answer, for the reason stated below for invention: it is an
# arbitrary number that eventually fires on something legitimate and gets widened until it means
# nothing.
#
# **What would make this gate material:** excluding mentions the graph cannot model, so the
# population is claims that *could* bind. Until then the number is an upper bound on drift, and
# an upper bound with a tenth of it structural noise is not something to fail a build over. The
# measurement stays — `state` reports it, `--oracle drift` reports it, `--check` trends it.
#
# `docs` is recorded beside `count` because zero drift on zero documents is not a clean result.
# Comprehension is `ratchet` and, unlike drift, it fails when the number goes DOWN: it is an
# accuracy, not a defect count. It reads 1.0000 on 10,788 anchored facts here, which is the
# claim worth protecting — every Function, Type and Field fact opens to a line naming it. The
# localization half stays `not_measured` until a gold set exists (G6 D1/D2); a zero there would
# be indistinguishable from never having measured, which is the failure §9 describes.
GATES = {
    "corpus": "strict",
    "parity": "ratchet",
    "invention": "strict",
    "drift": False,
    "comprehension": "ratchet",
    "runtime": False,
}


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


def localization_entry(report: Any = None) -> dict[str, Any]:
    """The `localization` block: a measurement when one was taken, a reason when not.

    ``report`` is a :class:`evals.localization.LocalizationReport` from a run against the pinned
    corpus. Without one this is *not measured*, and the reason tells "no gold set yet" apart from
    "not scored on this run" — an offline scoreboard cannot compute it, because the corpus has to
    be on disk and a gate CI runs by default must not depend on the network.
    """
    from orchestrator.evals.labels import gold_digest, load_labels

    try:
        gold = load_labels()
    except Exception:  # noqa: BLE001 — a malformed gold set must not break the scoreboard
        return {"status": "not_measured", "reason": "gold set could not be read"}

    if report is not None and report.measured:
        return {
            "status": "measured",
            "labelled": len(report.results),
            # What was scored. `compare_scoreboard` gates only when this matches: localization is
            # a ratio over a fixed denominator, so adding a label the tool gets wrong lowers every
            # rate, and a gate blind to that would fail a pull request for GROWING the corpus —
            # exactly how the doc-drift gate died a day earlier.
            "gold_digest": gold_digest(gold),
            "top_k": {str(k): report.hits_at(k) for k in report.ks},
            # Apart from a bad ranking: "returned nothing" is a different failure, and keeping
            # them separate is what made a whole-corpus plumbing failure legible when it scored
            # 0.00 at every k.
            "empty_results": int(report.as_dict()["empty_results"]),
        }
    if not gold.labels:
        return {"status": "not_measured", "reason": "no gold set — G6 D1"}
    return {
        "status": "not_measured",
        "reason": f"gold set of {len(gold.labels)} — run `pkg accuracy --scoreboard --pinned-corpus`",
    }


def build_scoreboard(
    corpus_root: Path | str = "corpus",
    repo: Path | str = ".",
    *,
    runtime: bool = False,
    localization: Any = None,
) -> dict[str, Any]:
    """The committed baseline: every oracle's numbers, and whether each one is gated.

    Deterministic — no timestamps, no paths, no ordering that depends on the filesystem — so
    the same tree produces a byte-identical file.

    ``runtime`` is opt-in because the runtime oracle *executes the repository's test suite*,
    and a command CI runs by default must not do that.
    """
    from orchestrator.pkg.invention import score_invention

    # A corpus is Spine's own ground truth and most repositories have none. That is not an
    # error: parity and invention need only the source, so a scoreboard is still worth having
    # — it just has no corpus section, and therefore nothing gated strictly.
    languages: dict[str, Any] = {}
    try:
        corpus = score_corpus(corpus_root)
    except CorpusError:
        corpus = None
    if corpus is not None:
        for lang, groups in corpus.totals().items():
            languages[lang] = {
                group: {s.kind: _score_entry(s) for s in scores} for group, scores in groups.items()
            }

    parity = score_parity(repo)
    invention = score_invention(repo)
    drift = score_drift(repo)
    provenance = score_comprehension(repo)

    board: dict[str, Any] = {
        "version": SCOREBOARD_VERSION,
        "metrics": {
            "corpus": {
                "gated": GATES["corpus"],
                "languages": languages,
                # Recorded so `--check` can tell "this language was not measured here" from
                # "this language collapsed to zero". Without it, running the gate on a machine
                # without an optional extra reports a catastrophic regression that is really an
                # absent dependency.
                "skipped_languages": list(corpus.skipped_languages) if corpus is not None else [],
            },
            "parity": {
                "gated": GATES["parity"],
                "shortfall": parity.shortfall,
                "surplus": parity.surplus,
                "declared": parity.declared,
                "in_graph": parity.in_graph,
            },
            "comprehension": {
                "gated": GATES["comprehension"],
                "provenance": {
                    "anchored": provenance.anchored,
                    "resolved": provenance.resolved,
                    # Kinds named by construction rather than by a token at the site — Module,
                    # Endpoint, Entity. Recorded so "not scored" cannot be read as "passed".
                    "excluded": provenance.excluded,
                    "unreadable": provenance.unreadable,
                },
                # Named, not omitted. An absent key reads as an oversight; this reads as a
                # measurement nobody has taken *here*, which is the truth. The two reasons are
                # different and are told apart: an empty gold set is work not yet done, while a
                # populated one simply cannot be scored offline — localization needs the pinned
                # corpus on disk, and a gate CI runs by default must not depend on the network.
                "localization": localization_entry(localization),
                "note": "provenance measured on this repo, offline; the pinned corpus is run explicitly",
            },
            "drift": {
                "gated": GATES["drift"],
                "count": drift.count,
                # `mentions` is the denominator; `docs` stays so a zero is legible as "nothing
                # to measure" rather than "nothing wrong".
                "mentions": drift.mentions,
                "docs": drift.docs,
                "note": (
                    "recorded, never gated — an upper bound: a tenth of the population "
                    "cannot bind by construction. See GATES"
                ),
            },
            "invention": {
                "gated": GATES["invention"],
                "count": len(invention.invented),
                "total_calls": invention.total_calls,
                # Per front-end, with each one's standing. A language absent from this map
                # emitted no CALLS edges here; a language present with `status` other than
                # `measured` was NOT examined, and its zero says nothing about its health.
                "languages": {
                    entry.language: {
                        "status": entry.status,
                        "invented": len(entry.invented),
                        "total_calls": entry.total_calls,
                        "examined": entry.examined,
                        "shadowable": entry.shadowable,
                        "unexamined": entry.unexamined,
                    }
                    for entry in invention.by_language
                },
                "note": "gated at zero per language, not against this baseline — see GATES",
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
    cur_corpus = current.get("metrics", {}).get("corpus", {})
    cur_langs = cur_corpus.get("languages", {})
    unmeasured = set(cur_corpus.get("skipped_languages", []))
    for lang, groups in base_langs.items():
        if lang in unmeasured:
            continue  # not measured here, so nothing to compare — see build_scoreboard
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

    # Invention: zero per language, measured against nothing. `baseline` is unused on purpose
    # — see GATES. A language whose status is not `measured` is skipped, because its 0 means
    # "not examined" and gating on it would report health nobody checked.
    if GATES["invention"] == "strict":
        for lang, entry in sorted(
            current.get("metrics", {}).get("invention", {}).get("languages", {}).items()
        ):
            if entry.get("status") != "measured":
                continue
            count = int(entry.get("invented", 0))
            if count:
                out.append(Regression("invention", f"{lang}: fabricated CALLS edge(s)", "0", str(count)))

    # Comprehension: an accuracy, so a DROP fails — the opposite direction to drift below.
    base_prov = baseline.get("metrics", {}).get("comprehension", {}).get("provenance", {})
    cur_prov = current.get("metrics", {}).get("comprehension", {}).get("provenance", {})
    before_prov = _ratio(int(base_prov.get("resolved", 0)), int(base_prov.get("anchored", 0)))
    after_prov = _ratio(int(cur_prov.get("resolved", 0)), int(cur_prov.get("anchored", 0)))
    if before_prov is not None and after_prov is not None and after_prov < before_prov:
        out.append(
            Regression(
                "comprehension",
                f"provenance validity fell ({cur_prov.get('resolved')} of {cur_prov.get('anchored')} facts "
                "open to a line naming them)",
                f"{float(before_prov):.4f}",
                f"{float(after_prov):.4f}",
            )
        )

    # Localization: an accuracy, so a DROP fails — and gated ONLY when the gold set is
    # unchanged. Comparing across different label sets would fail a pull request for adding a
    # label the tool gets wrong, which is corpus growth, not regression — the exact mistake that
    # killed the doc-drift gate a day earlier. Both sides must also have been *measured*: an
    # offline run has no number, and reading its absence as zero would report a catastrophe
    # every time CI ran without the network.
    base_loc = baseline.get("metrics", {}).get("comprehension", {}).get("localization", {})
    cur_loc = current.get("metrics", {}).get("comprehension", {}).get("localization", {})
    if (
        base_loc.get("status") == "measured"
        and cur_loc.get("status") == "measured"
        and base_loc.get("gold_digest") == cur_loc.get("gold_digest")
    ):
        for k in ("1", "10"):
            was_hits = base_loc.get("top_k", {}).get(k)
            now_hits = cur_loc.get("top_k", {}).get(k)
            if was_hits is None or now_hits is None or now_hits >= was_hits:
                continue
            n = cur_loc.get("labelled", 0)
            out.append(
                Regression(
                    "comprehension",
                    f"top-{k} localization fell on an unchanged gold set of {n}",
                    f"{was_hits}/{n}",
                    f"{now_hits}/{n}",
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
    cur_corpus = current.get("metrics", {}).get("corpus", {})
    cur_langs = cur_corpus.get("languages", {})
    unmeasured = set(cur_corpus.get("skipped_languages", []))
    for lang, groups in base_langs.items():
        if lang in unmeasured:
            continue  # not measured here, so nothing to compare — see build_scoreboard
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
    bp = baseline.get("metrics", {}).get("comprehension", {}).get("provenance", {})
    cp = current.get("metrics", {}).get("comprehension", {}).get("provenance", {})
    before_prov = _ratio(int(bp.get("resolved", 0)), int(bp.get("anchored", 0)))
    after_prov = _ratio(int(cp.get("resolved", 0)), int(cp.get("anchored", 0)))
    if before_prov is not None and after_prov is not None and after_prov > before_prov:
        out.append(f"provenance validity: {float(before_prov):.4f} -> {float(after_prov):.4f}")
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
