# Design + Plan: adding SQL to the PKG (the 7th language) — brownfield + greenfield

**Status:** **Track A COMPLETE + released (2.7.0).** **Track B (greenfield codegen)
implemented on `develop` (uncommitted): B1–B3 done + tested, B4 wired (Postgres live-prove
pending Docker); full gate green (1532 passed).** Adds a **SQL** front-end as the headline of
the **3.0.0** major, covering **DDL + DML + stored procedures** across **both** project modes:

- **Brownfield — comprehension** (Track A): read existing `.sql` → PKG facts. Lower risk,
  mostly reuse; the foundation.
- **Greenfield — codegen** (Track B): *generate* schema/migrations for a data-shaped intent
  and validate them against an **ephemeral database**. A new build-runner model, built on
  Track A.

> Unlike the six imperative languages (Python/Java/TypeScript/C#/C/C++), SQL is a
> **data-definition + query language**. Brownfield SQL is about **grounding the data layer**;
> greenfield SQL is about **generating a correct schema and proving it applies** — there is
> no `pytest`/`ctest` analogue, so "test" means *apply to a real DB and introspect the
> result*. That single fact drives the design.

Sequencing: **Track A first** (brownfield comprehension is the foundation and reuses the most),
then **Track B** (greenfield codegen), which reuses Track A's parser *and* its schema→facts
mapping to **validate generated DDL by introspecting it back**.

---

## 1. The core insight — most of the target side already exists

The PKG's fact vocabulary was built with data in mind, and the data-layer mapping is
already written:

- `FIELD` = *"column"*, `ENTITY` = *"data entity"*, `REFERENCES` = *"foreign key"* — see
  [facts.py](../../src/orchestrator/pkg/facts.py).
- [`pkg/schema.py`](../../src/orchestrator/pkg/schema.py) **already maps a `DBSchema`
  (tables / columns / foreign keys) → `Entity` / `Field` / `REFERENCES` facts**, from a
  *pluggable* source. Today the only source is **live-database introspection**
  (`orchestrator mcp ingest-db`).

This gives **two reuse payoffs**:

1. **Brownfield (Track A):** SQL support is a new **static `DBSchema` source** — parse
   `.sql` → the `DBSchema` that already exists → the `schema_to_facts` that already exists.
2. **Greenfield (Track B):** after codegen emits DDL and we apply it to an ephemeral DB, we
   **introspect it back through the very same `schema.py` path** and compare against the
   intent — so "did we generate the right schema?" is checkable with machinery Track A
   already built. Generation and validation share one model.

The genuinely new work is: **parsing SQL**, the two things introspection can't see
(**queries/DML** and **stored-procedure bodies**), and the **ephemeral-DB build-runner** for
greenfield.

---

## 2. Scope for 3.0.0

**In scope — both tracks, DDL + DML + stored procedures:**

- **Track A (brownfield / comprehension):** DDL (tables, views, indexes, constraints, FKs),
  DML (`SELECT/INSERT/UPDATE/DELETE` → `READS`/`WRITES`), stored procedures/functions
  (`Function` + `CALLS` + data-access edges), cross-language data-layer linking, migration
  awareness.
- **Track B (greenfield / codegen):** scaffold a migrations layout, **generate DDL /
  migrations** for a data-shaped intent, and **validate against an ephemeral database**
  (apply → introspect → compare → refine). Works standalone (a schema/migrations repo) and
  as the **data-layer companion** to a greenfield service (SQL generated alongside app code).

**Out of scope:** ORM-model generation (that's the app-language codegen, already shipped);
data migration/backfill logic beyond schema DDL; query-plan/perf tuning.

---

## 3. Parser choice — `sqlglot`

**Use [`sqlglot`](https://github.com/tobymao/sqlglot)** (lazy-imported under a new `[sql]`
extra), not `tree-sitter-sql`:

| Criterion | `sqlglot` | `tree-sitter-sql` |
|---|---|---|
| Dialects | Postgres, MySQL, T-SQL, SQLite, Oracle, Snowflake, BigQuery, … | limited |
| DDL **and** DML AST | ✅ rich, typed expression tree | partial |
| Pure Python (no native build) | ✅ | needs tree-sitter native |
| Table/column resolution + **transpile between dialects** | ✅ (`qualify`, lineage, `transpile`) | ✗ |

`sqlglot`'s dialect **transpile** is a bonus for Track B: generate once, target the repo's
dialect. Dialect is auto-detected per file with a `--dialect` override and a repo-level
default in the profile. Unparseable statements degrade to a skipped-file count (same
contract as every extractor), never a crash.

---

## 4. Fact mapping (reference)

| SQL construct | Node / edge | Notes |
|---|---|---|
| `CREATE TABLE t` | `Entity` (id `sql:t`) | provenance `file:line` (not synthetic `db://`) |
| column `c` | `Field` + `CONTAINS` (table→column) | type captured in node metadata |
| `FOREIGN KEY (c) REFERENCES u` | `REFERENCES` (t→u) | **ground truth**, replaces inferred FKs |
| `CREATE VIEW v AS SELECT …` | `Entity` (view) + `READS` to base tables | tag as view subkind |
| `CREATE INDEX` | metadata on the `Entity`/`Field` | not a first-class node |
| `CREATE PROCEDURE/FUNCTION p` | `Function` | body parsed for edges |
| `SELECT … FROM t` | `READS` (caller→t) | |
| `INSERT/UPDATE/DELETE t` | `WRITES` (caller→t) | |
| `CALL p2()` inside `p` | `CALLS` (p→p2) | |
| a `.sql` file | `Module` | `module_name()` = repo-relative POSIX path |

**No new node/edge kinds required.** (Open question for A1: view/index as a `Type` subkind
tag vs. metadata — decide during implementation.)

---

## 5. Track A — brownfield comprehension (do first)

Each phase independently shippable, mirroring the Java/TS/C# cadence.

### A1 — DDL → Entity/Field/REFERENCES  ·  *mostly reuse*  ·  **S**  ·  ✅ **DONE**
> Shipped: `pkg/sql_extractor.py` (sqlglot behind the `[sql]` extra) + `sql_source_to_facts`
> in `schema.py` (file:line provenance, `sql:` ids, cross-file FK placeholders that upgrade on
> merge); wired into `default_extractors()` + `catalog/profile.py` (`.sql→"sql"`). Handles
> CREATE TABLE (cols/PK/col+table FKs), same-file ALTER ADD COLUMN/CONSTRAINT, CREATE VIEW.
> `understand`/`state` render the entities + REFERENCES. 9 tests; full gate green.

- New `pkg/sql_extractor.py` implementing the `LanguageExtractor` protocol
  (`language="sql"`, `suffixes=(".sql",)`), lazy-importing `sqlglot`.
- Parse `CREATE/ALTER TABLE`, `VIEW`, constraints → populate the existing `DBSchema` →
  `schema_to_facts`. FKs become real `REFERENCES` edges.
- Wire into `extractor.py:default_extractors()` behind `[sql]`; `.sql→"sql"` in
  `catalog/profile.py`.
- **Accept:** `pkg extract <schema.sql>` yields correct Entity/Field/FK counts on a real
  Postgres/MySQL dump; `understand` renders them into `domain-model.md`.

### A2 — DML + stored procedures → READS/WRITES/CALLS  ·  **M**  ·  ✅ **DONE**
> Shipped in `pkg/sql_extractor.py`: standalone `SELECT/INSERT/UPDATE/DELETE` → `READS`/
> `WRITES` (attributed to the file's module); `CREATE VIEW … AS SELECT` → view `READS` its
> base tables; `CREATE FUNCTION/PROCEDURE` → `Function` node + `CONTAINS`, with body
> `READS`/`WRITES`/`CALLS` recovered by extracting the (sqlglot-opaque) `$$…$$` body and
> re-parsing it best-effort (strip `BEGIN`/`END`; regex fallback for `CALL`/`PERFORM`).
> Default dialect **postgres** (understands dollar-quoting); overridable. 3 tests.
- **Accept:** ✅ a proc's `READS`/`WRITES`/`CALLS` resolve; blast-radius traversal works.

### A3 — Cross-language data-layer linking  ·  **M**  ·  ✅ **DONE**
> Shipped as `pkg/data_layer_link.py:link_data_layer(batch)` — a post-extraction pass that
> collapses ORM entities (`csharp:entity:…`) onto matching source-SQL entities by a
> normalized name (case/underscore/singularize), makes the schema canonical, re-points the
> ORM entity's edges, and prefers `.sql`-grounded `REFERENCES` over inferred ones. No-op
> without a SQL schema. Wired into `understand` + `state`. 5 tests.
- **Accept:** ✅ a repo with both `schema.sql` and an EF model yields one entity per table
  with source-grounded FKs (no duplicate edges).

### A4 — Migration awareness  ·  **M**  ·  ✅ **DONE**
> Shipped as `pkg/migrations.py`: `find_migration_files` (ordered `.sql` under a
> migrations dir), `fold_migrations` (replays CREATE / ALTER ADD·DROP·RENAME COLUMN /
> RENAME TABLE / DROP TABLE in order → authoritative `DBSchema`), and `apply_migrations`
> (swaps the additive per-file SQL facts for the folded current schema). Wired into
> `understand` + `state` (runs before A3). 6 tests. Live-proven: a DROP/RENAME migration
> repo renders the correct current schema in `domain-model.md`.
- **Accept:** ✅ a schema expressed as ordered `ALTER` migrations extracts the correct final
  tables/columns; `DROP`/`RENAME` reflected.
- **Known limits (honest):** linear ordered dirs only (branching histories are a follow-on);
  DML inside migration files is dropped by the fold (migrations are DDL); folding runs in the
  comprehension path (`understand`/`state`), not raw `pkg extract`.

### A1.5 — dialect selection + auto-detect  ·  ✅ **DONE**
> `detect_dialect()` fingerprints Postgres / MySQL / **T-SQL (SQL Server)** / **Oracle** from
> distinctive syntax (`[brackets]`+`IDENTITY`, back-ticks+`AUTO_INCREMENT`, `VARCHAR2`+`SYSDATE`,
> `SERIAL`+`$$`); `SqlExtractor(dialect=None)` **auto-detects per file** (falls back to Postgres
> for portable DDL), threaded through `default_extractors`/`RepoCodeExtractor`/`understand`/
> `state`. A **`--dialect`** flag on `pkg extract`/`understand`/`state` pins it. Robustness: any
> sqlglot failure (incl. an internal `AttributeError` on a dialect mismatch) now degrades to a
> per-file skip, never a crash. Verified: T-SQL that *errors* under Postgres and MySQL DDL that
> *loses its FK* under Postgres both extract correctly under auto-detect. DB2 → closest
> supported dialect (sqlglot has no DB2). 15 SQL-extractor tests.

---

## 6. Track B — greenfield codegen (build on Track A)

Greenfield = the orchestrator **generates** the data layer. There is no unit-test analogue,
so validation = **apply generated DDL to a throwaway database and introspect the result**.

### The build-runner model (the new part)
Mirrors the per-language codegen surface (`sdlc/scaffold.py`, `layout.py`, `conventions.py`,
`testenv.py`, `testrunner.py`, `deps.py`) with a SQL branch:

- **testenv** — an **ephemeral database**. Default **SQLite in-memory** (zero dependency,
  always available). Optional **Postgres via testcontainers/Docker** for dialect fidelity —
  *skips cleanly when the toolchain is absent* (same contract as the C compiler / Node
  toolchain today).
- **scaffold/layout** — a migrations directory (raw ordered `NNNN_*.sql` by default; detect
  and honor Flyway/Alembic/Liquibase when present) + a tiny apply script.
- **testrunner** — apply the migrations to the ephemeral DB, **introspect it back through
  `schema.py`**, and assert the resulting `DBSchema` satisfies the intent (expected
  tables/columns/constraints); optionally run author-provided assertion queries.
- **refine loop** — reuse the existing agentic refine cycle: generate DDL → apply →
  introspect → diff vs intent → refine on mismatch (apply errors and schema diffs are the
  feedback signal, exactly like failing tests).

### Phases
- **B1 — ephemeral-DB testenv + validate-by-introspection**  ·  **M**  ·  ✅ **DONE**
  > Shipped `sdlc/sql_build.py`: `apply_sql` (transpile any dialect → SQLite via
  > `sqlglot.transpile`, apply to in-memory DB, real DDL errors surface as the refine
  > signal), `introspect_sqlite` (→ `DBSchema`, reusing the comprehension model),
  > `validate_schema` (diff applied vs expected → missing tables/columns/FKs). Plus
  > `SqlToolEnvironment` (always-available, stdlib sqlite3) + `SqlTestRunner` wired into
  > the `make_test_environment`/`make_test_runner` factories for `language="sql"`. 8 tests;
  > zero-toolchain. *Accept:* ✅ a known migration validates green/red correctly.
- **B2 — DDL/migration generation + refine**  ·  **L**  ·  ✅ **DONE**
  > `sdlc feature --language sql` is real: `_resolve_sql_layout` (a `migrations/` dir, dialect
  > on `build_tool`), `_sql_files` scaffold, `_IMPLEMENT_SYSTEM_SQL`/`_REFINE_SYSTEM_SQL`
  > prompts + a SQL `_layout_block`, and the feature runner **skips `author_tests`** for SQL
  > (the migration is the artifact). The refine loop drives against the B1 `SqlTestRunner`.
  > `--language` help updated. *Accept:* ✅ capstone test — a scripted model generates a
  > broken migration → apply fails → refine → applies clean (the full loop, no LLM). Live run
  > with a real model = B4's live-prove.
- **B3 — companion mode (SQL alongside app code)**  ·  **M**  ·  ✅ **DONE (via coexistence)**
  > The SQL layout (`migrations/`) is disjoint from app source (`src/`), and scaffold is
  > additive/idempotent — so generating the schema as a data-layer companion = running
  > `sdlc feature --language sql` against the app repo; it adds `migrations/` without touching
  > app code (tested). *Note:* single-pass multi-language generation (one run emits app code
  > **and** its schema) is a future enhancement; today it's two runs sharing the repo.
- **B4 — Postgres fidelity + live-proven**  ·  **M**  ·  ✅ **DONE (live-proven)**
  > `PostgresSqlTestRunner` (testcontainers Postgres + psycopg, `apply_sql_postgres`) behind
  > the `[sql-postgres]` extra; opt-in via `SDLC_SQL_ENGINE=postgres`, else the default SQLite
  > runner. Gracefully reports a missing toolchain instead of crashing. **Live-proven** on a
  > real ephemeral Postgres (Docker): a valid two-table migration applies; an FK to an
  > undefined table is **rejected at apply** (Postgres enforces FKs, unlike SQLite); the full
  > generate→apply-fail→refine→pass loop runs through the factory-selected runner. Opt-in
  > integration test `RUN_POSTGRES_IT=1` (skipped in CI). Fixed: `apply_sql_postgres` now
  > splits multi-statement files via `sqlglot.transpile` (psycopg runs one statement/call).

---

## 7. Brownfield vs greenfield (the two-mode story, mirrors KNOWLEDGE_GRAPH.md §5–6)

- **Brownfield:** `understand`/`pkg extract` read existing `.sql` → the data layer becomes
  source-grounded truth (FKs, proc data-access, cross-language links). New data-shaped
  changes are then grounded in the real schema and blast-radius-scoped by the tables/procs
  they touch.
- **Greenfield:** the first `sdlc feature --language sql` scaffolds a migrations layout and
  generates the initial schema, validated on an ephemeral DB. As features land, the schema
  grows and the PKG's data layer fills in via Track A extraction of the generated `.sql` —
  the same *"knowledge grows with the code"* loop the other languages have, applied to the
  database.

---

## 8. Packaging + wiring changes
- `pyproject.toml`: `[project.optional-dependencies] sql = ["sqlglot>=25"]`;
  optional `sql-postgres = ["testcontainers>=4"]` for B4.
- `pkg/sql_extractor.py` (new); register in `extractor.py:default_extractors()`.
- `catalog/profile.py`: `.sql→"sql"`.
- `pkg/schema.py`: add a `sql_source` populator (parsed DDL → `DBSchema`); `schema_to_facts`
  unchanged; **reused for greenfield validation-by-introspection**.
- Codegen branch: SQL cases in `sdlc/scaffold.py` / `layout.py` / `conventions.py` /
  `testenv.py` / `testrunner.py` / `deps.py`; a `sql-conventions` skill.
- Docs: KNOWLEDGE_GRAPH.md (language list, fact mapping, §5/§6), USER_GUIDE multi-language
  note, FEATURES.md capability row.
- Tests: golden-fact fixtures per dialect (A); ephemeral-DB apply/introspect tests (B).

---

## 9. Risks / gotchas
- **Dialect variance** — procedure bodies (PL/pgSQL vs T-SQL) are least portable; `sqlglot`
  covers statements well, exotic procedural constructs degrade to partial facts. Mitigate
  with `--dialect` + skip-counting.
- **Greenfield validation depth** — SQLite-in-memory proves *applies + shapes correctly* but
  not dialect-specific Postgres features; that's what B4's testcontainers path adds
  (toolchain-gated, skips cleanly).
- **Dynamic SQL** built as strings in app code stays invisible (static-analysis limit, like
  C macros) — document in "Honest limits".
- **Migration ordering** (A4) — start with linear ordered dirs; branching histories are a
  follow-on.
- **Entity-name collisions** across schemas — qualify by schema/namespace in the node id.

---

## 10. Rough totals
- **Track A (brownfield):** 4 comprehension phases (A1–A4) — one extractor module, one
  parser extra, one profiler line, one `schema.py` populator. Mostly reuse.
- **Track B (greenfield):** 4 codegen phases (B1–B4) — a new **ephemeral-DB build-runner**
  (the real new surface) reusing Track A's parser + schema→facts for validation, plus the
  SQL codegen branch across the existing scaffold/testrunner seams.
- **Net-new capability:** the data layer becomes **source-grounded truth** (brownfield) and
  the orchestrator can **generate and prove a schema** (greenfield) — with query/proc-level
  `READS`/`WRITES`/`CALLS` no introspector can provide, authoritative across languages.

**One-line takeaway:** SQL is the 7th PKG language and the best-fitting one — brownfield
turns the already-modeled data layer from *guessed/DB-dependent* into *source-grounded truth*,
and greenfield lets the orchestrator *generate a schema and prove it applies* — both reusing
`schema.py`, adding a parser and an ephemeral-DB runner.
