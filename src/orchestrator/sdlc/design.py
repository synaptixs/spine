"""Feature/Issue design milestone (M2): spec × knowledge graph → a grounded design.

For each issue, consumes the M1 comprehension artifacts (the module-level
knowledge-graph overview + the memory bank) and the spec, and produces a
**design** — approach, files to touch, interfaces, data changes, risks, test
strategy — anchored to the repo's real structure. An LLM writes it when one is
configured; otherwise a deterministic heuristic design is produced from the graph
+ acceptance criteria. Persisted under ``run/<sdlc_id>/feature/<issue_key>/``.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.runtime import ArtifactStore

if TYPE_CHECKING:
    from orchestrator.pkg import FactStore

_FIELDS = ("approach", "files_to_touch", "interfaces", "data_changes", "risks", "test_strategy")
_LIST_FIELDS = ("files_to_touch", "interfaces", "data_changes", "risks")


def _key(run_id: str, issue_key: str, name: str) -> str:
    return f"run/{run_id}/feature/{issue_key}/{name}"


async def _load_context(comprehension: dict[str, Any], store: ArtifactStore) -> dict[str, Any]:
    """The structural + conventional context from the M1 comprehension artifacts."""
    arts = (comprehension or {}).get("artifacts")
    ctx: dict[str, Any] = {"overview": None, "memory_bank": {}}
    if not isinstance(arts, dict):
        return ctx
    ov_key = arts.get("graph-overview.json")
    if ov_key:
        with contextlib.suppress(Exception):  # best-effort; design degrades without it
            ctx["overview"] = json.loads((await store.get_bytes(str(ov_key))).decode("utf-8"))
    for name in ("domain-model.md", "tech-context.md", "conventions.md"):
        k = arts.get(f"memory-bank/{name}")
        if k:
            with contextlib.suppress(Exception):
                ctx["memory_bank"][name] = (await store.get_bytes(str(k))).decode("utf-8")
    return ctx


def _structure_lines(overview: dict[str, Any] | None) -> list[str]:
    if not overview:
        return []
    lines: list[str] = []
    mods = overview.get("modules") or []
    if mods:
        lines.append("Top modules: " + ", ".join(f"{m['module']} ({m['nodes']})" for m in mods[:8]))
    edges = overview.get("module_edges") or []
    if edges:
        lines.append(
            "Key dependencies: " + "; ".join(f"{e['src']}→{e['dst']} ({e['kind']})" for e in edges[:6])
        )
    syms = overview.get("top_symbols") or []
    if syms:
        lines.append(
            "Most-connected symbols: " + ", ".join(f"{s['name']} in {s.get('module', '?')}" for s in syms[:8])
        )
    return lines


# Repo-relative source paths, matching `codegen._PATH_RE`. Duplicated rather than shared:
# a six-character regex is cheaper to repeat than a new coupling between two modules that
# otherwise do not know about each other.
_PATH_RE = re.compile(r"\b((?:src/|tests/)[\w./-]+\.py)\b")


def _stated_paths(spec: dict[str, Any], root: Path | None = None) -> list[str]:
    """Paths the spec *states*, which outrank paths inferred from its words.

    ``_landing_files`` reads only the title and summary, matching the ticket's language
    against the graph. A ticket about "the registry API" whose criteria name
    ``src/orchestrator/cli.py`` therefore came back proposing the registry *server* modules
    — the wrong side of the wire — while the file the spec named twice was absent. Codegen
    is then handed a design that contradicts its own spec, and on SSPN-49 it submitted
    nothing at all rather than choose between them.

    The criteria and technical notes are where a spec says which file it means, so they are
    read here for the same reason ``codegen._paths_from`` reads them. A stated path that
    does not exist is dropped: naming a file to create is a job for the approach, not for a
    list of files to open.
    """
    blob = " ".join(
        [
            str(spec.get("summary") or ""),
            str(spec.get("technical_notes") or ""),
            *[str(a) for a in (spec.get("acceptance_criteria") or [])],
        ]
    )
    out: list[str] = []
    for rel in _PATH_RE.findall(blob):
        if rel in out:
            continue
        if root is not None and not (root / rel).is_file():
            continue
        out.append(rel)
    return out


def _landing_files(spec: dict[str, Any], store: FactStore | None) -> list[str]:
    """Where this *ticket* lands, from the same reading `investigate` does.

    The previous heuristic listed the overview's biggest modules, which is a fact about the
    repo rather than about the ticket: a request to add a CLI flag came back as "touch
    registry/db/models.py, sdlc/testenv.py, sdlc/codegen.py". Plausible-looking and wrong is
    the worst thing a design can be, and once the design is carried into codegen it is an
    instruction to edit the wrong files.

    Reusing the investigation keeps the two stages agreeing with each other instead of
    contradicting each other — they now answer "where does this land" the same way, because
    it is the same code answering.
    """
    if store is None:
        return []
    from orchestrator.sdlc.investigate import build_investigation

    investigation = build_investigation(
        str(spec.get("title", "")), str(spec.get("summary", "")), store=store, max_symbols=8
    )
    files: list[str] = []
    for landing in investigation.landing:
        path = landing.where.split(":", 1)[0]
        if path and path not in files:
            files.append(path)
    return files[:5]


def _overview_files(spec: dict[str, Any], overview: dict[str, Any] | None) -> list[str]:
    """Ticket words matched against module and symbol names, for callers with no graph.

    The SDLC activity path carries a persisted overview, not a ``FactStore``. Matching the
    ticket's own words against what the overview names is weaker than the graph reading, but
    it is still *about the ticket* — where ranking modules by size was only ever about the
    repo. A module nothing in the ticket mentions is not proposed at all.
    """
    modules = (overview or {}).get("modules") or []
    if not modules:
        return []
    text = " ".join(
        [str(spec.get("title", "")), str(spec.get("summary", ""))]
        + [str(a) for a in (spec.get("acceptance_criteria") or [])]
    ).lower()
    tokens = {t for t in re.split(r"[^a-z0-9]+", text) if len(t) > 3}
    if not tokens:
        return []

    symbols_by_module: dict[str, list[str]] = {}
    for sym in (overview or {}).get("top_symbols") or []:
        symbols_by_module.setdefault(str(sym.get("module", "")), []).append(str(sym.get("name", "")).lower())

    scored: list[tuple[int, str]] = []
    for module in modules:
        name = str(module.get("module", ""))
        haystack = {*re.split(r"[^a-z0-9]+", name.lower()), *symbols_by_module.get(name, [])}
        hits = len(tokens & haystack)
        if hits:
            scored.append((hits, name))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [name for _, name in scored[:5]]


def _fallback_design(
    spec: dict[str, Any],
    overview: dict[str, Any] | None,
    store: FactStore | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    # What the spec *says* first; then the graph reading; then the overview's names; nothing
    # at all rather than a guess. A path the ticket names is not a heuristic — inferring
    # around it is how a design ends up contradicting the spec it was built from.
    stated = _stated_paths(spec, root)
    files = stated or _landing_files(spec, store) or _overview_files(spec, overview)
    ac = [str(a) for a in (spec.get("acceptance_criteria") or [])]
    # Say which it is. A consumer — a human reading design.md, or the codegen prompt now
    # carrying it — has to be able to tell a grounded reading from a shrug.
    risks = ["Heuristic design (no LLM) — confirm the affected files before building."]
    if stated:
        # Worth saying which reading produced the list: a reader who knows these paths came
        # from the ticket itself does not need to second-guess them the way a keyword match
        # deserves to be second-guessed.
        risks = ["Files taken from the paths this ticket names, not inferred from its words."]
    if not files:
        risks = [
            "Heuristic design (no LLM) and nothing in the graph matched this ticket's words, "
            "so no files are proposed. Locate the change before building rather than trusting "
            "this list."
        ]
    return {
        "approach": (
            f"Implement '{spec.get('title', 'the feature')}' following the repo's existing "
            "structure and conventions."
        ),
        "files_to_touch": files,
        "interfaces": [],
        "data_changes": [],
        "risks": risks,
        "test_strategy": "Add tests covering each acceptance criterion: " + "; ".join(ac[:6]),
        "grounded": bool(files),
        "llm": False,
    }


def _normalise(design: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in _FIELDS:
        v = design.get(f)
        if f in _LIST_FIELDS:
            out[f] = [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])
        else:
            out[f] = str(v) if v is not None else ""
    out["grounded"] = bool(design.get("grounded", True))
    out["llm"] = bool(design.get("llm", False))
    return out


async def _llm_design(spec: dict[str, Any], ctx: dict[str, Any], llm: Any) -> dict[str, Any]:
    from orchestrator.core.llm.client import Message
    from orchestrator.core.prompt_safety import fence_untrusted
    from orchestrator.sdlc.codegen import resolve_codegen_model

    structure = "\n".join(_structure_lines(ctx.get("overview"))) or "(no structure available)"
    conventions = "\n\n".join(f"## {n}\n{c[:1500]}" for n, c in (ctx.get("memory_bank") or {}).items())
    ac = "\n".join(f"- {a}" for a in (spec.get("acceptance_criteria") or []))
    prompt = (
        "Design how to implement this feature in THIS repository. Ground every decision in the "
        "structure + conventions below; reference REAL modules/symbols. Respond with a JSON object "
        "with keys: approach (string), files_to_touch (list of paths), interfaces (list of "
        "signatures/types to add or change), data_changes (list), risks (list), test_strategy (string).\n\n"
        f"FEATURE: {spec.get('title', '')}\n{spec.get('summary', '')}\n"
        f"Acceptance criteria:\n{ac}\n\n"
        f"REPO STRUCTURE (knowledge graph):\n{structure}\n\n"
        # memory-bank conventions are free-text markdown from the (untrusted) target
        # repo; fence them so injected instructions can't steer the design/codegen LLM.
        f"CONVENTIONS / DOMAIN (memory bank):\n{fence_untrusted('repo conventions', conventions[:4000])}"
    )
    result = await llm.complete(
        [
            Message(
                role="system",
                content="You are a senior engineer designing a change grounded in an existing codebase.",
            ),
            Message(role="user", content=prompt),
        ],
        model=resolve_codegen_model(),
        json_object=True,
        temperature=0.2,
    )
    # `_loads_json_object` rather than `json.loads`: the model answers with the object inside a
    # markdown fence or after a sentence, and strict parsing raised `JSONDecodeError` on the
    # first real call this path ever made. `produce_design` swallows that and returns the
    # deterministic design, so the failure was silent — an `llm` arm that measured the skeleton
    # and reported it as the model's work. Codegen already had a tolerant loader; reusing it
    # keeps one definition of "parse a model's JSON".
    from orchestrator.sdlc.codegen import _loads_json_object

    data = _loads_json_object(result.text)
    if data is None:
        raise ValueError("the design model returned no parseable JSON object")
    data["grounded"] = bool(ctx.get("overview"))
    data["llm"] = True
    return _normalise(data)


def render_design_md(spec: dict[str, Any], design: dict[str, Any]) -> str:
    from orchestrator.sdlc.impact import render_md as _render_blast

    def _list(title: str, items: list[str]) -> str:
        if not items:
            return ""
        body = "\n".join(f"- {i}" for i in items)
        return f"\n## {title}\n{body}\n"

    origin = "LLM-generated" if design.get("llm") else "heuristic (no LLM)"
    return (
        f"# Design — {spec.get('title', 'feature')}\n\n"
        f"_{origin}, grounded in the knowledge graph: {design.get('grounded')}_\n\n"
        f"## Approach\n{design.get('approach', '')}\n"
        + _list("Files to touch", design.get("files_to_touch") or [])
        + _list("Interfaces", design.get("interfaces") or [])
        + _list("Data changes", design.get("data_changes") or [])
        + _list("Risks", design.get("risks") or [])
        + f"\n## Test strategy\n{design.get('test_strategy', '')}\n"
        + _render_blast(design.get("blast_radius") or {})
    )


async def produce_design(
    spec: dict[str, Any],
    *,
    overview: dict[str, Any] | None,
    memory_bank: dict[str, str] | None = None,
    store: FactStore | None = None,
    llm: Any = None,
    root: Path | None = None,
    blast_radius: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce a grounded design dict for one spec — the pure core, no I/O.

    ``root`` is the repo the design is for. It is used only to drop a path the spec names
    that does not exist — naming a file to *create* belongs in the approach, not in a list
    of files to open. Without it, stated paths are taken as written.

    An LLM writes it when configured, else a deterministic heuristic from the
    graph overview + acceptance criteria.

    **``blast_radius`` is supplied, not computed — that is defect 2 of
    ``docs/specs/graphir-sdlc-workflow.md``.** This function used to call
    ``impact.blast_radius`` on its own ``files_to_touch``, so the impact analysis described the
    files the design *guessed at*. When the guess was wrong the result was a faithful analysis
    of a fiction, and it read as verification. Phase 1 computes the real one in ``Evidence``,
    keyed off where the ticket lands; Phase 2a passes it in here. The fallback path — computing
    it from ``files_to_touch`` when no caller supplies one — is retained **only** for callers
    that have no Evidence (the standalone `design` CLI command); every SDLC run supplies it.
    """
    ctx = {"overview": overview, "memory_bank": memory_bank or {}}
    try:
        design = (
            await _llm_design(spec, ctx, llm)
            if llm is not None
            else _fallback_design(spec, overview, store, root)
        )
    except Exception:  # noqa: BLE001 — LLM/parse failure → deterministic design, never blocks
        design = _fallback_design(spec, overview, store, root)
    if blast_radius is not None:
        design["blast_radius"] = dict(blast_radius)
    elif store is not None:
        with contextlib.suppress(Exception):  # impact is an annotation; never fail the design
            from orchestrator.sdlc.impact import blast_radius as _compute
            from orchestrator.sdlc.impact import to_dict

            design["blast_radius"] = to_dict(_compute(store, design.get("files_to_touch") or []))
    return design


async def design_feature(
    spec: dict[str, Any],
    *,
    comprehension: dict[str, Any],
    artifact_store: ArtifactStore,
    run_id: str,
    issue_key: str,
    llm: Any = None,
    store: FactStore | None = None,
) -> dict[str, Any]:
    """Produce + persist a grounded design for one issue; return a summary + refs."""
    ctx = await _load_context(comprehension, artifact_store)
    design = await produce_design(
        spec, overview=ctx["overview"], memory_bank=ctx["memory_bank"], store=store, llm=llm
    )

    artifacts: dict[str, str] = {}

    async def _put(name: str, data: bytes, content_type: str) -> None:
        k = _key(run_id, issue_key, name)
        await artifact_store.put_bytes(k, data, content_type)
        artifacts[name] = k

    await _put(
        "design.json", json.dumps(design, default=str, ensure_ascii=False).encode("utf-8"), "application/json"
    )
    await _put("design.md", render_design_md(spec, design).encode("utf-8"), "text/markdown")
    return {
        "issue_key": issue_key,
        "summary": design.get("approach", ""),
        "files_to_touch": design.get("files_to_touch") or [],
        "unverified_references": (design.get("blast_radius") or {}).get("unverified_references") or [],
        "llm": design.get("llm", False),
        "design": design,
        "artifacts": artifacts,
    }


__all__ = ["design_feature", "produce_design", "render_design_md"]
