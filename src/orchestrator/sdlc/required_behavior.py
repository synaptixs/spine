"""Required-behavior gates — SSPN-118.

A project opts in by committing ``.spine/required-behavior.yaml``: checks that must pass
before a run can report itself verified, distinct from both ``PreflightRunner`` (lint/type
quality) and ``TestRunner`` (does the model's own generated tests pass). ONTM-4 showed
generated tests can pass while the feature they test never exercises its own default
wiring — this closes that gap with checks the model does not author and cannot edit away.

A project without the manifest is unaffected: ``SubprocessRequiredBehaviorRunner`` self-skips
exactly like ``SubprocessPreflightRunner`` does for a missing ``pyproject.toml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.sdlc.contracts import RequiredBehaviorResult as RequiredBehaviorResult
from orchestrator.sdlc.contracts import RequiredBehaviorRunner as RequiredBehaviorRunner
from orchestrator.sdlc.contracts import RequirementResult
from orchestrator.sdlc.process import ExecCapture, exec_capture

_MANIFEST_RELATIVE_PATH = ".spine/required-behavior.yaml"
_MAX_OUTPUT_CHARS = 4000
_DEFAULT_TIMEOUT = 60.0
# Exit codes that mean "this never ran", not "this ran and failed" — 127 is the shell
# convention for command-not-found, -1 is exec_capture's own timeout sentinel.
_ENVIRONMENT_EXIT_CODES = (-1, 127)


class RequiredBehaviorManifestError(ValueError):
    """``.spine/required-behavior.yaml`` exists but cannot be used. Names the file and key."""


@dataclass(frozen=True)
class Requirement:
    """One required-behavior check, from the manifest or injected as hidden (SSPN-118/D16)."""

    requirement_id: str
    description: str
    command: tuple[str, ...]
    timeout: float = _DEFAULT_TIMEOUT
    required: bool = True


def _requirement_from_mapping(entry: Any, *, where: str) -> Requirement:
    if not isinstance(entry, dict):
        raise RequiredBehaviorManifestError(f"{where}: each requirement must be a mapping")
    req_id = entry.get("id")
    if not req_id or not isinstance(req_id, str):
        raise RequiredBehaviorManifestError(f"{where}: requirement missing a string 'id'")
    command = entry.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise RequiredBehaviorManifestError(
            f"{where}: requirement {req_id!r} needs a non-empty 'command' list"
        )
    return Requirement(
        requirement_id=req_id,
        description=str(entry.get("description", "")),
        command=tuple(command),
        timeout=float(entry.get("timeout", _DEFAULT_TIMEOUT)),
        required=bool(entry.get("required", True)),
    )


def load_manifest(root: Path) -> tuple[Requirement, ...]:
    """The project's public required-behavior requirements, or ``()`` without a manifest.

    Hidden requirements are never read from here (SSPN-118/D16) — they are injected into
    ``SubprocessRequiredBehaviorRunner`` directly by whoever loaded them outside the worktree.
    """
    import yaml

    manifest = root / _MANIFEST_RELATIVE_PATH
    if not manifest.is_file():
        return ()
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise RequiredBehaviorManifestError(f"{manifest}: cannot be read — {exc}") from exc
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RequiredBehaviorManifestError(f"{manifest}: invalid YAML — {exc}") from exc
    if not isinstance(doc, dict) or "requirements" not in doc:
        raise RequiredBehaviorManifestError(f"{manifest}: expected a top-level 'requirements:' list")
    requirements = doc["requirements"]
    if not isinstance(requirements, list) or not requirements:
        raise RequiredBehaviorManifestError(f"{manifest}: 'requirements:' must be a non-empty list")
    return tuple(_requirement_from_mapping(r, where=str(manifest)) for r in requirements)


class SubprocessRequiredBehaviorRunner:
    """Runs a project's public manifest plus any injected hidden requirements."""

    def __init__(self, *, hidden: tuple[Requirement, ...] = (), capture: ExecCapture = exec_capture) -> None:
        self._hidden = hidden
        self._capture = capture

    async def run(self, *, path: str) -> RequiredBehaviorResult:
        root = Path(path)
        try:
            public = load_manifest(root)
        except RequiredBehaviorManifestError as exc:
            # A broken manifest is not "no manifest" — the project opted in and the opt-in
            # is unusable. Fail loud, the same reasoning as PreflightBaselineError: silently
            # treating this as unverified would let `passed` mean less than it claims.
            return RequiredBehaviorResult(passed=False, output=str(exc))
        requirements = public + self._hidden
        if not requirements:
            return RequiredBehaviorResult(
                passed=True, output=f"no {_MANIFEST_RELATIVE_PATH} — required-behavior unverified"
            )
        items = []
        for req in requirements:
            try:
                rc, out = await self._capture(req.command, cwd=str(root), timeout=req.timeout)
            except OSError as exc:
                # exec_capture() never catches this — every other caller's argv[0] is a
                # Spine-controlled interpreter (sys.executable, "perl", …) that always
                # exists. A manifest's command is project-authored and can simply be
                # wrong (a typo, an uninstalled tool); that is an environment problem,
                # not a code-correctness one, same bucket as exit 127/-1 below.
                rc, out = -1, str(exc)
            items.append(
                RequirementResult(
                    requirement_id=req.requirement_id,
                    passed=rc == 0,
                    required=req.required,
                    command=req.command,
                    output=out[-_MAX_OUTPUT_CHARS:],
                    environment_blocked=rc in _ENVIRONMENT_EXIT_CODES,
                )
            )
        passed = all(item.passed for item in items if item.required)
        lines = [f"{'PASS' if item.passed else 'FAIL'} {item.requirement_id}" for item in items]
        failures = [item for item in items if not item.passed and item.required]
        for item in failures:
            lines.append(f"--- {item.requirement_id} failed ---\n{item.output}")
        return RequiredBehaviorResult(passed=passed, items=tuple(items), output="\n".join(lines))


class StubRequiredBehaviorRunner:
    """Always-pass, always-skipped required-behavior runner — unit tests."""

    async def run(self, *, path: str) -> RequiredBehaviorResult:
        _ = path
        return RequiredBehaviorResult(passed=True, output="stub required-behavior")


__all__ = [
    "Requirement",
    "RequiredBehaviorManifestError",
    "RequiredBehaviorResult",
    "RequiredBehaviorRunner",
    "StubRequiredBehaviorRunner",
    "SubprocessRequiredBehaviorRunner",
    "load_manifest",
]
