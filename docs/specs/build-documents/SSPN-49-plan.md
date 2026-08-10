# SSPN-49 — plan for review (no code)

Every stage the pipeline would run, laid out for a decision. Nothing has been written.

---

## 1. Intent

`orchestrator template list` and `orchestrator contract list` exit 1 with ~40 lines of
Rich-formatted stack frames ending in `ConnectError: [Errno 61] Connection refused`, when
the registry API simply is not running. An expected condition presented as a crash.

**Source:** a manual sweep of all 48 CLI commands. Two of them fail this way; the rest
either work or exit 2 with a usage message.

---

## 2. Spec — and a correction the code forced

The filed spec has six acceptance criteria. Reading `cli.py` changes the picture:

| # | criterion | actual state |
|---|---|---|
| 1 | connect failure → one line, no traceback | **genuine gap** |
| 2 | 401/403 → auth failure naming `ORCHESTRATOR_API_KEY` | **partly met** — `_check` already prints `Error 401: …` and exits 1, no traceback. Missing only the remedy wording |
| 3 | timeout → names `ORCHESTRATOR_API_TIMEOUT_SECONDS` | **genuine gap** |
| 4 | other status → status + server message, no traceback | **already met** by `_check` |
| 5 | happy path unchanged | regression obligation |
| 6 | every `_client()` call site, not just two | scope obligation |

**Why 2 and 4 are already handled:** `_check(resp)` inspects a *response*. Criteria 1 and 3
never reach it — `ConnectError` and `TimeoutException` are raised during the request, before
there is a response to check.

**Decision for you:** the real work is two exception types, not four criteria. The spec
should probably be narrowed to say so, or it will read as though more shipped than did.

---

## 3. PKG understanding — and where it misleads

`orchestrator investigate` (deterministic, no LLM) was asked where this ticket lands. It
answered:

```
orchestrator.registry.api          (Module, 0 callers)
orchestrator.registry.api.fs       (Module, 0 callers)
api_command                        (Function, 2 callers)  src/orchestrator/launch.py:170
RegistryClient                     (Type, 2 callers)      src/orchestrator/tui/client.py:15
orchestrator.registry.api.app      …
orchestrator.registry.api.approvals…
orchestrator.registry.api.audit    …
orchestrator.registry.api.backlog  …
```

**`src/orchestrator/cli.py` is not in the list.** The retrieval is lexical: the ticket says
"registry API", and the registry *server* modules are literally named that. The CLI client
that calls it is a 107 KB file called `cli.py` and matches nothing.

This is the same failure PR #164 fixed one stage later, in `design`, by preferring paths
the spec names. **The investigation brief is still wrong**, and it is carried into the
codegen prompt alongside the design.

**Decision for you:** the brief will tell the model to look at the registry server. That is
noise at best. Worth deciding whether investigate should also honour stated paths, or
whether the brief should be dropped when the design overrides it.

---

## 4. Design

With #164, `design` prefers what the spec states:

```
Files to touch
- src/orchestrator/cli.py
- tests/test_cli.py

Risks
- Files taken from the paths this ticket names, not inferred from its words.
```

Correct. Note it lists no new module, though the work plausibly wants one.

---

## 5. Blast radius (from the graph)

```
orchestrator.cli   importers: 5   tests.test_cli, tests.test_launch, tests.test_mcp_contracts_types
  _print                 callers=23  transitive=36
  _repo_arg              callers=16  transitive=14
  _check                 callers=6   transitive=16
  _client                callers=6   transitive=16
  _issue_key_from_branch callers=5   transitive=3
```

**Caveat, measured:** these are constructor/module-function counts. Method calls through an
instance produce no `CALLS` edge (SSPN-48), so per-method numbers under-report. `_client`
and `_check` are module functions, so their 6 each is real.

**The six call sites, all in the `template`/`contract` group plus one:**

| line | function | command |
|---|---|---|
| 148 | `_register` | `template register` / `contract register` |
| 158 | `_list` | `template list` / `contract list` |
| 164 | `_show` | `template show` / `contract show` |
| 169 | `_publish` | `template publish` / `contract publish` |
| 174 | `_deprecate` | `template deprecate` / `contract deprecate` |
| 263 | `task_submit` | `task submit` |

Ten user-facing commands behind six call sites.

---

## 6. Files likely to change, and what codegen would be shown

| file | size | why |
|---|---|---|
| `src/orchestrator/cli.py` | 107,722 b | the six call sites and `_client` |
| `tests/test_cli.py` | 14,064 b | existing CLI test shape |
| *(likely new)* `src/orchestrator/api_errors.py` | — | the model has chosen this three runs running |

**Context budget:** 121,786 b of 200,000 — 60%. Both existing files fit whole; nothing is
excerpted.

**What the codegen prompt will carry:** layout block, PKG grounding (~6.7 KB), the design
(~2 KB), the spec, the full text of both named files, repo test conventions, and the
`_IMPLEMENT_SYSTEM` rules — including the one #165 relaxed, which now permits a new module
provided the named files are also edited.

---

## 7. What I would want decided before any code

1. **Narrow the spec to the two real gaps** (connect, timeout), or keep all six and accept
   that two describe existing behaviour.
2. **The investigation brief is wrong for this ticket.** Carry it anyway, drop it, or fix
   investigate the way design was fixed.
3. **Shape:** a shared wrapper in a new module (what the model keeps choosing, and what the
   spec's notes ask for), or inline handling at six call sites. The first is cleaner and is
   the shape that has repeatedly tripped the pipeline.
4. **Does `task submit` count?** It is the sixth call site and outside the `template`/
   `contract` group the ticket names. Criterion 6 says every call site; the summary says
   ten commands. Worth stating explicitly.
