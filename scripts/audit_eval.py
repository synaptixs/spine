"""Auditor eval (Bet 4c) — score the codebase-auditor persona on the SAME harness.

One harness, two personas: the auditor runs over repos with **seeded** issues
(ground truth) and is scored on whether it catches them — acceptance / cost /
convergence / variance, exactly like the SWE arms. On demand; writes a dated
scorecard to ``docs/evals/``.

Config (EVAL_AUDIT_CONFIG=path.json):
    {"tasks": [
        {"id": "svc-injection", "root": "/abs/checkout", "focus": "security",
         "expected": [{"file": "svc.py", "line": 2, "label": "command injection"}]}
    ]}

Usage:
    EVAL_AUDIT_CONFIG=audit_tasks.json uv run python scripts/audit_eval.py
    EVAL_REPEATS=3 EVAL_AUDIT_CONFIG=audit_tasks.json uv run python scripts/audit_eval.py
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from orchestrator.core.env import load_local_env  # noqa: E402
from orchestrator.core.llm import LiteLLMClient, RecordingLLMClient  # noqa: E402
from orchestrator.evals import EvalTask, render_markdown, run_eval  # noqa: E402
from orchestrator.personas import make_audit_arm  # noqa: E402
from orchestrator.sdlc.codegen import resolve_codegen_model  # noqa: E402


def load_audit_tasks(doc: dict) -> list[EvalTask]:
    """Parse an audit-eval config → tasks. Pure; no clone/network."""
    return [
        EvalTask(
            id=str(t["id"]),
            category=str(t.get("focus", "audit")),
            payload={
                "root": str(Path(t["root"]).expanduser().resolve()),
                "focus": t.get("focus", "correctness risks and security"),
                "expected": list(t.get("expected", [])),
            },
        )
        for t in doc.get("tasks", [])
    ]


async def main() -> None:
    load_local_env(str(REPO / ".env"))
    config = os.getenv("EVAL_AUDIT_CONFIG")
    if not config:
        print("set EVAL_AUDIT_CONFIG=path.json (see this script's docstring for the format)")
        raise SystemExit(2)
    model = resolve_codegen_model() or "gpt-4o"
    repeats = int(os.getenv("EVAL_REPEATS", "1"))
    tasks = load_audit_tasks(json.loads(Path(config).read_text(encoding="utf-8")))
    print(f"auditor eval · model: {model} · repeats: {repeats} · tasks: {[t.id for t in tasks]}")

    llm = RecordingLLMClient(LiteLLMClient(request_timeout_seconds=300.0))
    card = await run_eval(
        tasks,
        make_audit_arm(llm, model=model),
        arm_name="auditor",
        model=model,
        repeats=repeats,
        on_progress=lambda m: print(f"  {m}"),
    )

    stamp = _dt.datetime.now().strftime("%Y-%m-%d")
    out_dir = REPO / "docs" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(card, title=f"auditor persona — {stamp}")
    (out_dir / f"{stamp}-auditor.md").write_text(md, encoding="utf-8")
    (out_dir / f"{stamp}-auditor.json").write_text(json.dumps(card.to_dict(), indent=2), encoding="utf-8")
    print("\n" + md)
    print(f"scorecard → docs/evals/{stamp}-auditor.{{md,json}}")


if __name__ == "__main__":
    asyncio.run(main())
