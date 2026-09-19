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
