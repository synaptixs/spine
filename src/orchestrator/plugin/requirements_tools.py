"""The requirements check as MCP tools — the logic behind ``requirements_check`` and
``requirements_answer``.

The tools themselves are registered in ``server.py`` (the registry table and the generated tool
inventory read them from there); the work lives here so a 114 KB module does not grow by
another feature. **Neither calls a model**: the gate is deterministic, the code check reads the
graph, and an answer is a file edit. ``tests/plugin/test_requirements_tools.py`` proves it by
making every model call raise.

**Who gave an answer.** Over MCP Spine cannot see who typed it — it may be the user, or an
agent that decided for them. So every answer recorded here is channel ``mcp``, origin
``relayed``: Spine writes down what it observed and never claims a person it did not see. Only
the CLI or an answers file record ``user``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.intake import requirements as rq


def _load(change_path: str) -> rq.LoadedChange:
    """A change by the path of its directory. Unlike the CLI there is no ``--root`` to resolve an
    id against — a tool's working directory is whatever the host launched it in — so a path that
    is not a change directory is an error that says so, never a silent look in the wrong place."""
    path = Path(change_path).expanduser()
    if not (path / "proposal.md").is_file():
        raise rq.RequirementsError(
            f"{change_path!r} is not a change directory: there is no proposal.md in it. "
            "Pass the path of an OpenSpec change, e.g. openspec/changes/<id>."
        )
    return rq.load_change(str(path))


def _code_check(loaded: rq.LoadedChange, repo_path: str) -> rq.CodeCheck:
    """The change's criteria against a repository, or the stated absence of one."""
    from orchestrator.intake import pkg_evidence

    if not repo_path:
        return rq.code_check(pkg_evidence.ungrounded())

    # The per-change retrieval and binding is the CLI's composition (`cli/build._facts_for_spec`),
    # used here rather than copied: it is the one place that reads both a graph and an intake
    # spec, and two copies would eventually disagree about what "names code that exists" means.
    # Imported inside the call: `cli.build` imports nothing from the plugin, so there is no cycle.
    from orchestrator.cli.build import _facts_for_spec
    from orchestrator.intake.specs import FeatureSpec
    from orchestrator.pkg.persistence import repo_state
    from orchestrator.plugin.server import _repo_store

    with _repo_store(repo_path) as (store, repo, _docs):
        _sha, dirty = repo_state(repo)
        base = pkg_evidence.from_store(store, where=repo_path, untrusted=(repo_path,) if dirty else ())
        intent = loaded.intent
        spec = FeatureSpec(
            intent_id=intent.id,
            title=intent.title,
            description=intent.description,
            acceptance_criteria=list(intent.acceptance_criteria),
        )
        return rq.code_check(_facts_for_spec(base, store, repo, None, spec))


def check(change_path: str, repo_path: str = "") -> dict[str, Any]:
    """The strict clarity gate over a change, and what the code says about it."""
    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        loaded = _load(change_path)
        code = _code_check(loaded, repo_path)
    except rq.RequirementsError as exc:
        return {"error": str(exc)}
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}
    return rq.check_intent(loaded.intent, change_id=loaded.change_id, code=code).to_dict()


def answer(change_path: str, question: str, answer: str = "", defer_to: str = "") -> dict[str, Any]:
    """Record one answer (or a deferral to a named owner), then say where the gate stands."""
    try:
        loaded = _load(change_path)
        result = rq.record_answers(loaded, [rq.AnswerRequest(question, answer, defer_to)], channel="mcp")
        after = rq.check_intent(_load(change_path).intent, change_id=loaded.change_id)
    except rq.RequirementsError as exc:
        return {"error": str(exc)}
    return {**result, "passes": after.passes, "unresolved": list(after.unresolved)}
