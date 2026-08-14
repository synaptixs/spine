# PKG accuracy — what is measured, what is not, what to do next

**As of 3.18.0** (live on PyPI), after the seven phases of
[`pkg-accuracy-roadmap.md`](pkg-accuracy-roadmap.md).

This is a review document, not a plan of record. It states the current numbers, separates
*measured and good* from *measured and bad* from **not measured at all** — the third
category being the one this programme exists to make visible — and proposes an order of
work. Every figure below was read from the committed scoreboard or produced by running the
scorer; none are recalled or estimated.

---

## 1. Where things actually stand

`src/orchestrator/pkg/scoreboard.json`, 19 corpus cases across 8 front-ends:

| | Result |
|---|---|
| **Precision** | **1.00 on every node kind and every edge kind, in all 8 languages.** No exceptions. |
| **Recall** | 1.00 on every kind **except `CALLS`**. |
| **Invention** | **0** invented call targets out of **15,212** call edges. |
| **Parity** | shortfall **0**, surplus 8 (83 in graph vs 75 declared). |

`CALLS` recall, the only column with any red in it:

| Language | matched/expected | Recall |
|---|---|---|
| c | 2/2 | 1.00 |
| sql | 1/1 | 1.00 |
| python | 8/11 | 0.73 |
| cpp, csharp, go, java | 2/3 each | 0.67 |
| typescript | 3/6 | **0.50** |

Gates today: `corpus` **strict**, `parity` **ratchet**, `invention` and `runtime`
ungated (recorded as trends). That split is deliberate — invention moves with ordinary
commits, so gating it would fail PRs for doing normal work.

**Read the precision row carefully, because it is the load-bearing claim.** Nothing in the
graph is invented: every edge Spine emits is one that exists. The gap is entirely
*silence* — edges that exist in the source and are not emitted. For a knowledge graph an
agent reasons over, those two failure modes are not equally bad, and Spine currently has
only the survivable one.

## 2. The one real recall gap: calls through a variable

Ten expectations are unmatched across the entire corpus. **All ten are `CALLS`, and all
ten are the same defect** — a call whose receiver is a variable rather than a name:

```
cpp/instance_calls          cpp:viaParameter                  -> cpp:Handler::run
csharp/instance_calls       csharp:Svc.Dispatch.ViaParameter  -> csharp:Svc.Handler.Run
go/instance_calls           go:svc.Dispatch                   -> go:svc.Handler.Run
java/instance_calls         java:svc.Dispatch.viaParameter    -> java:svc.Handler.run
typescript/instance_calls   ts:app/dispatch.viaParameter      -> ts:app/handler.Handler.run
typescript/instance_calls   ts:app/dispatch.viaLocal          -> ts:app/handler.Handler.run
typescript/instance_calls   ts:app/dispatch.viaLocal          -> ts:app/handler.Handler
python/dispatch             py:svc.handlers.dispatch          -> py:svc.handlers.Handler.run
python/dispatch             py:svc.registry.invoke            -> py:svc.handlers.Handler.run
python/dispatch             py:svc.registry.build             -> py:svc.handlers.Handler
```

This is one capability, not ten defects. And the corpus was built to split it into two
tiers that differ enormously in cost:

**Tier 1 — the class is named at the call site.** Nine of the ten. A parameter annotated
`h: Handler`, a local initialised `const h = new Handler()`, a call on the return value of
a function annotated `-> Handler`. No inference is required; the front-end simply is not
reading a type that is sitting in the syntax tree. Note that `viaLocal -> Handler` is not
even a receiver problem — `new Handler()` is a direct construction the extractor is
missing outright.

**Tier 2 — the class is never named at the call site.** Exactly one:

```python
TABLE = {"default": Handler}
def build(name: str) -> Handler:
    cls = TABLE[name]
    return cls()          # py:svc.registry.build -> py:svc.handlers.Handler
```

Resolving this needs value tracking through a dict subscript. It is the case where a
wrong guess would cost precision — the one property currently at 1.00 across the board.

**Projected effect of Tier 1 alone**, computed against the corpus:

| Language | now | after Tier 1 |
|---|---|---|
| cpp, csharp, go, java | 0.67 | **1.00** |
| typescript | 0.50 | **1.00** |
| python | 0.73 | **0.91** |
| c, sql | 1.00 | 1.00 |

Seven of eight languages reach perfect `CALLS` recall, and Python's residue is the single
Tier 2 case. **This is the highest-value work available and it carries no precision risk**,
because every edge it adds is justified by a declared type in the source.

## 3. Shipped extraction with zero accuracy coverage

The more important gap, because it is invisible in the scoreboard rather than red in it.
These kinds have **no corpus case anywhere**, so they have no precision or recall number —
not a bad one, *none*:

| Kind | Produced by | Coverage |
|---|---|---|
| `Doc` node, `MENTIONS` edge | `doc_link.py`, `doc_source.py` | none |
| `CONSUMES` edge | `python_client.py` | none |
| `Intent` node, `SERVES` edge | `intent_link.py` | none |

And several kinds are measured in *one* language while shipping in others:

| Kind | Measured in | Also emitted by, unmeasured |
|---|---|---|
| `Endpoint`, `EXPOSES` | python only | `csharp_extractor.py`, `java_extractor.py` |
| `Entity` | sql only | `python_orm.py`, `csharp_extractor.py`, `migrations.py`, `schema.py` |
| `IMPORTS` | c, python, typescript | others |
| `IMPLEMENTS` | csharp, go, java, typescript | others |
| `READS`/`WRITES`/`REFERENCES` | sql only | `data_layer_link.py` |

C# and Java route extraction, Python ORM entity extraction, and the whole documentation
tier are features users can run today against zero evidence that they are correct. That is
precisely the condition the roadmap was written to eliminate, and closing it is cheap —
these are fixture cases, not new machinery.

## 4. Facts with no reader

`intent_link.py` produces `Intent` nodes and `SERVES` edges, measured at **11.8% symbol
coverage** on this repository (1,172 of 9,969 symbols, 34 tickets). It is behind an
opt-in `--intents` flag for a good reason: **nothing renders it.** Not `understand`, not
`state`, not the web UI. The facts land in the graph and no surface shows them.

Either give it a reader or say plainly in the docs that it is a graph-level capability
with no presentation yet. Shipping a flag whose output is invisible is the kind of thing
that erodes the credibility this programme is buying.

## 5. Oracles

**Runtime oracle is Python-only.** `runtime_oracle.py` uses `sys.monitoring` (PEP 669),
which has no equivalent in the other seven front-ends. It is ungated, so this is a
coverage limit rather than a correctness risk — but "runtime-verified" currently means
"runtime-verified for Python", and any external claim needs that qualifier.

**Parity surplus of 8** (83 endpoints in graph, 75 declared) is unexplained in the
scoreboard. Shortfall is what the ratchet gates and it is 0; surplus is currently just a
number nobody has accounted for. It may be entirely legitimate — the same verb+path served
by multiple services collapses to one node — but it should be explained rather than
tolerated.

## 6. Operational, open right now

**The episteme regeneration loop on `main` is unfixed.** Merging a regeneration PR creates
a merge commit, which changes `HEAD`, which changes the stamp, which produces the next
regeneration branch — forever. Verified: merging #196 immediately produced
`chore/episteme-044cdfd`. Branch `chore/episteme-044cdfd` is held back deliberately;
merge it only *after* the loop is fixed, at which point it converges the stamp without
spawning a successor. Decision pending — recommended fix is
`paths-ignore: ['episteme/**']` on the push trigger.

**The stamp reports "plus uncommitted changes" on a clean CI checkout.** Verified not to be
self-reference: `repo_state()` is read at `understand.py:172`, before any file is written.
Leading hypothesis is that `uv sync --extra dev` rewrites `uv.lock` on the runner —
unverified. It matters beyond cosmetics: per invariant 8 a dirty tree means the
commit-keyed cache is never trusted, so CI re-extracts the whole repo every run.

**Local `uv` (0.8.0) is older than `develop`'s lock**, which is why commits here have
needed `--no-verify` — and that bypasses *all* hooks, not just the lock check. Upgrading
`uv` locally removes a standing reason to skip the gate.

## 7. Recommended order

1. **Tier 1 receiver resolution** (§2). Biggest measurable win, no precision risk, moves
   seven languages to 1.00 `CALLS` recall. Do this first.
2. **Fix the episteme loop** (§6). One line, and `main` stops generating busywork.
3. **Corpus cases for the unmeasured kinds** (§3), in priority order: C#/Java endpoints,
   Python ORM entities, `CONSUMES`, then `Doc`/`MENTIONS`. Fixtures only.
4. **Decide the Intent tier's fate** (§4) — reader, or documented as graph-only.
5. **Account for the parity surplus** (§5). Explain the 8, then decide whether surplus
   deserves its own ratchet.
6. **Tier 2 receiver resolution** (§2) — only with an invention check in the same change,
   since this is the first work that can plausibly cost precision.

## Decisions wanted

- **Gate `invention` once Tier 1 lands?** It is 0/15,212 today. A `strict` gate would make
  "Spine invents nothing" an enforced property rather than an observed one — but it fails
  any PR that legitimately adds an unresolvable call, which is why it is ungated now.
- **Does §3 block a marketing claim?** "Every node comes from a real parser, never a regex"
  is true and provable. "Measured accuracy across 8 languages" is true for the kinds in the
  corpus and not yet true for endpoints outside Python, entities outside SQL, or docs at
  all. The honest phrasing depends on which claim you intend to make.
- **Tier 2 at all?** One corpus case, real precision risk, and dynamic dispatch through a
  lookup table is rare in most codebases. Declining it and documenting the limit is a
  defensible answer.
