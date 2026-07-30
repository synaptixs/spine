# Design: code-grounded PKG that supersedes authored design-system context

**Status:** Proposed (design for review)
**Thesis owner:** orchestrator
**Goal:** make the Product Knowledge Graph *understand* a codebase deeply enough that
the design-system / API / convention context an authored tool (e.g. Supernova) curates
by hand is instead **extracted from the live code** — more accurate, always current,
zero curation — and fed into codegen + exposed over MCP.

## The wedge
Authored context (Figma → platform → MCP) is a *parallel artifact* that drifts from
what shipped. Our PKG reads the **source of truth**: the code. If we extract components,
props, tokens (resolved), compositions, conventions, and *real usage examples* directly,
we get the same context Supernova sells — plus the whole-codebase API + data-layer graph
they can't see — with no manual upkeep. The orchestrator then both **uses** this internally
(grounded codegen) and **publishes** it over MCP (so Cursor/Claude Code can consume it too).

> Honest boundary: we extract from *shipped code*, so we can't ground a component that
> only exists in Figma yet. We win the *code-context* axis (accuracy, freshness, breadth,
> zero curation); we don't replace design authoring. A design-tool MCP can be one *input*.

---

## 1. Schema additions (`pkg/facts.py`)

New `NodeKind`s and `EdgeKind`s — additive, so existing facts are untouched:

```
NodeKind += COMPONENT   # a UI component (React/Vue/Svelte)
            PROP        # a component prop (name + type + required)
            TOKEN       # a design token (color/space/typography/…)
            ROUTE       # an HTTP route/endpoint (fills the reserved ENDPOINT)
EdgeKind += RENDERS     # component → component it composes (observed from JSX)
            HAS_PROP    # component → prop
            USES_TOKEN  # component/style → token
            ALIASES     # token → token it references (resolution chain)
            DOCUMENTED_BY  # symbol → doc anchor (extends pkg/docs.py)
            READS/WRITES   # code → entity/field (already reserved — emit them, §6)
```

Nodes keep their `Provenance(file, line)`, so every component/token/example is
click-through grounded — a property Supernova's authored context lacks.

## 2. Extractors (the new front-ends)

### 2a. TypeScript/TSX component extractor (`pkg/typescript_extractor.py`)
Implements the existing `LanguageExtractor` protocol (`extractor.py`), modeled on
`java_extractor.py` (tree-sitter, lazy-imported behind a new `ui` extra:
`tree-sitter`, `tree-sitter-typescript`). Per file it emits:
- `COMPONENT` node per exported component (PascalCase fn/const returning JSX, or
  `class extends Component`) — with **import path** derived from the module + export.
- `HAS_PROP` edges from the component's props interface/type (name, type, required).
- `RENDERS` edges from JSX usage — *observed* compositions (`<Card><Button/></Card>` →
  `Card RENDERS Button`). This beats authored "valid compositions": it's what the code
  actually does.
- `USES_TOKEN` edges where a style/className references a token.
- `CALLS`/`CONTAINS`/`IMPORTS` like any module (reuse the universal vocab).

Registered via `RepoCodeExtractor(extractors=[PythonExtractor(), TypeScriptExtractor(), …])`.

### 2b. Design-token extractor (`pkg/tokens.py`, analogous to `schema.py`)
A *source-shaped* front-end (like DB schema → facts) that parses token sources —
`tailwind.config.*`, CSS custom properties, `theme.ts`/styled-system themes, Style
Dictionary / Tokens-Studio JSON — into `TOKEN` nodes + `ALIASES` edges. Then **resolve
the alias graph**: `color.button.bg → color.brand.500 → #6D28D9`, exposing the fully
resolved value *and* the chain (the exact "nested token resolution" Supernova markets).
Synthetic provenance (`token://theme/color.button.bg`) so tokens count as grounded.

### 2c. Usage-example miner (`pkg/examples.py`) — the "understanding" differentiator
For any symbol/component, mine **canonical real call sites** (prefer tests, then
in-repo usage) via existing `CALLS`/`RENDERS` edges + provenance reads. Output a short,
*verified* "here's how this is actually used" snippet. Verified examples beat authored
docs because they're guaranteed to compile against the current code.

## 3. Retrieval upgrades (`pkg/retrieval.py`, `pkg/store.py`)
Today: lexical scoring only. Add, without breaking the lexical default:
- **Graph-aware expansion**: for a retrieved `COMPONENT`, pull its `HAS_PROP`, its
  `RENDERS` neighbors, and its `USES_TOKEN` tokens (resolved) — so a UI spec gets the
  component *with its API + composition + tokens*, not a bare snippet.
- **Hybrid relevance (opt-in)**: lexical + embeddings + graph proximity (blast-radius /
  RENDERS distance). Lexical stays the no-dependency default.
- New `FactStore`/`GroundedRetriever` queries: `components(query)`, `component_api(id)`,
  `compositions(id)`, `tokens(query)`, `resolve_token(id)`, `usage_examples(id)`.

## 4. Grounding integration (`sdlc/grounding.py`)
`PKGCodegenGrounder.context_for_spec` becomes **task-aware** (the `profile`/catalog
already classifies task-type):
- **frontend** task → inject a **DESIGN CONTEXT** block: relevant components (with prop
  signatures + import paths), their compositions, resolved tokens, and a usage example
  each — under the existing 8000-char budget, prioritized over generic snippets.
- **api-backend** task → API surface + data-layer (`READS`/`WRITES`/FK) edges.
- **default** → today's behavior unchanged.

Duck-typed `CodegenGrounder` seam means `LLMCodegenAdapter(grounder=…)` needs no change.
Result: generated UI code uses the **real** components/tokens with correct imports —
Supernova's core value, derived from code.

## 5. Distribution: PKG-as-MCP (`plugin/server.py`, `agentic/tools.py`)
Add design-system query tools to the MCP surface (and the agentic loop), so **any**
external agent consumes our context exactly like Supernova's endpoint — but ours is
auto-derived and whole-stack:
`pkg_components`, `pkg_component_api(name)`, `pkg_compositions(name)`, `pkg_tokens`,
`pkg_resolve_token(name)`, `pkg_usage_examples(symbol)`. CLI: extend `pkg extract` with
`--ui` summary + a `pkg components|tokens` view.

## 6. Data-layer edges (finish the reserved set)
Emit `READS`/`WRITES` (code → entity/field) and the `ROUTE`/`EXPOSES` endpoint edges
the schema already reserves. This is the cross-stack reasoning (a route writes an entity
a component reads) that **no design-system tool can do** — the part that's purely ours.

## 7. Outcome-based feedback (beats "self-healing context")
Because we run codegen *and* the tests, we can learn from real outcomes, not just
interaction telemetry:
- Record which grounded facts a codegen pass *used* and whether the result passed tests /
  merged (tie into the existing run trace + audit).
- Re-rank retrieval toward facts that historically led to green; surface recurring gaps
  ("spec asked for X, PKG had no component for it") as the *measured* missing-knowledge
  signal. `pkg/docs.py` + `pkg/verifier.py` already detect doc↔code drift and stale facts
  — promote those to the "your context is stale" alert.

---

## Phased plan
1. **Schema + TS/TSX component extractor** (`COMPONENT`/`PROP`/`HAS_PROP`/`RENDERS`,
   import paths) behind a `ui` extra. → the core overlap.
2. **Token extractor + alias resolution** (`TOKEN`/`USES_TOKEN`/`ALIASES`). → nested
   token resolution.
3. **Usage-example miner** + doc binding. → "better than authored docs."
4. **Task-aware grounder** wiring (frontend DESIGN CONTEXT block). → codegen uses it.
5. **PKG-as-MCP** design tools. → external distribution parity.
6. **Data-layer edges** (`READS`/`WRITES`/route). → cross-stack reasoning.
7. **Outcome-based re-ranking**. → self-healing, measured on test/merge results.

## Honest limits
- Net-new design intent (Figma-only) is out of reach by construction.
- tree-sitter TS/TSX adds a real parser dependency (kept in an optional `ui` extra).
- "Understanding" here is structural + usage-grounded, not semantic comprehension of
  intent; hybrid embeddings narrow but don't close that.

## First step that proves the thesis
Phase 1 on a real React/TS repo: extract components (props + import paths + observed
compositions), surface them in the codegen grounding, generate a UI feature, and show it
**used the correct existing components with correct imports — with no authored design
system**. That single demo is the claim: *we got Supernova's value straight from the code.*
