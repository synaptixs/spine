"""The JavaScript front-end: CommonJS on top of the TypeScript reading (javascript-support-roadmap)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

from orchestrator.pkg.extractor import LanguageExtractor, RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind  # noqa: E402
from orchestrator.pkg.js_extractor import JavaScriptExtractor  # noqa: E402
from orchestrator.pkg.typescript_extractor import TypeScriptExtractor  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return RepoCodeExtractor(extractors=[TypeScriptExtractor(), JavaScriptExtractor()]).extract(tmp_path)


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


def _ids(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes}


def test_nodes_are_tagged_javascript_under_typescript_ids(tmp_path: Path) -> None:
    """The tag is what accuracy and the capability matrix count by; the id prefix is shared (D4)."""
    batch = _repo(tmp_path, {"lib/util.js": "function helper() {}\n"})
    node = next(n for n in batch.nodes if n.id == "ts:lib/util.helper")
    assert node.language == "javascript"
    assert {n.language for n in batch.nodes} == {"javascript"}


def test_module_name_strips_every_javascript_suffix(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"a.mjs": "function f() {}\n", "b.cjs": "function g() {}\n"})
    assert {"ts:a", "ts:b", "ts:a.f", "ts:b.g"} <= _ids(batch)


def test_require_namespace_member_call_resolves(tmp_path: Path) -> None:
    """`const m = require('./m')` binds the whole module, so `m.f()` is the export `f`."""
    batch = _repo(
        tmp_path,
        {
            "lib/util.js": "exports.double = function (x) { return x * 2; };\n",
            "lib/api.js": "const util = require('./util');\nfunction run(x) { return util.double(x); }\n",
        },
    )
    assert ("ts:lib/api", "ts:lib/util") in _edges(batch, EdgeKind.IMPORTS)
    assert ("ts:lib/api.run", "ts:lib/util.double") in _edges(batch, EdgeKind.CALLS)


def test_destructured_require_call_resolves(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "util.js": "exports.double = (x) => x * 2;\n",
            "api.js": "const { double } = require('./util');\nfunction run(x) { return double(x); }\n",
        },
    )
    assert ("ts:api.run", "ts:util.double") in _edges(batch, EdgeKind.CALLS)


def test_member_of_a_require_binds_under_the_export_name(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "util.js": "exports.double = (x) => x * 2;\n",
            "api.js": "const double = require('./util').double;\nfunction run(x) { return double(x); }\n",
        },
    )
    assert ("ts:api.run", "ts:util.double") in _edges(batch, EdgeKind.CALLS)


def test_a_renamed_destructuring_records_the_import_and_binds_nothing(tmp_path: Path) -> None:
    """Resolution names the target by the local, so `{double: twice}` would send `twice()` to
    `ts:util.twice`. The import is still real; the binding is not trusted."""
    batch = _repo(
        tmp_path,
        {
            # `twice` is exported too — a decoy, so a binding by the local name would land on a
            # real node. Without it `finalize` dropped the wrong edge anyway and this passed
            # whatever the binding did.
            "util.js": "exports.double = (x) => x * 2;\nexports.twice = (x) => x * 2;\n",
            "api.js": "const { double: twice } = require('./util');\nfunction run(x) { return twice(x); }\n",
        },
    )
    assert ("ts:api", "ts:util") in _edges(batch, EdgeKind.IMPORTS)
    assert not {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.run"}


def test_calling_a_whole_module_draws_no_edge(tmp_path: Path) -> None:
    """`m()` reaches whatever the module assigned to `module.exports` — not `ts:m.m`."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "function m() {}\nmodule.exports = { other() {} };\n",
            "api.js": "const m = require('./m');\nfunction run() { return m(); }\n",
        },
    )
    assert ("ts:api.run", "ts:m.m") not in _edges(batch, EdgeKind.CALLS)


def test_a_bare_require_is_an_import_with_no_binding(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"polyfill.js": "\n", "app.js": "require('./polyfill');\n"})
    assert ("ts:app", "ts:polyfill") in _edges(batch, EdgeKind.IMPORTS)


def test_a_computed_require_is_not_guessed(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"app.js": "const m = require(path.join(dir, 'x'));\n"})
    assert not _edges(batch, EdgeKind.IMPORTS)


def test_a_package_require_is_an_external_module(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"app.js": "const fs = require('fs');\n"})
    fs = next(n for n in batch.nodes if n.id == "ts:fs")
    assert fs.external and fs.kind is NodeKind.MODULE


def test_commonjs_exports_are_the_modules_surface(tmp_path: Path) -> None:
    """Each export form in a file that never replaces `module.exports`."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "exports.a = function () {};\nmodule.exports.b = () => {};\n",
            "n.js": "module.exports = { c() {}, d: function () {}, e: () => {} };\n",
        },
    )
    functions = {n.id for n in batch.nodes if n.kind is NodeKind.FUNCTION}
    assert {"ts:m.a", "ts:m.b", "ts:n.c", "ts:n.d", "ts:n.e"} <= functions


def test_members_set_before_module_exports_is_replaced_are_not_exported(tmp_path: Path) -> None:
    """`require` returns the *new* object, so `a` and `b` — set on the one `module.exports = {…}`
    threw away — are not exported, and a caller reaches neither. They are still functions in the
    source, so they keep their nodes: pass 2 of the review deleted them, which also deleted every
    call inside them and a same-file route to `exports.a`."""
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "exports.a = function () {};\n"
                "module.exports.b = () => {};\n"
                "module.exports = { c() {} };\n"
                "module.exports.d = () => {};\n"
            ),
            "api.js": "const m = require('./m');\nfunction go() { m.a(); m.b(); m.c(); m.d(); }\n",
        },
    )
    functions = {n.id for n in batch.nodes if n.kind is NodeKind.FUNCTION}
    assert {"ts:m.a", "ts:m.b", "ts:m.c", "ts:m.d"} <= functions
    reached = {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.go"}
    assert reached == {"ts:m.c", "ts:m.d"}


def test_a_named_default_export_is_emitted_and_an_anonymous_one_is_not(tmp_path: Path) -> None:
    named = _repo(tmp_path / "n", {"m.js": "module.exports = function build() {};\n"})
    anon = _repo(tmp_path / "a", {"m.js": "module.exports = function () {};\n"})
    assert "ts:m.build" in _ids(named)
    assert not [n for n in anon.nodes if n.kind is NodeKind.FUNCTION]


def test_calls_inside_an_exported_function_are_extracted(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"m.js": "function helper() {}\nexports.run = function () { helper(); };\n"})
    assert ("ts:m.run", "ts:m.helper") in _edges(batch, EdgeKind.CALLS)


def test_a_call_to_an_export_nothing_declares_is_dropped(tmp_path: Path) -> None:
    """`m.missing()` names `ts:m.missing`. Only the whole graph knows it is not there, so
    `finalize` drops the edge rather than leave it dangling."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "exports.present = function () {};\n",
            "api.js": "const m = require('./m');\nfunction run() { m.present(); m.missing(); }\n",
        },
    )
    calls = _edges(batch, EdgeKind.CALLS)
    assert ("ts:api.run", "ts:m.present") in calls
    assert ("ts:api.run", "ts:m.missing") not in calls
    known = _ids(batch)
    assert all(dst in known for _, dst in calls)


def test_a_renamed_object_export_is_not_invented(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": "function helper() {}\nmodule.exports = { run: helper };\n",
            "api.js": "const m = require('./m');\nfunction go() { m.run(); }\n",
        },
    )
    assert "ts:m.run" not in _ids(batch)
    assert ("ts:api.go", "ts:m.run") not in _edges(batch, EdgeKind.CALLS)


def test_jsx_inside_a_js_file_parses(tmp_path: Path) -> None:
    """Under the plain TypeScript grammar this mis-parses into a `type_assertion` (D2)."""
    batch = _repo(
        tmp_path,
        {"App.js": "export const App = () => <div className='x'>{greet()}</div>;\nfunction greet() {}\n"},
    )
    assert ("ts:App.App", "ts:App.greet") in _edges(batch, EdgeKind.CALLS)


def test_class_inheritance_across_a_require(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "base.js": "class Base {}\nmodule.exports = { Base };\n",
            "impl.js": "const { Base } = require('./base');\nclass Impl extends Base {}\n",
        },
    )
    assert ("ts:impl.Impl", "ts:base.Base") in _edges(batch, EdgeKind.IMPLEMENTS)


@pytest.mark.parametrize("importer", ["api.ts", "api.js"])
def test_an_explicit_js_extension_names_the_real_module(tmp_path: Path, importer: str) -> None:
    """ESM requires the extension — in TypeScript too. Stripping only `.ts`/`.tsx` minted
    `ts:mod.js`, a first-party-looking module no file declares, and a CALLS target on it."""
    batch = _repo(
        tmp_path,
        {
            "mod.js": "export function go() {}\n",
            importer: "import { go } from './mod.js';\nexport function run() { go(); }\n",
        },
    )
    assert "ts:mod.js" not in _ids(batch)
    assert ("ts:api.run", "ts:mod.go") in _edges(batch, EdgeKind.CALLS)


def test_typescript_importing_javascript_resolves(tmp_path: Path) -> None:
    """A gradual migration — the payoff for sharing the `ts:` namespace (D4)."""
    batch = _repo(
        tmp_path,
        {
            "legacy.js": "export function old() {}\n",
            "modern.ts": "import { old } from './legacy';\nexport function run(): void { old(); }\n",
        },
    )
    assert ("ts:modern.run", "ts:legacy.old") in _edges(batch, EdgeKind.CALLS)


def test_a_chained_exports_alias_is_the_exports_object(tmp_path: Path) -> None:
    """Express's `lib/application.js` shape — 43 of its 49 exported functions are spelled so."""
    batch = _repo(
        tmp_path,
        {
            "application.js": "var app = exports = module.exports = {};\napp.init = function init() {};\n",
            "server.js": (
                "const application = require('./application');\nfunction boot() { application.init(); }\n"
            ),
        },
    )
    assert "ts:application.init" in _ids(batch)
    assert ("ts:server.boot", "ts:application.init") in _edges(batch, EdgeKind.CALLS)


def test_an_object_assigned_to_module_exports_is_the_exports_object(tmp_path: Path) -> None:
    """Express's `lib/response.js` shape: declare the object, export it, then augment it."""
    batch = _repo(
        tmp_path,
        {
            "response.js": (
                "var res = Object.create(null);\nmodule.exports = res;\nres.send = function send() {};\n"
            )
        },
    )
    assert "ts:response.send" in _ids(batch)


def test_an_object_merely_read_from_exports_is_not_an_alias(tmp_path: Path) -> None:
    """`const e = module.exports` points at the *current* object; a later `module.exports = …`
    leaves it behind, so augmenting `e` does not export anything."""
    batch = _repo(
        tmp_path,
        {"m.js": "const e = module.exports;\nmodule.exports = {};\ne.stale = function () {};\n"},
    )
    assert "ts:m.stale" not in _ids(batch)


def _exposes(batch: FactBatch) -> set[tuple[str, str]]:
    return _edges(batch, EdgeKind.EXPOSES)


def test_a_router_bound_through_module_exports_is_read(tmp_path: Path) -> None:
    """`var app = module.exports = express()` — 18 of express's 28 example apps."""
    batch = _repo(
        tmp_path,
        {
            "app.js": "var express = require('express');\nvar app = module.exports = express();\n"
            "function home(req, res) {}\napp.get('/', home);\n"
        },
    )
    assert ("ts:endpoint:GET /", "ts:app.home") in _exposes(batch)


def test_a_handler_named_through_a_require_namespace_is_exposed(tmp_path: Path) -> None:
    """`app.get('/', site.index)` — express's route-separation idiom."""
    batch = _repo(
        tmp_path,
        {
            "site.js": "exports.index = function (req, res) {};\n",
            "app.js": "const express = require('express');\nconst site = require('./site');\n"
            "const app = express();\napp.get('/', site.index);\n",
        },
    )
    assert ("ts:endpoint:GET /", "ts:site.index") in _exposes(batch)


def test_a_handler_named_through_this_modules_exports_is_exposed(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "app.js": "const express = require('express');\nconst app = express();\n"
            "exports.version = function (req, res) {};\napp.get('/v', exports.version);\n"
        },
    )
    assert ("ts:endpoint:GET /v", "ts:app.version") in _exposes(batch)


def test_an_exposes_to_a_handler_nothing_declares_is_dropped(tmp_path: Path) -> None:
    """The route is real, so its Endpoint stays; the handler is not, so its edge goes."""
    batch = _repo(
        tmp_path,
        {
            "users.js": "exports.list = function () {};\n",
            "app.js": "const express = require('express');\nconst users = require('./users');\n"
            "const app = express();\napp.post('/u', users.missing);\n",
        },
    )
    assert "ts:endpoint:POST /u" in _ids(batch)
    assert not _exposes(batch)


def test_typescript_does_not_bind_member_handlers(tmp_path: Path) -> None:
    """TypeScript has no `finalize` check on what it names, so it must not resolve by name:
    a module without `list` would leave a dangling EXPOSES. Wiring this in for TypeScript needs
    that check first — this test is what should stop it arriving without one."""
    batch = _repo(
        tmp_path,
        {
            "handlers.ts": "export function list(): void {}\n",
            "app.ts": "import express from 'express';\nimport * as handlers from './handlers';\n"
            "const app = express();\napp.get('/', handlers.list);\n",
        },
    )
    assert "ts:endpoint:GET /" in _ids(batch)
    assert not _exposes(batch)


# ── review pass 2: precision rules added after the maintainer review of #435 ──────────


def test_a_member_call_through_a_rebound_namespace_name_does_not_resolve(tmp_path: Path) -> None:
    """`const user = require('./user')`, then a function that rebinds `user`: its `user.save()`
    reaches whatever that function was handed, not the module's export. Byte-accurate, so a
    one-liner is covered too."""
    batch = _repo(
        tmp_path,
        {
            "user.js": "exports.save = function () {};\n",
            "api.js": (
                "const user = require('./user');\n"
                "function direct() { user.save(); }\n"
                "function viaParam(user) { user.save(); }\n"
                "function viaLocal() { const user = makeUser(); user.save(); }\n"
                "function viaCallback(users) { users.forEach(function (user) { user.save(); }); }\n"
            ),
        },
    )
    assert {src for src, dst in _edges(batch, EdgeKind.CALLS) if dst == "ts:user.save"} == {"ts:api.direct"}


def test_typescript_still_calls_a_namespace_import(tmp_path: Path) -> None:
    """`import * as moment` then `moment()` is legal for an `export =` module. Refusing it is
    CommonJS-only; TypeScript resolves it as it always has."""
    batch = _repo(
        tmp_path, {"a.ts": "import * as moment from 'moment';\nexport function f(): void { moment(); }\n"}
    )
    assert ("ts:a.f", "ts:moment:moment") in _edges(batch, EdgeKind.CALLS)


def test_function_prototype_members_on_a_require_binding_name_no_export(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {"a.js": "const EventEmitter = require('events');\nfunction Foo() { EventEmitter.call(this); }\n"},
    )
    assert "ts:events:call" not in _ids(batch)
    assert not _edges(batch, EdgeKind.CALLS)


def test_requiring_the_package_root_reaches_the_root_module(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "index.js": "exports.f = function () {};\n",
            "lib/a.js": "const r = require('..');\nconst i = require('../index');\nfunction g() { r.f(); }\n",
        },
    )
    assert ("ts:lib/a.g", "ts:<root>.f") in _edges(batch, EdgeKind.CALLS)
    assert not {"ts:.", "ts:index"} & _ids(batch)


def test_a_renamed_export_routes_the_call_to_what_is_exported(tmp_path: Path) -> None:
    """`module.exports = { run: helper }` beside a private `function run`: `m.run()` is `helper`."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "function run() {}\nfunction helper() {}\nmodule.exports = { run: helper };\n",
            "api.js": "const m = require('./m');\nfunction go() { m.run(); }\n",
        },
    )
    calls = _edges(batch, EdgeKind.CALLS)
    assert ("ts:api.go", "ts:m.helper") in calls
    assert ("ts:api.go", "ts:m.run") not in calls


def test_a_renamed_export_routes_the_endpoint_to_what_is_exported(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "site.js": (
                "function index(req, res) {}\n"
                "function realIndex(req, res) {}\n"
                "module.exports = { index: realIndex };\n"
            ),
            "app.js": (
                "const express = require('express');\nconst site = require('./site');\n"
                "const app = express();\napp.get('/', site.index);\n"
            ),
        },
    )
    assert _exposes(batch) == {("ts:endpoint:GET /", "ts:site.realIndex")}


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("var o = {};\nexports = o;\no.run = function run() {};\n", id="bare-exports-rebind"),
        pytest.param(
            "var a = {}, b = {};\nmodule.exports = a;\na.run = function run() {};\nmodule.exports = b;\n",
            id="module-exports-twice",
        ),
        pytest.param(
            "let app = module.exports = {};\napp = {};\napp.run = function run() {};\n", id="alias-reassigned"
        ),
        pytest.param("module.exports = function run() {};\n", id="function-default-export"),
    ],
)
def test_an_export_the_file_undoes_is_not_an_export(tmp_path: Path, module: str) -> None:
    batch = _repo(
        tmp_path, {"m.js": module, "api.js": "const m = require('./m');\nfunction go() { m.run(); }\n"}
    )
    assert not {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.go"}


def test_the_chained_exports_alias_to_a_declared_object(tmp_path: Path) -> None:
    """`exports = module.exports = res` — express's `lib/response.js` rebinds both."""
    batch = _repo(
        tmp_path,
        {
            "r.js": "var res = {};\nexports = module.exports = res;\nres.send = function send() {};\n",
            "api.js": "const r = require('./r');\nfunction go() { r.send(); }\n",
        },
    )
    assert ("ts:api.go", "ts:r.send") in _edges(batch, EdgeKind.CALLS)


def test_a_route_handler_through_an_exports_alias(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "app.js": (
                "const express = require('express');\nvar app = module.exports = express();\n"
                "app.home = function home(req, res) {};\napp.get('/', app.home);\n"
            )
        },
    )
    assert ("ts:endpoint:GET /", "ts:app.home") in _exposes(batch)


def test_compiled_javascript_beside_its_typescript_is_skipped(tmp_path: Path) -> None:
    """`tsc` output: both map to `ts:foo`, and the `.js` sorts first and would take it over."""
    batch = _repo(
        tmp_path,
        {"foo.ts": "export function bar(): void {}\n", "foo.js": "function bar() {}\nexports.bar = bar;\n"},
    )
    foo = next(n for n in batch.nodes if n.id == "ts:foo")
    assert foo.language == "typescript" and foo.provenance is not None and foo.provenance.file == "foo.ts"


def test_a_string_key_no_call_site_can_spell_is_not_an_export(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"m.js": "module.exports = { 'a-b': function () {}, ok: function () {} };\n"})
    functions = {n.id for n in batch.nodes if n.kind is NodeKind.FUNCTION}
    assert "ts:m.ok" in functions and "ts:m.a-b" not in functions


def test_a_destructuring_default_binds_under_the_export_name(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "u.js": "exports.double = (x) => x * 2;\n",
            "api.js": "const { double = null } = require('./u');\nfunction run(x) { return double(x); }\n",
        },
    )
    assert ("ts:api.run", "ts:u.double") in _edges(batch, EdgeKind.CALLS)


def test_a_member_require_bound_under_another_name_records_the_import_only(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "u.js": "exports.double = (x) => x * 2;\nexports.twice = (x) => x * 2;\n",
            "api.js": "const twice = require('./u').double;\nfunction run(x) { return twice(x); }\n",
        },
    )
    assert ("ts:api", "ts:u") in _edges(batch, EdgeKind.IMPORTS)
    assert not {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.run"}


def test_externals_a_javascript_file_mints_are_tagged_javascript(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"a.js": "const fs = require('fs');\n"})
    assert next(n for n in batch.nodes if n.id == "ts:fs").language == "javascript"


def test_the_existence_check_leaves_typescript_edges_alone(tmp_path: Path) -> None:
    """Only this front-end's name-based edges are checked; TypeScript's are its own business."""
    batch = _repo(tmp_path, {"a.ts": "import { x } from './nowhere';\nexport function f(): void { x(); }\n"})
    assert ("ts:a.f", "ts:nowhere.x") in _edges(batch, EdgeKind.CALLS)


def test_a_typescript_router_bound_through_module_exports_is_read(tmp_path: Path) -> None:
    """The chain walk runs for TypeScript too — an additive change, recorded as such."""
    batch = _repo(
        tmp_path,
        {
            "app.ts": (
                "import express from 'express';\n"
                "const app = module.exports = express();\n"
                "function h(): void {}\n"
                "app.get('/x', h);\n"
            )
        },
    )
    assert ("ts:endpoint:GET /x", "ts:app.h") in _exposes(batch)


# ── review pass 3: scopes with an end, and an export map enforced only where it is read ──


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "ids.forEach(function (user) { user.save(); });\n  return user.findAll();", id="callback"
        ),
        pytest.param("ids.map((user) => user);\n  return user.findAll();", id="arrow"),
        pytest.param("for (const user of ids) { user.save(); }\n  return user.findAll();", id="loop"),
        pytest.param(
            "if (ids) { const user = make(); user.save(); } else { user.findAll(); }", id="sibling-block"
        ),
        # Review pass 4: `catch (e)` had no test, and `var` was hoisted to the function being read
        # rather than to the callback it sits in.
        pytest.param("try { run(); } catch (user) { user.save(); }\n  return user.findAll();", id="catch"),
        pytest.param(
            "ids.forEach(function (id) { var user = id; user.save(); });\n  return user.findAll();",
            id="var-in-callback",
        ),
        pytest.param(
            "ids.forEach(function () { for (var user of ids) { user.save(); } });\n  return user.findAll();",
            id="for-var-in-callback",
        ),
        pytest.param(
            "const f = () => { var user = 1; user.save(); };\n  return user.findAll();", id="var-in-arrow"
        ),
        pytest.param(
            "(function () { var user = 1; user.save(); })();\n  return user.findAll();", id="var-in-iife"
        ),
    ],
)
def test_a_binding_ends_with_its_scope(tmp_path: Path, body: str) -> None:
    """Pass 2 bound a callback parameter from its start to the end of the function, and dropped the
    true call after it. Each binding now ends where JavaScript ends it."""
    batch = _repo(
        tmp_path,
        {
            "user.js": "exports.findAll = function () {};\nexports.save = function () {};\n",
            "api.js": f"const user = require('./user');\nfunction go(ids) {{\n  {body}\n}}\n",
        },
    )
    reached = {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.go"}
    assert reached == {"ts:user.findAll"}


def test_typescript_bindings_end_with_their_scope_too(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "user.ts": "export function findAll(): void {}\nexport function save(): void {}\n",
            "api.ts": (
                "import * as user from './user';\n"
                "export function go(ids: number[]): void {\n"
                "  ids.forEach(function (user) { user.save(); });\n  user.findAll();\n}\n"
            ),
        },
    )
    assert {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.go"} == {"ts:user.findAll"}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("user.save();\n  var user = make();", id="declared-below"),
        pytest.param("if (ids) { var user = make(); }\n  user.save();", id="declared-in-a-block"),
        pytest.param("for (var user of ids) {}\n  user.save();", id="loop-var"),
    ],
)
def test_var_is_hoisted_over_the_whole_function(tmp_path: Path, body: str) -> None:
    """A `var` in the function's own body — not in a callback — is its local everywhere in it:
    above its declaration, and outside the block or loop that declares it. The pass-4 narrowing
    to "the nearest function" must not narrow these."""
    batch = _repo(
        tmp_path,
        {
            "user.js": "exports.save = function () {};\n",
            "api.js": f"const user = require('./user');\nfunction go(ids) {{\n  {body}\n}}\n",
        },
    )
    assert not {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.go"}


def test_typescript_var_in_a_callback_is_the_callbacks(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "user.ts": "export function findAll(): void {}\nexport function save(): void {}\n",
            "api.ts": (
                "import * as user from './user';\n"
                "export function go(ids: number[]): void {\n"
                "  ids.forEach(function (id) { var user = id; user.save(); });\n  user.findAll();\n}\n"
            ),
        },
    )
    assert {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:api.go"} == {"ts:user.findAll"}


def test_a_nested_function_declaration_shadows_an_import_over_its_block(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "u.js": "exports.helper = function () {};\n",
            "api.js": (
                "const { helper } = require('./u');\n"
                "function go() {\n"
                "  helper();\n"
                "  function helper() {}\n"
                "}\n"
            ),
        },
    )
    assert ("ts:api.go", "ts:u.helper") not in _edges(batch, EdgeKind.CALLS)


def test_an_aliased_object_is_read_as_the_export_surface(tmp_path: Path) -> None:
    """`const api = { find, create }; module.exports = api` — pass 2 dropped both calls."""
    batch = _repo(
        tmp_path,
        {
            "svc.js": (
                "function find() {}\n"
                "function create() {}\n"
                "const api = { find, create };\n"
                "module.exports = api;\n"
            ),
            "c.js": "const svc = require('./svc');\nfunction go() { svc.find(); svc.create(); }\n",
        },
    )
    assert {("ts:c.go", "ts:svc.find"), ("ts:c.go", "ts:svc.create")} <= _edges(batch, EdgeKind.CALLS)


def test_a_declared_class_that_is_module_exports_can_be_extended(tmp_path: Path) -> None:
    """`class Base {}; module.exports = Base;` — the canonical CommonJS inheritance shape."""
    batch = _repo(
        tmp_path,
        {
            "base.js": "class Base {}\nmodule.exports = Base;\n",
            "impl.js": "const Base = require('./base');\nclass Impl extends Base {}\n",
        },
    )
    assert ("ts:impl.Impl", "ts:base.Base") in _edges(batch, EdgeKind.IMPLEMENTS)


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("function f() {}\nmodule.exports = Object.assign({}, { f });\n", id="object-assign"),
        pytest.param("function f() {}\nmodule.exports = { ...other, f };\n", id="spread"),
        pytest.param(
            "function f() {}\nObject.defineProperty(exports, 'f', { get: function () { return f; } });\n",
            id="define-property",
        ),
        pytest.param("export function f() {}\nmodule.exports.g = function g() {};\n", id="mixed-esm"),
    ],
)
def test_an_export_surface_this_pass_cannot_read_is_not_enforced(tmp_path: Path, module: str) -> None:
    """Enforcing a map it only half read dropped true edges; a reader that cannot tell must not decide."""
    batch = _repo(tmp_path, {"m.js": module, "c.js": "const m = require('./m');\nfunction go() { m.f(); }\n"})
    assert ("ts:c.go", "ts:m.f") in _edges(batch, EdgeKind.CALLS)


def test_babels_es_module_marker_does_not_make_a_surface_unreadable(tmp_path: Path) -> None:
    """Every Babel output file starts with it, and it adds no member anyone calls."""
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                'Object.defineProperty(exports, "__esModule", { value: true });\n'
                "function run() {}\nfunction helper() {}\nmodule.exports = { run: helper };\n"
            ),
            "c.js": "const m = require('./m');\nfunction go() { m.run(); }\n",
        },
    )
    assert ("ts:c.go", "ts:m.helper") in _edges(batch, EdgeKind.CALLS)


def test_a_module_exports_inside_a_branch_exports_nothing_for_certain(tmp_path: Path) -> None:
    """UMD: which object is exported depends on which branch ran."""
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "if (typeof module === 'object') { module.exports = factory(); }\n"
                "exports.g = function g() {};\n"
            ),
            "c.js": "const m = require('./m');\nfunction go() { m.g(); }\n",
        },
    )
    assert ("ts:c.go", "ts:m.g") not in _edges(batch, EdgeKind.CALLS)


@pytest.mark.parametrize(
    "decoy",
    [
        pytest.param("", id="no-class-named-Handler"),
        pytest.param("class Handler { run() {} }\n", id="a-decoy-Handler"),
    ],
)
def test_a_renamed_class_export_is_rewritten_at_every_depth(tmp_path: Path, decoy: str) -> None:
    """`{ Handler: Impl }`: the constructor edge and the method edge must agree on `Impl`.

    Pass 3's test declared a decoy `class Handler` in the same file, and passed only because of
    it: the parent's existence check ran before the rename, so without the decoy `ts:m.Handler.run`
    did not exist and both edges were gone before the export map was asked. The normal shape has
    no such class.
    """
    batch = _repo(
        tmp_path,
        {
            "m.js": decoy + "class Impl { run() {} }\nmodule.exports = { Handler: Impl };\n",
            "c.js": "const { Handler } = require('./m');\nfunction go() { return new Handler().run(); }\n",
        },
    )
    calls = _edges(batch, EdgeKind.CALLS)
    assert {("ts:c.go", "ts:m.Impl"), ("ts:c.go", "ts:m.Impl.run")} <= calls
    assert ("ts:c.go", "ts:m.Handler.run") not in calls


def test_an_undone_export_keeps_its_function_and_its_same_file_route(tmp_path: Path) -> None:
    """The export was undone; the function was not. A same-file `exports.show` read is it."""
    batch = _repo(
        tmp_path,
        {
            "r.js": (
                "const express = require('express');\nconst router = express.Router();\n"
                "exports.show = function show(req, res) {};\nrouter.get('/show', exports.show);\n"
                "module.exports = router;\n"
            )
        },
    )
    assert "ts:r.show" in _ids(batch)
    assert ("ts:endpoint:GET /show", "ts:r.show") in _exposes(batch)


def test_a_chained_export_exports_every_name(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": "function f() {}\nexports.a = exports.b = f;\n",
            "c.js": "const m = require('./m');\nfunction one() { m.a(); }\nfunction two() { m.b(); }\n",
        },
    )
    calls = _edges(batch, EdgeKind.CALLS)
    assert {("ts:c.one", "ts:m.f"), ("ts:c.two", "ts:m.f")} <= calls


@pytest.mark.parametrize("suffix", [".mjs", ".cjs"])
def test_any_javascript_sibling_of_a_typescript_module_is_skipped(tmp_path: Path, suffix: str) -> None:
    batch = _repo(
        tmp_path,
        {"foo.ts": "export function bar(): void {}\n", f"foo{suffix}": "exports.bar = function () {};\n"},
    )
    assert next(n for n in batch.nodes if n.id == "ts:foo").language == "typescript"


# ── review pass 4: the module is a real module, and "readable" is an allowlist ──────────────


def test_a_dotted_module_beside_a_commonjs_one_keeps_its_edges(tmp_path: Path) -> None:
    """`models/user.js` beside `models/user.model.ts`: `ts:models/user.model.find` also *reads* as
    member `model` of `ts:models/user`. Pass 3 searched only CommonJS modules for the prefix, stopped
    at `user.js`, found no `model` export, and dropped every TypeScript edge into `user.model`."""
    batch = _repo(
        tmp_path,
        {
            "models/user.js": "function x() {}\nmodule.exports = { x };\n",
            "models/user.model.ts": (
                "export class User { save(): void {} }\nexport function find(): void {}\n"
            ),
            "app.ts": (
                "import { User, find } from './models/user.model';\n"
                "export class Admin extends User {}\n"
                "export function run(): void { find(); new User().save(); }\n"
            ),
        },
    )
    calls = _edges(batch, EdgeKind.CALLS)
    assert {
        ("ts:app.run", "ts:models/user.model.find"),
        ("ts:app.run", "ts:models/user.model.User"),
        ("ts:app.run", "ts:models/user.model.User.save"),
    } <= calls
    assert ("ts:app.Admin", "ts:models/user.model.User") in _edges(batch, EdgeKind.IMPLEMENTS)


def test_the_map_still_applies_past_a_dotted_prefix(tmp_path: Path) -> None:
    """The longest *module* prefix is `ts:models/user`, readable, and not exporting `y`."""
    batch = _repo(
        tmp_path,
        {
            "models/user.js": "function x() {}\nfunction y() {}\nmodule.exports = { x };\n",
            "c.js": "const user = require('./models/user');\nfunction go() { user.x(); user.y(); }\n",
        },
    )
    reached = {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:c.go"}
    assert reached == {"ts:models/user.x"}


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("module.exports = Object.freeze({ f, g });\n", id="freeze"),
        pytest.param("function make() { return { f, g }; }\nmodule.exports = make();\n", id="factory"),
        pytest.param("let api;\napi = { f, g };\nmodule.exports = api;\n", id="late-alias"),
        pytest.param("exports.g = g;\nexports['f'] = f;\n", id="subscript"),
        pytest.param(
            "const api = { g };\nObject.assign(api, { f });\nmodule.exports = api;\n", id="assign-alias"
        ),
        pytest.param(
            "const api = module.exports = {};\nObject.assign(api, { f, g });\n", id="assign-chained-alias"
        ),
        pytest.param("exports.g = g;\nfunction init() { exports.f = f; }\n", id="written-in-a-function"),
        pytest.param("module.exports = { g, [key]: f };\n", id="computed-key"),
        pytest.param("module.exports = class { static f() {} };\n", id="class-expression"),
    ],
)
def test_a_surface_off_the_allowlist_is_not_enforced(tmp_path: Path, module: str) -> None:
    """Each shape the third pass still called readable, and so dropped `m.f()`. Off the allowlist,
    a call is resolved by name and kept because `ts:m.f` exists."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "function f() {}\nfunction g() {}\n" + module,
            "c.js": "const m = require('./m');\nfunction go() { m.f(); }\n",
        },
    )
    assert ("ts:c.go", "ts:m.f") in _edges(batch, EdgeKind.CALLS)


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("module.exports = { g };\n", id="object"),
        pytest.param("const api = { g };\nmodule.exports = api;\n", id="declared-object"),
        pytest.param("exports.g = g;\n", id="member-writes"),
        pytest.param("var app = exports = module.exports = {};\napp.g = g;\n", id="chained-alias"),
        pytest.param(
            "var res = Object.create(Base.prototype);\nmodule.exports = res;\nres.g = g;\n",
            id="object-create",
        ),
    ],
)
def test_a_surface_on_the_allowlist_is_enforced(tmp_path: Path, module: str) -> None:
    """The other half: a surface read in full lists `g` and not `f`, so `m.f()` reaches nothing —
    `f` is a module-private function. Without this, "readable" could quietly become "never"."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "function f() {}\nfunction g() {}\n" + module,
            "c.js": "const m = require('./m');\nfunction go() { m.f(); m.g(); }\n",
        },
    )
    reached = {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == "ts:c.go"}
    assert reached == {"ts:m.g"}


_RENAMED = "class Impl {\n  run() {\n    return true;\n  }\n}\nmodule.exports = { Handler: Impl };\n"
_DECOY = "class Handler {\n  run() {\n    return false;\n  }\n}\n"
_CALLER = (
    "import {{ Handler }} from '{spec}';\n"
    "export function go(): boolean {{\n  return new Handler().run();\n}}\n"
)


@pytest.mark.parametrize("decoy", [False, True], ids=["plain", "decoy"])
@pytest.mark.parametrize("caller", ["a.ts", "app/z.ts"], ids=["ts-walked-first", "js-walked-first"])
@pytest.mark.parametrize("typescript_first", [True, False], ids=["ts-registered", "js-registered"])
def test_a_typescript_caller_reaches_a_renamed_commonjs_export_in_any_order(
    tmp_path: Path, decoy: bool, caller: str, typescript_first: bool
) -> None:
    """F4: TypeScript's deferred calls are routed through the JavaScript export map.

    Without the decoy the call was lost whichever order ran (TypeScript's existence check drops
    `Handler`, which no file declares, before anything routes it). With the decoy it landed on the
    decoy whenever the JavaScript finalizer ran first. An edge the TypeScript finalizer routed is
    not routed again by the JavaScript one — `Impl`, looked up as an export, is not there.
    """
    spec = "../m" if "/" in caller else "./m"
    files = {"m.js": (_DECOY if decoy else "") + _RENAMED, caller: _CALLER.format(spec=spec)}
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    front_ends: list[LanguageExtractor] = [TypeScriptExtractor(), JavaScriptExtractor()]
    ordered = front_ends if typescript_first else front_ends[::-1]
    batch = RepoCodeExtractor(extractors=ordered).extract(tmp_path)
    source = f"ts:{caller.removesuffix('.ts')}.go"
    reached = {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == source}
    assert reached == {"ts:m.Impl", "ts:m.Impl.run"}


def test_typescript_and_javascript_let_go_of_the_run_when_they_finalize(tmp_path: Path) -> None:
    ts, js = TypeScriptExtractor(), JavaScriptExtractor()
    (tmp_path / "m.js").write_text(_RENAMED)
    (tmp_path / "a.ts").write_text(_CALLER.format(spec="./m"))
    RepoCodeExtractor(extractors=[ts, js]).extract(tmp_path)
    assert ts._run.exports == {} and js._run.exports == {} and ts._run is not js._run


# ---- js-review-followup P3: three tiers, the reference allowlist, the default slot, no call on a module


def _calls_from(batch: FactBatch, source: str) -> set[str]:
    return {dst for src, dst in _edges(batch, EdgeKind.CALLS) if src == source}


_CALLER_OF = "const m = require('./m');\nfunction go() {{ {calls} }}\n"


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("exports.g = g;\nif (process.env.X) exports.f = f;\n", id="brace-less-if"),
        pytest.param("exports.g = g;\nwhile (false) exports.f = f;\n", id="brace-less-while"),
        pytest.param("exports.g = g;\nReflect.defineProperty(exports, 'f', { value: f });\n", id="reflect"),
        pytest.param(
            "exports.g = g;\nObject.defineProperties(exports, { f: { value: f } });\n", id="properties"
        ),
        pytest.param("exports.g = g;\nObject.assign(exports, { f });\n", id="assign"),
        pytest.param("exports.g = g;\nexports['f'] = f;\n", id="string-subscript"),
        pytest.param("exports.g = g;\nthis.f = f;\n", id="top-level-this"),
        pytest.param("module.exports = Object.freeze({ f, g });\n", id="frozen-literal"),
        pytest.param(
            "var api = module.exports = {};\napi.g = g;\nfunction init() { api.f = f; }\n", id="in-a-function"
        ),
    ],
)
def test_a_write_the_allowlist_reads_is_an_export_and_nothing_else_is(tmp_path: Path, module: str) -> None:
    """F2, F3, F5: every recognised write names an export; a function the file never writes to
    its exports is not reachable through the module, however it is declared."""
    source = "function f() {}\nfunction g() {}\nfunction secret() {}\n" + module
    batch = _repo(tmp_path, {"m.js": source, "c.js": _CALLER_OF.format(calls="m.f(); m.secret();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.f"}


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("const e = exports;\ne.f = f;\n", id="copied-to-a-name"),
        pytest.param("const api = module.exports;\napi.f = f;\n", id="module-exports-copied"),
        pytest.param(
            "function mixin(t, s) { return t; }\nmixin(exports, { f });\n", id="handed-to-a-function"
        ),
        pytest.param("const k = 'f';\nexports[k] = f;\n", id="computed-subscript"),
        pytest.param("({ f: exports.f } = { f });\n", id="destructuring-write"),
        pytest.param("module.exports = { __proto__: proto, g };\nconst proto = { f };\n", id="proto-key"),
        pytest.param(
            "const proto = { f };\nmodule.exports = Object.create(proto);\n", id="create-of-a-local"
        ),
        pytest.param("module.exports = Object.create(null, { f: { value: f } });\n", id="create-with-props"),
        pytest.param("let api = { g };\napi = { f };\nmodule.exports = api;\n", id="alias-reassigned"),
    ],
)
def test_an_unrecognised_reference_makes_the_surface_opaque(tmp_path: Path, module: str) -> None:
    """D6: a reference that is not on the allowlist means this pass has not read the surface, so
    the call is resolved by name and kept — never dropped by a map it only half read."""
    source = "function f() {}\nfunction g() {}\n" + module
    batch = _repo(tmp_path, {"m.js": source, "c.js": _CALLER_OF.format(calls="m.f();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.f"}


def test_a_parameter_named_like_the_alias_is_its_own_binding(tmp_path: Path) -> None:
    """F5: `tag(api)` shadows the alias; its `return api` must not make the surface opaque."""
    module = (
        "function f() {}\nfunction secret() {}\nvar api = module.exports = {};\napi.f = f;\n"
        "function tag(api) { api.x = 1; return api; }\n"
    )
    batch = _repo(tmp_path, {"m.js": module, "c.js": _CALLER_OF.format(calls="m.f(); m.secret();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.f"}


def test_constructing_the_default_in_its_own_file_is_not_a_write(tmp_path: Path) -> None:
    module = (
        "module.exports = User;\nfunction User() {}\nfunction secret() {}\n"
        "User.all = function all() {};\nnew User();\n"
    )
    batch = _repo(tmp_path, {"m.js": module, "c.js": _CALLER_OF.format(calls="m.all(); m.secret();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.all"}


def test_a_write_inside_a_declaration_is_read(tmp_path: Path) -> None:
    """`var pets = exports.pets = …` writes the export too; `_emit_statement` sees statements only."""
    module = "function list() {}\nvar pets = exports.list = list;\nfunction secret() {}\n"
    batch = _repo(tmp_path, {"m.js": module, "c.js": _CALLER_OF.format(calls="m.list(); m.secret();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.list"}


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("if (typeof module !== 'undefined') { module.exports = { f, g }; }\n", id="umd-branch"),
        pytest.param(
            "if (a) { module.exports = { f }; } else { module.exports = { g }; }\n", id="either-branch"
        ),
    ],
)
def test_an_ambiguous_surface_exports_the_union_of_its_names(tmp_path: Path, module: str) -> None:
    """D7, S3: never an empty map enforced — every name any branch writes may be exported."""
    source = "function f() {}\nfunction g() {}\nfunction hidden() {}\n" + module
    batch = _repo(tmp_path, {"m.js": source, "c.js": _CALLER_OF.format(calls="m.f(); m.g(); m.hidden();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.f", "ts:m.g"}


def test_module_exports_reassigned_at_the_top_level_is_the_last_object(tmp_path: Path) -> None:
    """Straight-line code is not ambiguous: the last assignment wins, and the first object's
    members were written onto something nobody exports."""
    module = "function f() {}\nfunction g() {}\nmodule.exports = { g };\nmodule.exports = { f };\n"
    batch = _repo(tmp_path, {"m.js": module, "c.js": _CALLER_OF.format(calls="m.f(); m.g();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.f"}


def test_an_opaque_surface_still_refuses_a_name_written_onto_a_replaced_object(tmp_path: Path) -> None:
    """`module.exports = make()` hides its names, but `exports.g` after it was written onto the
    object `module.exports` replaced: that one is known not to be exported."""
    module = "function f() {}\nfunction g() {}\nmodule.exports = make();\nexports.g = g;\n"
    batch = _repo(tmp_path, {"m.js": module, "c.js": _CALLER_OF.format(calls="m.f(); m.g();")})
    assert _calls_from(batch, "ts:c.go") == {"ts:m.f"}


_BASE = "class Base {\n  hello() {}\n}\nmodule.exports = Base;\n"


@pytest.mark.parametrize(
    ("caller", "kept"),
    [
        pytest.param("const B = require('./base');\nclass K extends B {}\n", True, id="renamed-require"),
        pytest.param(
            "const Base = require('./base');\nclass K extends Base {}\n", True, id="same-name-require"
        ),
        pytest.param("import B from './base';\nclass K extends B {}\n", True, id="esm-default-import"),
        pytest.param(
            "const { Base } = require('./base');\nclass K extends Base {}\n", False, id="destructured"
        ),
    ],
)
def test_the_default_is_reached_as_the_module_never_as_a_member(
    tmp_path: Path, caller: str, kept: bool
) -> None:
    """D7: `module.exports = Base` fills the default slot. A whole-module binding reaches it under
    any name; `{ Base }` reads a property `Base` off the class, which is `undefined`."""
    batch = _repo(tmp_path, {"base.js": _BASE, "k.js": caller})
    assert (("ts:k.K", "ts:base.Base") in _edges(batch, EdgeKind.IMPLEMENTS)) is kept


def test_a_renamed_default_reaches_its_methods(tmp_path: Path) -> None:
    caller = "const B = require('./base');\nfunction go() { return new B().hello(); }\n"
    batch = _repo(tmp_path, {"base.js": _BASE, "k.js": caller})
    assert _calls_from(batch, "ts:k.go") == {"ts:base.Base", "ts:base.Base.hello"}


def test_a_typescript_default_import_of_a_commonjs_default_keeps_resolving_by_name(tmp_path: Path) -> None:
    """The declared gap: TypeScript's default import is resolved by the local name, as before."""
    batch = _repo(
        tmp_path, {"base.js": _BASE, "k.ts": "import Base from './base';\nexport class K extends Base {}\n"}
    )
    assert ("ts:k.K", "ts:base.Base") in _edges(batch, EdgeKind.IMPLEMENTS)


@pytest.mark.parametrize(
    ("files", "calls", "expected"),
    [
        pytest.param(
            {
                "lib/db.js": "function loadConfig() {}\nmodule.exports = { config: loadConfig };\n",
                "lib/db.config.ts": "export function port(): number { return 1; }\n",
            },
            "const db = require('./lib/db');\nfunction go() { db.config(); }\n",
            {"ts:lib/db.loadConfig"},
            id="dotted-sibling-module",
        ),
        pytest.param(
            {"cfg.js": "exports.x = function x() {};\n", "cfg.json": "{}\n"},
            "const cfg = require('./cfg');\nconst j = require('./cfg.json');\n"
            "function go() { cfg.json(); }\n",
            set(),
            id="a-required-json-file",
        ),
        pytest.param(
            {
                "lib/db.js": "exports.x = function x() {};\n",
                "lib/db.model.ts": "export function f(): void {}\n",
            },
            "const db = require('./lib/db');\nfunction go() { db.model(); }\n",
            set(),
            id="a-member-the-map-lacks",
        ),
        pytest.param(
            {
                "lib/db.js": "export function x() {}\n",
                "lib/db.config.ts": "export function port(): number { return 1; }\n",
            },
            "import * as db from './lib/db';\nfunction go() { db.config(); }\n",
            set(),
            id="no-commonjs-module-at-all",
        ),
    ],
)
def test_a_call_never_lands_on_a_module(
    tmp_path: Path, files: dict[str, str], calls: str, expected: set[str]
) -> None:
    """F1: a target that is itself a module id is retried as a member of the next shorter
    module, through its map — and dropped when the map does not name it."""
    batch = _repo(tmp_path, {**files, "c.js": calls})
    assert _calls_from(batch, "ts:c.go") == expected
    modules = {n.id for n in batch.nodes if n.kind is NodeKind.MODULE}
    assert not {dst for _, dst in _edges(batch, EdgeKind.CALLS)} & modules


def test_a_var_in_a_class_static_block_is_the_blocks_own(tmp_path: Path) -> None:
    """Extractor N4: hoisted past `static {}`, `var m` there shadowed the import in the whole
    enclosing function, and the true `m.go()` after it was dropped."""
    caller = (
        "const m = require('./m');\nfunction top() {\n  const D = class {\n    static {\n      var m = 2;\n"
        "    }\n  };\n  return m.go();\n}\n"
    )
    batch = _repo(tmp_path, {"m.js": "exports.go = function go() {};\n", "c.js": caller})
    assert _calls_from(batch, "ts:c.top") == {"ts:m.go"}


@pytest.mark.parametrize(
    ("module", "tier"),
    [
        pytest.param("exports.f = f;\n", "readable", id="plain-write"),
        pytest.param("exports['f'] = f;\n", "readable", id="string-subscript"),
        pytest.param("module.exports = { f };\n", "readable", id="object-literal"),
        pytest.param("module.exports = f;\n", "readable", id="declared-default"),
        pytest.param("var module = { exports: {} };\nmodule.exports = { f };\n", "readable", id="own-module"),
        pytest.param("if (c) exports.f = f;\n", "names-known", id="brace-less-branch"),
        pytest.param("if (c) { exports.f = f; }\n", "names-known", id="braced-branch"),
        pytest.param("Object.assign(exports, { f });\n", "names-known", id="merge"),
        pytest.param("module.exports = Object.freeze({ f });\n", "names-known", id="frozen"),
        pytest.param("if (c) { module.exports = { f }; }\n", "names-known", id="umd"),
        pytest.param("const e = exports;\ne.f = f;\n", "opaque", id="copied"),
        pytest.param("module.exports = make();\n", "opaque", id="made"),
        pytest.param("if (c) { module.exports = make(); }\n", "opaque", id="umd-made"),
    ],
)
def test_each_surface_is_read_into_the_tier_its_forms_allow(tmp_path: Path, module: str, tier: str) -> None:
    """js-review-followup §3.1: the tier is what `settle` trusts, not only what calls route by."""
    from orchestrator.pkg.extractor import ExtractionRun

    js, run = JavaScriptExtractor(), ExtractionRun()
    js.bind_run(run)
    path = tmp_path / "m.js"
    path.write_text("function f() {}\n" + module)
    js.extract(path=path, module="m", rel="m.js")
    assert run.exports["ts:m"].tier == tier


def test_a_member_named_like_the_whole_module_local_is_the_member(tmp_path: Path) -> None:
    """`const util = require('./util'); util.util()` names the member `util`, not the module."""
    batch = _repo(
        tmp_path,
        {
            "util.js": "function util() {}\nmodule.exports = { util };\n",
            "c.js": "const util = require('./util');\nfunction go() { util.util(); }\n",
        },
    )
    assert _calls_from(batch, "ts:c.go") == {"ts:util.util"}


def test_the_reference_scan_is_not_quadratic_in_references_per_function(tmp_path: Path) -> None:
    """Every reference re-walked its enclosing function to ask whether it shadowed the alias:
    14 s on this 40 KB express-style file, and 36 s on 4,000 writes in one IIFE."""
    import time

    routes = (
        "const app = module.exports = express();\nfunction routes() {\n"
        + "  app.get('/x', h);\n" * 2000
        + "}\n"
    )
    iife = (
        "(function () {\n" + "".join(f"  exports.f{i} = function () {{}};\n" for i in range(4000)) + "})();\n"
    )
    for body in (routes, iife):
        (tmp_path / "m.js").write_text(body)
        started = time.perf_counter()
        RepoCodeExtractor(extractors=[JavaScriptExtractor()]).extract(tmp_path)
        assert (
            time.perf_counter() - started < 10.0
        )  # measured 0.06 s and 0.10 s; the old scan took 14 s and 36 s


def test_a_file_that_declares_its_own_module_exports_nothing_through_it(tmp_path: Path) -> None:
    """Review round 3, S2: `var module = { exports: {} }` makes every `module` in the file a local;
    `require` returns `{}`, so `m.f()` names nothing — and is not left to a name lookup."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "var module = { exports: {} };\nfunction f() {}\nmodule.exports = { f };\n",
            "c.js": "const m = require('./m');\nfunction go() { m.f(); }\n",
        },
    )
    assert _calls_from(batch, "ts:c.go") == set()


@pytest.mark.parametrize(
    ("module", "called"),
    [
        pytest.param(
            "{ let module = {};\n}\nexports.f = f;\nmodule.exports = { g };\n",
            {"ts:m.g"},
            id="let-in-a-block",
        ),
        pytest.param(
            "if (c) { class module {} }\nexports.f = f;\nmodule.exports = { g };\n",
            {"ts:m.g"},
            id="class-in-a-block",
        ),
        pytest.param("var module = { exports: {} };\nmodule.exports.f = f;\n", set(), id="own-module-member"),
    ],
)
def test_only_a_binding_of_the_file_makes_module_its_own(
    tmp_path: Path, module: str, called: set[str]
) -> None:
    """Review round 4, B1 and S1: a `module` declared inside a top-level block is the block's, so
    the real `module.exports` still decides; in a file that does declare its own, a member written
    onto `module.exports` is a local's."""
    batch = _repo(
        tmp_path,
        {
            "m.js": "function f() {}\nfunction g() {}\n" + module,
            "c.js": "const m = require('./m');\nfunction go() { m.f(); m.g(); }\n",
        },
    )
    assert _calls_from(batch, "ts:c.go") == called
