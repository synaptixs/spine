"""The build document — one ticket's plan, assembled before any code exists.

A run today spends real money before anyone sees anything, and its first output is
either a PR or a traceback. This module produces the thing that should come first: a
reviewable document assembled from the sources of truth, cheap enough to throw away.

**Twelve sections, fixed titles, fixed order** — see `docs/specs/build-document.md` §3.
The shape is the contract: a reviewer must be able to find section 9 without reading
sections 1–8, and a renderer can only be built against a stable shape.

**Every section says where it came from.** The four labels (§1) are the load-bearing
idea, and a document that mixes a quoted requirement with model inference without
saying which is which is worse than no document — it lends the authority of the first
to the second. A section that mixes takes the weaker label.

**Deterministic.** With a supplied spec there is no LLM anywhere in this path:
``produce_design(llm=None)`` is the heuristic design, and everything else reads the
graph, the tree, or git. Same commit and same spec in, byte-identical document out —
which is what makes the persisted history worth keeping.

Sections 3, 9, 11 and 12 are later phases. They render as headings that say what
would establish them rather than vanishing, because a missing section a reader cannot
see reads as a section with nothing to say.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

# The four provenance labels of docs/specs/build-document.md §1.
STATED = "stated"
DETERMINISTIC = "derived · deterministic"
MODEL = "derived · model"
HUMAN = "human"

# Bounds. Every aggregation caps its output and says what it elided (invariant 7):
# a clipped diagram that implies completeness is worse than a small honest one.
_MAX_IMPORTERS = 6
_MAX_HOTSPOTS = 5
_MAX_MODULES = 6

_ID_UNSAFE = re.compile(r"[^0-9A-Za-z_]")
_DERIVED_AT = re.compile(r"\*\*Derived at:\*\* `([^`]+)`")


# ---- provenance ------------------------------------------------------------


def _label(label: str, source: str) -> str:
    """The italic line that sits directly beneath a section heading.

    Trailing newline on purpose: without the blank line markdown folds the label into
    the first paragraph of the section, and a provenance label that reads as part of
    the content is the opposite of what it is for.
    """
    return f"*{label} — {source}*\n"


def _pending(what: str) -> str:
    """A section that has not been established, saying so rather than vanishing."""
    return _label("not established", what)


def derived_at(root: Path | str = ".") -> str:
    """The commit every deterministic section was computed from.

    Not decoration. A plan approved at X and built at Y is a document that *was* true,
    and without the stamp nothing downstream can tell. A dirty tree is marked, because
    a document derived from uncommitted work cannot be reproduced from the commit alone.
    """
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if rev.returncode != 0:
            return "unknown"
        commit = rev.stdout.strip() or "unknown"
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return f"{commit}-dirty" if dirty.returncode == 0 and dirty.stdout.strip() else commit
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---- section 5: the diagram ------------------------------------------------


def _mermaid_blast(bd: dict[str, Any]) -> str:
    """A `flowchart TD` of what imports what changes, and the fan-in inside it.

    Held to the subset `md.js` renders — nodes declared first with quoted labels, then
    bare-id edges, no chaining. Anything outside it falls back to `<pre>` in our own UI
    while still looking fine on GitHub, so a broken diagram is invisible until someone
    opens Spine. Returns "" rather than a guess when there is nothing to draw: no
    picture beats a wrong picture.
    """
    modules = (bd.get("modules") or [])[:_MAX_MODULES]
    if not modules:
        return ""

    ids: dict[str, str] = {}

    def _id(key: str) -> str:
        if key not in ids:
            ids[key] = f"n{len(ids)}"
        return ids[key]

    def _safe(text: str) -> str:
        # Only what would break the label: a quote closes it and a bracket closes the
        # node. Dots and slashes are fine inside quotes, and stripping them turned
        # `src/orchestrator/cli.py` into "src orchestrator cli py" — a path no reader
        # recognises. Ids are a separate, sanitised namespace (`_id`).
        return str(text).replace('"', "'").replace("[", "(").replace("]", ")").strip() or "?"

    importers: list[str] = []
    changed: list[str] = []
    hotspots: list[str] = []
    edges: list[str] = []

    for mod in modules:
        ref = str(mod.get("ref") or "")
        if not ref:
            continue
        mid = _id(f"m:{ref}")
        changed.append(f'    {mid}["{_safe(ref)}"]')
        for name in (mod.get("importer_names") or [])[:_MAX_IMPORTERS]:
            iid = _id(f"i:{name}")
            line = f'    {iid}["{_safe(name)}"]'
            if line not in importers:
                importers.append(line)
            edges.append(f"  {iid} --> {mid}")
        for spot in (mod.get("hotspots") or [])[:_MAX_HOTSPOTS]:
            name = str(spot.get("name") or "")
            if not name:
                continue
            hid = _id(f"h:{ref}:{name}")
            callers = int(spot.get("callers") or 0)
            hotspots.append(f'    {hid}["{_safe(name)}<br/>{callers} caller(s)"]')
            edges.append(f"  {mid} --> {hid}")

    lines = ["```mermaid", "flowchart TD"]
    if importers:
        lines.append('  subgraph inbound["what imports it"]')
        lines.extend(importers)
        lines.append("  end")
    lines.append('  subgraph target["what this ticket changes"]')
    lines.extend(changed)
    lines.append("  end")
    if hotspots:
        lines.append('  subgraph fanin["fan-in inside it"]')
        lines.extend(hotspots)
        lines.append("  end")
    lines.extend(dict.fromkeys(edges))  # dedupe, keep order
    lines.append("```")
    return "\n".join(lines)


def _is_test_module(name: str) -> bool:
    n = str(name)
    return n.startswith("test") or n.startswith("tests.") or ".test" in n or "_test" in n


def _blast_prose(bd: dict[str, Any]) -> str:
    """The three blocks the template requires, in order: reading, containment, caveat."""
    modules = bd.get("modules") or []
    shown = modules[:_MAX_MODULES]
    total_importers = sum(int(m.get("importers") or 0) for m in modules)
    total_hotspots = sum(len(m.get("hotspots") or []) for m in modules)

    elided = ""
    if len(modules) > len(shown):
        elided = f" Showing {len(shown)} of {len(modules)} module(s)."

    reading = (
        f"**Reading it:** {len(modules)} module(s) change; {total_importers} module(s) import "
        f"them, and {total_hotspots} symbol(s) inside them carry the fan-in.{elided}"
    )

    all_importers = [n for m in modules for n in (m.get("importer_names") or [])]
    if not all_importers:
        containment = (
            "**Containment:** nothing in the graph imports what changes. A change here "
            "cannot propagate outward."
        )
    elif all(_is_test_module(n) for n in all_importers):
        containment = (
            f"**Containment:** the only importers are tests ({', '.join(sorted(set(all_importers))[:8])}). "
            "Nothing in the product depends on what changes."
        )
    else:
        product = sorted({n for n in all_importers if not _is_test_module(n)})
        containment = (
            f"**Containment:** the neighbourhood reaches {len(product)} non-test module(s): "
            f"{', '.join(product[:8])}. A change here is visible to them."
        )

    if not bd.get("call_graph_available"):
        caveat = (
            "**Caveat:** no call graph for this language — this is module-level impact only, "
            "and symbol-level fan-in is omitted rather than zero."
        )
    else:
        caveat = (
            "**Caveat:** method calls through an instance emit no `CALLS` edge (SSPN-48), so "
            "per-method counts under-report. Module-function counts are exact."
        )

    unverified = bd.get("unverified_references") or []
    if unverified:
        caveat += (
            f"\n\n**Unverified references:** {', '.join(str(u) for u in unverified[:8])} — named by "
            "the design and absent from the graph."
        )
    return f"{reading}\n\n{containment}\n\n{caveat}\n"


# ---- section 8: criteria, in three states ----------------------------------


def _criteria_block(spec: dict[str, Any]) -> str:
    """Stated, stated-but-already-met, and proposed — never silently narrowed.

    An already-met criterion stays on the page with the evidence that satisfies it.
    Deleting it is how six criteria became four with no reader able to tell: a run
    would report it met having changed nothing, which is the failure this document
    exists to catch.
    """
    stated = [str(c) for c in (spec.get("acceptance_criteria") or [])]
    proposed = [str(c) for c in (spec.get("proposed_criteria") or [])]
    met = {str(k): str(v) for k, v in (spec.get("met_criteria") or {}).items()}

    rows: list[str] = ["| # | Criterion | State | Satisfied by |", "|---|---|---|---|"]
    n = 0
    for text in stated:
        n += 1
        if text in met:
            rows.append(f"| {n} | {text} | **stated · already met** | {met[text]} |")
        else:
            rows.append(f"| {n} | {text} | stated | — |")
    for text in proposed:
        n += 1
        rows.append(f"| {n} | {text} | proposed *(model)* | — |")

    out = "\n".join(rows) + "\n"

    already = sum(1 for t in stated if t in met)
    if already:
        out += (
            f"\n**{already} of {len(stated)} stated criteria already satisfied by code that "
            "exists.** A run would report them met having changed nothing. The delivery is the "
            f"remaining {len(stated) - already}.\n"
        )
    unmatched = [k for k in met if k not in stated]
    if unmatched:
        out += (
            "\n**Warning:** `met_criteria` names "
            f"{len(unmatched)} criterion/criteria that are not in `acceptance_criteria` — "
            "they are ignored above, and the mismatch is probably a typo: "
            + "; ".join(f"“{u[:60]}…”" for u in unmatched[:3])
            + "\n"
        )
    if not stated:
        out += "\n**No stated criteria.** There is nothing for the acceptance judge to verify.\n"
    return out


# ---- sections 7 and 10: files and the prompt -------------------------------


def _file_rows(paths: list[str], root: Path) -> tuple[list[str], list[str], int]:
    """Split named paths into those that exist and those that do not, with sizes."""
    changed: list[str] = []
    created: list[str] = []
    total = 0
    for raw in paths:
        rel = str(raw).strip()
        if not rel:
            continue
        p = root / rel
        try:
            size = p.stat().st_size if p.is_file() else 0
        except OSError:
            size = 0
        if size or p.is_file():
            total += size
            changed.append(f"| `{rel}` | {size:,} b |")
        else:
            created.append(f"| `{rel}` | named by the design, absent from the tree |")
    return changed, created, total


# ---- the document ----------------------------------------------------------


def render_build_md(
    spec: dict[str, Any],
    *,
    investigation: Any,
    design: dict[str, Any],
    validity: Any,
    root: Path,
    commit: str,
    context_budget: int,
    language: str = "python",
) -> str:
    """Assemble the twelve sections. Pure — no I/O beyond stat-ing the named files."""
    title = str(spec.get("title") or "untitled")
    intent = str(spec.get("intent_id") or "unknown")
    files = [str(f) for f in (design.get("files_to_touch") or [])]
    blast = design.get("blast_radius") or {}
    changed, created, carried = _file_rows(files, root)

    landing = list(getattr(investigation, "landing", []) or [])
    landing_files = {str(getattr(land, "where", "")).split(":", 1)[0] for land in landing}
    agreed = sorted(landing_files & set(files))

    out: list[str] = []
    add = out.append

    add(f"# {intent} — build document\n")
    add(f"**Spec:** `{intent}` · **Derived at:** `{commit}` · **Status:** proposed\n")
    # .value first: str-Enum stringifies as "Verdict.PROCEED", which is a Python repr
    # leaking onto a page a human is meant to read.
    raw_verdict = getattr(validity, "verdict", "")
    add(f"**Validity:** {getattr(raw_verdict, 'value', raw_verdict) or 'unknown'}\n")
    findings = list(getattr(validity, "findings", []) or [])
    if findings:
        add("> " + "\n> ".join(str(getattr(f, "detail", f)) for f in findings) + "\n")
    add(
        "Assembled by `orchestrator sdlc plan`. No code was written and nothing was "
        "spent. Every section carries where it came from.\n"
    )
    add("---\n")

    add("## 1. Requirement")
    add(_label(STATED, "the ticket body, quoted"))
    add(f"**{title}**\n")
    add(str(spec.get("summary") or "_The ticket says nothing beyond its title._") + "\n")

    add("## 2. Intent")
    add(_label(MODEL, "`intake/specs.py` — the spec writer"))
    add(str(spec.get("user_story") or "_No user story on the spec._") + "\n")

    add("## 3. Root cause")
    add(_pending("`orchestrator rca` produces this; wiring it in is Phase 3"))

    add("## 4. PKG — what the graph knows")
    add(_label(DETERMINISTIC, f"`FactStore` @ `{commit}`"))
    if landing:
        add("| symbol | kind | where | callers | module |")
        add("|---|---|---|---|---|")
        for land in landing[:10]:
            add(
                f"| `{getattr(land, 'name', '')}` | {getattr(land, 'kind', '')} | "
                f"`{getattr(land, 'where', '')}` | {getattr(land, 'callers', 0)} | "
                f"`{getattr(land, 'module', '')}` |"
            )
        add("")
    else:
        add("_Nothing in the graph matched this ticket's words._\n")
    areas = list(getattr(investigation, "areas", []) or [])
    if areas:
        add(f"**Areas:** {', '.join(f'`{a}`' for a in areas[:8])}\n")

    # The SSPN-49 finding, made deterministic: lexical retrieval matched the ticket's
    # words to modules literally named that, and never reached the file the ticket
    # states. A brief that names none of the files being changed is noise, and saying
    # so here costs nothing while carrying it silently costs a run.
    if not landing:
        add("**The brief is empty.** Locate the change by hand before building.\n")
    elif agreed:
        add(
            f"**The brief agrees with the design** on {len(agreed)} file(s): "
            + ", ".join(f"`{a}`" for a in agreed)
            + ".\n"
        )
    else:
        add(
            "**The brief names none of the files this ticket will change.** Retrieval is "
            "lexical — it matched the ticket's words, not its work. Treat it as noise here.\n"
        )

    add("## 5. Blast radius")
    add(_label(DETERMINISTIC, f"`sdlc/impact.py` @ `{commit}`"))
    diagram = _mermaid_blast(blast)
    if diagram:
        add(diagram + "\n")
    else:
        add("_Nothing to draw — no module in the graph matched the files being changed._\n")
    add(_blast_prose(blast))

    add("## 6. Design")
    origin = "an LLM" if design.get("llm") else "the deterministic heuristic (no LLM)"
    add(_label(DETERMINISTIC if not design.get("llm") else MODEL, f"`sdlc/design.py` — {origin}"))
    add(str(design.get("approach") or "_No approach._") + "\n")
    risks = [str(r) for r in (design.get("risks") or [])]
    if risks:
        add("*Risks, as the design states them:*\n")
        add("\n".join(f"- {r}" for r in risks) + "\n")
    add(f"**Test strategy:** {design.get('test_strategy') or '—'}\n")

    add("## 7. Files")
    add(_label(DETERMINISTIC, "the paths the spec states, plus the design"))
    if changed:
        add("**Changed**\n")
        add("| file | size |")
        add("|---|---|")
        out.extend(changed)
        add("")
    if created:
        add("**Created**\n")
        add("| file | note |")
        add("|---|---|")
        out.extend(created)
        add("")
    if not changed and not created:
        add("_The design proposes no files. Locate the change before building._\n")

    add("## 8. Acceptance criteria")
    add(_label(f"{STATED} + {MODEL}", "the spec, reconciled against the code"))
    add(_criteria_block(spec))

    add("## 9. Facts the generator needs")
    add(_pending("reading the named source and writing down what must not be duplicated — Phase 3"))

    add("## 10. Codegen prompt")
    add(_label(DETERMINISTIC, "`sdlc/codegen.py` — prompt assembly"))
    add(f"**System:** `_IMPLEMENT_SYSTEM` ({language})\n")
    add("**User payload:** sections 1, 3, 6, 8 and 9 of this document, plus the files below whole.\n")
    pct = (carried / context_budget * 100) if context_budget else 0.0
    add(f"**Context:** {carried:,} b of {context_budget:,} — {pct:.0f}%.\n")
    if carried > context_budget:
        add("**Over budget.** Codegen will excerpt; the model will not see these files whole.\n")

    add("## 11. Token usage & cost")
    add(_pending("the model catalog and this ticket's measured worklog history — Phase 6"))

    add("## 12. Confidence")
    add(_pending("what the plan could and could not establish, scored as two numbers — Phase 6"))

    return "\n".join(out).rstrip() + "\n"


async def build_plan(
    spec: dict[str, Any],
    *,
    root: Path | str = ".",
    language: str = "python",
) -> str:
    """Run the four cheap stages and render the document. No worktree, no codegen.

    The builders are called directly rather than through ``autorun``'s ``_stage_*``
    wrappers: those carry run records, checkpoints, approval parking and Jira worklogs,
    and a plan must touch none of that — the ticket moves when work begins, not when
    someone thinks about it.
    """
    from orchestrator.pkg import FactStore, load_or_extract
    from orchestrator.pkg.overview import build_overview
    from orchestrator.sdlc.codegen import _MAX_CONTEXT_BYTES
    from orchestrator.sdlc.design import produce_design
    from orchestrator.sdlc.investigate import build_investigation
    from orchestrator.sdlc.validity import assess

    root_path = Path(root)
    batch = load_or_extract(root_path)
    store = FactStore(batch)
    overview = build_overview(batch)

    investigation = build_investigation(
        str(spec.get("title") or ""),
        str(spec.get("summary") or ""),
        store=store,
        root=root_path,
    )
    landing = []
    for land in getattr(investigation, "landing", []) or []:
        where = str(getattr(land, "where", "")).split(":", 1)[0]
        if where and where not in landing:
            landing.append(where)

    # The gate runs, and its verdict is reported — but it does not stop a plan. Refusing
    # to *show* someone the evidence for a refusal is the opposite of the point.
    assessment = assess(
        spec,
        store=store,
        landing=landing,
        issue_type=str(spec.get("issue_type") or ""),
        root=root_path,
        context_budget=_MAX_CONTEXT_BYTES,
    )
    design = await produce_design(spec, overview=overview, store=store, llm=None, root=root_path)

    return render_build_md(
        spec,
        investigation=investigation,
        design=design,
        validity=assessment,
        root=root_path,
        commit=derived_at(root_path),
        context_budget=_MAX_CONTEXT_BYTES,
        language=language,
    )


# ---- persistence -----------------------------------------------------------


def plan_dir(root: Path | str = ".") -> Path:
    """Where plans live: `.spine/plans/` beside the code they describe.

    Three constraints meet here. It must be **permanent** — the hand-written SSPN-49
    template sat in a `/tmp` scratchpad for two days, which is not storage. It must be
    **invisible to `understand`**, which ingests markdown from disk whether or not git
    tracks it; a live plan in the working tree would become a `Doc` node and change the
    graph the next stage reads. And it must be **stable per ticket**, so re-running is a
    diff rather than a new file.

    A dot-directory satisfies all three: `doc_source` skips dirnames starting with ".",
    so nothing here reaches the graph. Approved documents are promoted to
    `docs/specs/build-documents/` deliberately, by a human.
    """
    return Path(root) / ".spine" / "plans"


def persist(
    document: str,
    *,
    intent_id: str,
    root: Path | str = ".",
    out: Path | str | None = None,
) -> tuple[Path, Path | None]:
    """Write the plan to its stable path, keeping what it replaced.

    Returns ``(path, superseded)``. The previous document is snapshotted under
    ``history/`` keyed by the commit it was derived at — not a timestamp, because the
    document is deterministic and the commit is the axis that actually changed. Writing
    an identical document is a no-op with no snapshot, so re-running at the same commit
    does not churn the history.
    """
    target_dir = Path(out) if out else plan_dir(root)
    path = target_dir / f"{intent_id}-build.md"
    superseded: Path | None = None

    if path.is_file():
        previous = path.read_text(encoding="utf-8")
        if previous == document:
            return path, None
        match = _DERIVED_AT.search(previous)
        key = (
            match.group(1)
            if match
            else hashlib.sha1(previous.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        )
        history = target_dir / "history"
        history.mkdir(parents=True, exist_ok=True)
        superseded = history / f"{intent_id}-{key}.md"
        superseded.write_text(previous, encoding="utf-8")

    target_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path, superseded


__all__ = [
    "DETERMINISTIC",
    "HUMAN",
    "MODEL",
    "STATED",
    "build_plan",
    "derived_at",
    "persist",
    "plan_dir",
    "render_build_md",
]
