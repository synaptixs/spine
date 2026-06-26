"""run_control gate-decision validation (offline — before any DB/Temporal I/O)."""

from __future__ import annotations

import pytest

from orchestrator.sdlc.run_control import _resolve_gate, decide_gate


def test_resolve_gate_maps_friendly_names() -> None:
    assert _resolve_gate("abc123", "intents") == "sdlc-abc123-0"
    assert _resolve_gate("abc123", "merge") == "sdlc-abc123-1"
    # A raw approval id passes through unchanged.
    assert _resolve_gate("abc123", "sdlc-abc123-1") == "sdlc-abc123-1"


async def test_decide_gate_rejects_unknown_action() -> None:
    # Validated before any engine/Temporal connection, so this never touches I/O.
    with pytest.raises(ValueError, match="action must be one of"):
        await decide_gate("abc123", "intents", "yolo")


async def test_decide_gate_modify_input_needs_patch() -> None:
    with pytest.raises(ValueError, match="non-empty patch"):
        await decide_gate("abc123", "intents", "modify_input", patch=None)
