# From a ticket to a place in the code

**Audience:** engineering · **Written 2026-08-25 against 3.22.0**
**Read alongside:** [`parsing-and-the-pkg.md`](parsing-and-the-pkg.md) (how the graph is built),
[`graphir-sdlc-workflow.md`](graphir-sdlc-workflow.md) (what consumes the result).

**The one-sentence version:** a ticket's own words are tokenised and scored against the *names*
of grounded PKG nodes; the top hits become `Landing` records carrying `file:line`, kind, owning
module and caller count — deterministically, with no model involved after the spec exists.

> ### A thin ticket does not produce a bad answer. It produces no answer.
>
> Everything below runs on the ticket's own words, so **ticket quality sets the ceiling** — a
> ticket naming nothing the codebase uses cannot produce good landing sites, and no amount of
> graph quality compensates.
>
> **But the floor is a refusal, not garbage.** Three mechanisms make that structural rather than
> hopeful: an empty landing list *says it is empty* instead of guessing; an acceptance criterion
> that cannot be bound to a `file:line` **refuses the ticket**; and `validity` can park a run
> before a line of code is written. Worked through in [§7](#7-ticket-quality-sets-the-ceiling--and-the-floor-is-a-refusal-not-garbage).
>
> **The precise claim, and its edge:** the pipeline will never point at a location that does not
> exist, and never pass a criterion bound to nothing. It *can* still build the wrong thing for a
> ticket that is confidently wrong about its own premise — a ticket that names real symbols for
> a change that should not be made lands cleanly and proceeds. Spine guards the *address*, not
> the *intent*. That is why there are two human gates.

---

## 1. The chain

```
issue source        spec                landing sites            everything downstream
(jira / file /  ──► title + problem ──► Landing[]           ──►  validity · design ·
 github / …)        (one model call)    (no model at all)        criteria · blast radius
```

**One model call, at the front, and it is the only one.** The issue tracker's text becomes a
spec — a title and a problem statement — and that spec is validated by `assess()` downstream.
From that point on, everything that decides *where the change lands* is deterministic.

That split is deliberate. Interpreting prose is a judgement and needs a model; deciding which
symbols the prose points at is a query and must not. If the second step called a model, the
landing sites would differ between runs on the same ticket, and nothing built on them —
Evidence, blast radius, bound acceptance criteria — could be reproduced or diffed.

## 2. What the retriever is given

Exactly two fields, concatenated: **the spec's title and its problem statement**
(`sdlc/investigate.py`, `build_investigation`). Not the raw ticket, not its comments, not its
labels or components, not the reporter's attachments.

This is worth stating plainly because it bounds everything below. The retriever's whole view of
the ticket is the sentence or two intake distilled it into. A detail present in the original
issue but dropped by intake is invisible here, and no amount of graph quality recovers it.

## 3. How a hit is decided

`GroundedRetriever.relevant_symbols` (`pkg/retrieval.py`). **Lexical, not semantic** — name and
token overlap, no embeddings, no model.

### 3.1 Both sides are tokenised the same way

`_tokens()` normalises text and identifiers into the same vocabulary:

| Step | Effect |
|---|---|
| Split on `camelCase` boundaries and `_` | `WebhookRetryPolicy` → `webhook retry policy` |
| Lowercase, keep `[a-z0-9]+` runs | punctuation and case stop mattering |
| Drop stopwords and tokens ≤ 2 chars | `the`, `add`, `support`, `file`, `code`, `data`, `new` … |
| Naive singularisation | `edges` → `edge`, so prose plurals meet singular class names |

The stopword list is aimed at spec prose specifically: words like **`add`**, **`support`**,
**`new`**, **`file`** and **`code`** appear in nearly every ticket and would otherwise match
half the codebase.

### 3.2 Every grounded node is scored

```
overlap = |query tokens ∩ node-name tokens|      # zero overlap → not a candidate at all

score   = 3.0 × (overlap / len(node-name tokens))   # how much of the NAME the ticket explains
        + 1.0 ×  overlap                            # how many tokens matched
        + 0.5   if the node is a Type or a Function
```

Each term is doing a specific job:

- **The ratio term dominates**, and it rewards *precision of the name rather than length*. A
  class whose name is entirely explained by the ticket outranks a longer name that merely
  contains the same words. Without it, the biggest identifiers in a codebase would win every
  query by accident.
- **The raw-overlap term** breaks ties toward hits that matched on more of the ticket.
- **The `+0.5` for `Type`/`Function`** makes the result read as an API surface. A module whose
  path happens to share a token is a container, not a landing site.

### 3.3 Two filters, both load-bearing

- **Ungrounded nodes are skipped entirely.** A node with no `file:line` cannot be a place to
  look, and returning one would be a landing site nobody can open.
- **Test files are excluded by default.** A ticket wants the API to change, not the tests that
  exercise it. Tests match strongly on the same tokens, so without this they crowd out the
  implementation they are named after.

### 3.4 The ordering is deterministic

Results sort by descending score, then by node id. Same commit plus same ticket always yields
the same ranking in the same order — including the ties, which is where a naive sort would
otherwise let dictionary order or filesystem order leak in.

## 4. What a hit becomes

Each surviving node is recorded as a `Landing`:

| Field | What it is | Why it is carried |
|---|---|---|
| `name` | the symbol | what to look for |
| `where` | **`file:line`** | the address that makes the claim falsifiable |
| `kind` | `Function` / `Type` / `Module` / … | tells the reader what kind of change this is |
| `callers` | count of inbound `CALLS` | **touch-risk** — what breaks if you change it |
| `module` | owning module | grouping, and blast-radius context |

`module` is resolved by **walking `CONTAINS` upward** to the owning `Module`, falling back to
`provenance.file`. It is never derived from the node id: C and C++ ids are symbols
(`cpp:HSL2RGB`), not locations, so id-grouping would make every function its own area. The
distinct owning modules become the investigation's `areas`.

Alongside the landing sites, the brief pulls in the committed **`episteme/`** domain model and
glossary, so it speaks the codebase's own vocabulary, plus any cross-run prior notes the caller
supplies. Both are best-effort and the brief is silent when they are absent rather than
implying they were consulted.

## 5. What the landing sites are then used for

They are not a suggestion list. They are the Evidence the rest of the run is bound to:

- **`validity`** judges the ticket against them, and is the only stage that can stop a run
  before code is written.
- **`design`** is handed a blast radius keyed off *where the ticket lands*, rather than
  computing one from its own proposal — a radius derived from a proposal is a faithful analysis
  of a fiction, and it reads as verification.
- **Every acceptance criterion** is bound to one of these `file:line`s **or the ticket is
  refused**. An unbound criterion cannot be failed, so it cannot gate anything.

## 6. Where this is honestly weak

**It matches names, not meaning.** A ticket describing a symptom in business language and code
named for its mechanism share no tokens, and the retriever returns nothing:

> *"customers are being charged twice"* → `{customer, charged, twice}`
> the code that fixes it → `IdempotencyGuard`, `DedupeKey`
> **overlap: zero**

Three things bound the damage in practice, and none of them is a fix:

1. **Tickets usually quote identifiers.** Stack traces, class names, endpoint paths and log
   lines are exactly the shape the tokeniser is good at — a ticket that pastes a traceback is
   easy, one that describes a feeling is not.
2. **`episteme/` supplies vocabulary.** The glossary and domain model connect the codebase's own
   terms to the prose around them.
3. **Empty is honest.** With no hits, `landing` is empty and the brief says so rather than
   offering something plausible. The run continues without landing sites, and any acceptance
   criterion that needed one refuses the ticket rather than passing unbound.

**The failure mode is silence, not a wrong address** — consistent with the rest of the system,
and the right way round: a missing landing site sends a human looking, a fabricated one sends
them somewhere that has nothing to do with the ticket.

**None of this is measured yet.** How often the true fix site appears in the top *k* on real
issues is unknown — there is no number, and "0" would not distinguish *bad* from *never
measured*. That metric is the first phase of
[`gap6-benchmarks-roadmap.md`](gap6-benchmarks-roadmap.md), and until it exists this section is
a description of a mechanism rather than a claim about its accuracy.

## 7. Ticket quality sets the ceiling — and the floor is a refusal, not garbage

Everything above runs on the ticket's own words. **A vague ticket cannot produce good landing
sites, and no amount of graph quality compensates**: the retriever's entire view of the problem
is the title and problem statement intake distilled, and if those contain no term the codebase
uses, there is nothing to match against. Input quality bounds output quality, and it bounds it
at the first step.

**But the classic phrasing is wrong for this system.** Garbage in does not produce garbage out
here; it produces a *stop*. That distinction is the whole point of the design, and it is worth
being precise about how a thin ticket actually degrades:

| What the ticket lacks | What happens |
|---|---|
| Any term the code uses | `landing` is **empty**, `areas` is empty, and the brief says so — it does not offer a plausible-looking guess |
| A clear problem statement | Intake's spec is thin, so the query is thin; the hits that survive are generic and their caller counts are the only signal left |
| Anything a criterion can bind to | The criterion cannot be bound to a `file:line`, and **the ticket is refused** rather than built |
| A real target, but named only by symptom | Hits land in the wrong area, and `validity` — judging the ticket against that Evidence — is the stage that can **park the run before code is written** |

So the honest failure mode is **an expensive no**, not a confident wrong answer. That is
deliberate and it is the same trade the graph makes everywhere: a missing fact sends a human
looking, a fabricated one sends them somewhere unrelated with full confidence. A pipeline that
produced a plausible change from an unclear ticket would be worse than one that stops, because
the plausible change still has to be reviewed by someone who now has to work out that the
premise was wrong.

**What that costs, stated rather than implied:** a thin ticket still spends the intake model
call and a full extraction before it parks. The refusal is cheap relative to reviewing a bad
change, not free.

### The same request, written two ways

Both tickets ask for the same thing. Only one of them can be acted on, and the other **does not
produce a worse change — it produces no change**.

---

**Ticket A — under-specified**

> **Title:** Orders are broken
> **Problem:** Customers are complaining that things go wrong at checkout sometimes. Please fix
> this, it is urgent.

**Tokens after normalisation:** `{order, broken, customer, complaining, thing, checkout,
sometimes, please, fix, urgent}` — "please", "urgent" and "sometimes" are noise; nothing here is
an identifier any codebase would use.

**What happens, step by step:**

| Stage | Outcome |
|---|---|
| Retrieval | `checkout` and `order` may hit a handful of generically-named symbols; most of the query matches nothing. Score is dominated by the ratio term, so broad hits rank low |
| `Landing[]` | Empty or near-empty, and the brief renders **"nothing grounded"** rather than a plausible list |
| Acceptance criteria | *"things go wrong sometimes"* names no observable behaviour, so nothing can be bound to a `file:line` |
| `validity` | **Parks the run.** No code is generated, no PR is opened, no diff is produced |

**Output: a stop, with a reason.** Not a speculative patch to a file that looked related.

---

**Ticket B — the same problem, specified**

> **Title:** Duplicate charges when the payment retry path re-submits
> **Problem:** `PaymentRetryHandler.submit()` re-sends the authorisation when the gateway times
> out, so a slow-but-successful call is charged twice. Seen in
> `payments/retry_handler.py:214`; trace attached. After the fix, a retry for an
> already-authorised order must be a no-op and must be recorded as such.

**Tokens:** `{duplicate, charge, payment, retry, path, submit, handler, gateway, authorisation,
order}` — dense with terms the codebase actually uses.

| Stage | Outcome |
|---|---|
| Retrieval | `PaymentRetryHandler` matches on nearly its whole name — the ratio term rewards exactly this — plus `submit`, and the surrounding payment module |
| `Landing[]` | Real symbols with `file:line`, kind, owning module, **and caller counts** — the last of which tells you what else breaks |
| Acceptance criteria | *"a retry for an already-authorised order is a no-op"* binds to a symbol, so it survives |
| `validity` | Proceeds. Design gets a blast radius keyed off where the ticket lands |

---

**The difference is not output quality. It is whether there is output at all.** Ticket A does not
yield a lower-confidence version of Ticket B's answer; it yields a refusal with the reason
attached. Every mechanism above is built to make that the outcome rather than a change that
looks right and is founded on nothing — because a plausible wrong change costs a reviewer more
than a stop does, and it costs them *after* they have started trusting it.

**Where the guarantee ends:** Ticket B would land just as cleanly if the duplicate charge were
actually caused elsewhere and `PaymentRetryHandler` were innocent. Naming real symbols confidently
is enough to proceed. The graph guarantees the *address exists*; it cannot guarantee the reporter
diagnosed the right one. That residual is what the two human gates are for, and no amount of
retrieval quality removes it.

### What makes a ticket land well

Nothing exotic — it is the same thing that makes a ticket good for a human engineer, which is
the point:

- **Name things the way the code does.** One real identifier — a class, a function, an endpoint
  path, a config key — is worth several paragraphs of description, because the tokeniser matches
  names and nothing else.
- **Paste the evidence.** A stack trace, a log line, a failing request. These are dense with
  exactly the identifiers the retriever is built to find.
- **Say what should be true afterwards.** Acceptance criteria that name observable behaviour
  can be bound to a symbol; criteria that describe a feeling cannot, and will refuse the ticket.
- **Describe the mechanism, not only the symptom.** *"Customers are charged twice"* is a
  symptom. Adding *"the retry path re-submits the payment"* gives the retriever something a
  codebase actually names.
- **One change per ticket.** Landing sites are ranked over the whole spec; two unrelated
  problems in one ticket split the token budget and dilute both.

**This is not a workaround for a weak retriever.** A ticket that names nothing in the system it
is about is under-specified for a human too — the difference is that a human will go and ask,
and this pipeline will stop. Improving the retriever (semantic matching, embeddings) would raise
the ceiling on vague tickets, but it would also reintroduce guessing into the one path that is
currently deterministic, which is a trade nobody has measured yet — see
[`gap6-benchmarks-roadmap.md`](gap6-benchmarks-roadmap.md).

## 8. Two variants worth knowing

- **`api_surface(text)`** — the same query, but every `Module` hit is expanded into its grounded
  `Type`/`Function` children. A module that matches a ticket is usually a *container* of the
  APIs the work needs, not the thing to edit.
- **`diff_impact(...)`** — the reverse direction, used after code exists: given changed line
  ranges, find the enclosing symbols and their callers, flagging **cross-file** callers, which
  are the ones a diff can silently break.

## 9. If you are extending this

- **Add facts, not heuristics.** If retrieval needs to know something new, extend `facts.py` and
  the front-ends. Scoring more cleverly over facts that were never extracted is a second, worse
  parser.
- **Keep it no-LLM.** Everything after the spec is deterministic by design, and the properties
  that depend on it — reproducible Evidence, a diffable brief, bound criteria — all fail
  together the moment a model enters this path.
- **Bound honestly.** `max_symbols` defaults to 10. A truncated list must read as *"top N of
  M"*, never as a complete answer.
