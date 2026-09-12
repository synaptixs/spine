"""PKG: the Perl front-end maps Perl source onto the universal facts (10th language;
P1 comprehension, P2 CALLS, P3 routes + typed receivers — perl-support-roadmap.md §3.1-3.3).

tree-sitter-perl is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.perl_extractor import PerlExtractor

pytest.importorskip("tree_sitter_perl", reason="install the 'perl' extra")

CART_PM = """\
use strict;
use warnings;
use Carp qw(croak);
use Shop::Tax;

package Shop::Cart;
use parent -norequire, 'Shop::Base';
with 'Shop::Role::Loggable';

has items => (is => 'rw');
has [qw(a b)];
__PACKAGE__->mk_accessors(qw(c d));

sub new {
    my ($class) = @_;
    return bless {}, $class;
}

sub total {
    my $self = shift;
    return 0;
}

package Shop::Cart::Sub {
    sub nested_sub {
        return 1;
    }
}
"""


def _facts(tmp_path: Path, src: str = CART_PM, name: str = "Cart.pm") -> tuple[FactBatch, str]:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src, encoding="utf-8")
    ex = PerlExtractor()
    module = ex.module_name(f, tmp_path)
    batch = ex.extract(path=f, module=module, rel=name)
    return batch, module


def test_module_name_is_always_the_repo_relative_path(tmp_path: Path) -> None:
    # D2: Module is path-keyed always, unlike a namespace-keyed language.
    _, module = _facts(tmp_path)
    assert module == "Cart.pm"


def test_every_package_is_its_own_type(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.Cart"].kind is NodeKind.TYPE
    assert by_id["perl:Shop.Cart.Sub"].kind is NodeKind.TYPE
    module_node = by_id["perl:Cart.pm"]
    assert module_node.kind is NodeKind.MODULE
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("perl:Cart.pm", "perl:Shop.Cart") in contains
    assert ("perl:Cart.pm", "perl:Shop.Cart.Sub") in contains


def test_subs_are_functions_owned_by_the_package_in_scope(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.Cart.new"].kind is NodeKind.FUNCTION
    assert by_id["perl:Shop.Cart.total"].kind is NodeKind.FUNCTION
    # block-form package: the nested sub belongs to the nested package, not Shop.Cart.
    assert by_id["perl:Shop.Cart.Sub.nested_sub"].kind is NodeKind.FUNCTION
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("perl:Shop.Cart", "perl:Shop.Cart.new") in contains
    assert ("perl:Shop.Cart.Sub", "perl:Shop.Cart.Sub.nested_sub") in contains


def test_fully_qualified_sub_declares_into_that_package_not_the_current_one(tmp_path: Path) -> None:
    """`sub Shop::Elsewhere::baz {}` declares into `Shop::Elsewhere` regardless of the
    lexically-current package (valid Perl — no `package` block needed) — and the id
    stays dotted (D3), never `::`-embedded. Nothing pinned this before: reverting the
    B5 fix in `_handle_sub` left every other test in this file green."""
    batch, _ = _facts(
        tmp_path,
        "package Shop::Qual;\nsub Shop::Elsewhere::baz { 1 }\nsub main::top { 1 }\nsub normal { 1 }\n1;\n",
        name="Qual.pm",
    )
    ids = {n.id for n in batch.nodes}
    assert "perl:Shop.Elsewhere.baz" in ids
    assert "perl:main.top" in ids
    assert "perl:Shop.Qual.normal" in ids
    assert not [i for i in ids if "::" in i]
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("perl:Shop.Elsewhere", "perl:Shop.Elsewhere.baz") in contains
    assert ("perl:Shop.Qual", "perl:Shop.Qual.normal") in contains


def test_implicit_main_keys_subs_on_the_file(tmp_path: Path) -> None:
    src = "sub usage {\n    return 1;\n}\n"
    batch, module = _facts(tmp_path, src=src, name="bin/report.pl")
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:bin/report.pl.usage" in by_id
    assert by_id["perl:bin/report.pl.usage"].kind is NodeKind.FUNCTION
    # No synthetic "Type main" — this is a script with no package statement at all.
    assert not any(n.kind is NodeKind.TYPE for n in batch.nodes)


def test_fields_from_has_mk_accessors(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    for name in ("items", "a", "b", "c", "d"):
        node = by_id[f"perl:Shop.Cart.{name}"]
        assert node.kind is NodeKind.FIELD


def test_field_5_38_class_syntax(tmp_path: Path) -> None:
    src = (
        "use v5.38;\n"
        "use experimental 'class';\n"
        "class Shop::Modern :isa(Shop::Base) {\n"
        "    field $x :param;\n"
        "    field $y :param = 0;\n"
        "    method greet {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    )
    batch, _ = _facts(tmp_path, src=src, name="Modern.pm")
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.Modern"].kind is NodeKind.TYPE
    assert by_id["perl:Shop.Modern.x"].kind is NodeKind.FIELD
    assert by_id["perl:Shop.Modern.y"].kind is NodeKind.FIELD
    assert by_id["perl:Shop.Modern.greet"].kind is NodeKind.FUNCTION
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.Modern", "perl:Shop.Base") in implements


def test_pragmas_are_not_imports(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    imports = {e.dst for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert "perl:strict" not in imports
    assert "perl:warnings" not in imports


def test_generic_use_is_imports_to_a_type_placeholder(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("perl:Cart.pm", "perl:Carp") in imports
    assert ("perl:Cart.pm", "perl:Shop.Tax") in imports
    by_id = {n.id: n for n in batch.nodes}
    # D2: a `use` target names a PACKAGE, so its placeholder is a Type, not a Module —
    # matching the id shape a real `package Shop::Tax` declaration would produce.
    assert by_id["perl:Shop.Tax"].kind is NodeKind.TYPE


def test_use_parent_is_implements_not_imports(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    imports = {e.dst for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert "perl:Shop.Base" not in imports
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.Cart", "perl:Shop.Base") in implements


def test_with_role_is_implements(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.Cart", "perl:Shop.Role.Loggable") in implements


def test_with_role_alone_does_not_feed_super(tmp_path: Path) -> None:
    """Real bug found in review: `with`/`extends` both used to append to the same
    `bases` list `SUPER::` reads. A role never participates in `SUPER::` dispatch in real
    Perl (roles flatten into the consumer at composition time) — `with 'SomeRole'` alone,
    no real parent, must leave `SUPER::helper()` unresolved even though `SomeRole`
    declares `helper` and gets a real `IMPLEMENTS` edge."""
    files = {
        "Role.pm": "package Shop::Role::Loggable;\nsub helper { return 1; }\n",
        "Cart.pm": (
            "package Shop::Cart;\nwith 'Shop::Role::Loggable';\n"
            "sub total { my $self = shift; $self->SUPER::helper(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.Cart", "perl:Shop.Role.Loggable") in implements  # the role edge is real
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)  # but SUPER:: has no base


def test_extends_and_with_super_resolves_the_real_parent_not_the_role(tmp_path: Path) -> None:
    """A class consuming a role *and* extending a real parent — `SUPER::helper()` must
    resolve through the real parent, never through the role, regardless of which
    statement (`with` or `extends`) appears first in source order."""
    files = {
        "Base.pm": "package Shop::Base;\nsub helper { return 1; }\n",
        "Role.pm": "package Shop::Role::Loggable;\nsub helper { return 2; }\n",
        "Cart.pm": (
            "package Shop::Cart;\n"
            "with 'Shop::Role::Loggable';\n"
            "extends 'Shop::Base';\n"
            "sub total { my $self = shift; $self->SUPER::helper(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Base.helper") in calls
    assert ("perl:Shop.Cart.total", "perl:Shop.Role.Loggable.helper") not in calls


def test_super_resolves_the_parent_that_declares_the_method(tmp_path: Path) -> None:
    """Real bug found in review: `owner.bases[0]` always won, even when a *later*
    parent in `use parent qw(A B)` is the one that actually declares the method.
    Perl's default MRO is depth-first, left to right across `@ISA` — the first parent
    that *declares* the method wins, not simply the first parent."""
    files = {
        "A.pm": "package Shop::A;\nsub ameth { return 1; }\n",
        "B.pm": "package Shop::B;\nsub bmeth { return 2; }\n",
        "C.pm": (
            "package Shop::C;\nuse parent qw(Shop::A Shop::B);\n"
            "sub go { my $s = shift; return $s->SUPER::bmeth(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.C.go", "perl:Shop.B.bmeth") in calls
    assert ("perl:Shop.C.go", "perl:Shop.A") not in calls


def test_super_with_several_parents_and_none_declaring_is_skipped(tmp_path: Path) -> None:
    """Two or more parents and neither declares the method: the method is inherited
    further up one of the chains, and which chain is not readable from this repo —
    skip rather than name a parent the call may never reach."""
    files = {
        "A.pm": "package Shop::A;\nsub ameth { return 1; }\n",
        "B.pm": "package Shop::B;\nsub bmeth { return 2; }\n",
        "C.pm": (
            "package Shop::C;\nuse parent qw(Shop::A Shop::B);\n"
            "sub go { my $s = shift; return $s->SUPER::missing(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    assert not any(e.kind is EdgeKind.CALLS and e.src == "perl:Shop.C.go" for e in batch.edges)


def test_isa_spellings_literal_only(tmp_path: Path) -> None:
    src = (
        "package Shop::A;\n"
        "our @ISA = ('Shop::Base1');\n"
        "package Shop::B;\n"
        "push @ISA, 'Shop::Base2';\n"
        "package Shop::C;\n"
        "our @ISA = (compute_base());\n"  # computed — must yield nothing
        "package Shop::D;\n"
        "use Mojo::Base 'Shop::Base4';\n"
    )
    batch, _ = _facts(tmp_path, src=src, name="Isa.pm")
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("perl:Shop.A", "perl:Shop.Base1") in implements
    assert ("perl:Shop.B", "perl:Shop.Base2") in implements
    assert ("perl:Shop.D", "perl:Shop.Base4") in implements
    assert not any(src_id == "perl:Shop.C" for src_id, _ in implements)


def test_require_literal_path_and_bareword(tmp_path: Path) -> None:
    src = 'require "lib/common.pl";\nrequire Shop::Common;\n'
    batch, _ = _facts(tmp_path, src=src, name="bin/report.pl")
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("perl:bin/report.pl", "perl:lib/common.pl") in imports
    assert ("perl:bin/report.pl", "perl:Shop.Common") in imports
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:lib/common.pl"].kind is NodeKind.MODULE
    assert by_id["perl:Shop.Common"].kind is NodeKind.TYPE


def test_computed_require_yields_nothing(tmp_path: Path) -> None:
    src = 'my $x = "common"; require "lib/$x.pl";\n'
    batch, _ = _facts(tmp_path, src=src, name="bin/report.pl")
    assert not any(e.kind is EdgeKind.IMPORTS for e in batch.edges)


def test_end_to_end_via_repo_extractor_resolves_require_by_path_suffix(tmp_path: Path) -> None:
    """Real bug found in review: this test used to place the requiring script under
    `bin/`, which `extractor.DEFAULT_IGNORE_DIRS` treats as .NET build output and skips
    for every language — the walker never extracted `bin/report.pl` at all, so the
    `require` edge this test claims to exercise was never even created. The assertion
    still passed, for an unrelated reason: `lib/common.pl` is grounded simply because it's
    a real, directly-walked file, regardless of whether anything requires it. `scripts/`
    isn't ignored (the `legacy_main` corpus fixture moved here for the same reason), and
    the test now checks the actual IMPORTS edge exists, not just that the target happens
    to be grounded some other way."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "common.pl").write_text("sub helper { 1 }\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "report.pl").write_text('require "lib/common.pl";\n', encoding="utf-8")

    batch = RepoCodeExtractor([PerlExtractor()]).extract(tmp_path)
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("perl:scripts/report.pl", "perl:lib/common.pl") in imports
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:lib/common.pl"].grounded is True


# --- P2: CALLS (§3.2) -------------------------------------------------------


def _repo_facts(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, src in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return RepoCodeExtractor([PerlExtractor()]).extract(tmp_path)


def test_calls_sibling_method_via_self_class_package_shift(tmp_path: Path) -> None:
    src = """\
package Shop::Cart;

sub helper { return 1; }

sub a { my $self = shift; $self->helper(); }
sub b { my ($class) = @_; $class->helper(); }
sub c { __PACKAGE__->helper(); }
sub d { shift->helper(); }
sub e { $self->nonexistent(); }
"""
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    for caller in "abcd":
        assert ("perl:Shop.Cart." + caller, "perl:Shop.Cart.helper") in calls
    # `e` calls a name Shop::Cart never declares — never fabricated.
    assert not any(src == "perl:Shop.Cart.e" for src, _ in calls)


def test_calls_has_field_via_self(tmp_path: Path) -> None:
    src = """\
package Shop::Cart;
has items => (is => 'rw');

sub total { my $self = shift; $self->items(); }
"""
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Cart.items") in calls


def test_calls_super(tmp_path: Path) -> None:
    files = {
        "Base.pm": "package Shop::Base;\nsub helper { return 1; }\n",
        "Cart.pm": (
            "package Shop::Cart;\nuse parent -norequire, 'Shop::Base';\n"
            "sub total { my $self = shift; $self->SUPER::helper(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Base.helper") in calls


def test_calls_super_skipped_when_no_base_resolved(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub total { my $self = shift; $self->SUPER::helper(); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_super_falls_back_to_type_when_base_doesnt_declare_it(tmp_path: Path) -> None:
    """A `Mojo::Base`-style base: declared in this repo, but `new` isn't an explicit `sub` —
    it's provided implicitly further up the chain. Real bug, found live against Mojolicious's
    own repo (`validate-frontend.py`): ``perl:Mojo.Log.new -CALLS-> perl:Mojo.EventEmitter.new``
    was emitted with no ``Mojo::EventEmitter::new`` node ever declared — a fabricated method id
    with no backstop. Row 3's own policy (call the type, not a guessed method) now applies here
    too."""
    files = {
        "Base.pm": "package Shop::Base;\nuse Mojo::Base -base;\n",
        "Cart.pm": (
            "package Shop::Cart;\nuse parent -norequire, 'Shop::Base';\n"
            "sub new { my $self = shift; $self->SUPER::new(@_); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.new", "perl:Shop.Base") in calls
    assert ("perl:Shop.Cart.new", "perl:Shop.Base.new") not in calls
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.Base"].external is False  # declared locally, just no `new` sub


def test_calls_super_falls_back_to_external_type_when_base_undeclared(tmp_path: Path) -> None:
    src = (
        "package Shop::Cart;\nuse parent -norequire, 'Some::External::Base';\n"
        "sub new { my $self = shift; $self->SUPER::new(@_); }\n"
    )
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.new", "perl:Some.External.Base") in calls
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Some.External.Base"].external is True


def test_calls_qualified_receiver_new(tmp_path: Path) -> None:
    files = {
        "Log.pm": "package Shop::Log;\nsub new { my ($c) = @_; return bless {}, $c; }\n",
        "Cart.pm": "package Shop::Cart;\nsub total { Shop::Log->new; }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Log.new") in calls


def test_calls_qualified_receiver_falls_back_to_type_when_undeclared(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub total { Shop::External->new; }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.External") in calls
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:Shop.External"].external is True


def test_calls_qualified_function(tmp_path: Path) -> None:
    files = {
        "Util.pm": "package Shop::Util;\nsub fmt { return 1; }\n",
        "Cart.pm": "package Shop::Cart;\nsub total { Shop::Util::fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Util.fmt") in calls


def test_calls_bare_same_file_sub(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub helper { return 1; }\nsub total { helper(); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Cart.helper") in calls


def test_calls_bare_explicit_use_qw_import(tmp_path: Path) -> None:
    files = {
        "Util.pm": "package Shop::Util;\nsub fmt { return 1; }\n",
        "Cart.pm": "package Shop::Cart;\nuse Shop::Util qw(fmt);\nsub total { fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Util.fmt") in calls


def test_use_qw_import_is_scoped_to_the_declaring_package_not_the_file(tmp_path: Path) -> None:
    """Real Perl bug found in review: `use X qw(f)` runs `X->import(qw(f))` at compile
    time, and `Exporter`-style `import` installs `f` into whichever package `caller()`
    names *at that line* — package-scoped, not file-scoped. A later `package Bar;` in the
    same file does not inherit `package Foo;`'s `use Shop::Util qw(fmt);`, so `fmt()`
    called from `Bar` must stay unresolved, never fabricated as `Shop::Util::fmt`."""
    files = {
        "Util.pm": "package Shop::Util;\nsub fmt { return 1; }\n",
        "Cart.pm": (
            "package Shop::Foo;\nuse Shop::Util qw(fmt);\nsub a { fmt(); }\n\n"
            "package Shop::Bar;\nsub b { fmt(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Foo.a", "perl:Shop.Util.fmt") in calls  # Foo's own import resolves
    assert not any(src == "perl:Shop.Bar.b" for src, _dst in calls)  # Bar never imported it


def test_use_qw_import_is_visible_when_the_same_package_reopens(tmp_path: Path) -> None:
    """The other half of the same rule: Perl's symbol table is per-package, not
    per-block, so `package Foo;` reopened later in the same file still sees what it
    imported the first time — package-scoping must not become "one shot per block"."""
    files = {
        "Util.pm": "package Shop::Util;\nsub fmt { return 1; }\n",
        "Cart.pm": (
            "package Shop::Foo;\nuse Shop::Util qw(fmt);\n\n"
            "package Shop::Bar;\nsub noop { return 1; }\n\n"
            "package Shop::Foo;\nsub a { fmt(); }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Foo.a", "perl:Shop.Util.fmt") in calls


def test_calls_bare_d10_default_export(tmp_path: Path) -> None:
    files = {
        "Util.pm": (
            "package Shop::Util;\nuse Exporter;\nour @ISA = qw(Exporter);\n"
            "our @EXPORT = qw(fmt);\nsub fmt { return 1; }\n"
        ),
        "Cart.pm": "package Shop::Cart;\nuse Shop::Util;\nsub total { fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Util.fmt") in calls


def test_calls_bare_d10_skips_export_without_exporter_base(tmp_path: Path) -> None:
    """Real bug found in review: `@EXPORT` is inert unless something reads it, and only
    `Exporter`'s own `import()` does. A package that sets `@EXPORT` without inheriting
    `Exporter` never actually makes the sub callable unqualified — `use X;` silently does
    nothing, and `fmt()` would die with "Undefined subroutine" at runtime. D10 must not
    resolve this, even though the shape otherwise looks identical to the case above."""
    files = {
        "Util.pm": "package Shop::Util;\nour @EXPORT = qw(fmt);\nsub fmt { return 1; }\n",
        "Cart.pm": "package Shop::Cart;\nuse Shop::Util;\nsub total { fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_bare_d10_skips_third_party_default_export(tmp_path: Path) -> None:
    """`use Carp;` (bare) then `croak()` — Carp is never declared in-repo, so D10 must
    refuse, exactly the exporter_default corpus case's control."""
    src = "package Shop::Cart;\nuse Carp;\nsub total { croak('bad'); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_bare_does_not_search_isa(tmp_path: Path) -> None:
    """Real Perl bug found in review: a bare `f()` never dispatches through `@ISA` — that
    is method-dispatch-only (`$self->m()`/`Class->m()`). `Shop::Cart` inheriting from
    `Shop::Base` does not give a bare `helper()` inside `Shop::Cart` access to
    `Shop::Base::helper`; Perl raises "Undefined subroutine" at runtime. D10 originally
    resolved this (wrongly) as its second sub-step — removed, not kept as a documented
    gap, since nothing about it was ever true of the language."""
    files = {
        "Base.pm": "package Shop::Base;\nsub helper { return 1; }\n",
        "Cart.pm": ("package Shop::Cart;\nuse parent -norequire, 'Shop::Base';\nsub total { helper(); }\n"),
    }
    batch = _repo_facts(tmp_path, files)
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_bare_unresolved_is_skipped(tmp_path: Path) -> None:
    src = "package Shop::Cart;\nsub total { nonexistent_sub(); }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_never_fabricates_dynamic_or_string_eval_shapes(tmp_path: Path) -> None:
    """Row 6: `&f`, `$self->$m()`, `$obj->can('m')->()`, `goto &f`, string `eval`, and
    `AUTOLOAD` must never produce a CALLS edge — excluded by CST shape, not a blocklist."""
    src = """\
package Shop::Cart;

sub helper { return 1; }

sub risky {
    my $self = shift;
    my $m = 'helper';
    &helper;
    $self->$m();
    eval "helper()";
}
"""
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)


def test_calls_qualified_ampersand_call_never_emits_a_sigil_in_the_id(tmp_path: Path) -> None:
    """Real bug found in review: `&Shop::Util::fmt()` (the old calling convention, fully
    qualified) parses with the same `&`-prefixed `function` node as bare `&f` — before the
    fix it slipped past row 4's qualified-call branch (only checks for `::`, not the
    sigil) and emitted `perl:&Shop.Util.fmt`, a literal `&` embedded in the id. Row 6
    excludes every ampersand-form call as a class, qualified or not."""
    files = {
        "Util.pm": "package Shop::Util;\nsub fmt { return 1; }\n",
        "Cart.pm": "package Shop::Cart;\nsub total { &Shop::Util::fmt(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    assert not any(e.kind is EdgeKind.CALLS for e in batch.edges)
    assert not any("&" in n.id for n in batch.nodes)


def test_instance_calls_typed_receiver_boundary(tmp_path: Path) -> None:
    """The instance_calls corpus control (§3.2 row 7, P3): a literal-constructed receiver
    resolves; an untyped parameter receiver — permanently — does not."""
    files = {
        "Log.pm": (
            "package Shop::Log;\nsub new { my ($c) = @_; return bless {}, $c; }\nsub write { return 1; }\n"
        ),
        "Cart.pm": (
            "package Shop::Cart;\n"
            "sub a { my $log = Shop::Log->new; $log->write; }\n"
            "sub b { my ($self, $thing) = @_; $thing->write; }\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.a", "perl:Shop.Log.write") in calls
    assert not any(src == "perl:Shop.Cart.b" for src, _ in calls)


# --- P3: routes (§3.3) ------------------------------------------------------


def test_mojo_full_app_route_string_shorthand(tmp_path: Path) -> None:
    src = (
        "package MyApp;\nuse Mojo::Base 'Mojolicious';\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->get('/orders')->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["perl:endpoint:GET /orders"].kind is NodeKind.ENDPOINT
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /orders", "perl:MyApp.Controller.Orders.index") in exposes


def test_mojo_full_app_route_hash_form(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->get('/orders')->to(controller => 'orders', action => 'index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /orders", "perl:MyApp.Controller.Orders.index") in exposes


def test_mojo_full_app_closure_handler_yields_endpoint_no_exposes(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->get('/orders')->to(sub { return 1; });\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:GET /orders" in by_id
    assert not any(e.kind is EdgeKind.EXPOSES for e in batch.edges)


def test_mojo_full_app_route_from_a_helper_method_yields_endpoint_no_exposes(tmp_path: Path) -> None:
    """Real bug found in review: a route registered from a helper/plugin method (not
    literally `startup`) used to resolve the controller against *that method's own*
    package — `MyApp::Routes::install` produced `perl:MyApp.Routes.Controller.Orders.index`,
    a placeholder that never grounds, since the real target (if it exists) lives under the
    actual app class `MyApp`, not the helper. The `Endpoint` is still real and still
    emitted; only the guessed-namespace `EXPOSES` is withheld."""
    src = (
        "package MyApp::Routes;\n"
        "sub install {\n    my $r = shift;\n"
        "    $r->get('/orders')->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"Routes.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:GET /orders" in by_id
    assert not any(e.kind is EdgeKind.EXPOSES for e in batch.edges)
    assert not any("MyApp.Routes.Controller" in n.id for n in batch.nodes)


def test_mojo_full_app_explicit_namespace_overrides_helper_method(tmp_path: Path) -> None:
    """The other half of the same fix: an explicit `namespace => 'X'` in the hash form is
    the developer's own literal statement of which app it belongs to — honoured even from
    a non-`startup` helper method, since it needs no guessing at all."""
    src = (
        "package MyApp::Routes;\n"
        "sub install {\n    my $r = shift;\n"
        "    $r->get('/orders')->to(namespace => 'MyApp', controller => 'orders', action => 'index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"Routes.pm": src})
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /orders", "perl:MyApp.Controller.Orders.index") in exposes


def test_mojo_full_app_hyphenated_controller_dots_not_double_colons(tmp_path: Path) -> None:
    """Real bug found in review: `_camelize('foo-bar')` produced `Foo::Bar` (Mojolicious's
    own real class-name separator), spliced straight into an otherwise fully-dotted id —
    `perl:MyApp.Controller.Foo::Bar.show`, a literal `::` next to dots (D3 violated), which
    never matches the real declaration's own dotted id (`perl:MyApp.Controller.Foo.Bar.show`
    — `_to_dotted` converts every `::`). Must dedup onto the real node when one exists."""
    src = (
        "package MyApp;\nuse Mojo::Base 'Mojolicious';\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->get('/foo-bar')->to('foo-bar#show');\n}\n"
    )
    other = "package MyApp::Controller::Foo::Bar;\nsub show { return 1; }\n"
    batch = _repo_facts(tmp_path, {"App.pm": src, "FooBar.pm": other})
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /foo-bar", "perl:MyApp.Controller.Foo.Bar.show") in exposes
    assert not any("::" in n.id for n in batch.nodes)


def test_mojo_full_app_any_verb_yields_nothing(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    $r->any('/orders')->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    assert not any(n.kind is NodeKind.ENDPOINT for n in batch.nodes)


def test_mojo_full_app_computed_path_yields_nothing(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    my $id = 1;\n    $r->get(\"/orders/$id\")->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    assert not any(n.kind is NodeKind.ENDPOINT for n in batch.nodes)


def test_mojo_under_group_composes_prefix(tmp_path: Path) -> None:
    src = (
        "package MyApp;\n"
        "sub startup {\n    my $self = shift;\n    my $r = $self->routes;\n"
        "    my $api = $r->under('/api');\n"
        "    $api->get('/orders')->to('orders#index');\n}\n"
    )
    batch = _repo_facts(tmp_path, {"App.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:GET /api/orders" in by_id


def test_mojo_lite_closure_route(tmp_path: Path) -> None:
    src = "get '/x' => sub {\n    return 1;\n};\n"
    batch = _repo_facts(tmp_path, {"app.pl": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:GET /x" in by_id
    assert not any(e.kind is EdgeKind.EXPOSES for e in batch.edges)


def test_mojo_lite_named_handler_route(tmp_path: Path) -> None:
    src = "get '/y' => \\&handler;\n\nsub handler {\n    return 1;\n}\n"
    batch = _repo_facts(tmp_path, {"app.pl": src})
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("perl:endpoint:GET /y", "perl:app.pl.handler") in exposes


def test_dancer2_del_verb_emits_delete_not_del(tmp_path: Path) -> None:
    """Real bug found in review: Dancer2 spells DELETE as the bareword `del` (`delete` is
    a Perl builtin), and the endpoint's own name/id used to be built from `verb.upper()`
    with no normalization — `perl:endpoint:DEL /x`, unjoinable against any cross-language
    consumer that made a real HTTP `DELETE` request. The verb is still recognized as `del`
    to trigger the route (Mojolicious spells the same HTTP method `delete`), but the
    emitted `Endpoint` always reads the real HTTP method name."""
    src = "del '/x' => sub {\n    return 1;\n};\n"
    batch = _repo_facts(tmp_path, {"app.pl": src})
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:endpoint:DELETE /x" in by_id
    assert "perl:endpoint:DEL /x" not in by_id


# --- P4: DBIx::Class entities (§3.4) ----------------------------------------


def test_dbic_table_marker_and_columns(tmp_path: Path) -> None:
    src = (
        "package App::Schema::Result::Order;\n"
        "use base 'DBIx::Class::Core';\n"
        "__PACKAGE__->table('orders');\n"
        "__PACKAGE__->add_columns(qw(id total));\n"
    )
    batch = _repo_facts(tmp_path, {"Order.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    eid = "perl:entity:App.Schema.Result.Order"
    assert by_id[eid].kind is NodeKind.ENTITY
    assert by_id[eid].external is False
    assert by_id[f"{eid}.id"].kind is NodeKind.FIELD
    assert by_id[f"{eid}.total"].kind is NodeKind.FIELD
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert (eid, f"{eid}.id") in contains
    assert (eid, f"{eid}.total") in contains


def test_dbic_columns_hash_form_reads_only_keys(tmp_path: Path) -> None:
    src = (
        "package App::Schema::Result::Order;\n"
        "__PACKAGE__->table('orders');\n"
        "__PACKAGE__->add_columns(id => { data_type => 'int' }, total => { data_type => 'text' });\n"
    )
    batch = _repo_facts(tmp_path, {"Order.pm": src})
    by_id = {n.id: n for n in batch.nodes}
    eid = "perl:entity:App.Schema.Result.Order"
    assert by_id[f"{eid}.id"].kind is NodeKind.FIELD
    assert by_id[f"{eid}.total"].kind is NodeKind.FIELD


def test_dbic_relations_both_directions_and_external_target(tmp_path: Path) -> None:
    files = {
        "Order.pm": (
            "package App::Schema::Result::Order;\n"
            "__PACKAGE__->table('orders');\n"
            "__PACKAGE__->belongs_to(customer => 'App::Schema::Result::Customer', 'customer_id');\n"
            "__PACKAGE__->has_many(items => 'App::Schema::Result::OrderItem', 'order_id');\n"
        ),
        "OrderItem.pm": (
            "package App::Schema::Result::OrderItem;\n"
            "__PACKAGE__->table('order_items');\n"
            "__PACKAGE__->belongs_to(order => 'App::Schema::Result::Order', 'order_id');\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    order_eid = "perl:entity:App.Schema.Result.Order"
    item_eid = "perl:entity:App.Schema.Result.OrderItem"
    customer_eid = "perl:entity:App.Schema.Result.Customer"
    assert (order_eid, item_eid) in refs
    assert (item_eid, order_eid) in refs
    assert (order_eid, customer_eid) in refs
    by_id = {n.id: n for n in batch.nodes}
    assert by_id[customer_eid].external is True
    assert by_id[order_eid].external is False


def test_dbic_has_one_relation_is_references(tmp_path: Path) -> None:
    """`has_one` — a required 1:1 relation, the fourth DBIx::Class relation declarator
    alongside belongs_to/has_many/might_have. Missing from the recognized set originally
    (found in review); `many_to_many` stays deliberately excluded (see perl_orm.py)."""
    files = {
        "Order.pm": (
            "package App::Schema::Result::Order;\n"
            "__PACKAGE__->table('orders');\n"
            "__PACKAGE__->has_one(receipt => 'App::Schema::Result::Receipt', 'order_id');\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    assert ("perl:entity:App.Schema.Result.Order", "perl:entity:App.Schema.Result.Receipt") in refs


def test_dbic_relation_with_quoted_name_resolves_the_target_not_the_name(tmp_path: Path) -> None:
    """`belongs_to('customer', 'App::Schema::Result::Customer', 'customer_id')` — a quoted
    relation name, valid DBIx::Class and not just the bareword `customer => ...` spelling.
    Real bug found in review: taking "the first string literal in the arg list" picks the
    *name* here, not the target, fabricating a `REFERENCES` edge to a node named `customer`
    that the source never declares. The target is always DBIx::Class's own positional
    argument 1 regardless of how argument 0 (the name) is spelled."""
    files = {
        "Order.pm": (
            "package App::Schema::Result::Order;\n"
            "__PACKAGE__->table('orders');\n"
            "__PACKAGE__->belongs_to('customer', 'App::Schema::Result::Customer', 'customer_id');\n"
        ),
    }
    batch = _repo_facts(tmp_path, files)
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    order_eid = "perl:entity:App.Schema.Result.Order"
    customer_eid = "perl:entity:App.Schema.Result.Customer"
    assert (order_eid, customer_eid) in refs
    by_id = {n.id: n for n in batch.nodes}
    assert "perl:entity:customer" not in by_id


def test_dbic_no_table_marker_is_not_an_entity(tmp_path: Path) -> None:
    """A plain package with a `belongs_to`-named method of its own isn't a DBIC Result
    class without the `table(...)` marker — never guessed from shape alone."""
    src = "package Shop::Cart;\nsub belongs_to { return 1; }\n"
    batch = _repo_facts(tmp_path, {"Cart.pm": src})
    assert not any(n.kind is NodeKind.ENTITY for n in batch.nodes)


# ---- one extractor instance reused across repos (a real usage, not a hypothetical) -------
#
# `RepoCodeExtractor.__init__` builds each front-end once; a caller looping over several
# repos with one `RepoCodeExtractor` (`load_or_extract_repos` in persistence.py, the engine
# behind `pkg extract --repos` and multi-repo `investigate`) reuses that same `PerlExtractor`
# instance across every repo's `.extract()` call. Confirmed real, found in review: `finalize()`
# never cleared `_types`/`_subs`, so a later repo's graph carried the previous repo's facts.


def test_types_and_subs_do_not_leak_across_repos_sharing_one_extractor(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    (repo_a / "Base.pm").parent.mkdir(parents=True, exist_ok=True)
    (repo_a / "Base.pm").write_text(
        "package Shop::Base;\nsub helper { return 1; }\nsub caller_sub { helper(); }\n",
        encoding="utf-8",
    )
    (repo_b / "Other.pm").parent.mkdir(parents=True, exist_ok=True)
    (repo_b / "Other.pm").write_text("package Other::Thing;\nsub noop { return 1; }\n", encoding="utf-8")

    shared = RepoCodeExtractor([PerlExtractor()])
    shared.extract(repo_a)  # populates the shared PerlExtractor's _types/_subs
    batch_b = shared.extract(repo_b)  # must not carry repo A's facts into repo B's graph

    ids = {n.id for n in batch_b.nodes}
    assert not any(i.startswith("perl:Shop.Base") for i in ids), "repo A's Type leaked into repo B"
    calls = {(e.src, e.dst) for e in batch_b.edges if e.kind is EdgeKind.CALLS}
    assert not any("Shop.Base" in src or "Shop.Base" in dst for src, dst in calls), (
        "repo A's CALLS edge leaked into repo B"
    )
    assert any(i == "perl:Other.Thing" for i in ids)  # repo B's own facts are still there


def test_finalize_still_resolves_within_the_same_repo_after_the_reset(tmp_path: Path) -> None:
    """The reset in `finalize()` must not throw away facts within the same repo — only
    facts belonging to a *different* one, extracted through a later `.extract()` call.
    Same-package bare calls (row 5) must still resolve exactly as before the fix."""
    files = {
        "Cart.pm": "package Shop::Cart;\nsub helper { return 1; }\nsub total { helper(); }\n",
    }
    batch = _repo_facts(tmp_path, files)
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("perl:Shop.Cart.total", "perl:Shop.Cart.helper") in calls
