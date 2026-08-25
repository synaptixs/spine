# Project constitution — durable rules a run is checked against

**Status:** **Spec only. Nothing built.** Phase 0 is a **blocking trigger probe** and its
result may close this spec unbuilt, which is an acceptable outcome.
**Written:** 2026-08-25 against 3.22.0. **Owner:** _unassigned_.
**Origin:** the one idea worth taking from
[`spec-kit-integration-analysis.md`](spec-kit-integration-analysis.md), where the tool itself
was declined.

---

## The idea, and why it is not just prompt text

Spec-kit ships `/speckit.constitution`: durable project principles as an artifact the agent is
told to honour. It is a good idea trapped in a system that cannot enforce it — the principles
go into the prompt, and the only thing checking that the model followed them is the same model.
A violated principle is indistinguishable from an honoured one.

**Spine has a graph, so a well-chosen principle can be *checked* instead of *hoped for*.**
That is the entire proposal:

> A repo carries `.spine/constitution.yaml`. Its **structured rules** are evaluated as
> deterministic PKG queries at `validity` and at `review`. Its **prose principles** go into
> model context and are labelled advisory. A rule that cannot be expressed as a query does not
> become a weaker rule; it becomes prose.

Nothing here is a new subsystem. `.spine/workflows/` already establishes repo-carried
configuration (GraphIR Phase 3), `validity.assess()` already refuses tickets, and
`design_validator.validate_design()` already refuses references the repository does not have.
A constitution extends that last one from *"names something that does not exist"* to *"names
something that exists and must not be touched this way."*

## The governing constraint: this must not be Phase 4 again

GraphIR Phase 4's bounded replan was built, six tests passed, and it was **reverted as
unreachable**. Probing the trigger rather than the mechanism showed `validate_design` refused
**0 of 6** real specs. Every test had `monkeypatch`ed the producer to manufacture the failure,
so the mechanism was verified and the trigger never was — internally perfect, externally inert.

A rule engine has exactly that shape. You ship a checker, write tests that feed it a violation,
and then one of two things happens quietly: **nobody writes rules**, or **people write rules
that never fire**. Either way the feature reports no violations, and "no violations found" reads
exactly like "everything is fine."

**Therefore the exit criterion for this programme is a trigger criterion, not a mechanism one.**
Stated once, in full:

> **Three rules must refuse three things that a real repository would otherwise have built,
> found on repositories nobody wrote the rules for.** Not fixtures. Not `monkeypatch`. Not this
> repository. If that cannot be demonstrated in Phase 0, the spec closes unbuilt.

## Phase 0 — the trigger probe (blocking)

### What was already probed, and the uncomfortable result

This repository's own invariants are a constitution already, written in prose:
[`CLAUDE.md`](../../CLAUDE.md) lists **eight**. Three are expressible as PKG queries today:

| # | Invariant | As a query |
|---|---|---|
| 2 | `understand` / `state` are deterministic and no-LLM | no module in the transitive `IMPORTS` closure of those entry points may be an LLM module |
| 3 | Layout is computed and seeded, never force-directed | no visual surface may import a randomness or force-layout module |
| 4 | The web UI has no build step | no bundler, `node_modules`, or npm dependency under `registry/api/web/` |

Invariant 2's query was **written and run** against 3.22.0:

```
modules transitively reachable from understand/state : 210
LLM modules in that closure                          : 0
```

It works, it is cheap, and it holds. Then the Phase 4 question was asked of all three — *has
this ever fired?* — against the full git history:

| Rule | Historical violations in this repo |
|---|---|
| LLM in the deterministic closure | **0** |
| Randomness in a visual surface | **0** |
| A bundler in the web UI | **0** |

**Zero, for all three, across the project's entire history.** That is the finding, and it is
recorded here rather than buried because it is the single most important input to whether this
gets built.

### What that result does and does not mean

It would be easy, and wrong, to conclude the idea is dead. It would be equally easy, and also
wrong, to wave it away. The honest reading distinguishes two things Phase 4 did not:

**Phase 4's trigger could not fire *in principle*.** `_fallback_design`'s three sources cannot
fabricate — stated paths are filesystem-filtered and the other two come from the graph — so no
real input could produce the failure the replanner existed to repair. The mechanism was
structurally inert.

**These triggers can fire; they have not, *here*.** The rules describe real violations that
real codebases commit constantly. What they do not describe is anything this repository's
maintainers were ever going to do, because they wrote the invariants and know them. **Measuring
a smoke alarm in a fireproof room tells you about the room.**

But "it will fire on customer repos" is precisely the untested assumption that let Phase 4 ship.
So Phase 0 does not get to assume it either.

### What Phase 0 must actually establish

**On repositories nobody wrote the rules for.** Pick 5–8 public codebases with a *documented*
architecture — a stated layering, a stated dependency direction, an ADR forbidding something —
pinned by commit SHA. Write the rules **from their documentation**, never from their code, then
run them.

Two numbers come out, and both matter:

| Number | What it decides |
|---|---|
| **Violations found** | If ~0 across all repos, the rules describe nothing real → **close the spec** |
| **False positives** | If high, the rules cannot gate anything without being switched off → **close or redesign** |

A third check, cheaper and sharper: for any violation found, **use `git log -S` to find when it
landed**, and read that commit. If the rule would have blocked a change the project clearly
wanted, the rule is wrong, not the code.

**Exit:** a written probe result with per-repo counts, hand-verified examples, and an explicit
recommendation to build or close. **Either recommendation is a success.** Phase 4's real cost
was not being wrong; it was building first and probing second.

## Phases 1–3, conditional on Phase 0

Written down so the shape is known, **not to be started before Phase 0 reports.**

| Phase | Work | Exit |
|---|---|---|
| **1 — Three rule kinds, no DSL** | `.spine/constitution.yaml` with exactly three typed rule kinds: import boundary, layer direction, endpoint reachability. Evaluated in `validity`. No expression language, no plugin hooks | The three rules from Phase 0 refuse the three real violations Phase 0 found, and pass on everything else in those repos |
| **2 — The prose half** | A labelled advisory section passed to model context. Explicitly **not** a gate | A run's Evidence shows which principles were advisory and which were checked |
| **3 — Review-time evaluation** | Same rules against the diff at `review`, alongside tests and preflight | A violating diff is refused before a human reads it |

**No DSL, in Phase 1 and preferably ever.** A general expression language is a second, worse
query engine over facts the PKG already holds, and it is how a config format becomes a program
nobody can reason about. Three named rule kinds with typed fields; add a fourth only when a
real repository needs it and a probe shows it fires.

## Invariants this must not break

- **Rule evaluation is deterministic and no-LLM.** A rule adjudicated by a model is prose with
  extra cost, and it puts a model call in the one path that currently has none.
- **Prose and rules never blur.** Every entry is *checked* or *advisory*, and the artifact says
  which. A principle that quietly downgrades from gate to suggestion is worse than one that was
  always a suggestion.
- **A rule refusal must name a `file:line`.** Same standard as an acceptance criterion: if the
  violation cannot be pointed at, it cannot be argued with.
- **Bound honestly.** "0 violations" must be distinguishable from "no rules configured" and
  from "rules present but not evaluated" — the `status` discipline the invention oracle now
  uses (`measured` / `not-applicable` / `unwalked`), for exactly the same reason.
- **The base install stays stdlib-only.** Rule evaluation is PKG queries; it adds no dependency.

## Non-goals

- **Adopting spec-kit, or its file format.** The tool was evaluated and declined; this takes one
  idea from it and nothing else.
- **A general policy engine.** Not OPA, not Rego, not a plugin API.
- **Replacing `CLAUDE.md`.** Prose guidance for humans and agents reading the repo stays where
  it is. This is about what a *run* is checked against.
- **Style and quality rules.** `ruff` and `mypy` own those, they are already in preflight, and a
  second opinion about line length helps nobody.
- **Enforcing rules on this repository.** Spine's own invariants are held by CI, review and the
  accuracy gate. If the constitution ships, adopting it here is a separate decision.

## Open questions

1. **Does anything fire?** Phase 0. Everything else is downstream of this.
2. **Who writes the rules?** A team that will not maintain a YAML file will not maintain a
   constitution, and the feature's whole value assumes they do. Worth asking a real adopter
   before Phase 1, not after.
3. **Refuse or warn on first adoption?** A repo turning this on will likely have existing
   violations. A gate that fails immediately gets switched off — the same reasoning that kept
   `invention` ungated until every front-end could pass it. Leaning: report-only until a repo
   opts into gating, per rule.
4. **Where does a rule refusal surface?** `validity` parks the run, which is right for a ticket
   that cannot be built. Less clear for a rule the ticket does not violate but the *design*
   would.

## What would make this obviously worth building

One sentence, so it is easy to check later: **a real team writes three rules from their own
architecture doc, and Spine parks a run that would have violated one of them — before any code
was generated.** Until that has happened once, this is an idea with a good argument and no
evidence, and it is recorded that way on purpose.
