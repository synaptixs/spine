"""What a codegen loop session accumulates — a leaf module, so both ends can import it.

``CodegenSession`` lived in ``agentic/codegen_tools.py``, which imports ``sdlc/codegen.py`` (for
``apply_files``); ``codegen.py`` needed the type to build one, so it imported it back, at module level
under ``TYPE_CHECKING`` and again inside ``_session``. CodeQL reads either as a cycle
(``py/cyclic-import``, alerts 313 and 314). The dataclass imports nothing from ``orchestrator``, so it
sits here and ``codegen_tools`` re-exports it for everything that still imports it from there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CodegenSession:
    """Accumulates what the loop wrote + the final summary, for the CodeChange."""

    tracker: dict[Path, list[Path]]
    #: Files this run created, per root — the adapter's run-wide tracker, so a later pass still knows
    #: a file is the run's own (``_ruff_fix`` only tidies those, or any file in a repo that configures ruff).
    created: dict[Path, list[Path]] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    summary: str = ""
    submitted: bool = False
