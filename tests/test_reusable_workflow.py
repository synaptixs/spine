"""The reusable comprehension workflow — G4 Phase 5a.

This file is a **published interface**: other repositories reference it by tag, so its shape is
a contract, not an implementation detail. These tests pin the three properties that make it safe
to hand to a stranger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/spine-comprehension.yml"


def _load() -> dict[Any, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(data: dict[Any, Any]) -> dict[str, Any]:
    """PyYAML reads a bare ``on:`` key as the boolean ``True`` — YAML 1.1, not a typo."""
    triggers = data.get("on", data.get(True))
    assert isinstance(triggers, dict), "no trigger block"
    return triggers


def test_it_is_callable_by_another_repository() -> None:
    """Without `workflow_call` it is not reusable, only runnable here."""
    assert "workflow_call" in _triggers(_load())


def test_it_asks_for_no_secrets() -> None:
    """The whole point of Phase 5a: Spine needs no credentials to read a repository.

    `investigate` is deterministic and model-free. The caller's own `GITHUB_TOKEN` posts the
    comment and nothing else; a workflow that declared `secrets:` would be asking the adopter
    for something, which is the friction this phase exists to remove.
    """
    data = _load()
    assert "secrets" not in _triggers(data)["workflow_call"]
    assert "secrets" not in data


def test_permissions_are_least_privilege() -> None:
    data = _load()
    assert data["permissions"] == {"contents": "read", "pull-requests": "write"}


def test_the_pull_request_title_never_reaches_a_shell_inline() -> None:
    """Script injection, the one that actually bites reusable workflows.

    A pull request title is attacker-controlled: anyone who can open a PR chooses it. Written as
    `${{ github.event.pull_request.title }}` **inside a `run:` block**, GitHub substitutes it
    before `bash` sees it, so a title containing `$(...)` executes. Passing it through `env:` and
    quoting `"$PR_TITLE"` makes it data.

    Checked against the parsed steps, not a regex over the file: an earlier version of this test
    matched text and reported a false positive, because a `run:` block and the *next step's*
    `env:` are indistinguishable once you stop tracking indentation.

    Verified by hand 2026-09-02 with a title carrying backticks and double quotes: it reached the
    comment intact and ran nothing.
    """
    data = _load()
    steps = data["jobs"]["comprehend"]["steps"]
    assert steps, "no steps to check"
    for step in steps:
        script = step.get("run") or ""
        assert "github.event" not in script, (
            f"step {step.get('name')!r} interpolates event data into a shell block; "
            "pass it via env: and quote the variable"
        )


def test_the_comment_is_updated_not_appended() -> None:
    """A bot that comments on every push is a bot people mute, and a muted check is not a check."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "spine-comprehension" in text, "no marker to find the previous comment by"
    assert "PATCH" in text, "no update path — every push would add a new comment"
