"""MCP prompts: the "which tool, in which order" workflow, through the protocol.

The ``understand-codebase`` skill tells a Claude Code host *when* to reach for each tool and
in what sequence. A skill is a Claude Code construct; Codex, Claude Desktop and claude.ai
never see it. MCP **prompts** are the protocol's own way to ship that guidance: a host lists
them, fills the arguments in its own UI, and the user gets the same ordered workflow the
skill describes.

The text lives here rather than in the skill file because the skill directory is not in the
wheel — a PyPI install has no skill to read. ``tests/plugin/test_manifests.py`` holds the two
to the same tools, so they cannot drift apart.

Each prompt is a plain function returning the user-turn text, registered by
``plugin.server.build_server`` through ``_PROMPTS``. Plain so it is testable without the
``mcp`` extra. The tools a prompt sequences are listed in ``PROMPT_TOOLS`` for the parity
test, next to the prompt, so adding a step means adding it in both places on one screen.
"""

from __future__ import annotations

from collections.abc import Callable

#: The tools each prompt walks a host through, in order. Read by the parity test.
PROMPT_TOOLS: dict[str, tuple[str, ...]] = {
    "orient": ("map_repo", "read_memory_bank"),
    "investigate-ticket": ("investigate", "blast_radius", "regression_gaps"),
    "triage-bug": ("localize", "regression_gaps", "root_cause"),
    "plan-then-approve": ("sdlc_plan", "sdlc_approve", "sdlc_feature"),
    "whats-waiting-on-me": ("registry_approvals", "registry_trace", "registry_decide"),
}

_CITE = (
    "Every tool returns `file:line` provenance and a `markdown` field — ground the answer in "
    "those and show them, rather than paraphrasing."
)


def _repo_clause(repo_path: str | None) -> str:
    return f"repo_path=`{repo_path}`" if repo_path else "the current repository (omit `repo_path`)"


def orient(repo_path: str | None = None) -> str:
    """Get oriented in a repository you don't know — before answering structural questions
    or planning a change. One `map_repo` call beats many greps."""
    return "\n".join(
        [
            f"Orient me in {_repo_clause(repo_path)} using Spine's read-only tools.",
            "",
            "1. Call `map_repo` first: languages, components, call hotspots, test-coverage gaps and "
            "prioritized recommendations. Do not grep or guess before it answers.",
            "2. Then call `read_memory_bank` with no section. If a committed `episteme/` bank exists, "
            "read its `architecture` and `conventions` sections; if it does not, say so — "
            "`understand_repo` can build one — and continue from the map.",
            "3. Summarize: what the system is, its biggest areas, where the risk sits (untested areas, "
            "hotspots), and the two or three things a newcomer should read first.",
            "",
            _CITE,
        ]
    )


def investigate_ticket(title: str, problem: str = "", repo_path: str | None = None) -> str:
    """Find where a ticket lands in the code and what changing it would affect — the
    research an engineer does before touching anything."""
    return "\n".join(
        [
            f"Research this ticket against {_repo_clause(repo_path)} before any code is written.",
            "",
            f"Title: {title}",
            f"Problem: {problem or '(none given — work from the title)'}",
            "",
            "1. Call `investigate` with the title and problem: the real symbols to start from, with "
            "caller counts and owning areas. If the answer carries `multi_repo_available`, stop and "
            "re-run with the `repos=` path it names — the change may land in more than one service.",
            "2. For each landing symbol worth touching, call `blast_radius` — direct callers plus the "
            "cross-layer set a change ripples into.",
            "3. Call `regression_gaps` for the same symbols: the production code a change reaches that "
            "no test covers is what could break silently.",
            "4. Report: where the work lands, what depends on it, what is untested, and a scoped "
            "approach. Say what you did not find. Change nothing.",
            "",
            _CITE,
        ]
    )


def triage_bug(bug: str, repo_path: str | None = None) -> str:
    """Trace → fault site → coverage → hypotheses. Stops at analysis; never edits."""
    return "\n".join(
        [
            f"Triage this bug against {_repo_clause(repo_path)}. Analysis only — do not change code.",
            "",
            "Bug report / trace:",
            bug,
            "",
            "1. If the report contains a stack trace or traceback, call `localize` with it: each frame "
            "resolved to a repo symbol, and the likely fault site with its callers. If it is a plain "
            "description, skip to step 3.",
            "2. Call `regression_gaps` with the same trace: the coverage around the fault site, so the "
            "fix's blast radius is known before it is written.",
            "3. Call `root_cause` with the report: ranked hypotheses with evidence, the regression "
            "surface a fix must cover, and a scoped fix approach. Add `use_llm=true` only if the "
            "deterministic hypotheses are thin and a model is configured.",
            "4. Report the fault site, the leading hypothesis and what would confirm it, and the tests "
            "a fix must add.",
            "",
            _CITE,
        ]
    )


def plan_then_approve(title: str, summary: str = "", criteria: str = "", repo_path: str | None = None) -> str:
    """The tiers, in order: a build document (no model, no credentials), a human decision on
    it, and only then the gated build."""
    return "\n".join(
        [
            f"Plan this change against {_repo_clause(repo_path)}, get the plan approved, and only "
            "then build it. Work down the tiers; never start at the build.",
            "",
            f"Title: {title}",
            f"Summary: {summary or '(none given)'}",
            "Acceptance criteria: "
            + (criteria or "(none given — draft them from the title and summary, and say so)"),
            "",
            "1. Draft the spec object: `intent_id`, `title`, `summary`, `acceptance_criteria` (a list, "
            "at least one). Call `sdlc_plan` with it. This spends nothing and needs no credentials: "
            "the twelve-section build document is rendered from the graph, git and the tree. Show "
            "the human the document — the blast radius, the files, the cost and confidence sections "
            "in particular.",
            "2. Do not approve it yourself. When the human has read it and decided, call "
            "`sdlc_approve` with their name in `decided_by` (or `reject=true`). The approval binds "
            "to a digest of the document, so a plan edited afterwards reads as stale.",
            "3. Only with a current approval, call `sdlc_feature` for the intent. Keep `live=false` "
            "unless the human has explicitly authorized external writes — then, and only then, "
            "pass `live=true` together with `confirm=true`.",
            "",
            _CITE,
        ]
    )


def whats_waiting_on_me() -> str:
    """The operator's question, answered from the registry: what is running, what is
    waiting on a decision, and deciding it."""
    return "\n".join(
        [
            "Tell me what is waiting on me at the Spine registry, and help me decide.",
            "",
            "1. Call `registry_approvals`: the gates waiting on a human, latest first, with risk and "
            "the run each belongs to. If the registry is down the tool says so with a hint — relay "
            "it and stop.",
            "2. For each approval I want to look at, call `registry_trace` with its run id: the newest "
            "audit entries, the verifier outcome, and the replan count. Summarize what the run did "
            "to reach this gate.",
            "3. Only when I say approve, reject or modify, call `registry_decide` with the approval id, "
            "the action and my rationale. A rejection ends the run — confirm that with me first.",
            "",
            "Use `registry_runs` if I ask what is running overall.",
        ]
    )


#: What ``build_server`` registers, with the name a host sees.
_PROMPTS: tuple[tuple[str, Callable[..., str]], ...] = (
    ("orient", orient),
    ("investigate-ticket", investigate_ticket),
    ("triage-bug", triage_bug),
    ("plan-then-approve", plan_then_approve),
    ("whats-waiting-on-me", whats_waiting_on_me),
)

__all__ = [
    "PROMPT_TOOLS",
    "investigate_ticket",
    "orient",
    "plan_then_approve",
    "triage_bug",
    "whats_waiting_on_me",
]
