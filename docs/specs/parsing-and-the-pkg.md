# How Spine parses code, and what lands in the PKG

**Audience:** engineering · **Written 2026-08-16 against 3.18.1 · §3 and §6 updated 2026-08-24**
**Read alongside:** [`knowledge-graph-architecture.md`](knowledge-graph-architecture.md) (the
graph itself), [`../../KNOWLEDGE_GRAPH.md`](../../KNOWLEDGE_GRAPH.md) (user-facing).

**The one-sentence version:** every fact in the PKG comes from a real parser — CPython's own
`ast`, a tree-sitter grammar, or `sqlglot` — walked as a typed tree and recorded with
`file:line` provenance. Nothing is inferred from a filename, a naming convention, or a regular
expression.

---

## 1. AST vs CST, and why Spine uses both

The distinction matters because it determines what the front-end can *see*, and where each one
breaks.

**An AST — Abstract Syntax Tree** — is what a compiler keeps after it has thrown away
everything that does not affect meaning. Parentheses, whitespace, comments and the exact token
sequence are gone; what remains is structure. Python's `ast` module is the canonical example:
`a + (b)` and `a+b` produce an identical tree.

**A CST — Concrete Syntax Tree** — keeps everything, including every token and its byte range.
You can reconstruct the source character-for-character from it. tree-sitter produces one.

Spine uses whichever is the *authoritative* parser for the language:

| Front-end | Parser | Tree | Why this one |
|---|---|---|---|
| `python` | CPython `ast` (stdlib) | AST | It is the same parser that *runs* the code. If Python can execute a file, we read it identically — there is no second implementation to disagree with. |
| `java` `csharp` `c` `cpp` `go` `typescript` | tree-sitter + that language's official grammar | CST | No stdlib parser is available to us. tree-sitter grammars are maintained by the language communities, are fast, and are **error-tolerant** — see §4. |
| `sql` | `sqlglot` | AST | Pure-Python, dialect-aware (postgres / mysql / tsql / oracle …), and understands SQL as a language rather than as text. |

**Why not one parser for everything?** Because a second implementation of a language is a
second opinion about what the language means, and the two will diverge on the hard cases. Using
CPython's own parser for Python removes that risk entirely for our largest front-end.

---

## 2. What actually happens to a file

```
repo → walk → per-suffix dispatch → parse → typed tree walk → FactBatch → merge → (post-passes)
```

**Dispatch is by suffix** (`LanguageExtractor.suffixes`, `.py` → `PythonExtractor`, and so on),
so adding a language is a new front-end plus a suffix registration — nothing else changes.
Directories starting with `.` are skipped by both walkers, which is why the accuracy corpus
lives under `corpus/*/.repo/` and why that leading dot is load-bearing.

**Python.** `ast.parse()`, then a walk over the node types that carry the facts we record:

| `ast` node | Becomes |
|---|---|
| `Module` | a `Module` node |
| `ClassDef` | a `Type` node, plus `CONTAINS` from its module, plus `IMPLEMENTS` per base |
| `FunctionDef` / `AsyncFunctionDef` | a `Function` node, plus `CONTAINS` from module or class |
| `AnnAssign` / `Assign` | a `Field` node (annotated attributes and class-level assignments) |
| `Import` / `ImportFrom` | an `IMPORTS` edge |
| `Call` | a `CALLS` edge — **if the callee resolves**, see §3 |
| `If` / `For` / `While` / `Try` / `With` / `AsyncWith` | walked *through*, so a function defined or called inside a conditional is not missed |

That last row is easy to overlook and is the difference between reading a file and reading the
top level of a file.

**tree-sitter front-ends** do the same job against named CST node types. Go, for example, keys
on `function_declaration`, `method_declaration`, `type_declaration`, `field_declaration`,
`import_declaration`, `call_expression`, `selector_expression`. The node names come from the
grammar, so they are the language's own vocabulary rather than ours.

**SQL** parses with `sqlglot` into expression trees; the dialect is auto-detected from
distinctive syntax and can be overridden with `--dialect`.

---

## 3. Resolution — the only place a front-end may be wrong

Parsing is not where accuracy is lost. **Every node kind and every edge kind except `CALLS`
scores 1.00 precision and 1.00 recall on the corpus, in all eight languages.** Structure is
either in the tree or it is not.

> **Read that sentence as the conditional it is: 1.00 *on the corpus*.** It held at 1.00 for
> four front-ends that were fabricating `CALLS` edges, for the whole time they were, because no
> fixture carried the shape — see the shadowed-callee section below. A corpus score bounds what
> the corpus contains and nothing else.

`CALLS` is different because it needs *resolution*: the tree tells you a call happened and what
it was spelled, not which definition it reaches. That is a judgement, and judgements can be
wrong.

**The rule is: skip rather than guess.**

```python
# extractor.py, _resolve_call
if func.id in imports:   return imports[func.id]
if func.id in names:     return names[func.id]
return None              # was: f"py:{func.id}"
```

That one-line change shipped in 3.18.0. The previous version invented an id for any unresolved
bare name — every parameter, local and nested function — which produced **497 fabricated edges
on this repo alone, 3.16% of the call graph**. Measured on external repositories the rate was
higher still: **14.8% of unique call relationships in Flask, 7.4% in httpx.**

Two properties of that bug are worth carrying:

- **It was self-consistent.** The inventor created the phantom *node* as well as the edge, so
  `pkg verify`'s dangling-edge check reported **0** the whole time. A graph can be internally
  perfect and externally false.
- **It corrupted real nodes.** The invented id collided with legitimate ungrounded `Type`
  nodes, so `py:Exception` — a `Type` named `Exception` — became a `Function` named literally
  `"py:Exception"`.

C could not take the same fix. An unresolved callee in C is usually a function declared in a
header and linked from another translation unit, so skipping those would silence every
cross-TU call in every C repository. The C test is therefore *"did this function bind the
name"* (a function-pointer parameter) rather than *"can we resolve it"*.

### The same class in four more front-ends — found 2026-08-24

**The story above was told as one Python bug, closed. It was one instance of a class, and the
other four front-ends had it the whole time.** When a parameter or local **shadows** a
resolvable name, TypeScript, Go, C++ and C# each resolved it anyway:

```ts
export function send(x: string): void {}
export function outer(send: (v: string) => void): void { send("hi"); }
// emitted: ts:a.outer -CALLS-> ts:a.send   — `outer` calls its own parameter
```

It hid better than the Python bug did, and for the opposite reason. Python's inventor
manufactured an id *and a phantom node*; these four resolve against their own file-level tables
and land on a **node that genuinely exists**. So there was no dangling edge for `pkg verify` to
report, no phantom node to notice, and no corpus case to fail — three checks agreeing on a case
none of them examined.

Found by widening the invention oracle past Python (`pkg/scope.py` walks five tree-sitter
front-ends), measured at **47 fabricated edges across 23,746 bare calls** on 11 pinned public
repositories, then fixed by giving each front-end **C's test**, which is why it is worth having
written down here: *did this function bind the name*, never *can we resolve it*. **47 edges
removed for 47 fabrications, with no true edge lost.**

Two things the port needed that the C version does not spell out:

- **A binding is not in scope on its own line, in three of the five languages.** Go's spec
  starts a short variable declaration's scope at the *end* of the statement, so
  `cmd := cmd(binaryPath, …)` calls the package-level `cmd` — idiomatic, and it occurs 5 times
  in grpc-go alone. C and C++ read the same way. Each helper therefore records the first line a
  name is in scope, not just the name.
- **Only a bare-identifier call can be shadowed.** `this.Handle()` in C# and `this.method()` in
  TypeScript are explicit member accesses that name the member whatever else is in scope, so
  the bare form is tracked separately. Skipping both would have dropped real edges.

The guard is a corpus case per front-end (`corpus/*/shadowed_calls`), each **written and scored
before the fix** and each failing at `CALLS` precision 0.50 — a fixture written afterwards only
proves the fix is self-consistent. Full record:
[`invention-oracle-cross-language.md`](invention-oracle-cross-language.md).

---

## 4. Failure modes, by design

**A file that will not parse is skipped, never guessed at.** `SyntaxError`,
`UnicodeDecodeError` and `ValueError` are caught per file and the path is recorded in
`skipped`. One unparseable file costs you that file, not the run.

**tree-sitter recovers; `ast` does not.** A CST parser produces a tree with `ERROR` nodes
around the damaged region and keeps going, so a file with one bad line still yields facts for
the rest. CPython's `ast` raises on the first syntax error and the file is skipped whole. That
asymmetry is a real difference in behaviour between our Python front-end and the other six, and
it favours the tree-sitter side on messy real-world code.

**C/C++ parse pre-preprocessor.** We never run `cpp`, so heavy macro use yields partial facts.
A macro-generated function does not exist as far as the tree is concerned.

---

## 5. What lands in the graph

Eight node kinds and eleven edge kinds — a deliberately small vocabulary that every front-end
maps onto, so a query works the same way across languages.

**Nodes:** `Module` · `Type` · `Function` · `Field` · `Endpoint` · `Entity` · `Doc` · `Intent`

**Edges:** `IMPORTS` · `CONTAINS` · `CALLS` · `IMPLEMENTS` · `READS` · `WRITES` · `EXPOSES` ·
`CONSUMES` · `REFERENCES` · `MENTIONS` · `SERVES`

Two of those are not artefacts you can point at in a file. `Doc`/`MENTIONS` come from the
documentation tier, and `Intent`/`SERVES` from git history — the ticket a symbol was last
changed for. `Intent` is the only node kind that is a *reason* rather than a thing, which is
why it carries no provenance.

### Every fact is a `Node` or an `Edge`, and both carry provenance

```python
@dataclass(frozen=True)
class Node:
    id: str                      # "py:pkg.mod.Cls" — language-prefixed, stable
    kind: NodeKind
    name: str
    language: str = ""
    provenance: Provenance | None = None    # file + line + end_line
    external: bool = False

    @property
    def grounded(self) -> bool:
        return self.provenance is not None and not self.external
```

**`grounded` is the load-bearing property.** A node is grounded when we can point at the line
that produced it *and* it is not an external reference. `stdlib` and third-party symbols appear
as ungrounded nodes so edges have somewhere to land, and they are excluded from every count
that claims to describe your code.

**Ids are language-prefixed and stable** — `py:orchestrator.pkg.stats.GraphStats`,
`go:svc.Handler.Run`, `c:uv__stream_eof`. Note that C/C++ ids are *symbols*, not locations, so
grouping by id makes every function its own component; group by the owning module by walking
`CONTAINS` upward instead.

### Post-passes

Some facts cannot be derived from one file. A front-end may expose `finalize` for a whole-repo
pass — Go's `IMPLEMENTS`, computed by matching a concrete type's method set (name + arity,
value **and** pointer receivers) against every in-repo interface, is the clearest example.
Doc-linking (`Doc` + `MENTIONS`) and the optional intent scan run as post-passes for the same
reason.

---

## 6. What this buys, measured

The parser choice is not an aesthetic preference. It is what makes the accuracy claim possible:

| | Result |
|---|---|
| Precision | **1.00** on every node kind and every edge kind, all 8 languages — on the corpus, which now includes the shadowed-callee shape (§3) |
| Recall | 1.00 on every kind except `CALLS` |
| `CALLS` recall | 1.00 (c, sql) · 0.73 (python) · 0.67 (cpp, csharp, go, java) · 0.50 (typescript) |
| Invention | **0** on this repo, and **0** across 11 pinned public repos in 6 front-ends (2026-08-24). Java and SQL are recorded *not-applicable* with reasons rather than scored 0 |
| Invention gate | **`strict`, zero per language** — the one metric gated on an absolute value rather than against the baseline, because it is the one with a correct value |

**The failure mode is silence, not fiction** — held, rather than assumed. It was untrue for
four front-ends until 2026-08-24, and what made it true again was a detector plus a fixture,
not a claim. Everything the graph asserts exists; what it misses, it misses quietly. For an agent reasoning over the graph those are not equally bad — a
missing edge makes it search, a fabricated one makes it confidently follow a call into a
function that was never written.

And the graph pays for itself downstream: across 260 runs on two frontier models, **47 of 68**
new modules integrated correctly with the graph in context versus **3 of 68** without, while
tickets that named their target file scored **122 of 124 either way** — the control that rules
out "more context helps".

---

## 7. Practical notes for anyone touching a front-end

- **Extend `facts.py`, not a renderer.** Comprehension surfaces render facts; they never
  re-derive them from paths or filenames. If you need a new fact, the vocabulary is the place.
- **`--language` is not validated in `cli.py`.** An unsupported value silently scaffolds a
  *Python* project — detection and extraction are independent systems, and a language can be
  detected while yielding zero graph nodes.
- **Changing a `Protocol`? Update its test fakes.** The gate runs `mypy src tests`; typing
  `src` alone passes locally and fails CI.
- **`pkg extract --json` omits edges** — nodes plus a summary. Use `pkg export --format json`
  for the whole graph.
- **Adding a language** is a `LanguageExtractor` (suffixes + `extract`), a `pyproject` extra
  for its tree-sitter grammar, and corpus cases. The universal schema does not change — which
  is why Go and four others landed without reworking the model.
