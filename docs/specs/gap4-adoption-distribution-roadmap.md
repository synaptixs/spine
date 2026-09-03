# G4 — Adoption & distribution: friction, channels, proof

> **"G4" is a label, not a position in a queue.** It's gap #4 in the Graphify comparison. The specs
> are not ordered and do not run in sequence.

**Status:** **Phase 1 ✅ COMPLETE** (2026-08-19) · Phases 2–4 not started.
**Owner:** _unassigned_

## Phase 1 result — measured 2026-08-19

**The exit criterion was already met.** Cold start on a clean machine, fresh venv, against the
**published 3.20.0 wheel** with `pip --no-cache-dir` (the first measurement used a warm cache and
flattered us by 2s):

| Step | Time |
|---|---|
| `python3 -m venv` | 1.4s |
| `pip install --no-cache-dir synaptixs-spine` | **25.4s** |
| `orchestrator state <repo>` | **0.8s** |
| **nothing → grounded answer** | **≈28s** |

No API key, no `.env`, nothing written to the target repo. On
[`pallets/click`](https://github.com/pallets/click) the first line is a real answer: *"173 types
and 1722 functions across 22 components (78 files). Top priority: refactor 3 god-classes, e.g.
`Context` (60)."*

**Against the bar of "< 60 s, no API key, on a clean box": met, with half the budget spare.**

### What the audit found anyway

The number passing did not mean the path was good. Two things were wrong, both fixed in
[#215](https://github.com/synaptixs/spine/pull/215):

1. **The documented first command was the wrong one.** README opened with `orchestrator init &&
   orchestrator doctor`, which says configuration is required before anything works — it is not —
   and then `understand`, which writes 62 files into the repo of someone who has not decided to
   adopt anything (`?? episteme/` in their `git status`). The quick-start now opens with
   `state`, which writes nothing and needs no key; setup moved under *"when you want it to write
   code"*.
2. **`understand` printed a file listing instead of an answer.** Its last output was a JSON array
   of all 62 filenames. It now leads with the shape of what it found and names **three** files to
   read first; `--json` keeps the old output for anything that parses it.

Neither was visible from the timer. Both were found by running the path as a stranger would and
reading what came back, which is the part of a friction audit that a stopwatch cannot do.

### What was not re-measured, and why

The fixes changed *what a stranger sees*, not how long it takes — install time is PyPI's, and
`state` was already sub-second. So the ≈28s stands as the Phase 1 number. It will need re-taking
when the dependency set changes materially, not when the copy does.

**These fixes are on `develop` and ship in the next release.** The 28s above was measured against
the published 3.20.0 artifact, so it describes what a stranger gets *today* — the findings above
describe what they get after the next release.

## Before you start

**Prerequisites: none for Phases 1–2. You can start today.**

| What this track needs | State |
|---|---|
| Something installable to reduce friction on | ✅ Exists — PyPI package, Codex plugin, Claude skill, 8 credential-free comprehension tools |
| Anything from G2, G3, G5 or the watch-items | Nothing. No shared source files. |
| **Phase 3 only** — proof assets to publish | Waits on **G5 Phase 1** (exports) and **G6** (numbers), because the assets *are* those outputs. Phases 1–2 don't. |

Phase 1 measures what exists today, so it needs nothing from anyone.
**Gap:** #4 in [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md) rev. 3.

**One-liner:** ~93.7k★ and 15+ assistant integrations vs. our comparative obscurity. Shipping the
`/spine` skill (3.8.0) put us on the channel; it did not put us on the map. This track is about
**removing friction, widening channels, and having proof** — the parts that are engineering.

> **Scope honesty:** adoption is mostly go-to-market, and most of GTM is not an engineering
> roadmap. This spec covers only the levers a team here can actually pull. It will not, by itself,
> produce stars — and any phase that promises a number is lying.

---

## Why

The comparison's honest read: reach differs by orders of magnitude. We cannot out-market a YC-backed
OSS project with 93.7k stars by trying harder at the same game. What we *can* do is make the thing
trivially adoptable and back the claims with evidence — so the people who *do* find us convert
instead of bouncing.

## The three levers that are actually engineering

1. **Friction** — how long from "never heard of it" to "got a useful answer about my repo?"
2. **Channels** — how many places can someone install it from, without reading docs?
3. **Proof** — is there evidence the claims are true (G6), and something to show (G5 exports)?

## What already exists (reuse, don't rebuild)

| Piece | Gives us |
|---|---|
| `plugin/server.py` + `plugins/spine/` + `codex-marketplace/` | Codex plugin, Claude plugin, `understand-codebase` Agent Skill, stdio **and** remote-HTTP transports |
| 8 read-only comprehension tools | Work with **zero credentials** — the frictionless entry point |
| `synaptixs-spine` on PyPI + public mirror | Distribution rails already built and proven through 3.8.1 |
| `CLAUDE_GUIDE.md` / `CODEX_GUIDE.md` / `USER_GUIDE.md` | Install + walkthrough docs already current |

**The asset to lead with:** the comprehension tools need **no API key and write nothing**. That is
the lowest-commitment first touch we have — the equivalent of `/graphify`.

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Friction audit + 60-second path** ✅ | Time the real cold-start on a clean machine: install → point at a repo → first useful answer. Remove every step that isn't essential (config, credentials, docs-reading) from the **read-only** path. Fix whatever the audit finds. | ~4–6 d | A stranger gets a grounded answer about their own repo in **< 60 s**, with **no API key**, measured on a clean box |
| **2 — Channel expansion** | List in the **MCP registry** and any assistant marketplaces we're absent from; verify the plugin actually installs in each documented host (don't assume — install it). One-line install snippets per host, kept in CI-checked docs. | ~5–7 d | Installable from N hosts, each **verified by an actual install**, not by documentation |
| **3 — Proof assets** | Publish the G6 benchmark page; a short "what Spine tells you that grep doesn't" walkthrough on a well-known OSS repo; the G5 export as a shareable artifact. Public, reproducible, honest. | ~4–5 d | Linkable evidence for each headline claim |
| **4 — Measure** | Opt-in, privacy-respecting install/usage signal (or, if that's unacceptable, PyPI download + plugin-install counts). Decide *before* Phase 1 what "working" looks like numerically. | ~2–3 d | We can tell whether phases 1–3 moved anything, instead of guessing |

| **5 — Central adoption** ✅ 5a | An org adopting Spine across N repositories, rather than a developer installing it once. **Two deliverables with different audiences, and they should not be built together — see below.** **5a:** a reusable GitHub Actions workflow (`workflow_call`) a repo references in one line to run read-only comprehension on a pull request. **5b:** a container image of Spine itself, for an org *hosting* the governed pipeline. | 5a ~2–3 d · 5b ~5–8 d | 5a: a repository outside this one adds ≤ 5 lines and gets a grounded PR comment, with **no credentials**. 5b: `docker run` brings up the registry API against the documented dependencies |

**Phase 1 is the one that matters most.** Every channel added in Phase 2 multiplies whatever
conversion rate Phase 1 leaves us with; widening the funnel before fixing the leak wastes both.

### Phase 5, and why its two halves are not one piece of work

`STATE-OF-SPINE` §8 carried *"Deployment image + reusable CI workflow for central adoption"* as a
single row, and as *"not started. **Not a G4 phase** — it was recommended as though it were; it
needs adding to that spec before it can be scheduled."* This is that addition, and scoping it
split the row in two.

**They serve different people.** 5a is for a repository that wants Spine to *read* it in CI —
the credential-free comprehension path, no infrastructure at all. 5b is for an organisation
*hosting* the governed pipeline, which needs Postgres, Temporal and MinIO (already described by
`docker-compose.dev.yml`, which today brings up **only those dependencies — there is no image of
Spine**).

**Measured 2026-09-02, and it removes the reason to bundle them:** a cold
`pip install 'synaptixs-spine[languages]'` into a clean virtualenv takes **21 seconds**, with all
seven tree-sitter grammars arriving as prebuilt wheels. The obvious argument for a container in
CI — *"installing eight parsers is slow"* — is false. **5a needs no image**, which makes it a
small, independent piece of work rather than something blocked behind 5b.

**5a is the higher-impact half by G4's own logic.** The three levers are friction, channels and
proof; 5a is a *channel* that multiplies across every repository in an organisation at the cost
of five lines each, and it stays inside the read-only, credential-free invariant that makes the
first touch safe. 5b is a deployment concern for the write path, and carries the credential and
governance surface that comes with it.

**✅ 5a shipped 2026-09-02** — [`.github/workflows/spine-comprehension.yml`](../../.github/workflows/spine-comprehension.yml).
A calling repository adds **three lines**:

```yaml
jobs:
  spine:
    uses: synaptixs/spine/.github/workflows/spine-comprehension.yml@v3.30.0
```

and gets a pull-request comment saying where its own change lands in its own code, with
`file:line` and caller counts. **It declares no `secrets:`** — `investigate` is deterministic and
model-free, and the only token is the caller's own `GITHUB_TOKEN`, used to post the comment.
Permissions are `contents: read` and `pull-requests: write`; `comment: false` writes nothing at
all. The comment is **updated in place** rather than appended, because a bot that comments on
every push is a bot people mute, and a muted check is not a check.

One property is worth naming because it is the failure mode of reusable workflows generally: a
pull-request title is **attacker-controlled**, and interpolating `${{ github.event.pull_request.title }}`
inside a `run:` block lets a title containing `$(…)` execute. It is passed through `env:` and
quoted, and a test asserts no step interpolates event data into a shell.

**5b remains unscheduled: build it only against a named operator who wants to self-host.**
An image nobody has asked to run is a maintenance burden with a version number.

## Invariants you must not break

- **Read-only stays read-only and credential-free.** The frictionless path's whole value is that it
  is safe to try. Do not make comprehension tools require a key or acquire write scopes.
- **Telemetry is opt-in, anonymous, and documented** — or absent. We sell governance and audit;
  quietly phoning home would be self-refuting. If in doubt, don't.
- **No claim without evidence.** Every number in a proof asset traces to G6's reproducible harness.
- Existing security posture holds: host allow-list / SSRF guard on any repo URL a new channel accepts.

## Non-goals

- Star-count targets, growth hacking, or paid acquisition (not an engineering roadmap).
- Relicensing or open-sourcing the private source to chase OSS reach — a business decision, out of
  scope here.
- Competing with Graphify on community size. Different category, different buyer.
- Rewriting positioning: the comparison doc already says it — *Graphify maps the codebase; Spine
  changes it, under governance.*

## Open questions

1. Is the biggest friction the **install**, or the **first-run experience** (needing a repo big
   enough for the answer to impress)? **Phase 1's audit should answer this before we build fixes.**
2. Do we want telemetry at all, given the governance positioning? (Lean: **PyPI/plugin counts
   only** — external, no code, no privacy question.)
3. Should the free read-only skill be the *product wedge* (land, then upsell the platform), and if
   so does that change what the tools return? (**Product decision, not this team's** — but flag it,
   because it changes Phase 1's definition of "useful answer".)
