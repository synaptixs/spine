# How well does Spine actually work?

**Measured 2026-09-01, released in Spine 3.29.0.** Everything on this page is reproducible from a
checkout; the commands are at the bottom and they are the same ones that produced these numbers.

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

**C# has no slot.** Five repositories, six front-ends — one language is unrepresented, and
nothing here says anything about it.

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
the other seven front-ends.

**6 · It measures retrieval, not repair.** Nothing here says the code Spine writes is correct.
That is a different programme, deliberately separate, with no number yet.

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
