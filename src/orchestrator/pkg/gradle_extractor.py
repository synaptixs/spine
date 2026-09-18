"""Gradle Kotlin-DSL build scripts → the module graph an Android app is built from.

P5 of docs/specs/kotlin-support-roadmap.md, D11.

**A `.kts` is not Kotlin source, and reading it as source is worse than not reading
it.** Its "functions" are Gradle configuration — ``plugins {}``, ``android {}``,
``dependencies {}`` — so extracting them the way the Kotlin front-end extracts a
file would add a phantom component per module and a phantom function per DSL
block. That is why ``KotlinExtractor`` claims ``.kt`` only and this reader exists
separately: same parser, deliberately different reading.

What it reads is the thing nothing else in the repository states:

* ``settings.gradle.kts``'s ``include(":core:data")`` declares the **modules** —
  the real architectural units of an Android build, which no source file names.
* each module's ``dependencies { implementation(project(":core:model")) }``
  declares **module-to-module dependencies** — the layering. This is the fact that
  lets ``state`` show ``feature/`` depending on ``core/`` instead of guessing
  architecture from package-name prefixes.

Modules get their own ``gradle:`` prefix and the id **is** the module path
(``gradle:core/data``), because a Gradle module is a directory, not a namespace —
it has no package, and two modules routinely publish the same packages.

**What it refuses.** Only a literal ``project(":…")`` produces an edge. A
``libs.something`` version-catalog coordinate is a third-party artifact and gets no
node at all; ``project(someVariable)`` names nothing knowable and gets nothing.

**Known gap: dependencies injected by a convention plugin are invisible here.** A
mature Gradle build factors shared wiring into `build-logic` precompiled plugins,
so a module's script reads ``plugins { id("<product>.android.feature") }`` and
its dependencies on ``core:*`` live in that plugin's **`.kt`** source instead.
Measured on the validation app: every ``app →`` and ``sync →`` edge is visible,
and **no ``feature → core`` edge is**, because all six feature modules get theirs
that way. The module *nodes* are complete; the edge set is a lower bound. Reading
them would mean resolving a plugin id to the Kotlin file that registers it and
then interpreting Gradle API calls in ordinary source — a different and much
less certain job than reading a literal, so it is recorded rather than guessed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import repo_relative
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.kotlin_names import string_value, text

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "gradle"

#: The two scripts that carry facts. `init.gradle.kts` and the `build-logic`
#: build's own scripts configure the *build*, not the product's module graph.
_SETTINGS = "settings.gradle.kts"
_BUILD = "build.gradle.kts"

#: Dependency configurations that make one module depend on another. Test
#: configurations are included: a test dependency is a real dependency, and a
#: change to `core:model` really can break `feature:topic`'s tests.
#:
#: §3.5 proposed tagging test edges "in provenance" so `state` could separate
#: them. `Provenance` has no field for that — it is file/line/end_line/repo — so
#: the tag is not representable today and the edges are emitted undifferentiated.
#: Recorded rather than faked; adding a field to `Provenance` is a change with 46
#: non-test importers and is not worth it for this.
_PROJECT_CONFIGURATIONS = frozenset(
    {
        "implementation",
        "api",
        "compileOnly",
        "runtimeOnly",
        "testImplementation",
        "testCompileOnly",
        "testRuntimeOnly",
        "androidTestImplementation",
        "debugImplementation",
        "releaseImplementation",
        "kapt",
        "ksp",
    }
)


def module_id(module_path: str) -> str:
    """``core:data`` or ``core/data`` → ``gradle:core/data``; the root is ``gradle:<root>``."""
    path = module_path.strip(":").replace(":", "/")
    return f"gradle:{path}" if path else "gradle:<root>"


class GradleExtractor:
    """Gradle Kotlin-DSL reader. Install the ``kotlin`` extra to use it."""

    language: str = _LANG
    suffixes: tuple[str, ...] = (".kts",)

    def module_name(self, path: Path, root: Path) -> str:
        """A build script names the module it configures — its **directory**."""
        rel = repo_relative(path, root)
        parent = rel.parent.as_posix()
        return "" if parent == "." else parent

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        batch = FactBatch()
        if path.name not in (_SETTINGS, _BUILD):
            # `init.gradle.kts` and friends configure the build tool, not the product.
            return batch
        from orchestrator.pkg.kotlin_extractor import _kotlin_parser

        source = path.read_bytes()
        tree = _kotlin_parser().parse(source)

        if path.name == _SETTINGS:
            if module:
                # A `settings.gradle.kts` below the root belongs to a *composite*
                # build — `includeBuild("build-logic")` — whose `include(":x")` paths
                # are relative to that build, not this repository. Reading it produced
                # a phantom top-level `convention` module beside the real
                # `build-logic/convention`.
                return batch
            self._read_includes(tree.root_node, source, rel, batch)
            return batch

        own = module_id(module)
        batch.add_node(Node(own, NodeKind.MODULE, module or "<root>", _LANG, Provenance(rel, 1)))
        self._read_dependencies(tree.root_node, own, source, rel, batch)
        return batch

    def _read_includes(self, root: TSNode, source: bytes, rel: str, batch: FactBatch) -> None:
        """``include(":core:data")`` — the declaration of what modules exist.

        Every argument counts: ``include(":a", ":b")`` is legal and declares two.
        """
        for call in _walk(root):
            if call.type != "call_expression" or _callee(call, source) != "include":
                continue
            line = call.start_point[0] + 1
            for literal in _string_arguments(call, source):
                if not literal.startswith(":"):
                    continue  # `includeBuild("build-logic")` is a composite build, not a module
                mid = module_id(literal)
                batch.add_node(
                    Node(
                        mid,
                        NodeKind.MODULE,
                        literal.strip(":").replace(":", "/"),
                        _LANG,
                        Provenance(rel, line),
                    )
                )

    def _read_dependencies(self, root: TSNode, own: str, source: bytes, rel: str, batch: FactBatch) -> None:
        """``implementation(project(":core:model"))`` → ``IMPORTS`` between modules.

        **Which module the dependency belongs to is not always this file's module.** A
        root ``build.gradle.kts`` may configure others from inside it, and the reader
        used to walk the whole tree and credit every ``project(":x")`` it found to the
        script's own module. So ``project(":app") { dependencies { implementation(project(":core:ui")) } }``
        asserted a root → ``core/ui`` edge that no file declares and lost the real
        ``app`` → ``core/ui`` one — a false edge and a missing edge from a single read.
        """
        for call in _walk(root):
            if call.type != "call_expression":
                continue
            if _callee(call, source) not in _PROJECT_CONFIGURATIONS:
                continue
            host = _configured_module(call, source, own)
            if host is None:
                continue  # inside `subprojects {}` / `allprojects {}` — see `_configured_module`
            for inner in _call_arguments(call):
                if inner.type != "call_expression" or _callee(inner, source) != "project":
                    continue  # `implementation(libs.foo)` is a third-party coordinate
                for literal in _string_arguments(inner, source):
                    if not literal.startswith(":"):
                        continue
                    target = module_id(literal)
                    batch.add_node(
                        Node(
                            target,
                            NodeKind.MODULE,
                            literal.strip(":").replace(":", "/"),
                            _LANG,
                            external=True,
                        )
                    )
                    batch.add_edge(
                        Edge(host, target, EdgeKind.IMPORTS, Provenance(rel, call.start_point[0] + 1))
                    )


#: Blocks that configure modules other than the one whose script they are written in.
_FOREIGN_SCOPES = frozenset({"subprojects", "allprojects", "project", "configure"})


def _configured_module(call: TSNode, source: bytes, own: str) -> str | None:
    """Which module a dependency call configures: ``own``, another one, or nothing.

    ``project(":app") { … }`` names its module, so the dependency is that module's.
    ``subprojects { … }`` and ``allprojects { … }`` apply to a set this file does not
    enumerate — the answer is "several modules, and this script does not say which", so
    the honest reading is no edge at all rather than one edge hung off the root.
    """
    node = call.parent
    while node is not None:
        if node.type == "call_expression":
            name = _callee(node, source)
            if name in _FOREIGN_SCOPES:
                if name in ("subprojects", "allprojects"):
                    return None
                named = next((lit for lit in _string_arguments(node, source) if lit.startswith(":")), None)
                return module_id(named) if named else None
        node = node.parent
    return own


def _callee(call: TSNode, source: bytes) -> str:
    """The called name, seeing through a trailing lambda's wrapping call."""
    first = next(iter(call.named_children), None)
    if first is None:
        return ""
    if first.type == "call_expression":  # `foo(...) { … }` wraps `foo(...)`
        return _callee(first, source)
    if first.type == "identifier":
        return text(first, source)
    if first.type == "navigation_expression":
        parts = first.named_children
        return text(parts[-1], source) if parts and parts[-1].type == "identifier" else ""
    return ""


def _call_arguments(call: TSNode) -> list[TSNode]:
    holder = call
    first = next(iter(call.named_children), None)
    if first is not None and first.type == "call_expression":
        holder = first
    args = next((c for c in holder.named_children if c.type == "value_arguments"), None)
    if args is None:
        return []
    return [
        a.named_children[-1] for a in args.named_children if a.type == "value_argument" and a.named_children
    ]


def _string_arguments(call: TSNode, source: bytes) -> list[str]:
    out: list[str] = []
    for argument in _call_arguments(call):
        literal = string_value(argument, source)
        if literal:
            out.append(literal)
    return out


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.named_children)
    return out


def gradle_parser_available() -> Any:
    """Whether the Kotlin grammar this reader borrows is installed."""
    import importlib.util

    return importlib.util.find_spec("tree_sitter_kotlin") is not None


__all__ = ["GradleExtractor", "gradle_parser_available", "module_id"]
