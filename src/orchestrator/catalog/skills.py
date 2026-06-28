"""First-class Skill artifacts (persona + skill system — Phase 0).

A ``Skill`` is the orchestrator's normalized representation of an engineering
capability's *procedure* — the portable knowledge a curated skill carries. It is
the **import target** every ecosystem adapter (Claude Agent Skills, Claude
subagents, Codex agents) will normalize into; native skills (origin ``NATIVE``)
are the ones authored in-repo.

A ``Skill`` pairs with a catalog ``Capability(kind=SKILL)`` **by id**: the planner
selects the Capability by project profile, and the selected id resolves to the
Skill's ``guidance`` (and, later, its governed ``tools``, ``verification`` hook,
and ``evals`` gates). Binding the Skill into ``Capability`` itself is a later
refinement; Phase 0 keeps an id-keyed registry so the change is additive.

Phase 0 establishes this schema and migrates the four prompt fragments that lived
in ``sdlc.codegen._SKILL_PROMPTS`` into native Skills with **zero behavior
change**: ``skill_guidance()`` returns exactly the id→fragment mapping codegen
used before. The provenance / tools / verification / evals fields are defined now
(empty for natives) so importers and persona binding have a stable target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkillOrigin(str, Enum):
    """Where a skill came from — its ecosystem of origin."""

    NATIVE = "native"  # authored in-repo
    CLAUDE_SKILL = "claude-skill"  # a Claude Agent Skill (SKILL.md)
    CLAUDE_SUBAGENT = "claude-subagent"  # a Claude subagent definition
    CODEX_AGENT = "codex-agent"  # a Codex / AGENTS-style agent


@dataclass(frozen=True)
class SkillProvenance:
    """Origin + pin of an (often imported) skill — the supply-chain record.

    Native skills leave ``source``/``pin``/``license`` empty; importers MUST
    populate them so an imported skill is traceable, pinned, and license-checked.
    """

    origin: SkillOrigin = SkillOrigin.NATIVE
    source: str = ""  # URL / repo ref the skill was imported from
    pin: str = ""  # version or content digest the import is pinned to
    license: str = ""  # SPDX id or note


@dataclass(frozen=True)
class SkillEval:
    """An eval gate a skill must clear before it may be selected / published.

    Mirrors the registry ``EvalReference`` semantics (id + ``min_score`` in
    ``[0, 1]``); kept as a light dataclass so the catalog stays pydantic-free.
    """

    id: str
    min_score: float = 0.0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SkillEval.id must be non-empty")
        if not 0.0 <= self.min_score <= 1.0:
            raise ValueError(f"SkillEval.min_score must be in [0, 1], got {self.min_score}")


# The codegen phases a skill may condition. A skill's guidance is applied only
# to the phase(s) it declares — so test-strategy reaches the phase that writes
# the tests, not implement (the bug that made the first A/B structurally blind).
PHASES: tuple[str, ...] = ("implement", "author_tests", "refine")


@dataclass(frozen=True)
class Skill:
    """A normalized engineering skill — native unit and importer target."""

    id: str
    guidance: str  # the portable procedure (a curated skill's body / SKILL.md)
    provenance: SkillProvenance = field(default_factory=SkillProvenance)
    tools: tuple[str, ...] = ()  # governed tool-contract / MCP allow ids (re-bound on import)
    verification: str | None = None  # optional assertion hook the skill must pass
    evals: tuple[SkillEval, ...] = ()
    provider_notes: str = ""  # known Claude-vs-Codex fidelity differences
    # Which codegen phase(s) this skill's guidance conditions. Default
    # ``("implement",)`` preserves today's behavior for the conventions/grounding
    # skills (they only ever shaped implement); a test-oriented skill declares
    # ``author_tests`` so it actually reaches the suite it's meant to improve.
    phases: tuple[str, ...] = ("implement",)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Skill.id must be non-empty")
        if not self.guidance:
            raise ValueError(f"Skill {self.id!r} must carry non-empty guidance")
        if not self.phases:
            raise ValueError(f"Skill {self.id!r} must declare at least one phase")
        bad = [p for p in self.phases if p not in PHASES]
        if bad:
            raise ValueError(f"Skill {self.id!r} has unknown phase(s) {bad}; valid: {list(PHASES)}")


# Native skills.
#
# The first four were migrated verbatim from ``sdlc.codegen._SKILL_PROMPTS`` — their
# guidance MUST stay byte-identical (a test pins them). They are wired into the
# capability catalog (`_SEED`), so the planner can select them.
#
# The remaining three are authored SWE candidates (persona+skill Phase 1). They are
# defined and available, but are NOT yet in the catalog `_SEED` — so the planner
# never selects them and runs are unchanged — pending the eval measurement that
# decides whether they earn catalog inclusion ("a skill that doesn't move a metric
# doesn't ship"). They are also the proving ground for the persona binding.
NATIVE_SKILLS: tuple[Skill, ...] = (
    Skill(
        "python-conventions",
        "Match the repo's Python conventions — naming, import order, type annotations.",
    ),
    Skill(
        "java-conventions",
        "Match the repo's Java conventions and package layout.",
    ),
    Skill(
        "typescript-conventions",
        "Match the repo's TypeScript conventions — layout, imports, strict types.",
    ),
    Skill(
        "csharp-conventions",
        "Match the repo's C# conventions — namespaces, file/type layout, nullable types.",
    ),
    Skill(
        "c-conventions",
        "Match the repo's C conventions — header/source split, include guards, naming.",
    ),
    Skill(
        "cpp-conventions",
        "Match the repo's C++ conventions — header/source split, RAII/ownership, namespaces.",
    ),
    Skill(
        "repo-pkg-grounding",
        "Reuse existing symbols — use the pkg_* tools to find them before writing code.",
    ),
    # --- SWE candidates (pending eval measurement before catalog inclusion) ---
    Skill(
        "test-strategy",
        "Cover every acceptance criterion with at least one assertion. Beyond the happy "
        "path, test error paths and boundary values (empty, zero, negative, max) and "
        "idempotency where it matters. Keep tests deterministic — no sleeps or real network.",
        # The suite is written in author_tests and patched in refine — conditioning
        # implement (the historical default) never reaches the phase it targets.
        phases=("author_tests", "refine"),
    ),
    Skill(
        "security-aware-coding",
        "Validate and sanitize external input. Never interpolate untrusted data into "
        "shell, SQL, or HTML; use parameterized queries and the platform's safe APIs. "
        "Keep secrets out of code, logs, and error messages, and fail closed on auth checks.",
        phases=("implement", "refine"),
    ),
    Skill(
        "convention-digest",
        "Before writing, infer the repo's conventions from nearby code — naming, file "
        "layout, error handling, logging, and test style — and match them. Prefer reusing "
        "existing helpers and patterns over introducing new ones.",
        phases=("implement", "refine"),
    ),
)


def default_skills() -> tuple[Skill, ...]:
    """The built-in (native) skill set."""
    return NATIVE_SKILLS


def get_skill(skill_id: str) -> Skill | None:
    """The native skill with ``skill_id``, or ``None``."""
    return next((s for s in NATIVE_SKILLS if s.id == skill_id), None)


def skill_guidance() -> dict[str, str]:
    """``{skill_id: guidance}`` — the drop-in for codegen's former ``_SKILL_PROMPTS``."""
    return {s.id: s.guidance for s in NATIVE_SKILLS}


def skill_phases() -> dict[str, tuple[str, ...]]:
    """``{skill_id: phases}`` — which codegen phase(s) each native skill conditions."""
    return {s.id: s.phases for s in NATIVE_SKILLS}


__all__ = [
    "NATIVE_SKILLS",
    "PHASES",
    "Skill",
    "SkillEval",
    "SkillOrigin",
    "SkillProvenance",
    "default_skills",
    "get_skill",
    "skill_guidance",
    "skill_phases",
]
