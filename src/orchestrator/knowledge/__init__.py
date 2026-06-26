"""Project comprehension — the committed memory bank.

``orchestrator understand`` builds a durable, code-true ``memory-bank/`` in the
target repo: structural files rendered deterministically from the PKG + project
profile, plus (later) cached LLM-synthesized prose. This is the knowledge-first
Phase 0 that grounds intake and codegen.
"""

from __future__ import annotations

from orchestrator.knowledge.understand import build_memory_bank, memory_bank_dir

__all__ = ["build_memory_bank", "memory_bank_dir"]
