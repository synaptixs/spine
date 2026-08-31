# WI — Watch items: doc-drift durability (and the half that is being dropped)

**Status:** **Written 2026-08-30 against 3.25.1**, to close a decision that had been open since
the docs came back into this repo on 2026-07-30. **WI-2 Phases 1 and 2 shipped 2026-08-31** — the
defect in §3.1 is fixed and guarded, and drift reaches the review path. Phase 3 outstanding. **WI-1 is recommended for removal** — its
premise cannot be recovered (§2).
**Owner:** _unassigned_

**Gap:** the **WI** row in [`gap-roadmap-index.md`](gap-roadmap-index.md).

---

## 1. Why this file exists, and why it did not

Three documents referenced `watch-items-roadmap.md` as a live track. **It was never written.**

`docs/specs/` was imported from a private `synaptixs/spine-docs` repository on 2026-07-30
(`0bb56ae`), and the gap index arrived already linking this filename. The file was not among the
imported specs, and `git log --all -- 'docs/specs/watch-items*'` returns nothing — it does not
exist in this repository's history at any commit. Whether it was lost in the move or never
written outside a conversation cannot be told from here.

For roughly a month, three pages presented a track with no spec:

| Page | What it said |
|---|---|
| [`gap-roadmap-index`](gap-roadmap-index.md) | **WI** — *"PR-workflow defense; doc-drift durability"*, with an owned-files row for WI-1 and WI-2, and WI-2 ranked **second** of everything remaining |
| [`SPEC-INDEX`](SPEC-INDEX.md) | a linked row in the outstanding table |
| [`STATE-OF-SPINE`](STATE-OF-SPINE.md) §8 | *"links `watch-items-roadmap.md`, which does not exist"* |

That is the failure §9 of `STATE-OF-SPINE` describes, in its quietest form: **nothing was wrong
with any individual page.** Each was internally consistent, each pointed at the others, and the
absence only showed up when someone followed the link. A roadmap row is a claim that work is
scoped; three of them pointed at a scope that had never been written down.

**The instruction was "write it or drop the row".** This file does both: it writes the half that
survives contact with the source, and recommends dropping the half that does not.

## 2. WI-1 — "PR-workflow defense": recommended for removal

**The premise cannot be recovered from this repository.** The phrase *"PR-workflow defense"*
appears in exactly one place — the gap index row — and nowhere else in `docs/specs/`, `src/`, or
the git history. The document that row cites as its source,
[`graphify-vs-spine-comparison.md`](graphify-vs-spine-comparison.md), contains no such framing:
its "Two takeaways for us" are language/modality breadth and the agent-skill distribution
channel. Neither is about a PR workflow.

Two readings are available from the file-ownership row (`sdlc/`, shared `plugin/_TOOLS`), and
**both are already built**:

| Reading | State |
|---|---|
| *Defend the PR Spine opens* — checks on the generated diff before it is proposed | Shipped. Preflight, baseline-diff, fit, and a review stage that re-runs the tests are all in `sdlc/` |
| *Defend against a PR* — verifiers that read someone else's diff | Shipped. `codereview/` runs `PKGGroundingVerifier.scan(diff)` over a `PRDiff` |

**Recommendation: drop the WI-1 row** rather than reverse-engineer a track from a filename. A
roadmap row that has to be guessed at is worse than no row: it reads as scoped work, so nobody
picks it up and nobody removes it, and it survives every refresh unexamined. That is precisely
how this file came to be missing for a month.

**Revive condition:** whoever wrote the original row states what it meant, in a sentence. If it
named something the two readings above do not cover, it earns a spec of its own. Until then the
row is an unsourced claim, and this document is where that is recorded so the question is not
re-asked from scratch a third time.

## 3. WI-2 — doc-drift durability

**This half is real, it is the more valuable half, and it is not in the state the index implies.**

Doc drift is Spine reporting *claims the prose makes about code that the graph cannot support* —
the docs lying about the code, as distinct from the code being stale. It is
[`doc_link.doc_drift`](../../src/orchestrator/pkg/doc_link.py), deterministic and no-LLM,
filtered to symbol-shaped claims by `symbolish_drift` so paths and filenames do not flood it.

The index ranks it second of everything remaining, on the argument that it is *"a lead, not a
moat — cheap for a competitor to copy once they have doc→code edges."* That argument is sound
and it points somewhere specific: **what is hard to copy is not the finding, it is the
guarantee.** Anyone with doc→code edges can print a drift list. Printing one that is
*deterministic, provable at a commit, and enforced* is the part that takes the rest of this
system.

Three things stand between here and that, and they are not the three the index implies.

### 3.1 — `stale_findings` fabricated staleness on every non-Python file ✅ **fixed 2026-08-31**

**A defect, live in the PR-review path until 3.25.1.**
[`verifier.py:143`](../../src/orchestrator/pkg/verifier.py) re-extracts each changed file with
`PythonExtractor()` **unconditionally**. On a Go, TypeScript, Java, C#, C, C++ or SQL file the
parse raises, the `except` clause treats an unparseable file as *"everything recorded for it is
stale"*, and every fact in that file is reported as a stale-graph **WARNING** on the pull
request — through [`codereview/grounding.py:133`](../../src/orchestrator/codereview/grounding.py),
which passes *every* changed filename with no language filter.

Measured on a three-file scratch repository, no file modified after extraction:

| File | Stale findings on an unmodified file |
|---|---|
| `app.py` | **0** — correct |
| `app.ts` | **2** — every symbol in the file |
| `main.go` | **3** — every symbol in the file |

```bash
uv run python -c "
from pathlib import Path
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.verifier import GroundingVerifier
root = Path('/path/to/a/polyglot/repo')
v = GroundingVerifier(RepoCodeExtractor().extract(root))
print(len(v.stale_findings(root, ['main.go'])))  # expect 0 on an unmodified file
"
```

**This is the invented-edges shape again**, and worth naming as such: the finding is internally
well-formed — a real node, a real provenance, a correctly-rendered message — and externally
false. Nothing catches it. The repository Spine is developed in is Python-only under `src/`, and
both walkers skip the dot-prefixed fixture roots in `corpus/`, so **the bug cannot fire here and
is invisible to the whole suite**. It fires on a target repository, which is the only place
Spine's review path is ever pointed.

**Fix, in order of preference:**

1. **Dispatch per suffix** through the same registry `extractor.py` already uses, so the
   freshness check works on all eight front-ends. This is the fix that makes the feature true.
2. **Skip files no front-end claims** — honest silence, and strictly better than a false
   warning. This is the interim if a front-end turns out not to support single-file extraction,
   and it must be *reported as skipped*, never counted as clean.

Either way the guard belongs beside the test that proves it: a fixture per non-Python front-end
asserting **zero** findings on an unmodified file. Revert the fix and that test must fail — the
countermeasure this project already uses, applied to a check that has never had one.

> **Shipped 2026-08-31 as option 1**, per-suffix dispatch, plus option 2 for a suffix this
> install has no front-end for — which on a base install is *every* non-Python file, since the
> tree-sitter front-ends load only with their extra. `module_name` moved with `extract`: each
> front-end owns its notion of a module (Go's is the package directory), and re-deriving one
> with the Python-shaped `module_qualname` would have renamed every symbol it checked — the same
> false-stale by another route. Skipped files land in `GroundingVerifier.skipped_freshness` and a
> debug log rather than a finding: a finding becomes a review `WARNING`, and *"could not check
> this file"* on every polyglot PR is noise worse than the bug. **Nine tests, one per front-end
> plus a Go two-file package, a no-front-end case, and the unparseable case whose behaviour is
> deliberately unchanged — all nine verified to fail with the dispatch reverted.**

### 3.2 — `doc_findings` was built and never wired ✅ **fixed 2026-08-31**

[`GroundingVerifier.doc_findings`](../../src/orchestrator/pkg/verifier.py) renders drift as a
review finding, anchored to the doc's `file:line`, deliberately informational (*"a review
comment, not a blocker"*). **Nothing calls it.** `PKGGroundingVerifier.scan` composes
`stale_findings` and `shacl_findings` only, so the drift finding is constructed by a method with
no reader.

This is the `--intents` situation in [`STATE-OF-SPINE`](STATE-OF-SPINE.md) §8 — a shipped
capability with no consumer — and it is one line to close: add it to `scan`, filtered the way
`shacl_violation` is already filtered to the diff's blast radius.

**Do 3.1 first.** Adding a second drift finding to a review path that is currently emitting
false staleness warnings makes the review noisier, not more trustworthy.

> **Shipped 2026-08-31, and the filter is wider than this section proposed.** Filtering to *the
> docs the PR touched* — the obvious reading, and what was planned — misses the case that
> matters most: **rename a symbol and the docs that still name it are nowhere in your diff.** A
> finding is now kept when **either** its document is in the diff **or** its mention appears on a
> **removed line** of the diff. The second rule is attributable rather than speculative, and the
> reason is worth keeping: a drift finding exists *only* when the graph has no such symbol, so
> "the graph lacks it" plus "this diff deleted it" is this change's doing. Were the symbol still
> there, there would be no finding to attribute. It needs no base graph and no second extraction
> — one regex pass over the patch.
>
> Two things had to be fixed beside the one-line wiring, and both would have made the feature
> useless in practice:
>
> - **The 20-cap ran before any filter.** A repository carrying 20 unrelated drift claims would
>   have returned nothing for the PR's own documents — quiet on exactly the repositories that
>   need it, and looking like a clean result while doing so. `doc_findings` now takes `files` and
>   `mentions` and applies them *inside* the loop.
> - **The finding had no line.** `DocDriftFinding` carried only `page_title`, so `scan` anchored
>   every comment at line 1. It now carries the page's `source_file` and section `line` — the
>   same pair `link_docs` already uses for `Doc` provenance — so the comment lands on the section
>   that makes the claim.
>
> **What the union filter still does not catch**, stated so the row is not read as "drift on PRs
> is solved": drift a *previous* merge caused (deliberately — not this author's to answer for);
> a deletion whose patch body GitHub truncates on a very large diff; and a symbol removed
> indirectly, by a build change rather than an edit. Closing those needs the drift *delta*
> between the PR's graph and its base — two extractions and a base graph the review path does
> not have. **That is the follow-up, and it is Phase 3-sized, not thirty minutes.**

### 3.3 — Drift is reported everywhere and gated nowhere

Drift is surfaced in `state`'s markdown, the HTML report, and the MCP `read_memory_bank`
payload — `doc_drift_total` plus a bounded top-8. Nothing fails on it. There is no
`--check` tier for drift the way `pkg accuracy --check` gates the graph and `understand --check`
gates the bank.

**That is the durability the row was pointing at**, and it is a decision rather than a task:

- **Ratchet, not strict** — the same argument G6 settled on in D4. Drift is a *count over real
  prose*, not a defect class with a correct value of zero. A strict gate would freeze whatever
  the repository happens to score into the definition of correct, and the first honest
  documentation expansion would fail CI.
- **Informational stays informational on the review path.** §3.2's finding is a comment. The
  gate in §3.3 is a repository-level ratchet. Conflating them turns a helpful review note into
  a merge blocker, which is how a good signal gets switched off.

## 4. Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Freshness stops lying about seven of eight languages** ✅ | Dispatch `stale_findings` per suffix, skip-and-report where this install has no front-end. Nine tests, each proved to fail with the fix reverted | ~1 d | ✅ **2026-08-31.** A polyglot PR gets zero stale-fact warnings when nothing is stale, and the suite would catch a regression |
| **2 — Wire the drift finding** ✅ | Call `doc_findings` from `PKGGroundingVerifier.scan`, filtered to docs in the diff **or** mentions on its removed lines. Severity stays `WARNING` | ~0.5 d | ✅ **2026-08-31.** A PR that renames a symbol its docs still name gets one comment, on the doc's section line |
| **3 — Gate the count** | A `drift` key alongside the existing scoreboard keys, on the **ratchet** tier. One baseline, one file — never a second scoreboard | ~1–2 d | A PR that increases repository drift is visible before merge, using the gate that already exists |

**Phase 1 is not optional and does not depend on the rest.** It is a live false-positive on the
one surface a target repository sees. If nothing else in this document is ever picked up, that
still wants fixing.

## 5. Invariants

- **Deterministic and no-LLM**, all three phases. Drift is a graph query, and the moment it needs
  a model it stops being gateable — the property §3 says is the whole value.
- **One baseline.** Phase 3's key lives in `pkg/scoreboard.json` with `corpus`, `invention` and
  `parity`. Two baselines is how a project ends up quoting whichever is kinder.
- **Bound honestly.** The drift surfaces already cap at 8 and say `+N more`. A skipped file in
  Phase 1 is *reported as skipped*, never folded into a clean result.
- **Silence over fiction.** The §3.1 fix must fail closed: a file no front-end can parse yields
  no finding, not a fabricated one.

## 6. Non-goals

- **Fixing the drift.** Spine reports that prose and code disagree; deciding which is wrong is
  the author's call.
- **Semantic matching of prose to code.** The 56% of `Doc` sections that bind to nothing
  ([`document-ingestion-reference.md`](document-ingestion-reference.md)) are a separate problem
  that needs a model, and it does not belong on a deterministic path.
- **Reviving WI-1** without a stated premise (§2).

## 7. Open questions

1. ~~**Does Phase 1's per-suffix dispatch need single-file extraction from every front-end?**~~
   **Closed 2026-08-31: no.** `LanguageExtractor.extract(*, path, module, rel)` is already
   per-file for all eight — `RepoCodeExtractor` does nothing but build a `{suffix: extractor}`
   map and call it once per file. No front-end needed a new capability, and none took the
   skip path for want of one.
2. **Should Phase 3's ratchet count symbol-shaped drift only, or all drift?**
   (Lean: **symbol-shaped only** — the same filter the surfaces already apply. A gate on a
   noisier population than the report is a gate nobody will trust.)
