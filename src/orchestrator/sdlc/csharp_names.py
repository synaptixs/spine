"""The namespace a .NET project's code actually lives in — read, never guessed from a file name.

**Why this exists (NSS-1243, three runs of three).** For an existing repository the layout's
``package_name`` is the chosen ``.csproj``'s file stem — ``commercial-secondary-sales`` — and the
C# guidance handed that straight to the model as *"C# namespace is `commercial-secondary-sales`
… declaring `namespace commercial-secondary-sales;`"*. The model did as it was told: one run
wrote ``using commercial-secondary-sales;``, another ``@namespace commercial-secondary-sales``
beside a code-behind that "repaired" it to ``commercial_secondary_sales``. The project's real
namespace, ``Commercial.Secondary.Sales``, sat in its ``<RootNamespace>`` one directory away,
and every existing file declared it. A project's *name* selects the project; its *namespace* is
a separate fact, and this module is where it is read.

Order of evidence, strongest first:

1. ``<RootNamespace>`` — what Razor stamps on every component without ``@namespace``, so a
   partial class that disagrees with it does not compile.
2. The namespaces the project's own ``.cs`` files declare, with their folder path stripped —
   the majority wins. What neighbouring code compiles with is what new code must match.
3. ``<AssemblyName>``.
4. The file stem, made a legal identifier the way the .NET templates do it (``-`` → ``_``).

An MSBuild property reference (``$(MSBuildProjectName)``) is not a value and is skipped.
Deterministic: files are visited in sorted order and the scan is bounded.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

#: How many source files one project scan reads. A namespace convention shows in the first few
#: dozen files; a 5,000-file project should not cost a full read to state it.
_MAX_FILES_SCANNED = 400
#: How much of each file is read looking for its namespace declaration (usings come first).
_HEAD_BYTES = 16_384

_DECLARATION = re.compile(r"^[ \t]*namespace[ \t]+(@?[A-Za-z_][\w.@]*)[ \t]*(;|\{|$)", re.MULTILINE)
_NOT_SOURCE_DIRS = frozenset({"bin", "obj", "node_modules", "wwwroot"})


def is_namespace(name: str) -> bool:
    """True when ``name`` is a legal dotted C# namespace (``Commercial.Secondary.Sales``).

    Each segment is an identifier, optionally ``@``-prefixed (a verbatim identifier).
    ``str.isidentifier`` applies the same Unicode letter/digit/underscore rule C# does.
    """
    if not name:
        return False
    return all((seg[1:] if seg.startswith("@") else seg).isidentifier() for seg in name.split("."))


def sanitize_namespace(name: str) -> str:
    """Make ``name`` a legal namespace the way the .NET templates do: invalid characters → ``_``.

    ``commercial-secondary-sales`` → ``commercial_secondary_sales``; a digit-leading segment gets
    a leading ``_``. Empty input → ``App``.
    """
    segments = []
    for raw in name.split("."):
        seg = re.sub(r"\W", "_", raw)
        if seg and seg[0].isdigit():
            seg = f"_{seg}"
        if seg:
            segments.append(seg)
    return ".".join(segments) or "App"


@dataclass(frozen=True)
class ProjectNamespace:
    """A project's root namespace, and the evidence for it."""

    root: str
    #: Which evidence settled it — ``RootNamespace``, ``declarations``, ``AssemblyName`` or
    #: ``project name`` — so the prompt and the log can say how sure to be.
    source: str
    #: One real file showing the folder convention, e.g. ``WebApp/Features/Home/Ui/Home.razor.cs
    #: declares `namespace Commercial.Secondary.Sales.Features.Home.Ui`` — or ``""``.
    example: str = ""
    #: True when most files use file-scoped ``namespace X;``, False for block ``namespace X { }``,
    #: None when no declaration was seen.
    file_scoped: bool | None = None


def _property(text: str, name: str) -> str:
    match = re.search(rf"<{name}>\s*([^<]*?)\s*</{name}>", text)
    value = match.group(1) if match else ""
    return "" if "$(" in value else value


def _source_files(project_dir: Path) -> list[Path]:
    from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS, is_nested_repo

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in DEFAULT_IGNORE_DIRS
            and d.lower() not in _NOT_SOURCE_DIRS
            and not d.startswith(".")
            and not is_nested_repo(here, d)
        )
        found.extend(here / f for f in sorted(filenames) if f.endswith(".cs"))
        if len(found) >= _MAX_FILES_SCANNED:
            break
    return found[:_MAX_FILES_SCANNED]


def _declared(path: Path) -> tuple[str, bool] | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    match = _DECLARATION.search(head)
    return (match.group(1), match.group(2) == ";") if match else None


def project_namespace(csproj: Path, root: Path) -> ProjectNamespace:
    """The root namespace new code in ``csproj``'s project should declare (see module docstring)."""
    try:
        text = csproj.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    project_dir = csproj.parent

    roots: Counter[str] = Counter()
    scoped: Counter[bool] = Counter()
    examples: dict[str, str] = {}
    for path in _source_files(project_dir):
        declared = _declared(path)
        if declared is None:
            continue
        namespace, file_scoped = declared
        scoped[file_scoped] += 1
        folder = ".".join(path.parent.relative_to(project_dir).parts)
        if not folder:
            roots[namespace] += 1
        elif namespace.endswith(f".{folder}"):
            base = namespace[: -len(folder) - 1]
            roots[base] += 1
            examples.setdefault(
                base, f"`{path.relative_to(root).as_posix()}` declares `namespace {namespace}`"
            )

    style = None if not scoped else scoped[True] >= scoped[False]
    stated = _property(text, "RootNamespace")
    if is_namespace(stated):
        return ProjectNamespace(stated, "RootNamespace", examples.get(stated, ""), style)
    if roots:
        # Most votes; ties broken by name so the answer never depends on filesystem order.
        best = sorted(roots.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if is_namespace(best):
            return ProjectNamespace(best, "declarations", examples.get(best, ""), style)
    assembly = _property(text, "AssemblyName")
    if is_namespace(assembly):
        return ProjectNamespace(assembly, "AssemblyName", examples.get(assembly, ""), style)
    fallback = sanitize_namespace(csproj.stem)
    return ProjectNamespace(fallback, "project name", examples.get(fallback, ""), style)


# --- The pre-write directive check (P3) ----------------------------------------------------
#
# Every defect NSS-1243's three runs produced was a directive line: `using commercial-secondary-
# sales;`, `@namespace commercial-secondary-sales`, and an `_Imports.razor` line truncated to a
# bare `.Ui`. Each was written, then `dotnet test` failed, and the refine loop read a wall of
# cascade errors and edited the wrong file five times. None of them needs a compiler to catch:
# a directive's target is dotted identifiers or it is not a directive.
#
# Only unindented lines are read. Directives sit at column 0; a `using` *statement* (`using var
# stream = …;`, `using (…)`) lives inside a method body and is always indented, so it is never
# mistaken for one.

_CS_USING = re.compile(r"^(?:global[ \t]+)?using[ \t]+(?:static[ \t]+)?(?P<target>[^;(]+?)[ \t]*;")
_CS_NAMESPACE = re.compile(r"^namespace[ \t]+(?P<name>[^\s;{]+)")
_RAZOR_USING = re.compile(r"^@using[ \t]+(?:static[ \t]+)?(?P<target>[^;]+?)[ \t]*;?[ \t]*$")
_RAZOR_NAMESPACE = re.compile(r"^@namespace[ \t]+(?P<name>\S+)")
_GENERIC_ARGS = re.compile(r"<[^<>]*>")


def _using_target_ok(target: str) -> bool:
    """``Name.Space`` or ``Alias = Name.Space.Type<Args>``, with an optional ``global::``."""
    if "=" in target:
        alias, _, named = target.partition("=")
        return is_namespace(alias.strip()) and "." not in alias and _using_target_ok(named)
    name = target.strip().removeprefix("global::")
    while _GENERIC_ARGS.search(name):  # `List<int>` → `List`, nested args innermost first
        name = _GENERIC_ARGS.sub("", name)
    return is_namespace(name.replace(" ", ""))


def _bad(rel: str, number: int, line: str, what: str) -> str:
    return (
        f"{rel}: line {number}: `{line.strip()}` is not a valid {what} — a namespace is "
        "dot-separated identifiers (letters, digits, `_`; never `-`). Use the namespace the "
        "PROJECT LAYOUT gives."
    )


def directive_error(rel: str, source: str) -> str:
    """``""`` when every namespace/using directive in ``source`` is legal, else one line naming
    the first that is not — file, line, and the text. Only ``.cs``, ``.razor`` and ``.cshtml``."""
    lower = rel.lower()
    if lower.endswith(".cs"):
        for number, line in enumerate(source.splitlines(), 1):
            if (m := _CS_USING.match(line)) and not _using_target_ok(m["target"]):
                return _bad(rel, number, line, "using directive")
            if (m := _CS_NAMESPACE.match(line)) and not is_namespace(m["name"]):
                return _bad(rel, number, line, "namespace declaration")
        return ""
    if not lower.endswith((".razor", ".cshtml")):
        return ""
    imports = Path(lower).name == "_imports.razor"
    in_comment = False
    for number, line in enumerate(source.splitlines(), 1):
        text = line.strip()
        if in_comment or text.startswith("@*"):
            in_comment = "*@" not in text or text.endswith("@*")
            continue
        if (m := _RAZOR_USING.match(line)) and not _using_target_ok(m["target"]):
            return _bad(rel, number, line, "@using directive")
        if (m := _RAZOR_NAMESPACE.match(line)) and not is_namespace(m["name"]):
            return _bad(rel, number, line, "@namespace directive")
        # `_Imports.razor` holds directives and nothing else. A bare `.Ui` left by a botched
        # edit (NSS-1243, run A) is not one, and breaks every component in the project.
        if imports and text and not text.startswith(("@", "<!--")):
            return (
                f"{rel}: line {number}: `{text}` is not a Razor directive — `_Imports.razor` may "
                "only hold `@using`/`@inject`/`@attribute`-style lines."
            )
    return ""


__all__ = [
    "ProjectNamespace",
    "directive_error",
    "is_namespace",
    "project_namespace",
    "sanitize_namespace",
]
