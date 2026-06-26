"""A/B: PKG-grounded vs ungrounded codegen on a real ticket. First
acceptance-rate data point for Track 2.1.

The ticket is a real roadmap item (PKG caching groundwork): add JSON
save/load for the PKG ``FactBatch``. Both arms get the *identical* spec and
model; the grounded arm additionally gets ``PKGCodegenGrounder`` context (real
symbols + source read off this repo). Each arm writes into its own scratch dir;
the generated tests are then run for real with the repo importable.

Read-only with respect to the repo — all writes land under /tmp.

Usage:
    python scripts/codegen_ab.py
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from orchestrator.core.env import load_local_env  # noqa: E402
from orchestrator.core.llm import LiteLLMClient, LLMError  # noqa: E402
from orchestrator.sdlc.codegen import CodegenError, LLMCodegenAdapter  # noqa: E402
from orchestrator.sdlc.grounding import PKGCodegenGrounder  # noqa: E402

# Realistic Jira-shaped tickets for THIS repo. Identical in both arms.
TICKETS: list[tuple[str, dict[str, object]]] = [
    (
        "PKG-STATS-1",
        {
            "title": "Fact-graph statistics: summarise an extracted knowledge graph",
            "summary": (
                "Add a function that computes a statistics summary for an extracted "
                "fact graph: node counts by kind, edge counts by kind, the number of "
                "external vs grounded nodes, and the five most-called functions by "
                "caller count."
            ),
            "user_story": (
                "As a platform operator, I want a one-call summary of a repo's "
                "knowledge graph so I can sanity-check extraction coverage."
            ),
            "technical_notes": (
                "Build on this repository's existing fact model and store in the "
                "orchestrator.pkg package (FactBatch / FactStore); do not re-parse "
                "source code or invent a parallel fact schema."
            ),
            "acceptance_criteria": [
                "graph_stats(batch) returns a dict with nodes_by_kind and edges_by_kind counts",
                "the dict reports grounded vs external node counts",
                "top_called lists at most five (function id, caller count) pairs, most-called first",
                "an empty batch yields zero counts and an empty top_called list",
            ],
        },
    ),
    (
        "LEDGER-MD-1",
        {
            "title": "Render a token ledger as a Markdown table",
            "summary": (
                "Cost reporting: add a function that renders the per-stage token ledger "
                "(the recording client's usage accounting) as a Markdown table with one "
                "row per stage plus a TOTAL row — columns for stage, calls, prompt "
                "tokens, completion tokens, total tokens and cost in USD."
            ),
            "user_story": (
                "As a pipeline operator, I want the token ledger as Markdown so runs can "
                "post their cost breakdown into PR descriptions and Confluence pages."
            ),
            "technical_notes": (
                "Reuse this repository's existing token-ledger accounting classes from "
                "the core llm recording module; do not define your own usage classes."
            ),
            "acceptance_criteria": [
                "render_ledger_markdown(ledger) returns a Markdown table string",
                "one row per recorded stage, in insertion order",
                "a final TOTAL row sums calls, tokens and cost",
                "cost is formatted with 4 decimal places",
            ],
        },
    ),
    (
        "DOC-DRIFT-MD-1",
        {
            "title": "Render doc-drift findings as a Markdown report",
            "summary": (
                "The doc-semantic layer produces drift findings when documentation "
                "references symbols or files the code doesn't define. Add a renderer "
                "that turns a list of those findings into a Markdown report grouped "
                "by page title, with one bullet per finding showing the mention and "
                "its kind."
            ),
            "user_story": (
                "As a tech lead, I want doc-drift findings as Markdown so the pipeline "
                "can post them to a PR or a Confluence page."
            ),
            "technical_notes": (
                "Consume this repository's existing doc-drift finding type from the "
                "orchestrator.pkg docs module; do not re-implement mention extraction "
                "or reconciliation."
            ),
            "acceptance_criteria": [
                "render_drift_markdown(findings) returns a Markdown string",
                "findings are grouped under a heading per page title",
                "each finding renders as a bullet naming the mention and its kind",
                "an empty findings list yields a short 'no drift' message",
            ],
        },
    ),
]

MODEL = os.getenv("SDLC_CODEGEN_MODEL") or os.getenv("ORCHESTRATOR_INTAKE_MODEL") or "gpt-4o"


def make_worktree(name: str) -> Path:
    """A real git worktree of this repo — the same setting Block C generates into."""
    path = Path(tempfile.mkdtemp(prefix=f"codegen-ab-{name}-")) / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(path), "HEAD"],
        cwd=str(REPO),
        capture_output=True,
        check=True,
    )
    return path


def drop_worktree(path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)], cwd=str(REPO), capture_output=True, check=False
    )


def run_pytest(workdir: Path, test_files: list[str]) -> tuple[bool, str]:
    """Run only the *generated* tests, inside the worktree."""
    if not test_files:
        return False, "no test files were generated"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workdir / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *test_files],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(workdir),
        check=False,
    )
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out


# Classes that already exist in this repo — generating a same-named class is
# the "parallel schema" failure mode the PKG grounding exists to prevent.
_EXISTING_CLASSES = (
    "FactBatch|Node|Edge|Provenance|FactStore|GroundedRetriever|RepoCodeExtractor|TokenLedger|StageUsage"
)


def grade(written: list[str], root: Path) -> dict[str, bool]:
    """Objective fit checks over the files the adapter wrote."""
    source = "\n".join(Path(f).read_text(encoding="utf-8") for f in written if Path(f).exists())
    rel = _rel(written, root)
    placed = any(p.startswith("src/orchestrator/") for p in rel)
    absolute_import = bool(re.search(r"from orchestrator\.|import orchestrator\.", source))
    # A module placed inside the package legitimately uses relative imports
    # ("from orchestrator.pkg.facts import …" OR "from .facts import …").
    relative_import = placed and bool(re.search(r"^from \.\w*", source, re.M))
    # Rewriting a tracked file means the change can't merge as a clean,
    # additive feature — the brownfield failure mode capable models hit.
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=False
    ).stdout
    clobbered = [line[3:] for line in status.splitlines() if line[:2].strip().startswith("M")]
    return {
        "imports real pkg model": absolute_import or relative_import,
        "placed inside the package": placed,
        "reinvents fact classes": bool(re.search(rf"^\s*class ({_EXISTING_CLASSES})\b", source, re.M)),
        "clobbers tracked files": bool(clobbered),
    }


# Mirrors the Block C test→refine loop; AB_MAX_REFINES overrides for depth runs.
MAX_REFINES = int(os.getenv("AB_MAX_REFINES", "2"))


def _rel(files: list[str], root: Path) -> list[str]:
    # resolve() both sides: on macOS the worktree is under /var but adapters
    # write resolved paths under /private/var.
    return [str(Path(f).resolve().relative_to(root.resolve())) for f in files]


async def run_arm(
    name: str, key: str, spec: dict[str, object], adapter: LLMCodegenAdapter, workdir: Path
) -> dict[str, object]:
    print(f"\n--- {key} / {name} → {workdir}")
    impl = await adapter.implement(spec=spec, path=str(workdir), issue_key=key)
    print(f"  implement: {_rel(impl.files, workdir)} — {impl.summary}")
    tests = await adapter.author_tests(spec=spec, path=str(workdir), issue_key=key)
    print(f"  tests:     {_rel(tests.files, workdir)} — {tests.summary}")

    written = [*impl.files, *tests.files]
    test_files = [f for f in written if Path(f).name.startswith("test_")]
    passed, out = run_pytest(workdir, test_files)
    refines = 0
    while not passed and refines < MAX_REFINES:
        refines += 1
        print(f"  pytest:    FAIL — refining ({refines}/{MAX_REFINES}) …")
        fix = await adapter.refine(spec=spec, path=str(workdir), issue_key=key, failures=out)
        print(f"  refine:    {_rel(fix.files, workdir)} — {fix.summary}")
        written.extend(fix.files)
        test_files = [f for f in written if Path(f).name.startswith("test_")]
        passed, out = run_pytest(workdir, test_files)

    tail = out.splitlines()[-1] if out else ""
    print(f"  pytest:    {'PASS' if passed else 'FAIL'} after {refines} refine(s) — {tail}")
    checks = grade(written, workdir)
    for label, value in checks.items():
        print(f"  {label}: {value}")
    fit = (
        checks["imports real pkg model"]
        and checks["placed inside the package"]
        and not checks["reinvents fact classes"]
        and not checks["clobbers tracked files"]
    )
    return {
        "ticket": key,
        "arm": name,
        "tests_pass": passed,
        "refines": refines,
        "fit": fit,
        "accepted": passed and fit,
    }


async def main() -> None:
    load_local_env(str(REPO / ".env"))
    # Coding-tuned models emit several files per call — 60s is too tight.
    llm = LiteLLMClient(request_timeout_seconds=300.0)
    print(f"model: {MODEL} · tickets: {[k for k, _ in TICKETS]}")

    print("\nbuilding PKG for the grounded arm …")
    grounder = PKGCodegenGrounder.from_repo(REPO)

    results: list[dict[str, object]] = []
    for key, spec in TICKETS:
        symbols = re.findall(r"### \w+ `([^`]+)`", grounder.context_for_spec(spec))
        print(f"\n=== ticket {key} — grounded context symbols: {symbols}")
        for arm, adapter in (
            ("ungrounded", LLMCodegenAdapter(llm, model=MODEL)),
            ("grounded", LLMCodegenAdapter(llm, model=MODEL, grounder=grounder)),
        ):
            workdir = make_worktree(f"{key.lower()}-{arm}")
            try:
                results.append(await run_arm(arm, key, spec, adapter, workdir))
            except (CodegenError, LLMError) as exc:
                # One bad model emission shouldn't abort the sample — score it
                # as a failed run and keep going.
                print(f"  ABORTED: {exc}")
                results.append(
                    {
                        "ticket": key,
                        "arm": arm,
                        "tests_pass": False,
                        "refines": 0,
                        "fit": False,
                        "accepted": False,
                    }
                )
            finally:
                drop_worktree(workdir)

    print("\n=== acceptance summary ===")
    print(f"  {'ticket':<14} {'arm':<11} {'tests':<6} {'refines':<8} {'fit':<5} mergeable")
    for r in results:
        print(
            f"  {r['ticket']:<14} {r['arm']:<11} "
            f"{'PASS' if r['tests_pass'] else 'FAIL':<6} {r['refines']:<8} "
            f"{'yes' if r['fit'] else 'no':<5} {'YES' if r['accepted'] else 'NO'}"
        )
    for arm in ("ungrounded", "grounded"):
        rows = [r for r in results if r["arm"] == arm]
        accepted = sum(1 for r in rows if r["accepted"])
        print(f"\n  {arm} acceptance rate: {accepted}/{len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
