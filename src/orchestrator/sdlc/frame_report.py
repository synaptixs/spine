"""Greenfield framing, composed once for the CLI and the MCP tools (P9c of SSPN-297, D26/D32).

``openspec frame`` prints what these return and ``requirements_frame`` /
``requirements_generate_capabilities`` return it, so the two surfaces cannot drift (the parity
test compares them). Three actions: draft the framing change from an idea; draft one change per
capability from a framing that passes ``check``; and propose the framing's capabilities with the
one capped call (``ideation.generate_capabilities``). Only the last one spends.

A framing is named by its id under an OpenSpec root, and the id must be bare — letters, digits
and dashes — so a caller cannot point a write outside ``<root>/changes/``; a framing directory
that is a symlink is refused for the same reason. Errors are raised (``FramingError`` and its
``FramingNotReadyError``, ``RequirementsError``) for each surface to turn into its own answer: an
exit code, or an ``{"error": …}`` dict."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from orchestrator.intake import framing as fr
from orchestrator.intake import requirements as rq
from orchestrator.intake.openspec_writer import change_id_for

__all__ = ["capabilities_report", "framing_change", "framing_report", "generation_report"]

_BARE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def framing_change(root: str | Path, framing: str) -> rq.LoadedChange:
    """The framing change ``framing`` under ``root``, refused unless its id is bare and its
    directory is a real one inside ``<root>/changes/``."""
    if not _BARE_ID.match(framing):
        raise fr.FramingError(
            f"{framing!r} is not a change id (letters, digits and dashes): a framing is named by its id "
            f"under {root}/changes/"
        )
    directory = Path(root) / "changes" / framing
    if directory.is_symlink():
        raise fr.FramingError(f"{directory} is a symlink: a framing is only read and written where it is")
    return rq.load_change(framing, root=root)


def _cmd(*parts: str) -> str:
    return shlex.join(["orchestrator", "openspec", *parts])


def framing_report(root: str | Path, idea: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Draft the framing change for ``idea``; say what to answer next."""
    directory = Path(root) / "changes" / change_id_for(fr.framing_intent(idea))
    if directory.is_symlink():
        raise fr.FramingError(f"{directory} is a symlink: a framing is only written where it is")
    change_id, written = fr.draft_framing(root, idea, overwrite=overwrite)
    answer = _cmd("answer", change_id, "--root", str(root), "--answers", "why.yaml")
    draft = _cmd("frame", "--from", change_id, "--root", str(root))
    return {
        "root": str(root),
        "framing": change_id,
        "written": [str(p) for p in written],
        "next": f"Answer its four questions ({answer}), then list the capabilities and draft them ({draft}).",
    }


def capabilities_report(
    root: str | Path, framing: str, capabilities: list[str] | None = None, *, overwrite: bool = False
) -> dict[str, Any]:
    """Draft one change per capability — those given, else the framing's ``## Capabilities``.

    From the list, the capabilities already drafted from this framing are left as they are and
    reported (``already_drafted``): the list grows as it is edited or proposed into, and drafting
    it again must not refuse, or replace the answers those changes have since been given."""
    loaded = framing_change(root, framing)
    texts = list(capabilities or [])
    already: list[str] = []
    if not texts:
        drafted = set(fr.drafted_from(root, loaded.change_id))
        listed = fr.listed_capabilities(loaded)
        already = [i for i in (fr.capability_id(loaded.change_id, t) for t in listed) if i in drafted]
        texts = [t for t in listed if fr.capability_id(loaded.change_id, t) not in drafted]
        if not texts:
            raise fr.FramingError(
                f"every capability in {loaded.change_id}'s `## Capabilities` is already drafted "
                f"({', '.join(already)}): add to the list, or name the capabilities to draft"
            )
    ids = fr.draft_capabilities(root, loaded, texts, overwrite=overwrite)
    answer = _cmd("answer", "<change>", "--root", str(root), "--question", "...", "--answer", "...")
    return {
        "root": str(root),
        "framing": loaded.change_id,
        "drafted": ids,
        "already_drafted": already,
        "next": f"Each asks its own outcome: {answer}, then {_cmd('check', '<change>', '--root', str(root))}",
    }


async def generation_report(
    root: str | Path, framing: str, *, approve_usd: float = 0.0, model: str | None = None
) -> dict[str, Any]:
    """At most one capped model call proposing the framing's capabilities; never drafts a change.
    Refused before any spend (``FramingNotReadyError``) when the framing does not pass ``check``."""
    from orchestrator.sdlc import option_report as orp

    loaded = framing_change(root, framing)
    if (not_ready := fr.readiness(loaded)) is not None:
        raise fr.FramingNotReadyError(not_ready)
    result = await orp.generate_capabilities_for(loaded, approve_usd=approve_usd, model=model)
    report: dict[str, Any] = {
        "root": str(root),
        "framing": loaded.change_id,
        "status": result.status,
        "capabilities": list(result.capabilities),
        "cost_usd": round(result.cost_usd, 6),
        "estimate_usd": round(result.estimate.usd, 6) if result.estimate is not None else None,
        "problems": list(result.problems),
        "refusal": result.refusal.message if result.refusal is not None else None,
    }
    if result.status == "refused" and result.refusal is not None:
        shown = round(result.refusal.estimate_usd, 4)
        report["approve_usd"] = shown  # the number that approves it, as the check rounds it
        report["next"] = f"Refused before the call. Approve exactly {shown:.4f}, or park it."
    elif result.status == "written":
        draft = _cmd("frame", "--from", loaded.change_id, "--root", str(root))
        report["next"] = (
            f"Edit the list in {loaded.design_path}'s `## Capabilities`, then draft it ({draft})."
        )
    else:
        report["next"] = "Nothing was written; see problems."
    return report
