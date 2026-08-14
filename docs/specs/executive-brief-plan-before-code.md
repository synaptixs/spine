# Why we plan before we code — a brief for leadership

**One page of context, one worked example, and what it changes.**

---

## The problem is not that AI writes bad code

It writes plausible code, quickly. The expensive failures are not typos — they are
**decisions made silently**: building the wrong thing, in the wrong place, to satisfy a
requirement that was already satisfied.

We measured this on one real ticket. Six automated attempts. Every one produced usable
source. **None finished.** Total spend: about $4.75, and nothing shipped.

The reason matters more than the number: **every failure was a decision nobody saw.** A
design named the wrong files. A rule forbade the shape the requirement asked for. Two of
the six requirements described behaviour the code already had.

All three were visible in a document that costs nothing to produce.

---

## What the machine knows, and what it doesn't

Spine builds a map of the codebase — every function, what calls what, which tests cover
which lines, which database tables connect. Think of it as a wiring diagram of a building.

**The wiring diagram is excellent at mechanism.** *This wire runs from here to there. Cut
it and these six rooms go dark.*

**It knows nothing about purpose.** It cannot tell you *why* the wire was run, which
customer asked for it, or whether the job it was installed for is already done.

That gap is where the money goes. A machine that knows only mechanism will confidently
rewire a room that was finished last year.

---

## A worked example

**The ticket:** *"Our command-line tool shows a wall of red error text when the server is
switched off. It should show one clear line."*

A reasonable-sounding job. Here is what the free, pre-work document showed — before a
single line was written, and before any spend:

| What it found | Why it mattered |
|---|---|
| **Two of the six requirements were already met.** The code had done that since last year. | An automated run would have reported them "done" having changed nothing. We would have paid for, reviewed, and shipped a change that did a third of what the ticket claimed. |
| **The search tool pointed at the wrong eight files.** It matched the words in the ticket — "registry API" — to files literally named that, and never found the file that actually needs changing. | The machine would have been handed the wrong starting point and worked confidently from it. |
| **Ten of the eleven affected commands have no tests at all.** | The real job is three times the size the ticket implies. That is a scheduling fact, discovered before committing to a date rather than after. |

Three findings. Roughly two minutes of a person's attention. **Zero cost.**

Six automated attempts, costing real money each, surfaced none of them.

---

## Where the build document stands out

It is the artefact that did not exist before: **one page per ticket, produced before any
work, that a human can disagree with.**

Three properties make it different from a status report:

**1. Every statement says where it came from.** Quoted from the ticket · computed from the
code · inferred by a model · decided by a person. A document that mixes a quoted
requirement with a machine's guess, and does not say which is which, lends the authority of
the first to the second. Ours says which.

**2. It is free and repeatable.** No model is called. The same codebase produces the same
document, every time. That is what lets a person approve it and the system *hold them to
that exact version* — if the code moves underneath, the approval goes stale automatically.

**3. It says what it does not know.** Sections it cannot establish say so, rather than
filling the space with something plausible. Confidence is reported as a band with its
reasoning attached — never a single invented percentage.

---

## Why the diagram itself can be trusted

All of that rests on the map being right. A map of a codebase can be built two ways, and
the difference does not show up in the output — which is exactly why it is worth stating.

**The cheap way is pattern-matching.** Scan the source as plain text and pick out anything
that *looks like* a function. It is fast, it works on any language, and it is wrong in ways
nobody can predict: a commented-out block reads as live code, a name mentioned inside a
piece of text reads as a real thing, and anything written in an unusual style is missed
silently.

**The other way is to read the code the way the compiler does** — with the same
understanding of structure as the program that actually builds your software. Nothing is
inferred from appearance.

**Spine only does the second. Every function, type and file in the map comes from a real
parse of the source — never from text-matching.**

Two consequences that matter to a buyer, and to us:

**1. Pattern-matchers do not know when they are wrong.** They return a confident, slightly
shorter answer, and nothing downstream can tell the difference. That is the failure mode
this whole approach exists to avoid — a plausible artefact with no signal that it is thin.

**2. Where the proper parser is unavailable, we produce nothing rather than something
worse.** If the parsing component for a language is not installed, that language is not
mapped and the tool says so. It does not quietly fall back to guessing. This is the same
principle as the document declining to fill a section it cannot establish — applied one
layer down, where nobody would have checked.

*(An earlier version of this brief claimed database schema files were the exception — read by
pattern-matching rather than parsed. That was wrong, and the correction belongs here rather
than in a silent edit: SQL is parsed by a real multi-dialect parser like every other language.
The claim above has no asterisk.)*

This is the foundation under the honest limit stated below. We can say the map is built
correctly. We cannot yet say it is complete — and those are different claims.

---

## What it changes

| | before | after |
|---|---|---|
| First thing a human sees | a pull request, or a failure | a one-page plan |
| Cost before the first human decision | ~$1.19 per attempt | **$0** |
| A failed attempt | analysis thrown away | analysis kept |
| Approval | after the code exists | **before it is written** |

**A failed run costs the same as a successful one.** That is the economics that makes
planning first obviously correct: the expensive step is the one you can skip when the plan
is wrong.

---

## What this does not fix — stated plainly

The build document makes decisions better and failures cheaper. **It does not make the code
generation more likely to succeed.** Every failure in that six-run experiment happened
*downstream* of everything a plan covers.

Expect the first failed hand-off after a good plan. It will feel like the approach did not
work. It did — it moved the failure to a cheaper place and left evidence behind.

**Where we are honest about maturity:** the mechanism is built and released. What we cannot
yet state is a *measured* number for how complete the codebase map is — we can prove it is
self-consistent, not that it is complete. That measurement is scoped and estimated
separately, and it is the difference between a tool we trust internally and one we can put
numbers behind for someone else.
