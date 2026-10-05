"""Rust crate identity and precision boundaries, independent of Cargo execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.rust_cargo import CargoIndex
from orchestrator.pkg.rust_extractor import RustExtractor
from orchestrator.pkg.verify import verify_batch

pytest.importorskip("tree_sitter_rust", reason="install the 'rust' extra")


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _extract(root: Path) -> tuple[FactBatch, RustExtractor]:
    front_end = RustExtractor()
    batch = RepoCodeExtractor([front_end]).extract(root)
    return batch, front_end


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind == kind}


def test_cargo_target_scopes_and_source_admission(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="billing"\nversion="0.1.0"\nedition="2024"\n'
            '[[bin]]\nname="admin"\npath="src/admin.rs"\n',
            "src/lib.rs": "pub fn run() {}\n",
            "src/main.rs": "fn run() {}\n",
            "src/admin.rs": "fn run() {}\n",
            "tests/api.rs": "fn run() {}\n",
            "src/unowned.rs": "fn should_not_exist() {}\n",
        },
    )
    batch, _ = _extract(tmp_path)
    funcs = {n.id for n in batch.nodes if n.kind == NodeKind.FUNCTION}
    assert funcs == {
        "rust:billing@lib/billing::run",
        "rust:billing@bin/billing::run",
        "rust:billing@bin/admin::run",
        "rust:billing@test/api::run",
    }
    assert verify_batch(batch, tmp_path).ok


def test_semantic_modules_inline_outlined_and_path_override(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="module-demo"\nversion="0.1.0"\n',
            "src/lib.rs": 'mod foo;\nmod nested { mod leaf; }\n#[path = "custom.rs"] mod override_file;\n',
            "src/foo.rs": "pub struct Foo;\nmod child;\n",
            "src/foo/child.rs": "pub fn child() {}\n",
            "src/nested/leaf.rs": "pub fn leaf() {}\n",
            "src/custom.rs": "pub union Choice { a: u8, b: u16 }\n",
        },
    )
    batch, front_end = _extract(tmp_path)
    scope = "rust:module-demo@lib/module_demo"
    assert {
        scope + "::foo",
        scope + "::foo::child",
        scope + "::nested",
        scope + "::nested::leaf",
        scope + "::override_file",
    } <= {n.id for n in batch.nodes if n.kind == NodeKind.MODULE}
    assert (scope + "::nested", scope + "::nested::leaf") in _edges(batch, EdgeKind.CONTAINS)
    assert scope + "::override_file::Choice" in {n.id for n in batch.nodes}
    assert not front_end.known_gaps


def test_impl_identity_shadowing_and_cfg_ambiguity(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="invoice"\nversion="0.1.0"\n',
            "src/lib.rs": """struct Invoice;
trait Display { fn show(&self); }
impl Invoice { fn show(&self) {} fn new() { Self::new(); self::local(); } }
impl Display for Invoice { fn show(&self) {} }
impl<T> Display for T { fn show(&self) {} }
impl !Send for Invoice {}
fn local() {}
fn call() { local(); let local = || {}; local(); Invoice::new(); }
#[cfg(unix)] fn platform() {}
#[cfg(windows)] fn platform() {}
fn choose() { platform(); }
""",
        },
    )
    batch, front_end = _extract(tmp_path)
    scope = "rust:invoice@lib/invoice"
    nodes = {n.id for n in batch.nodes}
    assert {scope + "::Invoice::show", scope + "::Invoice::<Display>::show"} <= nodes
    calls = _edges(batch, EdgeKind.CALLS)
    assert (scope + "::call", scope + "::local") in calls
    assert (scope + "::call", scope + "::Invoice::new") in calls
    assert (scope + "::choose", scope + "::platform") not in calls
    assert (scope + "::Invoice", scope + "::Display") in _edges(batch, EdgeKind.IMPLEMENTS)
    assert (scope + "::Invoice", "rust:external::Send") not in _edges(batch, EdgeKind.IMPLEMENTS)
    assert "cfg-ambiguous duplicate declarations" in front_end.known_gaps


def test_nested_use_tree_reexports_and_field_references(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="billing"\nversion="0.1.0"\n',
            "src/lib.rs": """mod customer;
use crate::customer::{Customer as Client, self};
pub use crate::customer::Customer;
struct Invoice { buyer: Option<Box<Customer>> }
""",
            "src/customer.rs": "pub struct Customer;\n",
        },
    )
    batch, _ = _extract(tmp_path)
    scope = "rust:billing@lib/billing"
    imports = _edges(batch, EdgeKind.IMPORTS)
    assert (scope, scope + "::customer::Customer") in imports
    assert (scope, scope + "::customer") in imports
    assert (scope + "::Invoice::buyer", scope + "::customer::Customer") in _edges(batch, EdgeKind.REFERENCES)


def test_generic_impl_uses_concrete_constructor_and_skips_blanket(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="repository"\nversion="0.1.0"\n',
            "src/lib.rs": "trait Store<T> { fn save(&self); }\nstruct Sql<T>(T);\n"
            "impl<T> Store<T> for Sql<T> { fn save(&self) {} }\n"
            "impl<T> Store<T> for T { fn save(&self) {} }\n",
        },
    )
    batch, _ = _extract(tmp_path)
    scope = "rust:repository@lib/repository"
    assert (scope + "::Sql", scope + "::Store") in _edges(batch, EdgeKind.IMPLEMENTS)
    assert (scope + "::T", scope + "::Store") not in _edges(batch, EdgeKind.IMPLEMENTS)
    assert scope + "::Sql::<Store>::save" in {n.id for n in batch.nodes}


def test_public_facade_reexport_repoints_to_definition(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="facade"\nversion="0.1.0"\n',
            "src/lib.rs": "mod inner { pub struct Item; }\n"
            "mod api { pub use crate::inner::Item; }\n"
            "use crate::api::Item as Exported;\n",
        },
    )
    batch, _ = _extract(tmp_path)
    scope = "rust:facade@lib/facade"
    assert (scope, scope + "::inner::Item") in _edges(batch, EdgeKind.IMPORTS)
    assert (scope + "::api", scope + "::inner::Item") in _edges(batch, EdgeKind.IMPORTS)


def test_async_no_std_and_macro_boundaries(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="embedded"\nversion="0.1.0"\n',
            "src/lib.rs": "#![no_std]\nextern crate alloc;\nuse core::fmt;\n"
            "async fn work() { helper(); }\nfn helper() {}\n"
            "macro_rules! generated { () => { fn hidden() {} }; }\ngenerated!();\n"
            'include!("missing.rs");\n',
        },
    )
    batch, _ = _extract(tmp_path)
    scope = "rust:embedded@lib/embedded"
    nodes = {n.id for n in batch.nodes if n.grounded}
    assert {scope + "::work", scope + "::helper"} <= nodes
    assert scope + "::hidden" not in nodes
    assert (scope + "::work", scope + "::helper") in _edges(batch, EdgeKind.CALLS)
    assert (scope, "rust:external::core::fmt") in _edges(batch, EdgeKind.IMPORTS)


def test_foo_mod_rs_is_one_semantic_module(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="nested"\nversion="0.1.0"\n',
            "src/lib.rs": "mod foo;\n",
            "src/foo/mod.rs": "mod leaf;\n",
            "src/foo/leaf.rs": "fn f() {}\n",
        },
    )
    batch, _ = _extract(tmp_path)
    scope = "rust:nested@lib/nested"
    assert scope + "::foo::leaf::f" in {n.id for n in batch.nodes}


def test_loop_match_and_closure_bindings_do_not_invent_calls(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="scope"\nversion="0.1.0"\n',
            "src/lib.rs": "fn helper() {}\n"
            "fn run(xs: Vec<fn()>) {\n"
            "  for helper in xs { helper(); }\n"
            "  match None { Some(helper) => helper(), _ => {} }\n"
            "  let call = |helper: fn()| helper();\n"
            "  helper();\n"
            "}\n",
        },
    )
    batch, _ = _extract(tmp_path)
    scope = "rust:scope@lib/scope"
    calls = [
        e
        for e in batch.edges
        if e.kind == EdgeKind.CALLS and e.src == scope + "::run" and e.dst == scope + "::helper"
    ]
    assert [e.provenance.line for e in calls if e.provenance] == [6]


def test_workspace_glob_exclude_inheritance_and_path_alias(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[workspace]\nmembers=["crates/*"]\nexclude=["crates/ignored"]\n'
            'default-members=["crates/app"]\n[workspace.package]\nedition="2024"\nrust-version="1.90"\n',
            "crates/app/Cargo.toml": '[package]\nname="app"\nversion="0.1.0"\nedition.workspace=true\n'
            'rust-version.workspace=true\n[dependencies]\nshared={package="shared",path="../shared"}\n',
            "crates/app/src/main.rs": "fn main() {}\n",
            "crates/shared/Cargo.toml": '[package]\nname="shared"\nversion="0.1.0"\n',
            "crates/shared/src/lib.rs": "pub fn f() {}\n",
            "crates/ignored/Cargo.toml": '[package]\nname="ignored"\nversion="0.1.0"\n',
            "crates/ignored/src/lib.rs": "pub fn ignored() {}\n",
        },
    )
    idx = CargoIndex(tmp_path)
    assert idx.workspace_members == ("app", "shared")
    assert idx.default_members == ("app",)
    assert idx.packages["app"].edition == "2024"
    assert idx.packages["app"].rust_version == "1.90"
    assert idx.packages["app"].path_dependencies["shared"] == (tmp_path / "crates/shared").resolve()
    batch, _ = _extract(tmp_path)
    assert not any(n.id.startswith("rust:ignored@") for n in batch.nodes)


def test_implicit_workspace_proc_macro_member_matches_cargo(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[workspace]\nmembers=["crates/app"]\n',
            "crates/app/Cargo.toml": '[package]\nname="app"\nversion="0.1.0"\n'
            '[dependencies]\nderive={path="../derive"}\n',
            "crates/app/src/lib.rs": "pub fn run() {}\n",
            "crates/derive/Cargo.toml": '[package]\nname="derive"\nversion="0.1.0"\n[lib]\nproc-macro=true\n',
            "crates/derive/src/lib.rs": "",
        },
    )
    index = CargoIndex(tmp_path)
    assert index.workspace_members == ("app", "derive")
    assert [(target.kind, target.name) for target in index.packages["derive"].targets] == [
        ("proc-macro", "derive")
    ]
