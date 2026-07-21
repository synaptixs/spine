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


def _fallback_design(spec: dict[str, Any], overview: dict[str, Any] | None) -> dict[str, Any]:
    mods = [str(m["module"]) for m in (overview or {}).get("modules", [])[:5]]
    ac = [str(a) for a in (spec.get("acceptance_criteria") or [])]
    return {
        "approach": (
            f"Implement '{spec.get('title', 'the feature')}' following the repo's existing "
            "structure and conventions."
        ),
        "files_to_touch": mods,
        "interfaces": [],
        "data_changes": [],
        "risks": (
            ["Heuristic design (no LLM) — confirm the affected modules before building."] if mods else []
        ),
        "test_strategy": "Add tests covering each acceptance criterion: " + "; ".join(ac[:6]),
        "grounded": bool(overview),
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
    data = json.loads(result.text)
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
) -> dict[str, Any]:
    """Produce a grounded design dict for one spec — the pure core, no I/O.

    An LLM writes it when configured, else a deterministic heuristic from the
    graph overview + acceptance criteria. When a ``FactStore`` is supplied, the
    design is annotated with its **blast radius** (module dependents + call
    hotspots) and any **unverified references** (named paths absent from the
    graph). Shared by the SDLC activity (persists artifacts) and the CLI.
    """
    ctx = {"overview": overview, "memory_bank": memory_bank or {}}
    try:
        design = await _llm_design(spec, ctx, llm) if llm is not None else _fallback_design(spec, overview)
    except Exception:  # noqa: BLE001 — LLM/parse failure → deterministic design, never blocks
        design = _fallback_design(spec, overview)
    if store is not None:
        with contextlib.suppress(Exception):  # impact is an annotation; never fail the design
            from orchestrator.sdlc.impact import blast_radius, to_dict

            design["blast_radius"] = to_dict(blast_radius(store, design.get("files_to_touch") or []))
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
