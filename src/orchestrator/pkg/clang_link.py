"""Optional semantic enrichment beside the C/C++ CST front-ends.

A second parser may contribute edges only between nodes the primary parser already
grounded. USRs are identities to check, never authority to invent PKG nodes. This
module deliberately declines template and anonymous declaration identities. Ordinary
parameter and method qualifiers collapse to the existing name-keyed graph identity.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import EdgeKind, FactBatch, Node, Provenance
from orchestrator.pkg.finalize_names import declared_ids, resolve_or_drop

if TYPE_CHECKING:
    from clang.cindex import Cursor

_IDENTIFIER = r"[A-Za-z_][A-Za-z_0-9]*"
_CPP_FUNCTION = re.compile(rf"c:((?:@(?:N|S)@{_IDENTIFIER})*)@F@({_IDENTIFIER})")
# LLVM 18 USRGeneration.cpp: parameters precede a final '#', followed by
# static/CVR/ref method qualifiers. Parameter types are opaque here: the graph
# keys functions by name, and this input comes from a resolved clang declaration.
# A segment starting '@' introduces another declaration scope after the function
# (e.g. a local class or lambda), not a parameter type. It must not collapse to
# the enclosing function. Qualified parameter types start with type encodings
# such as '$', '*', or '&', so their embedded '@' scopes remain supported.
_CPP_SIGNATURE = re.compile(r"(?:[^@\s#][^\s#]*#)*S?[1-7]?(?:&{1,2})?")
_CPP_CALLER_FUNCTION = re.compile(
    rf"c:([^@]+)?((?:@(?:N|S)@{_IDENTIFIER})*)@F@(~?{_IDENTIFIER}|operator\(\))"
)
_C_FUNCTION = re.compile(rf"c:@F@({_IDENTIFIER})")
_C_STATIC = re.compile(rf"c:([^@]+)@F@({_IDENTIFIER})")


def usr_to_id(usr: str, *, language: str, rel: str) -> str | None:
    """Map only recognised, name-keyed function USRs; reject everything else.

    ``rel`` is the declaration's repository-relative path, not the caller's TU.
    C statics use that full path (the USR itself contains only the basename).
    Parameter encodings and method qualifiers do not participate in existing IDs.
    Validate the declaration prefix separately, so templates, local declarations
    and anonymous scopes cannot collapse onto an unrelated grounded name. This
    projects a clang-generated USR; it is not a validator for arbitrary USR text.
    The caller must still verify that the result is a grounded function.
    """
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or ".." in path.parts or "\\" in rel or ":" in rel:
        return None
    if language == "c":
        match = _C_FUNCTION.fullmatch(usr)
        if match:
            return f"c:{match[1]}"
        match = _C_STATIC.fullmatch(usr)
        if match and match[1] == path.name:
            return f"c:{path.as_posix()}::{match[2]}"
    elif language == "cpp":
        name, separator, signature = usr.partition("#")
        if separator and not _CPP_SIGNATURE.fullmatch(signature):
            return None
        match = _CPP_FUNCTION.fullmatch(name)
        if match:
            parents = re.findall(r"@(?:N|S)@([^@]+)", match[1])
            return "cpp:" + "::".join([*parents, match[2]])
    return None


# The side-channel names call *sites*, not symbol guesses. Full byte ranges distinguish
# nested calls with the same start and match clang without text heuristics.
@dataclass(frozen=True)
class PendingMemberCall:
    caller: str
    file: str
    offset: int
    line: int
    end_offset: int


@dataclass
class ClangReport:
    available: bool = False
    pending: int = 0
    resolved: int = 0
    total_tus: int = 0
    parsed_tus: int = 0
    failed_tus: int = 0
    diagnostic_tus: int = 0
    unresolved_reasons: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        availability = "" if self.available else " (extra unavailable)"
        return (
            f"clang: resolved {self.resolved} of {self.pending} unresolved call sites "
            f"in {self.parsed_tus} of {self.total_tus} TUs{availability}; "
            f"{self.diagnostic_tus} with diagnostics, {self.failed_tus} failed"
        )


def clang_available() -> bool:
    """Only the bundled distribution counts; never fall back to a system libclang."""
    try:
        importlib.metadata.version("libclang")
        return importlib.util.find_spec("clang") is not None
    except (ImportError, importlib.metadata.PackageNotFoundError):
        return False


def _repo_file(path: str, root: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _tu_files(batch: FactBatch) -> tuple[dict[str, str], dict[str, set[str]], set[str]]:
    modules = {
        n.id: n for n in batch.nodes if n.grounded and n.kind.value == "Module" and n.language in {"c", "cpp"}
    }
    files = {n.provenance.file: n.language or "c" for n in modules.values() if n.provenance}
    includes: dict[str, set[str]] = {}
    for e in batch.edges:
        if e.kind.value == "IMPORTS" and e.src in modules and e.dst in modules:
            src, dst = modules[e.src].provenance, modules[e.dst].provenance
            if src and dst:
                includes.setdefault(src.file, set()).add(dst.file)
    tus = {f: lang for f, lang in files.items() if Path(f).suffix in {".c", ".cpp", ".cc", ".cxx"}}
    return tus, includes, set(files)


def _caller_usr_to_id(usr: str, *, language: str, rel: str) -> str | None:
    """Project additional caller shapes only; callee eligibility stays unchanged.

    The caller guard separately checks exact CST identity and source grounding.
    This admits file statics, destructors and call operators without stripping
    namespace/class scope or confusing a destructor with its constructor.
    """
    mapped = usr_to_id(usr, language=language, rel=rel)
    if mapped is not None or language != "cpp":
        return mapped
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or ".." in path.parts or "\\" in rel or ":" in rel:
        return None
    name, separator, signature = usr.partition("#")
    if separator and not _CPP_SIGNATURE.fullmatch(signature):
        return None
    match = _CPP_CALLER_FUNCTION.fullmatch(name)
    if not match or (match[1] is not None and match[1] != path.name):
        return None
    parents = re.findall(r"@(?:N|S)@([^@]+)", match[2])
    return "cpp:" + "::".join([*parents, match[3]])


def _declaration_scope_matches(cursor: Cursor, identity: str, language: str, rel: str) -> bool:
    """Verify the projected name against clang's actual declaration parents.

    Function/local/lambda contexts cannot be smuggled through an opaque USR
    parameter encoding. Only the existing namespace/record name scopes qualify.
    """
    parents: list[str] = []
    parent = cursor.semantic_parent
    while parent is not None and parent.kind.name != "TRANSLATION_UNIT":
        if parent.kind.name == "LINKAGE_SPEC":
            parent = parent.semantic_parent
            continue
        if parent.kind.name not in {"NAMESPACE", "CLASS_DECL", "STRUCT_DECL"} or not parent.spelling:
            return False
        parents.append(parent.spelling)
        parent = parent.semantic_parent
    if parent is None:
        return False
    if language == "c":
        return not parents and identity in {f"c:{cursor.spelling}", f"c:{rel}::{cursor.spelling}"}
    return identity == "cpp:" + "::".join([*reversed(parents), cursor.spelling])


def _caller_matches(
    caller: Cursor | None, site: PendingMemberCall, node: Node, language: str, root: Path
) -> bool:
    """Require the actual enclosing function to agree with the grounded CST caller.

    Existence alone admits destructors collapsed onto constructors, macro test
    bodies collapsed onto a macro name, and headers whose CST lost class scope.
    A name also cannot move a call onto a different program's main. Keep the
    existing name-based overload identity when an out-of-line member's class
    contains the grounded overload in a header.
    """
    if caller is None or not caller.location.file or node.provenance is None:
        return False
    rel = _repo_file(caller.location.file.name, root)
    if _caller_usr_to_id(caller.get_usr(), language=language, rel=rel or "") != site.caller:
        return False
    if not _declaration_scope_matches(caller, site.caller, language, rel or ""):
        return False
    if node.provenance.file == rel:
        return True
    parent = caller.semantic_parent
    return bool(
        parent
        and parent.kind.name in {"CLASS_DECL", "STRUCT_DECL"}
        and parent.location.file
        and _repo_file(parent.location.file.name, root) == node.provenance.file
        and parent.extent.start.line <= node.provenance.line <= parent.extent.end.line
    )


def link_clang(
    batch: FactBatch,
    root: Path,
    *,
    pending: list[PendingMemberCall],
    report: ClangReport | None = None,
    admitted_files: set[str] | None = None,
) -> FactBatch:
    """Enrich grounded CST facts with bounded, optional semantic CALLS edges.

    Parse only source TUs with pending sites, including sites in their reachable
    headers. Synthesised flags use only repository header directories. Missing
    includes cost recall; host SDKs and compilation databases are never consulted.
    Reports are side-channel metadata, so facts stay deterministic and edge-only.
    """
    report = report if report is not None else ClangReport()
    sites = {(p.file, p.offset, p.end_offset): p for p in pending}
    report.pending = len(sites)
    report.unresolved_reasons = {"extra_unavailable": len(sites)} if sites else {}
    tus, includes, files = _tu_files(batch)
    report.total_tus = len(tus)
    report.available = clang_available()
    if not sites or not report.available:
        return batch
    from clang import cindex

    try:
        # Explicitly select the wheel's library; LIBCLANG_LIBRARY_PATH must not
        # select a different compiler on another checkout or machine.
        library = Path(cindex.__file__).parent / "native"
        filename = (
            "libclang.dll"
            if sys.platform == "win32"
            else "libclang.dylib"
            if sys.platform == "darwin"
            else "libclang.so"
        )
        if not cindex.Config.loaded:
            cindex.Config.set_library_file(str(library / filename))
        elif Path(cindex.conf.get_filename()).resolve() != (library / filename).resolve():
            report.available = False
            return batch
        index = cindex.Index.create()
    except (OSError, cindex.LibclangError):
        report.available = False
        return batch
    root = root.resolve()

    @cache
    def repo_file(path: str) -> str | None:
        # Header cursors repeat across TUs. Resolve each path once per extraction,
        # retaining symlink/boundary checks without repeating filesystem work.
        return _repo_file(path, root)

    grounded = {n.id: n for n in batch.nodes if n.grounded and n.kind.value == "Function"}
    declared = declared_ids(batch)
    header_dirs = sorted(
        {str((root / f).parent) for f in files if Path(f).suffix in {".h", ".hpp", ".hh", ".hxx"}}
    )
    if admitted_files is not None:
        from orchestrator.pkg.clang_includes import infer_include_roots

        extra_roots = infer_include_roots(root, admitted_files & files, header_dirs)
        header_dirs.extend(str(root / rel) for rel in extra_roots)
    resolved: set[tuple[str, int, int]] = set()
    # A shared header may be parsed in multiple TUs. Conflicting static targets
    # are refused rather than letting TU iteration order choose the graph.
    candidates: dict[tuple[str, int, int], set[str]] = {}
    sites_by_file: dict[str, set[tuple[str, int, int]]] = {}
    for key in sites:
        sites_by_file.setdefault(key[0], set()).add(key)
    # Keep the furthest observed stage for each distinct site across all TUs.
    # These are observations, not claims about the root cause of parser recovery.
    stages = (
        "no_matching_call",
        "indirect_or_unsupported_target",
        "outside_repository",
        "unsupported_usr",
        "ungrounded_caller",
        "caller_identity_mismatch",
        "ungrounded_target",
    )
    progress = dict.fromkeys(sites, 0)
    function_kinds = {
        cindex.CursorKind.FUNCTION_DECL,
        cindex.CursorKind.CXX_METHOD,
        cindex.CursorKind.CONSTRUCTOR,
        cindex.CursorKind.DESTRUCTOR,
        cindex.CursorKind.FUNCTION_TEMPLATE,
        cindex.CursorKind.CONVERSION_FUNCTION,
    }
    record_kinds = {
        cindex.CursorKind.CLASS_DECL,
        cindex.CursorKind.STRUCT_DECL,
        cindex.CursorKind.CLASS_TEMPLATE,
        cindex.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
    }
    for file, language in sorted(tus.items()):
        reachable: set[str] = set()
        stack = [file]
        while stack:
            item = stack.pop()
            if item not in reachable:
                reachable.add(item)
                stack.extend(sorted(includes.get(item, ())))
        wanted = {key for rel in reachable for key in sites_by_file.get(rel, ())}
        if not wanted:
            continue
        wanted_files = {key[0] for key in wanted}
        report.parsed_tus += 1
        args = [
            "-x",
            "c" if language == "c" else "c++",
            "-std=c11" if language == "c" else "-std=c++17",
            "-nostdinc",
            "-target",
            "x86_64-unknown-linux-gnu",
            "-ferror-limit=0",
            *[f"-I{d}" for d in header_dirs],
        ]
        try:
            tu = index.parse(str(root / file), args=args)
        except (OSError, cindex.TranslationUnitLoadError):
            report.failed_tus += 1
            continue
        report.diagnostic_tus += bool(list(tu.diagnostics))
        # A function body in another header cannot contain a wanted site unless
        # that file can include a wanted file. Use this TU's actual inclusions
        # (including computed includes), not the CST graph used for TU selection.
        # Unknown/outside paths merge at None, conservatively retaining parents.
        parents: dict[str | None, set[str | None]] = {}
        for inclusion in tu.get_includes():
            parent = repo_file(inclusion.source.name) if inclusion.source else None
            child = repo_file(inclusion.include.name)
            parents.setdefault(child, set()).add(parent)
        containing_files: set[str | None] = set(wanted_files)
        ancestors: list[str | None] = list(wanted_files)
        while ancestors:
            for parent in parents.get(ancestors.pop(), ()):
                if parent not in containing_files:
                    containing_files.add(parent)
                    ancestors.append(parent)
        cursors: list[tuple[Cursor, Cursor | None]] = [(tu.cursor, None)]
        while cursors:
            cursor, caller = cursors.pop()
            if cursor.kind == cindex.CursorKind.LAMBDA_EXPR:
                continue
            if cursor.kind in function_kinds:
                if cursor.location.file:
                    function_file = repo_file(cursor.location.file.name)
                    if function_file is not None and function_file not in containing_files:
                        continue
                caller = cursor
            elif cursor.kind in record_kinds:
                caller = None
            if cursor.kind == cindex.CursorKind.CALL_EXPR and cursor.location.file:
                rel = repo_file(cursor.location.file.name)
                call_key = (
                    (rel, cursor.extent.start.offset, cursor.extent.end.offset)
                    if rel is not None and rel in wanted_files
                    else None
                )
                if call_key is not None and call_key in wanted:
                    key = call_key
                    progress[key] = max(progress[key], 1)
                    target = cursor.referenced
                    if (
                        target
                        and target.kind in {cindex.CursorKind.CXX_METHOD, cindex.CursorKind.FUNCTION_DECL}
                        and target.location.file
                    ):
                        progress[key] = max(progress[key], 2)
                        target_rel = repo_file(target.location.file.name)
                        if target_rel in files:
                            progress[key] = max(progress[key], 3)
                            target_id = usr_to_id(target.get_usr(), language=language, rel=target_rel or "")
                            if target_id is not None and _declaration_scope_matches(
                                target, target_id, language, target_rel or ""
                            ):
                                progress[key] = max(progress[key], 4)
                                site = sites[key]
                                if site.caller in grounded:
                                    progress[key] = max(progress[key], 5)
                                    if _caller_matches(caller, site, grounded[site.caller], language, root):
                                        progress[key] = max(progress[key], 6)
                                        if target_id in grounded:
                                            candidates.setdefault(key, set()).add(target_id)
            cursors.extend((child, caller) for child in cursor.get_children())
    for key, targets in sorted(candidates.items()):
        if len(targets) != 1:
            continue
        site = sites[key]
        if resolve_or_drop(
            batch,
            site.caller,
            sorted(targets),
            EdgeKind.CALLS,
            Provenance(site.file, site.line),
            declared=declared,
        ):
            resolved.add(key)
    report.resolved = len(resolved)
    report.unresolved_reasons = dict(
        sorted(
            Counter(
                "conflicting_targets" if len(candidates.get(key, ())) > 1 else stages[stage]
                for key, stage in progress.items()
                if key not in resolved
            ).items()
        )
    )
    return batch
