# How well does Spine actually work?

**Measured 2026-09-01, released in Spine 3.29.0.** Everything on this page is reproducible from a
checkout; the commands are at the bottom and they are the same ones that produced these numbers.
**The SWE-bench section, added 2026-10-16, is re-runnable from a checkout through
`scripts/swebench_localization/`;** its published results file and written pre-registration are kept outside
the repository, and a full re-run through the moved harness has not yet been done (see "Re-running the SWE-bench
figure"). **The end-to-end section, added 2026-10-18, is not re-runnable from a checkout** (see "End to end: a random sample of 100").

> **Update, 2026-10-16 — an independent check on 500 public issues found that plain text search does as
> well.** On SWE-bench Verified (labels nobody here wrote) Spine puts a fixing file in its top ten for
> **0.668** of 500 issues; a BM25 text search over the same files, with no graph, scores **0.702**. The
> difference is not statistically significant, but it points the wrong way at every cut-off, and giving
> Spine the whole issue text does worse than giving it only the first line. Read
> "Independent check: SWE-bench Verified" below before quoting any figure on this page.
> A later change, pre-registered and checked on 250 fresh issues, makes `investigate` rank on the ticket's title; see "First-line
> ranking" below for what it did and did not show.
>
> **Update, 2026-10-18 — the first end-to-end number.** On a random sample of 100 SWE-bench Verified issues, Spine `bc2a6af0` with
> `claude-opus-5` resolved **26 of 100** (95% interval 18.4–35.4%), counting parked and failed runs as not resolved; 29 of the 100
> were parked before any code was written. Not a Verified score, not a leaderboard figure, not compared with any other system. See
> "End to end: a random sample of 100" below.

> **Correction, 2026-10-14 — the localization figures below no longer hold for current Spine.**
> Re-measured on 2026-10-13 on the same 38 labels, the same pinned trees and the same scorer, the
> shipped result size (up to ten landing sites) scores:
>
> | | 3.29.0 (published here) | 3.57.0 (`b84ee905`) |
> |---|---|---|
> | top-1 | 12/38 (0.32) | **9/38 (0.24)** |
> | top-3 | 18/38 (0.47) | **14/38 (0.37)** |
> | top-5 | 22/38 (0.58) | **17/38 (0.45)** |
> | top-10 | 27/38 (0.71) | **23/38 (0.61)** |
>
> Both columns are deterministic and reproduce exactly. Two changes account for the drop at top-10,
> two labels each. In 3.35.0 (`d87a0440`) C++ headers moved to the C++ parser; the old C parse had
> emitted function-local variables as fields, and two fmt labels were hits only because of that, so
> they are not expected to return. In 3.38.0 (`1cd7766d`) a ticket's word endings are cut to a stem
> (`closing` matches `close`), which lets generic words crowd out the specific one; on this gold set
> it won no label (two libuv labels fell out of the top ten). Four labels fell from hit to miss and
> none rose. At 0.61 Spine is about **seven** times more often right than the 0.085 chance
> baseline, not the eight claimed below. The page's own rule — that a benchmark must publish what it
> cannot show — applies here too: **this figure was wrong for four weeks, because the gate described
> under "How this is kept honest" was recorded but not running.** The tables below are left as
> published and describe 3.29.0.

> **Update, 2026-10-14 — what the ranking work changed.** Ranking is measured over the first fifty
> symbols reduced to distinct files, so that showing more files cannot be counted as ranking better.
> On the same 38 labels (top-1 / 3 / 5 / 10): 3.57.0 **9 / 16 / 20 / 27** → now **12 / 18 / 22 / 27**.
> What a reader is shown is a separate matter: the answer now lists up to ten distinct *files* where it
> listed ten *symbols* (six or seven files), which moves the shipped top-10 from **23 to 27** of 38 —
> four labels that are display, not ranking. The ranking changes kept are: a weak match (one that only
> shares a common word) no longer outranks a specific one, and the words of a symbol's file path add a
> little to a hit that already matches by name. Two others — cutting word endings less aggressively,
> and weighting rare words — were tried and dropped because each lost labels that had been found.
>
> Checked on **40 further labels** from seven other projects (Java, C#, Rust, Kotlin, PHP and Python)
> written before any change was tried: on the 30 used to choose settings, 7 / 14 / 17 / 20 → 7 / 17 / 20 / 22;
> on the 10 sealed and read once, 2 / 4 / 5 / 6 → 3 / 6 / 6 / 6. Pooled over all 78 labels the top three
> goes from 34 to 41 (gained 9, lost 2; exact sign test p = 0.065). **No difference here reaches
> p < 0.05; the direction is the same on every set.** The 38 informed the diagnosis, the 40 are few and
> were written by the team, not independently, and four of the ten sealed labels are missed before and after.
> The method, the grids and every dropped step are in the pre-registration kept with the work.
>
> Note that the new shown figures (12 / 18 / 22 / 27) are identical, cell for cell, to the 3.29.0 table
> below. They were reached by different means: the 3.29.0 figure counted the first ten *symbols*, this
> one the first ten *files*, and the code changed between the two in ways this page cannot disentangle.

Spine's central claim is that it can read a bug report and tell you where in a codebase the fix
belongs. This page is that claim, measured — on five open-source projects nobody here controls,
against fixes we did not choose after the fact.

It also states, at length, what these numbers **do not** show. That half matters more than the
headline: a benchmark whose limits are not published is marketing.

---

## What we measured, and why this one

Every published benchmark we could find in the code-intelligence category measures **efficiency**
— *"70% fewer tokens"*, *"fewer tool calls"*. That answers *how cheap*, not *is it right*. So
there is nothing to compare against here, and *"better than chance"* is the only baseline
available. We say that plainly rather than implying a field of rivals we beat.

Two metrics, both deterministic and neither involving a language model:

| Metric | The question |
|---|---|
| **Top-k localization** | Given a real bug report, is the file that actually fixed it among the first *k* places Spine names? |
| **Provenance validity** | Does every fact Spine records open to a line that genuinely names that symbol? |

---

## The corpus

Five repositories, one per language front-end, each pinned to an exact commit.

| Repo | Language | Pinned commit | Date |
|---|---|---|---|
| [vuejs/core](https://github.com/vuejs/core) | TypeScript | `25ebe3a42cd80ac0256355c2740a0258cdd7419d` | 2025-11-24 |
| [gin-gonic/gin](https://github.com/gin-gonic/gin) | Go | `f416d1e594a027063e73f66ac873a82113036fd8` | 2025-11-30 |
| [fmtlib/fmt](https://github.com/fmtlib/fmt) | C++ | `2d839bbc61659d8a464bdcd2717b577f03991d94` | 2025-11-30 |
| [libuv/libuv](https://github.com/libuv/libuv) | C | `8fc70344df789d95c18f3c5282f11dcd85205545` | 2025-11-29 |
| [pallets/flask](https://github.com/pallets/flask) | Python | `ad68a12645d96fe51558da13e18544ba458d7af0` | 2025-11-28 |

**The date is load-bearing.** Every labelled bug was fixed *after* its repository's pin, so the
tree Spine searches is the **pre-fix** state. We are asking it to find where a fix will go, not
to notice one that has already happened.

**C# has no slot.** Five repositories, six front-ends at 3.29.0 (there are fourteen now) — one
language measured then is unrepresented, and nothing here says anything about it.

Rust has a separate [source-fact validation](docs/evals/rust-comprehension-validation.md),
including two pinned repositories and a labelled corpus. It is not part of this five-repository
bug-localization benchmark.

---

## How a label was made

The answer key comes from **the commit that fixed the issue**. What that commit changed *is* the
answer — that is git, not anyone's opinion.

1. Take a merged pull request that closes exactly one issue, after the pin.
2. Read the source files that commit changed.
3. **A human decides which of them is the fix.** A commit carries tests, changelog entries and
   incidental tidying; the fix is usually one or two files.
4. Record the issue URL, its title, the full 40-character fix commit, and the fix path(s).

**Step 3 is deliberately not automated, and that is the whole design.** A candidate proposed by
reading the ticket the way Spine reads it would not be independent of the thing being scored — it
would measure two readers of the same clues agreeing, and report it as accuracy.

Every label is machine-checked before it can score:

- the fix commit must be a full 40-character id (an abbreviation reads like a commit and cannot be
  handed to git);
- the issue must be a URL a reader can open, and must be the issue GitHub itself records the
  fixing PR as closing;
- the repository must be one in the corpus above;
- **every labelled path must exist in the pinned tree** — this catches naming a file the fix
  *created*, which no run against the pre-fix state could ever have found.

38 labels survive: libuv 14, fmt 8, vue-core 8, flask 4, gin 4.

---

## The results

*Spine 3.29.0, measured 2026-09-01. Current Spine scores lower; see the correction at the top.*

Spine is given **the issue title only** and returns up to ten candidate locations. A label counts
as a hit at *k* when any of its recorded fix paths appears in the first *k*.

| | hits | rate |
|---|---|---|
| **top-1** | 12/38 | **0.32** |
| top-3 | 18/38 | 0.47 |
| top-5 | 22/38 | 0.58 |
| **top-10** | 27/38 | **0.71** |

**The number that makes those readable: 0.085.** That is what a tool would score at top-10 by
picking ten files at random from these repositories (85–486 source files each, weighted per
label). Spine is right roughly **eight times more often than chance**, from one line of prose,
with no access to the fix and no human hints.

Two of the 38 returned *nothing at all*. That is counted separately from a bad ranking, because
"found nothing" and "ranked it tenth" are different failures and averaging them hides one.

### Provenance validity

Of every `Function`, `Type` and `Field` fact Spine records, how many open to a line that actually
names that symbol:

| Repo | Language | |
|---|---|---|
| vue-core | TypeScript | **1.0000** |
| gin | Go | **1.0000** |
| flask | Python | 0.9955 |
| fmt | C++ | 0.9857 |
| libuv | C | 0.9850 |

Scored only for kinds named by a token at the site. `Module`, `Endpoint` and `Entity` are named
by construction — a dotted path, `GET /v1/x`, a table name — so scoring them would measure a
naming convention and call it provenance. They are counted as *excluded*, never as passed.

---

## Independent check: SWE-bench Verified

*Measured 2026-10-16 on Spine `69ada66a` (the develop line of 3.59.0). Pre-registered before any rank was
computed; run once, untuned.*

The 78 labels above were written by the team, and the gain from the ranking work on them is not
statistically significant (top-3 34 → 41 of 78, p = 0.065). SWE-bench Verified supplies **500 issues whose
answer key nobody here chose**: the files changed by the fixing patch. This section scores the same question —
*is a fixing file among the first k files Spine names?* — on them. It costs no model call and does not use
SWE-bench's resolving harness.

**What it is not.** Not a resolving rate, not a leaderboard figure, and nothing about patch quality. It is
**Python only** (12 projects; Django is 231 of the 500, seaborn 2 and flask 1), so it says nothing about the
other front-ends.

**How it was measured.** For each instance the repository was checked out at its pre-fix base commit and
`investigate` was run on the issue text; up to ten distinct files come back. A hit at *k* means any file the
fixing patch changed is among the first *k*. Two inputs were fixed in advance: **the full issue text** (first
line as title, the rest as body, the pre-registered headline) and **the first line alone**, which is what the
78 labels were scored on. Two baselines ran on the same trees: **chance** (random distinct `.py` files), and
**BM25** over every `.py` file's path and text (lowercased words, split at underscores and camelCase, no
stemming, k1 = 1.5, b = 0.75, each query word counted once) given the same text as the full-text run. No
retrieval change was made after seeing any result.

| 500 issues, hit at *k* | top-1 | top-3 | top-5 | top-10 |
|---|---|---|---|---|
| **Spine, full issue text (headline)** | 0.210 | 0.428 | 0.534 | **0.668** (334/500; 95% interval 0.626–0.710) |
| Spine, first line only | 0.362 | 0.552 | 0.630 | 0.702 (351/500) |
| BM25 text search, no graph | 0.266 | 0.470 | 0.568 | 0.702 (351/500) |
| chance | 0.002 | 0.005 | 0.009 | 0.017 |

Intervals are percentile bootstraps over instances (10,000 resamples, seed 17). Run 1 scored all 500, none skipped.

**What the numbers say, plainly**

1. **Spine is far above chance** — about 39 times at top-10 — and a fixing file is in its ten files for two issues in three.
2. **A text search with no graph does as well.** BM25 scores 0.702 at top-10 and 0.266 at top-1 against Spine's 0.668 and 0.210.
   On paired outcomes, Spine alone hits 72 and BM25 alone 89 at top-10 (exact sign test p = 0.21); at top-3, 83 against 104
   (p = 0.14). Neither is significant, but the direction favours BM25 at every cut-off. **On this set we have not shown that the
   graph adds anything to file retrieval over a text search.** (These two comparisons were added in the analysis and were not
   pre-registered.)
3. **Giving Spine the whole issue is worse than giving it the first line.** At top-3, 0.428 against 0.552 (the full text alone
   hits 64 issues, the first line alone 126; p = 8e-6). The loss grows with the length of the issue: by length quartile,
   full-text top-3 is 0.58, 0.39, 0.38, 0.35 while first-line top-3 stays between 0.54 and 0.57. At top-10 the gap is not
   significant (p = 0.20). The headline stays the pre-registered full-text figure.
4. **Per project the spread is wide.** Top-10 with the full text: scikit-learn 0.94, sphinx 0.70, django 0.68, matplotlib 0.41
   (n = 32, 44, 231, 34). The mean of the 12 per-project rates is 0.731, but seaborn (2) and flask (1) carry no information.
5. **A quarter of the issues name the fix.** 133 of 500 (26.6%) contain a fixing file's path or file name; top-10 is 0.744 on
   those and **0.640** on the other 367.
6. **Fixes that touch several files are mostly not fully found.** 71 issues have two or more fixing files; a fixing file is in
   the top ten for 56 of them (0.789), but *all* of them are for only 15 (0.211).

**Where it misses.** Spine misses at top-10 on 166 issues; the first-line run or BM25 finds the file for 87 and 89 of
them, and all three miss on 45 (43 of those are single-file fixes, 3 quote the fixing file). Reading a seeded sample of 24
of the 45, the fix usually sits in a lower-level module the issue does not name (the serializer for an issue about enum
values in migrations, the code printer for one about `lambdify`), and Spine's top three often contain example, demo or
test-support files. That second observation comes from 24 rows; it is a hypothesis, was **not** acted on, and no retrieval
change has been made because of it.

**Limits.**
- One run on one Spine version. The path is deterministic, so a repeat gives the same numbers; a different version will not.
- The BM25 baseline was written by the team, once, before the run. A stronger or weaker text search would move item 2.
- "Any fixing file" is generous for multi-file fixes (item 6 shows how much).
- Some issue texts contain the answer or a strong hint (item 5); a ticket that does not is the harder and more common case.
- 46% of the instances are Django; the pooled figure is largely a Django figure.
- The retrieval code was tuned on the earlier 78 labels, not on these 500; but the team has now seen these results, so a
  later change tuned on them would need its own held-out half. The plan fixes one (a seeded 50/50 split by project).

### Re-running the SWE-bench figure

The harness is in this repository: `scripts/swebench_localization/`, with the steps in
[CONTRIBUTING.md](CONTRIBUTING.md) ("Localization benchmark"). In short: `clone_mirrors.sh` fetches bare mirrors of
the 12 projects, `check_commits.py` confirms all 499 base commits are present, `loc_harness.py --mode run --all
--out run.jsonl --expect-sha <spine sha>` scores (about three hours, no model call, no spend) and `analyze.py
run.jsonl` prints the tables. The results file for the published figure (500 rows) and the written
pre-registration are **not** in the repository. What identifies the published run: Spine
`69ada66a47deaeb2be2183dc597d2d13715c1519`; dataset `princeton-nlp/SWE-bench_Verified` revision
`c104f840cc67f8b6eec6f759ebc8b2693d585d4a`; results file (500 rows) sha256
`76822b34dc08be057771d9438615f401b53c52d7508662822d0c4bc126dac831`.

**What was checked, and what was not.** The published numbers were produced by the harness as it stood before it was
moved here (sha256 `8f5429529d948f4db1be761cd5d941d72ae499ec768026f575aeb84a0717a7b2`). The moved version (sha256
`539a4b3fedb63e274f10630f35780c8b56d61f685676d0eac9b34a8b5d3cff7d`) changed only how the Spine checkout and the
mirrors are named, how the pin is checked, and where peak memory is read; the scoring is the same. On a seeded
sample of 41 of the 500 issues (spread over all 12 projects) it reproduced the original run's ranked file lists,
gold files, rank values and graph sizes exactly, once against Spine `69ada66a` and once against current develop
`8e71fd22` (after the dependency updates since); `analyze.py` reproduces the published tables from the original results
file exactly. **A full 500-issue re-run through the moved harness has not been done**; until it is, treat the figure as
checked on a sample, not re-derived in full.

### First-line ranking (SSPN-192)

*Measured 2026-10-16 on Spine `2194fd99`. Pre-registered before any variant was scored; each arm read once.*

The run above showed Spine doing worse when given the whole issue than the first line alone, because the retriever was handed
title and body as one flat set of words. `investigate` now ranks landing sites on the ticket's title; hits with an equal score are
ordered by the full text's score, and hits that match the body only follow the title's. A ticket with no body is ranked as before,
`design`, `build` and the other callers keep the flat query, and `INVESTIGATE_TITLE_RANK=0` restores it for `investigate`.

**The independent test.** The first-line finding was made on all 500 Verified issues, so none of them is blind for it. The primary
arm is **250 random issues from the full SWE-bench test split that are not in Verified** (about 1,794 candidates, the same 12
projects, drawn with seed 17, proportional by project; none had ever been run through any Spine measurement). Hit at *k* means a file
the fixing patch changed is among the first *k* files named.

| 250 fresh issues, hit at *k* | top-1 | top-3 | top-5 | top-10 |
|---|---|---|---|---|
| Spine before (title and body as one flat query) | 0.264 | 0.456 | 0.564 | 0.688 |
| **Spine now (title first, body breaks ties)** | **0.368** | **0.544** | **0.620** | **0.700** |
| Spine on the first line only | 0.348 | 0.496 | 0.560 | 0.648 |
| BM25 text search, no graph | 0.316 | 0.536 | 0.624 | 0.728 |
| chance | 0.003 | 0.009 | 0.015 | 0.030 |

The pre-registered bar was top-3, new against before, by a paired one-sided exact sign test at p < 0.05, with top-10 losing no more
than two issues net. It was met: at top-3 the new ranking alone hits 56 issues and the old one alone 34 (**p = 0.013**); at top-10 it
hits 175 against 172. The 38-label localization floor (12 / 18 / 22 / 27) holds exactly, and the 40 further labels are unchanged,
because both sets are title-only and cannot move.

**What this does not show**

- **It does not beat a plain keyword search.** At top-3 the two are level (0.544 and 0.536); at top-10 the keyword search is still
  ahead (0.728 against 0.700). No test of that was pre-registered, and none is claimed.
- **The earlier explanation did not replicate.** Spine on the first line alone is not reliably better than the flat query on
  fresh issues (top-3 0.496 against 0.456, p = 0.34). The gain comes from the combination: first-line ranking, the body as a
  tie-break, and the body filling the list afterwards.
- **The gain is smaller than the first estimate.** +22 of 250 at top-3 (+8.8 points), against the 12-point gap seen on Verified.
- **Absolute rates are not comparable with Verified's.** The rest of the test split includes issues reviewers judged under-specified;
  only the paired comparison inside the arm is meaningful.
- **The sealed half of Verified (248 issues) is disclosed as not blind** and gave a larger gain (top-3 143 against 101). It checks
  that the shipped change delivers the effect; it is not independent confirmation, so quote the fresh-issue figures.
- Python only; not a resolving rate.

**Re-running it.** The harness takes `--dataset-file` (the `test-00000-of-00001.parquet` of `princeton-nlp/SWE-bench` at revision
`e48e2bd1e9fecd5bbd641e9414ac59da9f2e69f6`, sha256 `db4f70ef735b3162c74801ddcdf8d7bae8d704193788c6d844f898c20b571cbb`, 12 MB) and
`--ids-file scripts/swebench_localization/first_line_fresh_sample.json` (the 250 ids, sha256
`aa29648da9d2305e8980dcc1a551c0893e97eddd8df14ce0f62ef2dcba8939c4`); `firstline_bar.py` applies the bar. The run used harness sha256
`60c477ea52ebc368beb705f22c65ba94ae56cb0f836d04294c40c46f714c0af2`, took about 1 h 26 min, no model call and no spend. Its results
file (sha256 `c7253568f40400e321ded9ee33357eab5f7809a5a288f4da32dcef6b00553ca6`) and the written pre-registration are kept outside the
repository. A full re-run through this repository's copy of the harness has not been done.

---

## End to end: a random sample of 100 SWE-bench Verified issues

*Run 2026-10-17 17:49Z to 2026-10-18 00:23Z on Spine `bc2a6af0a2355fd34965eb72bcf73271315f483c` with `claude-opus-5`; graded 2026-10-18.
Pre-registered before the sample was drawn (the seed was written down before the draw script ran); each issue run once.*

The sections above ask whether Spine *finds* a fixing file. This one asks the question they leave open: does the pipeline, given an
issue, produce a patch the official SWE-bench grader accepts? The answer is a number we would rather have been higher, and it is
published as it came out.

> On a random sample of 100 SWE-bench Verified issues that were never run through Spine's generation pipeline during development
> (retrieval was measured on all 500, which is disclosed), Spine `bc2a6af0a2355fd34965eb72bcf73271315f483c` with `claude-opus-5`
> (native structured output, no thinking blocks, mean 1,599 output tokens per codegen reply; settings as captured by the proxy)
> resolved **26 of 100 [18.4–35.4%]**, counting parked and failed runs as not resolved; mean cost $0.93 per issue. This is not a
> Verified score and not a leaderboard figure.

It is not compared with any other system, here or elsewhere in this repository: no other system's number has been fetched from its
primary source, and without that a comparison would be a guess.

**What was run.** The sample is 100 instances drawn at random, proportional by repository, from the 460 Verified instances that had
never been run through the generation pipeline (the 40 used in earlier pilots were excluded), with seed 20261017. Each ran once through
`sdlc autorun --issue-type Bug --language python` on its pre-fix commit, one at a time, with a 30-minute wall-clock limit and a $5
ceiling per instance. Every patch the pipeline released was graded by the official grader (`swebench` 5.0.2, offline, image digests
checked against the pinned ones). **The denominator is the whole 100:** an instance that was parked, stopped, timed out or failed to
set up counts as not resolved.

**Where the 100 went**

| Outcome | Instances |
|---|---|
| **Resolved** (the patch passes the grader) | **26** |
| Patch released, not resolved | 9 |
| Stopped in implement without a green change (tests never green 18; refine stopped on an unchanged failure 6; `CodegenError`, a search text not found, 4; other 4) | 32 |
| Parked at the validity gate: a criterion names a symbol the graph does not hold | 17 |
| Parked at the validity gate: the design's files exceed the context budget | 12 |
| Setup failed: the base tree has a git submodule the generation tree lacks (`astropy-7671`, `astropy-8707`) | 2 |
| Stopped by the wall-clock limit (`sympy-17139`, `sympy-23824`) | 2 |

**By repository** (descriptive; most cells are too small to say anything about a repository)

| Repository | In sample | Patch released | Resolved |
|---|---|---|---|
| django | 45 | 16 | 12 |
| sympy | 15 | 2 | 1 |
| sphinx | 9 | 2 | 2 |
| matplotlib | 7 | 3 | 3 |
| scikit-learn | 7 | 4 | 4 |
| astropy | 4 | 1 | 0 |
| xarray | 4 | 1 | 1 |
| pytest | 4 | 2 | 2 |
| pylint | 2 | 1 | 0 |
| requests | 2 | 2 | 1 |
| seaborn | 1 | 1 | 0 |
| **Total** | **100** | **35** | **26** |

**Cost.** The usage proxy's true spend for the run was $92.75 for 100 instances: mean $0.93, median $0.63, 90th percentile $2.46,
maximum $4.30 per instance (cap $240, ceiling $5 per instance, neither reached). With the aborted first attempt described below,
$92.97. These are API list-price equivalents at the model's published rates; on a subscription plan they are not an invoice.

**What the numbers say, plainly**

1. **26 of 100, with an interval about nine points either side** (Wilson 95%, 18.4–35.4%). One run per instance; the run-to-run
   variance of a model that is not deterministic is unknown. The earlier five-instance runs resolved different pairs of the same
   five problems.
2. **Most of the loss comes before a patch exists.** Of the 74 instances that did not resolve, 65 never released a patch: 29 were
   parked at the validity gate, 32 stopped in implement, 4 hit a setup or time failure. Of the 35 patches the pipeline released, 26
   resolved (74%). That is a description of this run, not a claim about the gate's precision.
3. **The gates are part of what was measured.** 29 of the 100 were parked at the validity gate (17 on a criterion that names a
   symbol the graph does not hold, such as a name from the reporter's example, a symbol the fix is asked to create, or a built-in;
   12 on the context budget). Whether those parks were right is not measured here.
4. **A coverage-gate withdrawal fired three times** (`django-16612`, `pytest-5631`, `scikit-learn-13328`): the pipeline dropped an
   untested production file from the patch and kept the rest. All three patches resolved. Three observations are not evidence that
   the withdrawal helps.

**The aborted first attempt, and the one change made after seeing it.** The run was started once before this one, on Spine
`4bca699a`, and aborted. Its first invocation failed to pull ten instance images (Docker Hub's unauthenticated rate limit; $0 spent, no
model call). In a later invocation an operator error left two processes working `astropy-13236` at once, and that instance's result
from the attempt is not used. Then `astropy-14096` was parked at the validity gate because the one stated criterion was the issue's own
last error line (`AttributeError: 'custom_coord' …`), whose name the graph does not hold. We decided that was a defect, not a result,
stopped, fixed it with a plan and tests (B93, [#600](https://github.com/synaptixs/spine/pull/600)), re-pinned to `bc2a6af0` and
re-ran **all 100 from scratch** with the same sample and seed. The attempt's true spend was $0.22 and no instance went past the validity
gate. So the pipeline was changed once after the first two instances of the sample parked; `astropy-13236` and `astropy-14096` are two
of the 100 and ran again with the rest, and the attempt's outcomes are not counted.

**What this does not show**

- **It is not a SWE-bench Verified score and not a leaderboard figure.** It is a random 100 of 500, run by us, on a pinned
  commit, with a pipeline of our own.
- **Python only.** Nothing here says anything about the other front-ends.
- **The fixes under test were found on other instances, except one.** The code carries the validity gate's file set (B89,
  [#598](https://github.com/synaptixs/spine/pull/598)), the coverage gate (B91, [#596](https://github.com/synaptixs/spine/pull/596) and
  [#597](https://github.com/synaptixs/spine/pull/597)), the acceptance judge's handling of transcripts (B92,
  [#599](https://github.com/synaptixs/spine/pull/599)) and the validity gate's handling of the reporter's own example (B93,
  [#600](https://github.com/synaptixs/spine/pull/600)). B89, B91 and B92 were found on the five problems of the earlier small and
  pre-spend runs, which are not in this sample; B93 was found on the first two instances of this sample, as described above. None was
  fixed by looking at which instances would then pass, but the pipeline's gates, thresholds and prompts were tuned on 40 other
  instances and nothing proves the 100 are free of influence from knowing these repositories. The retrieval figures above were
  measured on all 500, which is why the sentence says so.
- **The earlier end-to-end results are not part of this number and are not dropped:** two pilots resolved 1 of 20 each, the small
  run 2 of 5, and the pre-spend gate run 2 of 5, on 40 distinct instances, with earlier versions of the code.
- **The grader's dataset.** Its `verified` alias loaded cached revision `78f471bf`, not the pinned `c104f840`. For the 35 graded
  instances the only difference is how `FAIL_TO_PASS` and `PASS_TO_PASS` are stored (a JSON string against a list); the tests, patches,
  base commits and versions are identical.
- **Run-to-run variance, other models, the full 500 and a second run of the same 100 were not measured.**

**What identifies the run.** Spine `bc2a6af0a2355fd34965eb72bcf73271315f483c` (develop, after 3.59.0; released as 3.60.0, whose code is the same: the release cut changed only version stamps, documents and the bot's `episteme/`); dataset
`princeton-nlp/SWE-bench_Verified` revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`; sample (100 ids) sha256
`18f928e2a6b6b90be44684036f17772f360b0c8cb758a42410a0dbeb5123ee9b`, drawn by a script with sha256
`a991746ff1cd300c1819286394839a6e96ac2f4931cdf5ce76f47f9f89878869`; runner sha256
`1b18124a300b5dfac6e24ac176f0751c9823b984a3fc4edc756ac70ea41e3fc5`; the 35 submitted patches (sha256
`c4bb23abd25e209f98eaee742e3930e3bba1ec77d7fa95175fa3ef83971afb5f`); verdicts file (26 resolved, 9 unresolved) sha256
`7c8c3123a70f45c5601d87c13c9c17036c21115a9f98d841183fdaf8b628e77e`. **The runner, the run's books and the written pre-registration are
kept outside the repository, and this figure cannot be re-run from a checkout.** Re-running would also cost about $90 in model calls and
about 6.5 hours.

---

## What these numbers do not show

**1 · The sample is small.** With 38 labels the 95% interval on top-1 runs roughly **0.17 to
0.47**. This is a real measurement, not a precise one. Reaching ±0.09 needs about 100 labels,
which needs older pins or more repositories.

**2 · The bugs are cleaner than average, so the number is optimistic.** Every label is a fix
touching one to three source files; six of the 38 touch more than one, and a multi-file label
scores a hit if *any* of its paths lands. A bug whose fix is smeared across ten files would be
harder, and none is in here.

**3 · Spine got less than it would in practice, so the number is also pessimistic.** It was given
the issue *title* only, not the body. A real ticket carries stack traces, reproduction steps and
version details.

**4 · The corpus is not balanced.** libuv contributes 14 of 38. flask and gin contribute 4 each,
because their post-pin windows hold few qualifying fixes — not because Python and Go were
deprioritised. A test refuses any gold set where one repository exceeds half the labels, so this
cannot quietly become a measurement of one project.

**5 · Four things this programme names and does not measure at all:** impact recall (does the
predicted blast radius contain what a PR actually touched), fault-site top-1 on real tracebacks,
`regression_gaps` precision, and anything about C#. Two of Spine's four accuracy oracles
(`runtime`, and parts of `invention`) are Python-only, so their clean results say nothing about
the other front-ends — seven at 3.29.0, twelve now.

**6 · The retrieval sections measure retrieval, not repair.** Nothing in them says the code Spine writes is
correct. The one repair measurement is "End to end: a random sample of 100", above: 26 of 100, with limits of its own.

---

## Reproduce it

These commands ship in **3.29.0**. Install with **every language extra** — this matters more
than it looks:

```bash
pip install 'synaptixs-spine[languages]'
```

Or from a checkout, which is what produced the figures below:

```bash
git clone https://github.com/synaptixs/spine.git && cd spine && uv sync --all-extras
```

Without the extras a front-end silently produces no facts, every label in that repository becomes
unfindable, and the localization number drops for a reason that has nothing to do with Spine. The
command warns when a corpus repository yields nothing; heed it before quoting anything.

Then, in one command — it fetches the five pinned repositories (shallow, at their exact commits)
and scores both metrics:

```bash
orchestrator pkg accuracy . --oracle comprehension --pinned-corpus
```

Inspect the answer key itself, and check every label against the pinned trees:

```bash
orchestrator pkg labels --check --paths
```

And see what any fixing commit actually changed, which is how each label was derived:

```bash
orchestrator pkg fix-sites flask 05e9c6bd630ecf4ec0ec884b1fc7901663737bc7
```

The gold set is [`src/orchestrator/evals/comprehension_labels.yaml`](src/orchestrator/evals/comprehension_labels.yaml)
and the corpus is [`src/orchestrator/evals/comprehension_corpus.yaml`](src/orchestrator/evals/comprehension_corpus.yaml).
Both are plain text. Every row names an issue you can open and a commit you can read.

---

## How this is kept honest

The number is on the same scoreboard as every other accuracy metric
(`src/orchestrator/pkg/scoreboard.json`) and **gated**: a drop in top-1 or top-10 fails the build.

**Until 2026-10-14 that was the design, not the practice.** The localization entry in the scoreboard
read `not_measured`, and CI does not fetch the pinned corpus, so the gate had never compared two
measured values. That is how the drop in the correction above went unnoticed. The scoreboard now
records a floor (top-1 12/38, top-3 18, top-5 22, top-10 27 — a floor, not a target; it was 9/38 and 23/38 on 3.57.0), and
`.github/workflows/localization-gate.yml` re-scores it weekly and on pull requests that touch the
graph, retrieval or scorer code. It is not yet a required check: it needs the network, and a failed
fetch must not block a merge. Only top-1 and top-10 are gated; top-3 and top-5 are recorded.

**The scorer ranks by file.** It was corrected to rank by distinct file, as its description always said;
counted by symbol position, top-3 read 14 where it is 16 by file. The tables further down are 3.29.0's
and are left as published.

Two conditions on that gate are worth stating, because they are what stop it becoming decoration:

- It compares **hit counts, and only when the gold set is byte-identical** (a content digest).
  Changing the labels moves the number without Spine having moved at all, so a changed corpus is
  a *rebaseline*, not a regression — otherwise the gate would fail anyone who grew it.
- It gates **only when both sides were actually measured**. Localization needs the corpus on disk;
  reading its absence as zero would report a catastrophe every time the network was down.

That second rule exists because it happened. The first run of this scorer reported **0.00 at every
k across the whole gold set** — a clean, plausible, entirely publishable number — because the
checkouts had been deleted before scoring ran. A total plumbing failure is indistinguishable from
a catastrophic result unless something refuses to score what it cannot read.

---

## Found something wrong?

The labels are hand-made and the corpus is small; both will have mistakes in them. If a row looks
wrong — the fix site is not really the fix, the issue is mismatched, the bug is not a bug — open
an issue. A benchmark nobody can dispute is not a benchmark.
