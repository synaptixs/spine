# Design: persona + skill system — curated skills, multi-role, provider-neutral

**Status:** Proposed (spec only — no implementation). The next strategic bet after
multi-language codegen: turn the credible *software-engineer* persona into a **governed,
provider-neutral host for multiple SWE-org personas** (software engineer, QA, product/BA,
project manager, release/deploy, support) — assembled largely from **already-curated skills**
(Claude Agent Skills, Claude subagents, Codex/AGENTS-style agents) rather than hand-authored
prompts. Works across **Codex and Claude** because the orchestrator is already model-neutral
(litellm) and MCP-native.

**Thesis:** the product is "a software engineer (and, in time, a software *team*) you delegate
to." Engineering judgment — not UI — is the moat. Curated skills are the cheapest path to that
judgment; the orchestrator's value is the **governance, composition, evals, and workflow
handoffs** around them, not authoring the Nth "write good tests" prompt.

## The core insight
A **persona = a governed bundle of {role + skills + tools + policies + model + workflow slot}.**
Almost every part already exists:

| Persona ingredient | Already in the codebase | File |
|---|---|---|
| Versioned, published, deprecatable spec | `AgentTemplate` / `AgentSpec` (registry) | `registry/agent_template.py` |
| Model (any provider via litellm) | `AgentSpec.model` | `registry/agent_template.py:41` |
| Tool allow-list | `AgentSpec.allowed_tools` | `:37` |
| Policies (governance) | `AgentSpec.policies` | `:39` |
| Eval gating (with `min_score`) | `AgentSpec.evals` / `EvalReference` | `:25-43` |
| Stated limits | `AgentSpec.known_limitations` | `:40` |
| Skill as a first-class capability kind | `CapabilityKind.SKILL` | `catalog/models.py:23` |
| Profile-driven, gate-ready selection | `plan_capabilities` → `CapabilityPlan` | `catalog/planner.py`, `catalog/models.py:69` |
| Governed external tools (stdio/HTTP/OAuth) | MCP registry + allow-list + write-gate | `mcp/registry.py`, `agentic/mcp_tools.py` |
| Phased workflow with handoffs | Block-C / Temporal SDLC pipeline | `sdlc/`, `temporal/` |
| Outcome measurement | evals (standout Bet 1) | (evals harness) |

**What's missing is the connective tissue, not a new engine:** (1) a **Skill artifact** that
curated external skills import *into*; (2) **importers** per ecosystem; (3) binding personas to
the `AgentTemplate` registry with a **role** + **skills** + **workflow slot**; (4) a
**provider-projection** layer; (5) **handoff contracts** between personas.

## Where skills stand today (the gap)
A "skill" today is a one-line prompt fragment in `_SKILL_PROMPTS`
(`sdlc/codegen.py`) — `python-conventions`, `java-conventions`, `typescript-conventions`,
`repo-pkg-grounding` — appended to the agentic implement prompt when the capability plan selects
it. That's a *hint*, not a skill: no procedure, no tools, no verification, no provenance, no
eval. The catalog's `Capability(kind=SKILL)` is the right home, but its `payload` is currently
just internal prompt wiring. This spec makes skills first-class and importable.

## Design

### 1. The Skill artifact (the normalization target)
Define a `Skill` as an **extension of `Capability(kind=SKILL)`** (reuse the catalog, planner, and
gate — do not fork a parallel system). The `payload` gains a structured schema:

- `provenance` — origin ecosystem (`claude-skill` | `claude-subagent` | `codex-agent` |
  `native`), source URL/ref, **pinned version/digest**, license.
- `trigger` — already expressible as the existing `CapabilitySelector` (languages / task_types /
  requires_db); extend with spec/PKG signals as needed.
- `guidance` — the procedure (the portable part of a curated skill: its SKILL.md / prompt body).
- `tools` — references to **governed tool contracts / MCP allow-list entries** (NOT the source
  ecosystem's tool bindings — those are re-bound, see §"Honest limits").
- `verification` — optional hook the skill asserts before declaring itself satisfied (e.g. a
  security skill runs semgrep; a test-strategy skill checks coverage of acceptance criteria).
- `evals` — `EvalReference`s with `min_score`; the skill cannot be published/selected until it
  clears them (reuse `AgentSpec.evals` semantics).
- `provider_notes` — known fidelity differences when projected onto Claude vs Codex.

### 2. Importers (per ecosystem)
Small adapters that translate a curated skill into the Skill artifact above:
- **Claude Agent Skills** (`SKILL.md` + frontmatter + progressive-disclosure files + bundled
  tools) → guidance from the body, trigger from frontmatter, tools **re-bound** to governed
  contracts, progressive-disclosure files referenced as attachments.
- **Claude subagents** (`.claude/agents/*.md`) → these are *role-shaped* → seed a **persona**
  (role + system prompt + tool allow-list), not just a skill.
- **Codex / AGENTS.md / OpenAI agent prompts** → guidance + tool hints → Skill artifact.

**What ports cleanly is the knowledge; what doesn't is the invocation/tooling contract** — the
importer normalizes the prose and re-binds tools to the orchestrator's governed layer. Importers
are deterministic (parse + map), and each import is **vetted, pinned, and eval-gated** before a
persona may use it (supply-chain discipline — curated ≠ vetted-for-your-pipeline).

### 3. Persona as an `AgentTemplate` binding
A persona is an `AgentTemplate` whose `AgentSpec` references: a **role** (system-prompt identity),
a list of **skills** (capability ids), `allowed_tools`, `policies`, `model`, `evals` (with
`min_score`), `known_limitations`, and a new **`workflow_slot`** (which SDLC phase / handoff it
occupies). Add `skills: list[ResourceId]`, `role: str`, and `workflow_slot: str` to `AgentSpec`
(extra-forbid model → an additive, validated change). Personas inherit the registry's existing
**versioning, publish/deprecate lifecycle, and policy/eval gating** for free.

Target personas (sequenced, not all at once): **software engineer** (have it), **QA engineer**
(closest — reuses test/refine), **product/BA** (reuses intake), **project manager**,
**release/deploy engineer**, **support engineer**.

### 4. Provider-neutral host + projection
The persona spec stays neutral (skills as prose + governed tool contracts + a litellm `model`
id). A **projection layer** renders a persona for the chosen runtime: Claude (skills/tool-use,
optionally the native Skills mechanism) vs Codex/OpenAI (prompt + tool schema). Provider
divergence is handled *here*, not in the persona definition — so the same persona runs on either.
This is the differentiated position: **BYO model (Codex or Claude); we are the governed persona +
skill layer.**

### 5. Selection, governance, and handoffs
- **Selection:** `plan_capabilities` already picks skills by profile; extend it to also resolve a
  **persona** per workflow slot, producing a gate-ready plan with rationale (reuse
  `CapabilityPlan.summary_lines` + the existing approval gate + audit).
- **Governance:** imported skills + personas flow through the existing policy/approval/write-gate
  spine; nothing ungoverned enters a run.
- **Handoffs:** define the **artifact contracts** passed between personas (PM → spec → SWE →
  diff+tests → QA → verdict → release → PR/merge → support). These map onto Block-C/Temporal
  phases. *This is the real work* — personas without handoff contracts are prompt costumes.

### 6. Eval-gating (the discipline that prevents prompt-bloat)
Every imported skill and every persona must clear its `evals` `min_score` before publish.
Role-specific eval suites (a QA persona's evals ≠ a SWE persona's) are part of each persona's
definition of done. **A skill that doesn't move a metric doesn't ship** — this is what keeps a
"skill library" from becoming attention-diluting prompt sprawl.

## Phasing (each shippable; spec is the only deliverable now)
- **Phase 0 — Skill schema + provenance/pinning** (deterministic, no LLM): extend
  `Capability(kind=SKILL)` payload with the §1 schema; **migrate the existing 4 `_SKILL_PROMPTS`
  into it with zero behavior change** — proves the normalization target before any importer.
- **Phase 1 — First importer (Claude Agent Skills) + vetting/eval gate:** import 2–3 curated SWE
  skills (test-strategy, security-aware coding, convention digest), measure against existing
  evals, keep the winners. **Machinery DONE (uncommitted):** `catalog/skill_import.py`
  (`import_claude_skill` — SKILL.md frontmatter+body → normalized `Skill`; body→guidance,
  provenance with sha256 pin + license, declared tools *noted not bound*, dependency-free flat
  frontmatter parser) and `catalog/vetting.py` (`evaluate_vetting`/`approved_skills` — native
  trusted, imported skills UNVETTED until they declare and clear `SkillEval` gates; pure function
  over a `{eval_id: score}` map from a `Scorecard`). Fully unit-tested. **Still open (the
  "measure the winners" step — needs source/license decisions + a live eval run):** choosing the
  2–3 real curated skills, vendoring them under a pinned source, and running them through the
  `evals` harness to populate scores and approve the winners.
- **Phase 2 — Persona binding + the SWE persona:** add `role`/`skills`/`workflow_slot` to
  `AgentSpec`; re-express today's codegen as a published SWE persona (parity first, then improve).
  Run provider-neutral on Claude; smoke on Codex/OpenAI. **Binding model DONE (uncommitted):**
  `AgentSpec` gained `role`/`skills` (`list[str]` — skill ids are hyphenated, not `ResourceId`)/
  `workflow_slot`, all defaulted (non-persona templates unaffected; `spec_json` is JSON so the add
  is backward-compatible). `personas/software_engineer.py` defines `SOFTWARE_ENGINEER` (a real
  `AgentTemplate`, slot `implement`, referencing the proven native skills). `personas/binding.py`
  `resolve_persona_skills`/`persona_skill_guidance` resolve a persona's skill ids **through the
  vetting gate** — proven by a test where an imported skill is excluded until it clears its eval
  bar, then auto-adopted with no edit to the persona. Fully unit-tested. **Run-driver (2b) DONE
  (uncommitted):** the codegen adapter takes an optional `persona` (+ `skill_scores`); when set,
  `_agentic_system` leads with the persona's `role` and resolves skill guidance via
  `persona_guidance_for_selection` — the **persona-endorsed ∩ plan-selected ∩ vetting-approved**
  set, in persona order. `worker.py` wires `SOFTWARE_ENGINEER` into the **agentic** adapter only
  (`SDLC_AGENTIC_CODEGEN=1`); the single-shot default path is untouched (`persona=None` → prior
  behavior exactly). **Single-shot path DONE:** the conditioning was factored into a shared
  `_condition_system(base, skills)` and applied to the single-shot `implement` too; `feature_runner`
  now builds the project profile + capability plan and drives the `sdlc feature` CLI run as the SWE
  persona (role + profile-selected, vetting-gated skills). **Provider-neutral smoke DONE
  (live):** a `sdlc feature --safe` run on **gpt-4o** (OpenAI, via `SDLC_CODEGEN_MODEL`) drove the
  SWE persona to a green result — same persona definition, non-Claude model. **Measurement hook DONE + first
  A/B run (live):** `scripts/codegen_benchmark.py` honors `EVAL_SKILL=<id>` (injects a candidate's
  guidance into the implement prompt) for controlled baseline-vs-skill-on runs. A first bounded A/B
  (`convention-digest`, 2 edit tickets, sonnet) was **inconclusive — a ceiling effect**: baseline
  already 2/2 accepted, skill-on 2/2, no acceptance delta, +~1.5% cost. Per "a skill that doesn't
  move a metric doesn't ship," **none of the 3 candidates were promoted to `_SEED`** — a credible
  verdict needs a signal-bearing eval (harder/under-specified tickets, more repeats, conditioning
  the phase the skill targets). **Open:** that designed measurement; conditioning author_tests/refine
  (so test-strategy reaches the test phase); the Codex/OpenAI *agentic*-path smoke.
- **Phase 3 — Second importer (Codex/AGENTS) + projection layer:** prove the *same* SWE persona
  runs on both Claude and Codex.
- **Phase 4 — QA persona + first real handoff:** QA reuses test/refine; define + verify the
  SWE→QA artifact contract.
- **Phase 5 — PM/BA persona + multi-persona handoffs:** PM reuses intake; then project-manager,
  release/deploy, support as the org chart fills in.

## Decisions to confirm
1. **Extend `Capability(kind=SKILL)`** (recommended — reuse catalog/planner/gate) vs a new Skill
   resource type.
2. **Bind personas to the existing `AgentTemplate` registry** (recommended — versioning, policies,
   evals, allowed_tools already there) vs a new `Persona` resource. Recommended additive fields:
   `AgentSpec.skills`, `AgentSpec.role`, `AgentSpec.workflow_slot`.
3. **First importer = Claude Agent Skills** (recommended) vs subagents vs Codex.
4. **First persona = software engineer** (recommended — live-proven codegen to anchor parity).
5. **Provider scope for v1 = Claude first, Codex projection in Phase 3** (recommended) vs both at
   once.
6. **Sourcing/trust policy:** which curated catalogs are allowed sources, the pinning/digest
   policy, license handling, and the minimum eval-score bar for an import to be usable.

## Honest risks / limits
- **No universal skill format.** Claude Skills, Claude subagents, and Codex/OpenAI agents are
  different runtime contracts. Knowledge ports; **tooling/invocation does not** — importers carry
  translation loss and must re-bind tools to governed contracts. The projection layer (§4) is
  non-trivial and may degrade fidelity on one provider.
- **Supply chain.** External skills must be vetted, pinned, license-checked, and eval-gated;
  "curated" is not "safe for your governed pipeline."
- **Persona theater.** Without real handoff artifacts and role-specific tools, personas collapse
  into "same model, different system prompt." Gate persona work on handoff contracts.
- **Scope.** Six personas is a *program*, not a feature. Sequence strictly; prove the mechanism on
  the SWE persona before spreading across the org chart.
- **Eval coverage is the ceiling.** Persona/skill value claims are only as trustworthy as the
  role-specific eval suites behind them.
- **Out of scope (adjacent):** the *identity* half of the persona (consistent name/voice in
  commits/PRs/Slack) — competence first, identity later.

## First step
**Phase 0:** define the Skill artifact as an extension of `Capability(kind=SKILL)` (provenance +
pinning + eval refs + governed tool refs), and migrate the existing four `_SKILL_PROMPTS` into it
with **zero behavior change** — establishing the normalization target that every importer and
persona will build on, fully unit-testable and LLM-free.
