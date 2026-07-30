# G4 — Adoption & distribution: friction, channels, proof

> **"G4" is a label, not a position in a queue.** It's gap #4 in the Graphify comparison. The specs
> are not ordered and do not run in sequence.

**Status:** Not started.
**Owner:** _unassigned_

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
| **1 — Friction audit + 60-second path** | Time the real cold-start on a clean machine: install → point at a repo → first useful answer. Remove every step that isn't essential (config, credentials, docs-reading) from the **read-only** path. Fix whatever the audit finds. | ~4–6 d | A stranger gets a grounded answer about their own repo in **< 60 s**, with **no API key**, measured on a clean box |
| **2 — Channel expansion** | List in the **MCP registry** and any assistant marketplaces we're absent from; verify the plugin actually installs in each documented host (don't assume — install it). One-line install snippets per host, kept in CI-checked docs. | ~5–7 d | Installable from N hosts, each **verified by an actual install**, not by documentation |
| **3 — Proof assets** | Publish the G6 benchmark page; a short "what Spine tells you that grep doesn't" walkthrough on a well-known OSS repo; the G5 export as a shareable artifact. Public, reproducible, honest. | ~4–5 d | Linkable evidence for each headline claim |
| **4 — Measure** | Opt-in, privacy-respecting install/usage signal (or, if that's unacceptable, PyPI download + plugin-install counts). Decide *before* Phase 1 what "working" looks like numerically. | ~2–3 d | We can tell whether phases 1–3 moved anything, instead of guessing |

**Phase 1 is the one that matters most.** Every channel added in Phase 2 multiplies whatever
conversion rate Phase 1 leaves us with; widening the funnel before fixing the leak wastes both.

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
