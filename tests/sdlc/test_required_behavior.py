"""Required-behavior gates — SSPN-118/119: a project opts in via `.spine/required-behavior.yaml`."""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.required_behavior import (
    RequiredBehaviorManifestError,
    Requirement,
    StubRequiredBehaviorRunner,
    SubprocessRequiredBehaviorRunner,
    load_manifest,
)


def _manifest(root: Path, body: str) -> None:
    (root / ".spine").mkdir(parents=True, exist_ok=True)
    (root / ".spine" / "required-behavior.yaml").write_text(body, encoding="utf-8")


async def test_no_manifest_self_skips_with_pass(tmp_path: Path) -> None:
    result = await SubprocessRequiredBehaviorRunner().run(path=str(tmp_path))
    assert result.passed and "unverified" in result.output
    assert result.items == ()


async def test_passing_requirement(tmp_path: Path) -> None:
    _manifest(
        tmp_path,
        "requirements:\n  - id: default-wiring\n    command: ['python3', '-c', 'pass']\n",
    )
    result = await SubprocessRequiredBehaviorRunner().run(path=str(tmp_path))
    assert result.passed, result.output
    assert len(result.items) == 1
    assert result.items[0].requirement_id == "default-wiring"
    assert result.items[0].passed and not result.items[0].environment_blocked


async def test_failing_requirement_fails_the_gate(tmp_path: Path) -> None:
    _manifest(
        tmp_path,
        "requirements:\n  - id: default-wiring\n    command: ['python3', '-c', 'import sys; sys.exit(1)']\n",
    )
    result = await SubprocessRequiredBehaviorRunner().run(path=str(tmp_path))
    assert not result.passed
    assert not result.items[0].passed and not result.items[0].environment_blocked
    assert "default-wiring" in result.output


async def test_missing_command_is_environment_blocked_not_a_code_failure(tmp_path: Path) -> None:
    _manifest(
        tmp_path,
        "requirements:\n  - id: default-wiring\n    command: ['this-binary-does-not-exist-anywhere']\n",
    )
    result = await SubprocessRequiredBehaviorRunner().run(path=str(tmp_path))
    assert not result.passed
    assert result.items[0].environment_blocked


async def test_optional_requirement_failure_does_not_fail_the_gate(tmp_path: Path) -> None:
    _manifest(
        tmp_path,
        "requirements:\n"
        "  - id: optional-check\n"
        "    required: false\n"
        "    command: ['python3', '-c', 'import sys; sys.exit(1)']\n",
    )
    result = await SubprocessRequiredBehaviorRunner().run(path=str(tmp_path))
    assert result.passed, result.output
    assert not result.items[0].passed
    assert not result.items[0].required


async def test_hidden_requirements_run_alongside_the_public_manifest(tmp_path: Path) -> None:
    _manifest(tmp_path, "requirements:\n  - id: public\n    command: ['python3', '-c', 'pass']\n")
    hidden = (
        Requirement(
            requirement_id="hidden", description="", command=("python3", "-c", "import sys; sys.exit(1)")
        ),
    )
    result = await SubprocessRequiredBehaviorRunner(hidden=hidden).run(path=str(tmp_path))
    assert {item.requirement_id for item in result.items} == {"public", "hidden"}
    assert not result.passed  # the hidden requirement failed and defaults to required


async def test_hidden_requirements_run_even_without_a_public_manifest(tmp_path: Path) -> None:
    hidden = (Requirement(requirement_id="hidden", description="", command=("python3", "-c", "pass")),)
    result = await SubprocessRequiredBehaviorRunner(hidden=hidden).run(path=str(tmp_path))
    assert result.passed and [item.requirement_id for item in result.items] == ["hidden"]


async def test_malformed_manifest_fails_loud_rather_than_skipping(tmp_path: Path) -> None:
    _manifest(tmp_path, "not_requirements: []\n")
    result = await SubprocessRequiredBehaviorRunner().run(path=str(tmp_path))
    assert not result.passed
    assert "requirements:" in result.output


def test_load_manifest_returns_empty_without_a_file(tmp_path: Path) -> None:
    assert load_manifest(tmp_path) == ()


def test_load_manifest_rejects_a_requirement_with_no_command(tmp_path: Path) -> None:
    _manifest(tmp_path, "requirements:\n  - id: bad\n")
    try:
        load_manifest(tmp_path)
    except RequiredBehaviorManifestError as exc:
        assert "command" in str(exc)
    else:
        raise AssertionError("expected RequiredBehaviorManifestError")


async def test_stub_always_passes_and_is_unverified(tmp_path: Path) -> None:
    result = await StubRequiredBehaviorRunner().run(path=str(tmp_path))
    assert result.passed
