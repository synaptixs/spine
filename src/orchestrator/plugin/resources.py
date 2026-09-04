"""MCP resources: the knowledge a repo has already committed, readable as documents.

A tool result scrolls out of a host's context; a **resource** is listable, addressable by
URI, and attachable as context whenever the host wants it back. Three things Spine
produces are documents by nature and belong here rather than behind a tool call:

- ``spine://bank`` and ``spine://bank/{section}`` — the committed ``episteme/`` knowledge
  base ``understand`` writes (``README.md`` index, ``architecture``, ``domain-model``,
  ``conventions``, …).
- ``spine://plans`` and ``spine://plan/{intent_id}`` — the build documents ``sdlc_plan``
  persists under ``.spine/plans/``, each with its approval state.
- ``spine://state`` — the current-state report (developer lens), from the commit-keyed
  cache.

**They address the default repository.** A URI segment matches one path element, so an
absolute repo path cannot ride inside a URI without encoding no host renders well. A stdio
plugin is launched per project, so the process working directory is the repo — and
``SPINE_REPO_ROOT`` overrides it for a host that launches from elsewhere. The tools keep
taking ``repo_path`` as before; only the resources are per-process.

Every resource returns markdown, and a missing thing returns a short note saying what
would create it rather than an error — a host renders a note; it hides an error.

Read-only by construction. Over HTTP the server-wide scope floor (``spine:read``) covers
resource reads; there is nothing to guard per resource.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT_ENV = "SPINE_REPO_ROOT"


def default_repo_root() -> Path:
    """The repository the resources describe: ``SPINE_REPO_ROOT``, else the working directory."""
    return Path(os.getenv(REPO_ROOT_ENV) or os.getcwd()).resolve()


def bank_index() -> str:
    """The committed knowledge base's index and section list, or how to build one."""
    from orchestrator.knowledge.access import read_memory_bank

    root = default_repo_root()
    bank = read_memory_bank(root)
    if not bank["exists"]:
        return (
            f"_No knowledge base at `{bank['dir']}`._\n\n"
            f"Build one with the `understand_repo` tool (or `orchestrator understand .`) — "
            f"deterministic, no model — then read `spine://bank/architecture` and friends."
        )
    sections = "\n".join(f"- `spine://bank/{name[:-3]}`" for name in bank["sections"])
    return f"{bank['index']}\n\n---\n\nSections:\n\n{sections}\n"


def bank_section(section: str) -> str:
    """One committed page of the knowledge base, e.g. ``architecture`` or ``conventions``."""
    from orchestrator.knowledge.access import read_memory_bank

    bank = read_memory_bank(default_repo_root(), section)
    if not bank["exists"]:
        return bank_index()
    content = bank.get("content")
    if content is None:
        return f"_No section `{section}` in the knowledge base._ See `spine://bank` for the list."
    return str(content)


def plans_index() -> str:
    """The build documents under ``.spine/plans/``, each with its approval state."""
    from orchestrator.sdlc.builddoc import load_approval, plan_dir

    root = default_repo_root()
    plans = plan_dir(root)
    docs = sorted(plans.glob("*-build.md")) if plans.is_dir() else []
    if not docs:
        return (
            f"_No build documents under `{plans}`._\n\n"
            "`sdlc_plan` writes one per ticket — the twelve-section document, no model, no "
            "credentials — and `sdlc_approve` records the decision on it."
        )
    lines = ["| intent | decision | decided by | document |", "|---|---|---|---|"]
    for doc in docs:
        intent = doc.name[: -len("-build.md")]
        approval = load_approval(intent, root=root)
        decision = approval.decision if approval else "not decided"
        who = approval.decided_by if approval else ""
        lines.append(f"| `{intent}` | {decision} | {who} | `spine://plan/{intent}` |")
    return "\n".join(lines) + "\n"


def plan_document(intent_id: str) -> str:
    """One build document, as ``sdlc_plan`` wrote it, with its approval state on top."""
    from orchestrator.sdlc.builddoc import load_approval, plan_dir

    root = default_repo_root()
    plans = plan_dir(root).resolve()
    # ``intent_id`` arrives from a host: confine the read to the plans dir, the way the bank
    # reader does, so "../../secrets" reaches nothing.
    path = (plans / f"{intent_id}-build.md").resolve()
    if not path.is_relative_to(plans) or not path.is_file():
        return f"_No build document for `{intent_id}`._ See `spine://plans` for the list."
    approval = load_approval(intent_id, root=root)
    head = (
        f"> **{approval.decision}** by {approval.decided_by} on {approval.decided_at}"
        + (f" — {approval.note}" if approval.note else "")
        if approval
        else "> **Not decided.** A human reads this, then `sdlc_approve` records the decision."
    )
    return f"{head}\n\n{path.read_text(encoding='utf-8')}"


def state_report() -> str:
    """The current-state report for the repository — the same document ``map_repo``
    renders, developer lens, from the commit-keyed cache."""
    from orchestrator.knowledge.current_state import load_current_state, render_current_state

    state, _batch = load_current_state(default_repo_root())
    return render_current_state(state, lens="developer")


@dataclass(frozen=True)
class ResourceSpec:
    uri: str
    name: str
    description: str
    fn: Callable[..., str]


#: What ``build_server`` registers. A ``{param}`` in the URI makes it a template the host
#: can list and fill; the rest are direct resources.
_RESOURCES: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        "spine://bank",
        "knowledge-base",
        "The committed episteme/ knowledge base: index and section list.",
        bank_index,
    ),
    ResourceSpec(
        "spine://bank/{section}",
        "knowledge-base-section",
        "One page of the knowledge base (architecture, domain-model, conventions, …).",
        bank_section,
    ),
    ResourceSpec(
        "spine://plans",
        "build-documents",
        "The build documents under .spine/plans, with their approval state.",
        plans_index,
    ),
    ResourceSpec(
        "spine://plan/{intent_id}",
        "build-document",
        "One build document, with its approval state on top.",
        plan_document,
    ),
    ResourceSpec(
        "spine://state",
        "current-state",
        "The current-state report (developer lens), from the commit-keyed cache.",
        state_report,
    ),
)

__all__ = [
    "REPO_ROOT_ENV",
    "ResourceSpec",
    "bank_index",
    "bank_section",
    "default_repo_root",
    "plan_document",
    "plans_index",
    "state_report",
]
