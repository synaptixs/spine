# A worked example — one ticket, start to finish

This is Spine doing the whole job on a **real, public codebase you can clone yourself**:
[`pallets/click`](https://github.com/pallets/click). Every command below is one you can run, and
**every output on this page is real** — copied from an actual run, not illustrated.

> **The one part that isn't:** the code-generation step near the end needs a model API key, so its
> output is shown as a shape rather than a transcript. It's clearly marked. Everything before it is
> deterministic, needs no credentials, and will produce the **same output for you** — that's the
> product's central claim, and this page is a way to check it.

**The ticket we're working:**

> *Progress bar leaves a stale line when the terminal is resized.*
> When a user resizes the terminal mid-render, the progress bar redraws at the old width and leaves
> artefacts on screen.

A normal day: a bug report in someone else's 78-file codebase, and no idea where it lives.

---

## Setup — 30 seconds

```bash
pip install synaptixs-spine
git clone --depth 1 https://github.com/pallets/click.git
cd click
```

No API key needed for steps 1–5. Nothing is written to your repo until you ask.

---

## Step 1 — Understand the codebase

```bash
orchestrator understand .
```

```
[understand] brownfield — 2613 grounded nodes
[understand] wrote 61 files → ./episteme
```

**1.4 seconds.** It read every file, built a graph of 2,613 facts — each one pointing at a real
`file:line` — and wrote a committed, human-readable knowledge base into `episteme/`.

No LLM was involved. Run it again on the same commit and you get byte-identical output.

## Step 2 — What *is* this project?

```bash
orchestrator state .
```

```
This is a python library / service — 173 types and 1698 functions across 22 components
(78 files). Top priority: Refactor 3 god-classes (>40 members), e.g. `Context` (60),
`ProgressBar` (48).

- Stack: python · tests pytest
- Call graph: available
```

It also read the project's **documentation** into the same graph:

```
## Documentation
- 264 docs ingested; they name 115 of 1949 symbols (6% doc coverage).
- 250 potential drift — doc claims that reference code the graph doesn't have.
```

> **A detail worth noticing.** `orchestrator pkg extract` reports **2,349** grounded nodes; step 1
> reported **2,613**. The difference is exactly **264** — the ingested docs. The numbers reconcile
> because they're counted, not estimated.

## Step 3 — Where does this ticket land?

You don't know this codebase. Ask:

```bash
orchestrator investigate . \
  --title "Progress bar leaves a stale line when the terminal is resized" \
  --text "Resizing mid-render redraws at the old width and leaves artefacts."
```

```
## Where it lands in the code

- `ProgressBar` (Type, 0 callers)          — src/click/_termui_impl.py:57
- `render_progress` (Function, 5 callers)  — src/click/_termui_impl.py:256
- `format_progress_line` (Function, 2 callers) — src/click/_termui_impl.py:229
- `format_bar` (Function, 1 caller)        — src/click/_termui_impl.py:210
- `render_finish` (Function, 1 caller)     — src/click/_termui_impl.py:156

Likely areas: click._termui_impl, click.core, click.formatting
```

From a prose bug report to **the five functions that actually render the bar**, each with a line
number and a caller count. No grepping, no guessing — and again, no LLM.

## Step 4 — What breaks if I change it?

`render_progress` looks like the place. Before touching it:

```bash
orchestrator pkg extract . --query render_progress
```

```
Function py:click._termui_impl.ProgressBar.render_progress @ src/click/_termui_impl.py:256
  called by (5):
    - ProgressBar.__enter__   @ src/click/_termui_impl.py:131
    - ProgressBar.__iter__    @ src/click/_termui_impl.py:145
    - ProgressBar.update      @ src/click/_termui_impl.py:347
    - ProgressBar.generator   @ src/click/_termui_impl.py:380
    - ProgressBar.generator   @ src/click/_termui_impl.py:386
  touches (9): term_len, format_progress_line, get_terminal_size, echo, …
```

## Step 5 — What could break *silently*?

The question that actually matters:

```bash
orchestrator regression . --symbol render_progress
```

```
**Change target:** render_progress at src/click/_termui_impl.py:256
**Target coverage:** ⚠ no test exercises it directly

## Regression gaps — add tests here
_In the blast radius and NOT reached by any test:_

- `__enter__`  — src/click/_termui_impl.py:129
- `__iter__`   — src/click/_termui_impl.py:142
- `update`     — src/click/_termui_impl.py:324
- `generator`  — src/click/_termui_impl.py:355
```

**This is the payoff.** Four functions sit in the blast radius of your change and **no test covers
any of them**. Break one and the suite still goes green. You now know that *before* you write a line
— which is the difference between shipping a fix and shipping a regression.

## Step 6 — Which docs describe this, and are they still true?

```bash
orchestrator pkg docs . -d docs/prompts.md
```

```
8 code-intent mentions · 6 bound to anchors · 2 drift finding(s)
  [drift/backtick] docs/prompts.md: `True` — unbound
  [drift/dotted]   docs/prompts.md: `os.environ.get` — unbound
```

Six of the eight code references in that page bind to real symbols; two don't. Spine links docs to
code down to the **section** — `confirm` is described in `docs/prompts.md#user-input-prompts`,
`Choice` in `docs/parameter-types.md#choice` — so when you change a symbol you can see which doc
sections now lie about it. That's where the "250 potential drift" in step 2 comes from.

*(From your AI assistant, the same question is the `docs_for` tool — see
[CLAUDE_GUIDE.md](CLAUDE_GUIDE.md).)*

---

## Step 6.5 — But is any of this actually right?

Every step above trusted the graph. That trust should be earned with a number, not an adjective:

```bash
orchestrator pkg accuracy
```

It scores the extractor against a committed corpus of 19 hand-labelled repositories covering all
eight front-ends. **Precision is 1.00 on every node kind and every edge kind, in every language** —
so nothing you saw in steps 3–6 was invented. Recall is 1.00 on everything except `CALLS`:

| language | `CALLS` recall |
|---|---|
| `c` `sql` | 1.00 |
| `python` | 0.73 |
| `cpp` `csharp` `go` `java` | 0.67 |
| `typescript` | 0.50 |

That asymmetry is the point. The blast radius in step 4 may be **incomplete**, but it is not
**wrong** — every caller it named is a real caller. A missing edge makes you look further; a
fabricated one sends you somewhere that does not exist.

To measure *this* repo rather than the fixtures:

```bash
orchestrator pkg accuracy . --oracle parity      # declared routes/tables vs the graph
orchestrator pkg accuracy . --oracle invention   # calls to names that don't exist (Python only)
```

---

## Step 7 — Build the fix

Everything so far was read-only. Now Spine writes code — grounded in the structure it just mapped,
so the fix looks like the surrounding codebase rather than like a language model.

```bash
orchestrator sdlc feature --source file://./ticket.md --safe
```

> ⚠️ **This step needs a model API key**, so the output below is the *shape* of a run, not a captured
> transcript — unlike everything above it. Set one key (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) plus
> `ORCHESTRATOR_INTAKE_MODEL` and you can run it yourself.

```
→ spec        derived from the ticket
→ grounding   PKG context: ProgressBar, render_progress, format_progress_line  (real code, file:line)
→ codegen     edit src/click/_termui_impl.py
→ tests       write tests covering render_progress + the 4 uncovered symbols
→ run         pytest … ✗ 1 failed
→ refine      re-generate against the failure … ✓ all pass
→ branch      feat/<id>/PROGRESS-1 committed locally
→ diff        printed for review

No push. No PR. No ticket updated.
```

`--safe` is the default, and it means exactly what it says: a **local branch and a diff**. Nothing
left your machine.

## Step 8 — The two gates

Going live (`--live`) is where Spine stops and asks:

```
🔒 GATE 1 · approve intents   — before any code is generated
🔒 GATE 2 · approve merge     — before anything is pushed or merged
```

Between them it works on its own. Outside them it does nothing you didn't approve. The result is a
**reviewed, CI-green pull request** — and the pipeline ends there. Deployment stays yours.

---

## What just happened

| | |
|---|---|
| **You didn't read the codebase** | Steps 1–3 took seconds and pointed at exact lines |
| **You learned what would break** | Including 4 functions no test protects — before writing code |
| **Nothing was guessed** | Every fact came with `file:line`; steps 1–6 used no LLM at all |
| **Nothing happened without you** | Read-only by default, two approval gates before anything ships |

**Check the claim.** Delete `episteme/` and re-run step 1. Same numbers, same output, every time —
because the comprehension layer is deterministic by construction, not by luck.

## Where to go next

- **[USER_GUIDE.md](USER_GUIDE.md)** — the full walkthrough, including `--live` and the web inbox
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how the pieces above fit together
- **[KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md)** — what the graph knows and how it's built
- **[CLAUDE_GUIDE.md](CLAUDE_GUIDE.md)** / **[CODEX_GUIDE.md](CODEX_GUIDE.md)** — ask all of the
  above in plain language from your AI assistant, no CLI required
