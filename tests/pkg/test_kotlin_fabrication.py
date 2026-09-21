"""PKG: the refusals that keep invented Kotlin facts out of the graph.

Every rule here was stated somewhere — in ``kotlin-support-roadmap.md`` §3.2, in a
docstring, in a corpus case's prose — and none of them was tested. They were all found
broken in maintainer review (§11), and the shape of the failure was the same every time:
a reader that could not answer a question returned a *plausible* answer instead of none,
and the plausible answer was minted as an ``external`` placeholder node, so the edge never
dangled and ``pkg verify`` reported the graph clean.

That is why these tests assert on **what is absent**. A fabrication is not a wrong edge
beside a right one; it is a wrong edge where the honest answer is silence, and the only
way to pin it is to name the id that must not appear.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch
from orchestrator.pkg.kotlin_extractor import _SCOPE_FUNCTIONS

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")


def _facts(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for name, src in files.items():
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _calls(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def _targets(batch: FactBatch) -> set[str]:
    return {dst for _, dst in _calls(batch)}


def _ids(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes}


# ---- §11 finding 1: the same-package type guess must not reach CALLS -----------


def test_a_default_import_is_not_a_class_in_the_callers_package(tmp_path: Path) -> None:
    """`s.uppercase()` is not a call to `<this package>.String.uppercase`.

    Kotlin's default imports (`String`, `System`, `Math`) are in scope in every file
    without an import line, so the same-package fallback fired on essentially all of them.
    """
    batch = _facts(
        tmp_path,
        {
            "Screen.kt": """\
package app.ui

class Screen {
    fun render(s: String) = s.uppercase()
    fun stamp() = System.currentTimeMillis()
}
"""
        },
    )
    assert "java:app.ui.String.uppercase" not in _ids(batch)
    assert "java:app.ui.System.currentTimeMillis" not in _ids(batch)
    assert not _targets(batch), "nothing here resolves; a placeholder would hide that"


def test_a_wildcard_import_finds_the_real_declaration_instead_of_guessing(tmp_path: Path) -> None:
    """The other half: refusing the guess must not cost the true edge.

    `import app.data.*` says where `TopicDao` may come from, so the call resolves to the
    class that actually declares it — which is what the per-file guess replaced with a
    same-named class under the *caller's* package.
    """
    batch = _facts(
        tmp_path,
        {
            "TopicDao.kt": "package app.data\n\nclass TopicDao {\n    fun getTopics() {}\n}\n",
            "Screen.kt": """\
package app.ui

import app.data.*

class Screen(private val dao: TopicDao) {
    fun load() = dao.getTopics()
}
""",
        },
    )
    assert ("java:app.ui.Screen.load", "java:app.data.TopicDao.getTopics") in _calls(batch)
    assert "java:app.ui.TopicDao.getTopics" not in _ids(batch)


def test_a_library_call_through_an_explicit_import_keeps_its_placeholder(tmp_path: Path) -> None:
    """The line the rule must not cross.

    A receiver type read from an `import` is a real, fully-qualified name the file itself
    wrote, so the call into it lands with an external placeholder. Dropping these would
    cost 387 measured edges on the validation app and fix nothing.
    """
    batch = _facts(
        tmp_path,
        {
            "Screen.kt": """\
package app.ui

import androidx.compose.ui.Modifier

class Screen {
    fun pad(m: Modifier) = m.padding()
}
"""
        },
    )
    assert ("java:app.ui.Screen.pad", "java:androidx.compose.ui.Modifier.padding") in _calls(batch)


# ---- §11 finding 2: a declared type's members are known, so a non-member is a lie ----


@pytest.mark.parametrize("scope_fn", ["let", "apply", "run", "also"])
def test_a_scope_function_is_not_a_member_of_its_receiver(tmp_path: Path, scope_fn: str) -> None:
    """§3.2 lists these under "never", and all four were emitted.

    `topic.let { }` resolved to `Topic.let`, so `blast_radius` on any type named every
    file that had ever written `x.let { }`.
    """
    batch = _facts(
        tmp_path,
        {
            "Topic.kt": "package app.data\n\nclass Topic(val id: String)\n",
            "Use.kt": f"""\
package app.data

class Use {{
    fun go(topic: Topic) {{
        topic.{scope_fn} {{ }}
    }}
}}
""",
        },
    )
    assert f"java:app.data.Topic.{scope_fn}" not in _ids(batch)


# ---- §11 finding 11: a binding the oracle cannot see is one it cannot police ----


def test_a_for_loop_variable_shadows_a_bare_call(tmp_path: Path) -> None:
    """D9. `for (helper in fns) { helper() }` invokes the loop variable, not the member."""
    batch = _facts(
        tmp_path,
        {
            "Runner.kt": """\
package app

class Runner {
    fun go(fns: List<() -> Unit>) {
        for (helper in fns) { helper() }
    }
    fun helper() {}
}
"""
        },
    )
    assert ("java:app.Runner.go", "java:app.Runner.helper") not in _calls(batch)


def test_a_destructuring_for_loop_variable_shadows_a_bare_call(tmp_path: Path) -> None:
    """#392. `for ((key, value) in m) { key() }` invokes the destructured local, not the

    member. The grammar hangs a `multi_variable_declaration` off the `for_statement`
    instead of a plain `variable_declaration`, which is the same shape `val (a, b) = pair`
    already handles for `property_declaration` — the `for` branch just didn't call it.
    """
    batch = _facts(
        tmp_path,
        {
            "Screen.kt": """\
package app

class Screen {
    fun key(): String = "k"
    fun show(m: Map<String, String>) {
        for ((key, value) in m) { key() }
    }
}
"""
        },
    )
    assert ("java:app.Screen.show", "java:app.Screen.key") not in _calls(batch)


def test_a_catch_parameter_shadows_a_bare_call(tmp_path: Path) -> None:
    """The same rule for `catch (report: Throwable) { report() }`."""
    batch = _facts(
        tmp_path,
        {
            "Runner.kt": """\
package app

class Runner {
    fun go() {
        try { risky() } catch (report: Throwable) { report() }
    }
    fun report() {}
    fun risky() {}
}
"""
        },
    )
    calls = _calls(batch)
    assert ("java:app.Runner.go", "java:app.Runner.report") not in calls
    assert ("java:app.Runner.go", "java:app.Runner.risky") in calls, "the real call still lands"


# ---- §11 finding 20: an imported extension belongs to no receiver type ----------


def test_an_imported_extension_resolves_to_the_free_function(tmp_path: Path) -> None:
    """D4 across files. `ctx.extensions` only ever held *this file's*, so the call fell
    through to the receiver type and lost a grounded target for an invented one."""
    batch = _facts(
        tmp_path,
        {
            "Topic.kt": "package app.data\n\nclass Topic(val id: String)\n",
            "Slug.kt": 'package app.util\n\nimport app.data.Topic\n\nfun Topic.slugify(): String = ""\n',
            "Use.kt": """\
package app.ui

import app.data.Topic
import app.util.slugify

class Use {
    fun go(t: Topic) = t.slugify()
}
""",
        },
    )
    assert ("java:app.ui.Use.go", "java:app.util.slugify") in _calls(batch)
    assert "java:app.data.Topic.slugify" not in _ids(batch)


def test_a_member_still_beats_an_imported_extension(tmp_path: Path) -> None:
    """Kotlin's own resolution order, so the candidate order has to match it."""
    batch = _facts(
        tmp_path,
        {
            "Topic.kt": 'package app.data\n\nclass Topic {\n    fun slugify(): String = ""\n}\n',
            "Slug.kt": 'package app.util\n\nimport app.data.Topic\n\nfun Topic.slugify(): String = ""\n',
            "Use.kt": """\
package app.ui

import app.data.Topic
import app.util.slugify

class Use {
    fun go(t: Topic) = t.slugify()
}
""",
        },
    )
    assert ("java:app.ui.Use.go", "java:app.data.Topic.slugify") in _calls(batch)


# ---- §11 finding 12: the backstop that lets a same-package call be checked ------


def test_a_same_package_call_across_files_resolves_when_the_repo_declares_it(tmp_path: Path) -> None:
    """§3.2 row 1 refused this for want of a `finalize` backstop. There is one now."""
    batch = _facts(
        tmp_path,
        {
            "Orders.kt": "package svc\n\nfun orders() {}\n",
            "App.kt": "package svc\n\nfun module() {\n    orders()\n}\n",
        },
    )
    assert ("java:svc.module", "java:svc.orders") in _calls(batch)


def test_a_same_package_call_nothing_declares_is_still_refused(tmp_path: Path) -> None:
    """The backstop *checks*; it does not licence a guess."""
    batch = _facts(tmp_path, {"App.kt": "package svc\n\nfun module() {\n    orders()\n}\n"})
    assert "java:svc.orders" not in _ids(batch)


# ---- #389: the same fabrication one level out — an *imported* receiver ---------


@pytest.mark.parametrize("scope_fn", sorted(_SCOPE_FUNCTIONS))
def test_a_scope_function_on_an_imported_receiver_is_not_a_member(tmp_path: Path, scope_fn: str) -> None:
    """The half `declared_ids` structurally cannot answer.

    A repo-declared receiver has a known member list, so `finalize` refuses a call to a
    member it does not declare. An **imported** receiver has none — `Modifier` is
    third-party and this tree knows nothing about it — so `modifier.let { }` minted
    `java:androidx.compose.ui.Modifier.let`, a member `Modifier` does not declare.

    Parametrized over `_SCOPE_FUNCTIONS` itself, not a copy: a name added to the set
    without a thought about this test is exactly the drift worth failing on.
    """
    batch = _facts(
        tmp_path,
        {
            "Screen.kt": f"""\
package app.ui

import androidx.compose.ui.Modifier

class Screen {{
    fun draw(m: Modifier) {{
        m.{scope_fn} {{ }}
    }}
}}
""",
        },
    )
    assert f"java:androidx.compose.ui.Modifier.{scope_fn}" not in _ids(batch)


def test_a_real_member_on_an_imported_receiver_still_lands(tmp_path: Path) -> None:
    """The line the #389 fix must not cross, and the first attempt did.

    Refusing on the member *name* alone drops `Runnable.run()` and `LocalDate.with(…)`,
    which are genuine JVM members that merely share a scope function's name — measured,
    a three-call probe fell from three `CALLS` to one. What separates them is not the
    name but the shape: a scope function is handed a function, these are not.
    """
    batch = _facts(
        tmp_path,
        {
            "Cycle.kt": """\
package app.billing

import java.lang.Runnable
import java.time.LocalDate

class Cycle(private val r: Runnable) {
    fun endOfMonth(d: LocalDate): LocalDate = d.with(null)
    fun go() {
        r.run()
    }
}
""",
        },
    )
    assert ("java:app.billing.Cycle.endOfMonth", "java:java.time.LocalDate.with") in _calls(batch)
    assert ("java:app.billing.Cycle.go", "java:java.lang.Runnable.run") in _calls(batch)


def test_a_scope_function_passed_a_callable_reference_is_still_refused(tmp_path: Path) -> None:
    """`x.let(::f)` is the parenthesised form of the same thing, and still not a member."""
    batch = _facts(
        tmp_path,
        {
            "Screen.kt": """\
package app.ui

import androidx.compose.ui.Modifier

class Screen {
    fun draw(m: Modifier) {
        m.let(::helper)
    }

    fun helper(x: Modifier) {}
}
""",
        },
    )
    assert "java:androidx.compose.ui.Modifier.let" not in _ids(batch)


def test_a_declared_type_that_really_declares_run_still_resolves(tmp_path: Path) -> None:
    """The denylist must never outrank a grounded declaration.

    `resolve_or_drop` runs first, so a repository that genuinely declares `run` wins
    however the call is written — including with a trailing lambda, the shape the
    refusal keys on.
    """
    batch = _facts(
        tmp_path,
        {
            "Task.kt": "package app.job\n\nclass Task {\n    fun run(block: () -> Unit) {}\n}\n",
            "Use.kt": """\
package app.job

class Use {
    fun go(t: Task) {
        t.run { }
    }
}
""",
        },
    )
    assert ("java:app.job.Use.go", "java:app.job.Task.run") in _calls(batch)


def test_an_imported_extension_outranks_the_receiver_member_guess(tmp_path: Path) -> None:
    """`Modifier.padding` is a fabrication too — `padding` is an extension.

    Kotlin requires a file to import an extension in order to call it, so when the
    import is there it is a fully-qualified name the source *wrote*, where the
    receiver-member reading is a guess about a type nothing here can introspect. The
    edge is kept either way; only its target changes.
    """
    batch = _facts(
        tmp_path,
        {
            "Screen.kt": """\
package app.ui

import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.padding

class Screen {
    fun pad(m: Modifier) = m.padding(8)
}
""",
        },
    )
    assert ("java:app.ui.Screen.pad", "java:androidx.compose.foundation.layout.padding") in _calls(batch)
    assert "java:androidx.compose.ui.Modifier.padding" not in _ids(batch)


# ---- #391: an inherited member call must resolve through IMPLEMENTS, not drop ---


def test_a_call_to_an_inherited_member_resolves_through_implements(tmp_path: Path) -> None:
    """`i.ping()` where `ping` is declared on `Impl`'s supertype, not on `Impl` itself.

    Before #391's fix `_settle_calls` dropped this: the receiver's own type (`Impl`) is
    declared, but does not declare `ping`, and the call was refused outright rather than
    walking the `IMPLEMENTS` edge already recorded for the same batch. Inheritance plus an
    instance call is the most ordinary shape in the language, so this was a real recall
    loss, not merely a theoretical one.
    """
    batch = _facts(
        tmp_path,
        {
            "Base.kt": "package app\n\nopen class Base { fun ping() {} }\n",
            "Impl.kt": "package app\n\nclass Impl : Base()\n",
            "User.kt": "package app\n\nclass User { fun go(i: Impl) { i.ping() } }\n",
        },
    )
    assert ("java:app.User.go", "java:app.Base.ping") in _calls(batch)


def test_an_inherited_member_two_levels_up_still_resolves(tmp_path: Path) -> None:
    """The walk is not limited to one hop: `Child : Parent`, `Parent : Grandparent`."""
    batch = _facts(
        tmp_path,
        {
            "G.kt": "package app\n\nopen class Grandparent { fun ping() {} }\n",
            "P.kt": "package app\n\nopen class Parent : Grandparent()\n",
            "C.kt": "package app\n\nclass Child : Parent()\n",
            "U.kt": "package app\n\nclass User { fun go(c: Child) { c.ping() } }\n",
        },
    )
    assert ("java:app.User.go", "java:app.Grandparent.ping") in _calls(batch)


def test_an_inherited_member_ambiguous_between_two_supertypes_is_refused(tmp_path: Path) -> None:
    """`C : A(), B()` and both declare `ping` — Kotlin itself would reject this as

    unresolved without an explicit override, so a unique-hit-only walk refuses it too
    rather than guessing one of the two.
    """
    batch = _facts(
        tmp_path,
        {
            "A.kt": "package app\n\nopen class A { fun ping() {} }\n",
            "B.kt": "package app\n\nopen class B { fun ping() {} }\n",
            "C.kt": "package app\n\nclass C : A(), B()\n",
            "U.kt": "package app\n\nclass User { fun go(c: C) { c.ping() } }\n",
        },
    )
    assert not {e for e in _calls(batch) if e[0] == "java:app.User.go"}


# ---- #390: an import matching the called name is not evidence it's an extension ---


def test_an_import_matching_the_name_is_not_a_call_to_a_plain_function(tmp_path: Path) -> None:
    """`t.format()` where `app.util.format` is imported but is an ordinary top-level

    function, not an extension of `Topic`. Before #390's fix, `_resolve_navigated`
    offered any import whose simple name matched the member as a candidate with no check
    that it was even a function, let alone an extension of the receiver — so this
    resolved onto `app.util.format`, a real, first-party `CALLS` edge to the wrong target
    entirely.
    """
    batch = _facts(
        tmp_path,
        {
            "Topic.kt": "package app.data\n\nclass Topic\n",
            "U.kt": 'package app.util\n\nfun format(x: Int): String = "$x"\n',
            "S.kt": """\
package app.ui

import app.data.Topic
import app.util.format

class Screen(private val t: Topic) {
    fun show() {
        t.format()
    }
}
""",
        },
    )
    assert not _calls(batch)


def test_an_import_matching_the_name_is_not_a_call_to_a_type(tmp_path: Path) -> None:
    """`t.render()` where `app.util.render` is imported but is a class, not a function.

    Same shape as the plain-function case above, and #390's original report: a
    receiver call must never resolve onto a `Type` node just because an import shares
    the called member's simple name.
    """
    batch = _facts(
        tmp_path,
        {
            "Topic.kt": "package app.data\n\nclass Topic\n",
            "U.kt": "package app.util\n\nclass render\n",
            "S.kt": """\
package app.ui

import app.data.Topic
import app.util.render

class Screen(private val t: Topic) {
    fun show() {
        t.render()
    }
}
""",
        },
    )
    assert not _calls(batch)


def test_an_import_matching_the_name_is_not_a_call_when_the_receiver_is_undeclared(
    tmp_path: Path,
) -> None:
    """The same fabrication, one hop further out: the receiver's own type is not

    declared in this repo at all — the dominant real-world shape (`androidx.*`,
    `java.io.*`, …), and the one #390 was originally filed against. The receiver
    being undeclared used to reach a *different* branch of `_settle_calls`, one that
    re-read the raw, unverified `imported_extension` field independently of the
    candidate filter above it — so an import matching the called name still grounded
    onto a real, unrelated declaration through `FactBatch.add_node`'s dedup, even
    though the exact same call refused correctly when `Topic` was declared locally.
    """
    batch = _facts(
        tmp_path,
        {
            "U.kt": 'package app.util\n\nfun format(x: Int): String = "$x"\n\nclass render\n',
            "S.kt": """\
package app.ui

import app.data.Topic
import app.util.format
import app.util.render

class Screen(private val t: Topic) {
    fun show() {
        t.format()
        t.render()
    }
}
""",
        },
    )
    assert not (_targets(batch) & {"java:app.util.format", "java:app.util.render"})


def test_an_import_extending_a_different_type_is_not_offered_for_this_receiver(tmp_path: Path) -> None:
    """`app.util.slugify` is a genuine extension, but of `Other`, not `Topic`.

    A repo-wide extension table has to check the *receiver*, not just "is this id an
    extension of something" — otherwise any extension anywhere would satisfy any call
    that happens to import it under the right name.
    """
    batch = _facts(
        tmp_path,
        {
            "Topic.kt": "package app.data\n\nclass Topic\nclass Other\n",
            "Slug.kt": 'package app.util\n\nimport app.data.Other\n\nfun Other.slugify(): String = ""\n',
            "S.kt": """\
package app.ui

import app.data.Topic
import app.util.slugify

class Screen(private val t: Topic) {
    fun show() {
        t.slugify()
    }
}
""",
        },
    )
    assert not _calls(batch)


def test_an_extension_on_a_supertype_resolves_through_an_external_receiver(tmp_path: Path) -> None:
    """The shape that made the #390 check fabricate: a subtype receiver it cannot see.

    `fun NavController.navigateToSearch()` called on a `NavHostController` is the standard
    Compose navigation pattern, and on the validation app it appears four times. Comparing
    receiver *names* for equality answers "no" and refused the import — then minted
    `androidx.navigation.NavHostController.navigateToSearch`, an id no source declares, in
    place of the true grounded edge. Neither type is declared here, so the repository
    cannot disprove that the extension applies, and unknown must accept.
    """
    batch = _facts(
        tmp_path,
        {
            "Nav.kt": """\
package app.feature

import androidx.navigation.NavController

fun NavController.navigateToSearch() {}
""",
            "State.kt": """\
package app.ui

import androidx.navigation.NavHostController
import app.feature.navigateToSearch

class AppState(private val navController: NavHostController) {
    fun search() {
        navController.navigateToSearch()
    }
}
""",
        },
    )
    assert ("java:app.ui.AppState.search", "java:app.feature.navigateToSearch") in _calls(batch)
    assert "java:androidx.navigation.NavHostController.navigateToSearch" not in _ids(batch)


def test_an_extension_on_a_declared_supertype_is_walked_not_matched(tmp_path: Path) -> None:
    """The same rule where the repository *can* see the hierarchy: `Db : Transacter`.

    Name equality refuses this too, and here it does not even fabricate — it drops the
    edge outright, because the receiver's own type is declared and has no such member.
    The `IMPLEMENTS` walk is what makes a subtype receiver compatible rather than absent.
    """
    batch = _facts(
        tmp_path,
        {
            "Core.kt": "package app.core\n\ninterface Transacter\n\nclass Db : Transacter\n",
            "Ext.kt": "package app.ext\n\nimport app.core.Transacter\n\nfun Transacter.runIt(): Int = 1\n",
            "Use.kt": """\
package app.use

import app.core.Db
import app.ext.runIt

class Helper {
    private val db: Db = Db()
    fun go(): Int = db.runIt()
}
""",
        },
    )
    assert ("java:app.use.Helper.go", "java:app.ext.runIt") in _calls(batch)


def test_two_extensions_sharing_one_id_do_not_cancel_each_other(tmp_path: Path) -> None:
    """`fun Int.toDp()` and `fun Float.toDp()` are both `java:app.ui.toDp`.

    A table with one receiver slot per id kept whichever file was parsed last, so the
    other receiver's calls were refused — and which one survived depended on filesystem
    order. The receivers accumulate instead.
    """
    batch = _facts(
        tmp_path,
        {
            "Ext.kt": "package app.ui\n\nfun Int.toDp(): Int = this\n\nfun Float.toDp(): Int = 1\n",
            "S.kt": "package app.screen\n\nimport app.ui.toDp\n\nfun show(n: Int): Int = n.toDp()\n",
        },
    )
    assert ("java:app.screen.show", "java:app.ui.toDp") in _calls(batch)


def test_a_type_parameter_receiver_applies_to_every_type(tmp_path: Path) -> None:
    """`fun <T> T.alsoLog()` extends everything, and `T` is not a type to resolve.

    Resolving it would mint `java:app.util.T`, an id nothing declares, and comparing that
    against a real receiver refuses every call. The type-parameter list is read from the
    declaration rather than guessed at from the name's shape — `E`, `R` and `T` are all
    names a repository is allowed to give a real class.
    """
    batch = _facts(
        tmp_path,
        {
            "Ext.kt": "package app.util\n\nfun <T> T.alsoLog(): T = this\n",
            "S.kt": """\
package app.ui

import app.util.alsoLog

class Topic

fun show(t: Topic): Topic = t.alsoLog()
""",
        },
    )
    assert ("java:app.ui.show", "java:app.util.alsoLog") in _calls(batch)
    assert "java:app.util.T" not in _ids(batch)


def test_a_same_named_type_in_another_package_is_not_the_same_receiver(tmp_path: Path) -> None:
    """#390's residue, closed: `app.data.Topic` and `app.legacy.Topic` are different types.

    The extension is declared against the *legacy* `Topic` and the call is on the *data*
    one. Comparing bare names read both as `Topic` and resolved the call onto an extension
    that cannot apply to it — a name-only match, which is #390's own definition of the
    defect. Both types are declared here and no `IMPLEMENTS` path joins them, so this is
    the one case the repository can genuinely disprove.
    """
    batch = _facts(
        tmp_path,
        {
            "Data.kt": "package app.data\n\nclass Topic\n",
            "Legacy.kt": "package app.legacy\n\nclass Topic\n",
            "Slug.kt": 'package app.util\n\nimport app.legacy.Topic\n\nfun Topic.slugify(): String = "s"\n',
            "S.kt": """\
package app.ui

import app.data.Topic
import app.util.slugify

class Screen(private val t: Topic) {
    fun show(): String = t.slugify()
}
""",
        },
    )
    assert not _calls(batch)


def test_a_guessed_receiver_still_matches_a_certainly_resolved_extension(tmp_path: Path) -> None:
    """One end of the check is read from an import, the other is a same-package guess.

    `Screen` names `Topic` with no import, because Kotlin needs none inside a package —
    so the receiver resolves to the *guess* `java:app.ui.Topic`, while the extension's own
    receiver was read from `import app.ui.Topic` and is certain. They agree, and a guess
    that lands on a real declared type is not a guess about whether it is the target;
    `resolve_or_drop` refuses one that lands on nothing.

    Pinned because requiring a *certain* receiver on both ends would read this as
    unverifiable and silently drop it, and an unimported same-package receiver is the
    commonest way a Kotlin file names a type at all.
    """
    batch = _facts(
        tmp_path,
        {
            "Topic.kt": "package app.ui\n\nclass Topic\n",
            "Slug.kt": 'package app.util\n\nimport app.ui.Topic\n\nfun Topic.slug(): String = "s"\n',
            "S.kt": """\
package app.ui

import app.util.slug

class Screen(private val t: Topic) {
    fun show(): String = t.slug()
}
""",
        },
    )
    assert ("java:app.ui.Screen.show", "java:app.util.slug") in _calls(batch)


def test_the_extension_table_does_not_leak_between_repositories(tmp_path: Path) -> None:
    """One `RepoCodeExtractor` over two repositories must not carry the first one's table.

    `load_or_extract_repos` hands a single instance to every declared repository, so a
    repo-wide accumulator that `finalize` does not clear makes repo A's extensions verify
    repo B's imports — and reinstates #390 for B. `_nav`, `_ktor`, `_deferred` and
    `_client` are all cleared there for exactly this reason.

    It is worse than a stale fact: `load_or_extract` returns early on a cache hit and
    never runs `finalize`, so whether repo A happened to be cached would change repo B's
    emitted graph for the same commit.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    for path, files in (
        (first, {"Ext.kt": 'package app.util\n\nclass Topic\n\nfun Topic.format(): String = "x"\n'}),
        (
            second,
            {
                "U.kt": 'package app.util\n\nfun format(): String = "plain"\n',
                "S.kt": """\
package app.ui

import app.util.format

class Topic

fun show(t: Topic): String = t.format()
""",
            },
        ),
    ):
        for name, src in files.items():
            path.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(src, encoding="utf-8")

    shared = RepoCodeExtractor()
    shared.extract(first)
    after = _calls(shared.extract(second))
    assert after == _calls(RepoCodeExtractor().extract(second)), (
        "repo A's extensions verified repo B's import"
    )
    assert ("java:app.ui.show", "java:app.util.format") not in after


def test_an_inherited_member_overridden_in_between_resolves_to_the_override(tmp_path: Path) -> None:
    """#391, the half the first fix left. An override is not an ambiguity.

    `Impl : Mid : Base` declares `ping` twice on the way up, and collecting hits across
    the whole walk into one set held two ids — so the "exactly one match" rule refused
    the commonest inheritance shape in the language. An override always sits strictly
    nearer than the thing it overrides, which is what Kotlin resolves to; the walk goes
    level by level and the first level with one answer wins.
    """
    batch = _facts(
        tmp_path,
        {
            "A.kt": """\
package app

interface Base {
    fun ping()
}

abstract class Mid : Base {
    override fun ping() {}
}

class Impl : Mid()

class User {
    fun go(i: Impl) {
        i.ping()
    }
}
"""
        },
    )
    assert ("java:app.User.go", "java:app.Mid.ping") in _calls(batch)
    assert ("java:app.User.go", "java:app.Base.ping") not in _calls(batch)


def test_two_supertypes_declaring_one_member_at_the_same_distance_are_refused(tmp_path: Path) -> None:
    """The rule the level walk must not weaken: nearer beats further, ties do not.

    Two interfaces at the same distance both declaring `ping` is a real ambiguity —
    Kotlin requires an explicit override — so neither is guessed.
    """
    batch = _facts(
        tmp_path,
        {
            "A.kt": """\
package app

interface Base {
    fun ping()
}

interface Other {
    fun ping()
}

class Impl : Base, Other {
    override fun ping() {}
}

class User {
    fun go(i: Impl) {
        i.ping()
    }
}
"""
        },
    )
    assert ("java:app.User.go", "java:app.Impl.ping") in _calls(batch)
    assert not (
        {("java:app.User.go", "java:app.Base.ping"), ("java:app.User.go", "java:app.Other.ping")}
        & _calls(batch)
    )


def test_a_destructured_lambda_parameter_shadows_a_sibling_member(tmp_path: Path) -> None:
    """#392, the half the first fix left: the same destructuring, one level deeper.

    `m.forEach { (name, key) -> key() }` hangs a `multi_variable_declaration` off the
    lambda's parameter list rather than off a `for_statement`, so `key` was never bound
    and `key()` resolved to the sibling member the lambda never calls.
    """
    batch = _facts(
        tmp_path,
        {
            "A.kt": """\
package app

class L {
    fun key(): String = "k"

    fun go(m: Map<String, () -> Unit>) {
        m.forEach { (name, key) -> key() }
    }
}
"""
        },
    )
    assert ("java:app.L.go", "java:app.L.key") not in _calls(batch)
