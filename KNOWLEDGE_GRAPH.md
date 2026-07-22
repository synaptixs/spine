<p align="center">
  <img src="assets/pkg-hero.png" alt="Spine — Product Knowledge Graph: a code-native graph of modules, types, functions, fields, endpoints, and entities" width="820">
</p>

# The Product Knowledge Graph (PKG)

> How Spine *understands your codebase* — a code-native knowledge graph that grounds
> every feature, fix, and review in what your repo actually contains.

This is the single guide to the PKG: what it is, the model it builds, how to use it,
and how it powers both **brownfield** (existing code) and **greenfield** (new) projects.

---

## TL;DR

```bash
pip install synaptixs-spine

orchestrator understand .          # build the PKG → write a committed episteme/
orchestrator pkg extract . -q User # inspect: callers + blast radius of a symbol
```

The PKG is built **from your code** (deterministic, no LLM). Spine reads it before it
writes anything, so generated code matches your repo's real structure and conventions.

---

## 1. What the PKG is (and isn't)

The PKG is a **graph of your code** — the modules, types, functions, fields, endpoints,
and data entities in your repo, plus the relationships between them (calls, imports,
implements, reads/writes, foreign keys). It is extracted directly from source via
language-native parsers, so it's **accurate, not guessed**.

> **It is not ontomesh.** A common confusion: the PKG understands *code structure*;
> ontomesh (optional) understands the *business domain*. The PKG is always-on and
> required; ontomesh is an optional layer that composes on top. See
> [§7](#7-how-grounding-uses-the-pkg) and [OPERATIONS.md](OPERATIONS.md#the-semantic-spine).

```mermaid
flowchart TB
    repo["📁 Your repository"]
    subgraph code["Code-native — always on"]
        pkg["PKG<br/>(code graph)"]
        mb["episteme/<br/>(committed knowledge)"]
    end
    subgraph dom["Domain — optional"]
        onto["ontomesh<br/>(business ontology)"]
    end
    ground["Grounding"]
    deliver["Governed delivery<br/>(features · fixes · findings)"]

    repo --> pkg --> mb
    pkg --> ground
    onto -. optional .-> ground
    ground --> deliver
```

---

## 2. The data model

Everything in the PKG is one of seven **node kinds**, connected by nine **edge kinds**.
Every node and edge carries **provenance** — the exact `file:line` it came from — so any
claim is traceable back to source.

### Node kinds

| Node | Represents |
|---|---|
| `Module` | A file / module |
| `Type` | A class, struct, interface, or enum |
| `Function` | A function, method, or procedure |
| `Field` | An attribute, property, or column |
| `Endpoint` | An HTTP route or RPC |
| `Entity` | An ORM model / data entity |
| `Doc` | A documentation page or section (README, design doc, PDF) — section-granular, e.g. `doc:README.md#usage`, with provenance at the heading line |

Each node has a stable, language-prefixed id (e.g. `py:billing.invoice.Invoice`), a
`name`, its `language`, and `provenance` (`file:line`). Nodes referenced but not defined
in-repo (e.g. a third-party class) are marked `external`.

### Edge kinds

| Edge | Meaning |
|---|---|
| `CONTAINS` | module → type, type → method |
| `IMPORTS` | module → module |
| `CALLS` | function → function |
| `IMPLEMENTS` | subclass / interface implementation |
| `READS` / `WRITES` | function → field/column |
| `EXPOSES` | endpoint → handler |
| `REFERENCES` | entity → entity (foreign key) |
| `MENTIONS` | doc → the code symbol/module it describes (bound, `file:line`-grounded) |

### How it fits together

```mermaid
flowchart LR
    M[Module] -->|CONTAINS| T[Type]
    M -->|IMPORTS| M2[Module]
    T -->|CONTAINS| F[Function]
    T -->|IMPLEMENTS| T2[Type]
    F -->|CALLS| F2[Function]
    F -->|READS / WRITES| FL[Field]
    EP[Endpoint] -->|EXPOSES| F
    EN[Entity] -->|REFERENCES| EN2[Entity]
    D[Doc] -->|MENTIONS| F
```

This is what lets Spine answer questions like *"what calls this function?"*, *"what's the
blast radius of changing this type?"*, and *"which endpoints touch this table?"* — by
walking edges, not by guessing.

---

## 3. How the PKG is built

```mermaid
flowchart LR
    src["Source files<br/>(.py · .java · .ts/.tsx · .cs · .c/.h · .cpp · .go · .sql)"] --> ext["Language extractor<br/>(tree-sitter / AST / sqlglot)"]
    ext --> facts["Facts<br/>Nodes + Edges + Provenance"]
    facts --> cache["Per-commit cache"]
    facts --> store["Fact store<br/>(queryable)"]
    store --> mb["episteme/*.md"]
    store --> grd["Codegen grounding"]
    store --> db["SQLite projection"]
```

- **Deterministic, no LLM.** Extraction is pure parsing — same code in, same facts out.
- **Per-language front-ends.** A common schema with pluggable parsers:
  | Language | Status | Enable with |
  |---|---|---|
  | Python | ✅ built-in | (default) |
  | Java | ✅ | `pip install 'synaptixs-spine[java]'` |
  | TypeScript / TSX | ✅ | `pip install 'synaptixs-spine[typescript]'` |
  | C# | ✅ + framework edges | `pip install 'synaptixs-spine[csharp]'` |
  | C | ✅ + `#include` graph | `pip install 'synaptixs-spine[c]'` |
  | C++ | ✅ classes/namespaces/inheritance | `pip install 'synaptixs-spine[cpp]'` |
  | Go | ✅ + interface satisfaction (`IMPLEMENTS`) | `pip install 'synaptixs-spine[go]'` |

  C# additionally lifts **framework edges** into the graph: ASP.NET Core controllers
  and Minimal-API routes become `Endpoint` nodes with `EXPOSES` edges to their
  handlers, and EF Core entities (`DbSet<T>` / `[Table]`) become `Entity` nodes with
  `REFERENCES` edges following navigation properties.

  C uses the **translation unit (file)** as the module and builds the **`#include`
  graph** (`IMPORTS`); a function prototype in a `.h` and its definition in a `.c`
  **merge onto one node** (the definition wins), `CALLS` resolve across files by name,
  and a struct member whose type is another struct becomes a `REFERENCES` data edge.

  C++ is a **superset of the C front-end** — it reuses the include graph and the
  header/source merge, then adds the object model: `class`/`struct`/`union`/`enum`
  become namespace-qualified `Type` nodes, base classes become `IMPLEMENTS` edges
  (multiple inheritance → multiple edges), member functions merge an in-class
  declaration with an out-of-line `Class::method` definition, templates emit their
  `Type`/`Function`, and `CALLS`/`REFERENCES` carry over.

  Go's module unit is the **package (its directory)** — every `.go` file in a dir merges
  onto one `Module`. Structs/interfaces/aliases become `Type` nodes, funcs and receiver
  methods become `Function`s, and struct fields become `Field`s. Its distinctive edge is
  **interface satisfaction** (`IMPLEMENTS`): because Go has no `implements` keyword, a
  concrete type is matched to each in-repo interface by **method set** — name + arity over
  value **and** pointer receivers — so a type that structurally satisfies an interface is
  linked to it. `CALLS` resolve same-package functions and receiver-method calls, and a
  struct field whose type is another same-package type becomes a `REFERENCES` edge.
- **Cached per commit.** Re-running on an unchanged tree reuses the cache; `--refresh`
  forces a re-extract. So `understand` is cheap to re-run as the code evolves.

---

## 4. Using the PKG — CLI reference

### `orchestrator understand` — the everyday entry point
Builds the PKG and renders a committed, human- and AI-readable **episteme**:

```bash
orchestrator understand .                 # writes ./episteme/*.md
orchestrator understand . --refresh       # re-extract instead of using the commit cache
```

It produces: `architecture.md`, `domain-model.md`, `tech-context.md`, `conventions.md`,
`glossary.md`, and `progress.md`. **Commit `episteme/`** so your whole team — and any
AI tool — reads the same code-true project truth.

A **doc-ingestion post-pass** folds the repo's own documentation (Markdown, reST, plain
text, and — with the `[docs]` extra — **PDF**) into the same graph: a `Doc` node per doc
section, `MENTIONS`-linked to each code symbol it names. Nothing to configure; a repo with
no docs is unaffected. This is what lets `state` report doc coverage and the `docs_for`
`/spine` tool answer *"which docs describe this symbol?"*.

### `orchestrator state` — the team-facing current-state report
A higher-level view rendered from the same graph (deterministic, no LLM) — *what the repo
is today and how healthy it looks*:

```bash
orchestrator state .                       # developer view (architecture, components, hotspots)
orchestrator state . --lens stakeholder    # plain-language view
orchestrator state . --out STATE.md        # write to a file (otherwise printed)
```

It renders a plain-language **overview**, an **infrastructure & runtime** breakdown (the
datastores, queues, cloud, container services and external APIs the repo *declares* it
needs — read from manifests, build files, and `docker-compose`), a **code-structure** map
(layout by component + entry points), a **system-architecture diagram** (components grouped
into zones with weighted dependency arrows from the import/`#include` graph), a
**component-dependency** table, **call-graph hotspots**, complexity / god-components,
test-coverage and recent-activity signals, a **Documentation** section (how much of the code
the docs describe — symbol coverage % — and the top **doc drift**: doc claims about code the
graph can't resolve), a name-based security surface, and prioritized recommendations. A
report is a *view* — re-run to refresh.

### `orchestrator pkg extract` — inspect the raw graph
```bash
orchestrator pkg extract .                # summary of nodes/edges by kind
orchestrator pkg extract . -q Invoice     # callers + blast radius of a symbol
orchestrator pkg extract . --json         # dump all facts as JSON
```

### `orchestrator pkg export` — the queryable projection
```bash
orchestrator pkg export . --db pkg-facts.db   # a kind-per-table SQLite database
```
Query it with any SQLite tool — one table per node/edge kind, provenance included. (This
is also the "ontomesh-ready" projection that bridges code facts to the domain layer.)

### `orchestrator pkg docs` — reconcile specific docs on demand
```bash
orchestrator pkg docs . -d README.md -d ARCHITECTURE.md
```
Reconciles the documentation claims in the docs you *name* against the actual fact graph and
flags drift (docs that describe code that no longer exists, etc.). This is the **targeted,
read-only** counterpart to whole-repo ingestion: `understand`/`state` fold **all** the repo's
docs into the graph as `Doc` nodes + `MENTIONS` edges automatically, whereas this command
checks a specific file (or two) and prints the binding/drift summary without touching the graph.

### SQL — the data layer, extracted from source

`.sql` files are a first-class language (install the **`sql`** extra —
`pip install 'synaptixs-spine[sql]'`; parsing is [`sqlglot`](https://github.com/tobymao/sqlglot),
pure-Python, multi-dialect). Unlike the *live-DB* introspector (which needs a running
database), this reads your schema **from source**, so every table and column is grounded to
a `file:line` you can jump to:

| SQL | PKG |
|---|---|
| `CREATE TABLE` / column | `Entity` / `Field` |
| `FOREIGN KEY … REFERENCES` | `REFERENCES` edge (**ground truth**) |
| `CREATE VIEW … AS SELECT` | `Entity` + `READS` its base tables |
| `SELECT` / `INSERT` / `UPDATE` / `DELETE` | `READS` / `WRITES` |
| `CREATE FUNCTION` / `PROCEDURE` | `Function` + body `READS` / `WRITES` / `CALLS` |

Three things make it more than a table dump:

- **Migration-aware.** A `migrations/` directory of ordered `.sql` files is *folded in order*
  (applying `ADD` / `DROP` / `RENAME` / `DROP TABLE`), so `understand` / `state` show the
  **current** schema — not every column that ever existed.
- **Cross-language authoritative.** When a repo has both a `.sql` schema and an ORM model
  (e.g. C# EF Core), the two are reconciled onto one entity per table and the **schema's**
  foreign keys win over the ORM's inferred ones.
- **Grounded like code.** Data-shaped tickets ("add a column to `orders`", "who writes to
  `sessions`?") retrieve the real schema and blast-radius, instead of the agent guessing.

**Multi-dialect.** The dialect is **auto-detected per file** (PostgreSQL, MySQL, SQL Server /
T-SQL, Oracle, SQLite, …) from distinctive syntax — so T-SQL `[bracketed]` identifiers, MySQL
back-ticks, and Oracle `VARCHAR2` parse under their own grammar instead of degrading as
Postgres. Portable DDL with no tell-tale falls back to Postgres. Pin it with `--dialect`
(on `pkg extract` / `understand` / `state`) when detection can't tell.

**Greenfield too.** SQL isn't only read — `sdlc feature --language sql` *generates* the data
layer: it scaffolds a `migrations/` directory, writes a DDL migration for the intent, and
validates it by **applying it to an ephemeral database** — in-memory SQLite by default (zero
toolchain), or a throwaway Postgres (`SDLC_SQL_ENGINE=postgres`, the `sql-postgres` extra +
Docker) for dialect fidelity. A failed apply is the refine signal, exactly like a failing
test; the applied schema is introspected back through this same model to confirm it matches
the intent.

### Where each artifact is persisted

Knowledge lives in **four layers**, each with a different lifecycle. The **source of truth
is always your code** — everything else is either a regenerable cache, a committed
rendering, or a durable store that accumulates over time. Nothing can silently drift,
because the graph is rebuilt from source whenever the commit changes.

| Layer | Location | Committed? | Lifecycle |
|---|---|---|---|
| **PKG (the graph)** | `~/.cache/orchestrator/pkg/<repo-hash>-<HEAD-sha>.json` | No | **Regenerable cache.** Commit-keyed and used only on a *clean* tree at the exact HEAD SHA; a dirty tree or new commit triggers a fresh, deterministic re-extraction. Delete it anytime — it rebuilds from code. |
| **`episteme/`** | `<repo>/episteme/*.md` | **Yes** — commit it | **Durable, versioned doc.** The human- and AI-readable rendering of the graph. Travels with the code, shows up in diffs/PRs, and is the one artifact meant to live in version control. Refresh with `understand --refresh`. |
| **Current-state report** | stdout, or `--out <file>` | No | **Ephemeral view.** A point-in-time snapshot for a person/audience; nothing is written unless you pass `--out`. Re-run to refresh. |
| **Cross-run memory** | Registry DB (`MemoryRow`, keyed per repo), via `ORCHESTRATOR_DATABASE_URL` | Durable DB | **Accumulating.** Learned conventions and abstractions distilled *across* runs, surfaced to codegen as a `recall_memory` tool. Active only when the full pipeline's database is configured (see [USER_GUIDE Step 7](USER_GUIDE.md)). |

**In practice:** run `understand` and **commit `episteme/`** — that's the durable,
team-visible "what's already in place." The PKG cache regenerates per commit under the
hood (this is what blast-radius and grounding read from — see §7). The current-state
report is a view you regenerate on demand; cross-run memory compounds automatically once
the pipeline DB is on.

---

## 5. Brownfield projects — comprehend, then deliver

For an **existing** repo, the PKG gives Spine an instant, accurate map so new work fits in.

```mermaid
flowchart TD
    A["Existing repo"] --> B["orchestrator understand .<br/>(PKG + episteme/)"]
    B --> C{What do you want?}
    C -->|New feature| D["sdlc feature --safe<br/>(grounded in real layout)"]
    C -->|Bug fix| E["scoped by blast radius<br/>(callers, impacted symbols)"]
    C -->|Findings| F["profile · audit · reviewer"]
    D --> G["Reviewed PR"]
    E --> G
    F --> H["Issues / report"]
```

1. **Comprehend** — `orchestrator understand .` builds the graph; `profile`/`audit`
   surface a map and findings. No LLM, so it's fast and deterministic.
2. **Deliver, grounded** — `orchestrator sdlc feature --source <spec> --safe`. Codegen
   uses `--layout auto`, which **follows the repo's existing structure and never
   scaffolds**. The run prints `[grounding] target-KG context: N chars` — that's the PKG
   feeding real symbols and conventions into generation, so new code reuses what's there.
3. **Fix & review** — the same graph powers **blast-radius**-scoped fixes (it knows the
   callers of what you change) and the reviewer/auditor passes.

> **Why it matters:** on a large unfamiliar codebase, the PKG is the difference between
> "an agent guessing from a few files" and "an agent that knows the call graph, the blast
> radius, and your conventions."

---

## 6. Greenfield projects — knowledge that grows with the code

For a **new** repo, there's little to extract at first — so the PKG **accumulates as you
build**. Knowledge isn't a one-time scan; it compounds.

```mermaid
flowchart LR
    s0["Empty repo<br/>(stub episteme)"] --> s1["Feature 1<br/>scaffolds src/ + tests/"]
    s1 --> s2["PKG grows<br/>(new nodes + edges)"]
    s2 --> s3["Feature 2<br/>grounded in Feature 1"]
    s3 --> s4["PKG grows again…"]
    s4 --> s5["Mature, self-describing repo"]
```

1. The first `understand` writes a **stub** (there's barely any code yet).
2. The first `sdlc feature` run **scaffolds** `src/<package>/` + `tests/` and a
   pytest-ready layout, then generates into it.
3. As each feature lands, the PKG gains nodes and edges — and the **next** feature is
   grounded in everything built so far. Re-run `orchestrator understand . --refresh` (or
   it refreshes on the next run) to keep `episteme/` in step.
4. Over time the repo **builds its own code-true memory**, so even a brand-new project
   quickly becomes one an agent (or a new teammate) can navigate.

So: **brownfield** starts with a full map; **greenfield** grows one. Either way, by the
time Spine writes code, it's grounded in the current truth of the repo.

---

## 7. How grounding uses the PKG

Before generating, Spine retrieves the **relevant slice** of the PKG for the task and
prepends it to the model's context (the `PKGCodegenGrounder`). That slice includes:

- the **relevant existing symbols** (so new code reuses them, matching conventions),
- the **API surface** around the change,
- the **blast radius** — callers and impacted symbols of what's changing,

and a **verifier** checks the generated code's claims back against the graph. When
ontomesh is configured, its cited *domain* knowledge composes with this *code-true*
context (`CompositeGrounder([PKG, ontomesh])`) — code structure **and** business meaning.

The headline retrieval query is **blast radius**: *given the lines I'm about to change,
what's impacted and where do I look for breakage?* That's what keeps changes scoped and
reviews honest.

---

## 8. Inspecting & querying

- **Quick CLI:** `orchestrator pkg extract . -q <Symbol>` → callers + blast radius.
- **Full graph:** `orchestrator pkg extract . --json` → every node and edge.
- **SQL:** `orchestrator pkg export . --db pkg-facts.db` → a kind-per-table SQLite DB you
  can query directly, e.g.:
  ```sql
  -- which endpoints expose handlers that write to a given column's table?
  SELECT * FROM edge_EXPOSES;     -- one table per edge kind
  SELECT * FROM node_Function;    -- one table per node kind, with file:line provenance
  ```
- **Committed prose:** `episteme/*.md` — the human-readable rendering of the graph.

---

## 9. Honest limits

- **Static, not runtime.** The PKG is built from source structure; it doesn't capture
  runtime behavior, dynamic dispatch it can't see, or values only known at execution.
- **Parser coverage.** Python/Java/TypeScript/C#/C/C++ and **SQL** today. Other languages
  aren't extracted yet (their files are simply not represented). For C, parsing is
  pre-preprocessor — heavy macro use yields partial facts (we never run `cpp`). For SQL, the
  dialect is auto-detected (override with `--dialect`); stored-procedure bodies are re-parsed
  best-effort — exotic procedural PL/pgSQL / T-SQL constructs degrade to partial facts;
  migration folding assumes linearly-ordered files.
- **Heuristic edges.** Some edges (e.g. ORM-inferred foreign keys) are inferred and improve
  over time; treat them as strong hints, not proofs. When a repo ships a `.sql` schema, it
  is treated as **authoritative** and those FKs become ground truth (see §4).
- **Domain meaning is separate.** The PKG knows *structure*, not business intent — that's
  ontomesh's job, and it's optional.

---

## See also

- [USER_GUIDE.md](USER_GUIDE.md) — the everyday workflow (the Understand step uses the PKG).
- [FEATURES.md](FEATURES.md) — where the PKG sits among Spine's capabilities.
- [OPERATIONS.md](OPERATIONS.md#the-semantic-spine) — the optional ontomesh domain layer.
</content>
