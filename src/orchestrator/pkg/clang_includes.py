"""Infer additional clang include roots from admitted files and literal includes.

Existing search directories keep their precedence. A new root may resolve a
previously missing literal only when the complete added search list is unambiguous.
This is deliberately independent of node construction and TU selection.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path, PurePosixPath


def infer_include_roots(root: Path, admitted: set[str], baseline: list[str]) -> list[str]:
    """Return deterministic repository-relative roots, appended after ``baseline``.

    ``admitted`` comes from the extractor's actual file walk, including its custom
    exclusions. Module nodes created by import resolution are not admission proof.
    Literal includes are read from the CST, so comments, strings and computed
    include names cannot introduce flags. Existing quoted-local and search-path
    resolutions retain precedence. Missing literals that would gain conflicting
    targets invalidate every proposed root that supplies one of those targets.
    """
    from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS, is_nested_repo

    root = root.resolve()
    files: set[str] = set()
    for rel in admitted:
        path = PurePosixPath(rel)
        if path.is_absolute() or ".." in path.parts or "\\" in rel:
            continue
        if any(part.startswith(".") or part in DEFAULT_IGNORE_DIRS for part in path.parts[:-1]):
            continue
        if any(
            is_nested_repo(root.joinpath(*path.parts[:i]), part) for i, part in enumerate(path.parts[:-1])
        ):
            continue
        try:
            if (root / rel).resolve().relative_to(root).as_posix() == rel and (root / rel).is_file():
                files.add(rel)
        except (OSError, ValueError):
            continue
    headers = {f for f in files if PurePosixPath(f).suffix in {".h", ".hpp", ".hh", ".hxx"}}
    if not headers:
        return []
    # Either installed grammar recognizes literal preprocessor includes. No new
    # grammar becomes mandatory for a C-only or C++-only installation.
    if importlib.util.find_spec("tree_sitter_cpp") is not None:
        from orchestrator.pkg.cpp_extractor import _cpp_parser

        parser = _cpp_parser()
    else:
        from orchestrator.pkg.c_extractor import _c_parser

        parser = _c_parser()
    literals: set[tuple[str, str, bool]] = set()
    for rel in sorted(files):
        if PurePosixPath(rel).suffix not in {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"}:
            continue
        try:
            source = (root / rel).read_bytes()
        except OSError:
            continue
        stack = [parser.parse(source).root_node]
        while stack:
            node = stack.pop()
            if node.type == "preproc_include":
                target = node.child_by_field_name("path")
                if target is not None and target.type in {"string_literal", "system_lib_string"}:
                    spelling = source[target.start_byte + 1 : target.end_byte - 1].decode(errors="replace")
                    path = PurePosixPath(spelling)
                    if (
                        spelling
                        and not path.is_absolute()
                        and ".." not in path.parts
                        and "\\" not in spelling
                    ):
                        literals.add((rel, spelling, target.type == "string_literal"))
            stack.extend(node.named_children)
    suffixes: dict[str, set[str]] = {}
    for header in headers:
        parts = PurePosixPath(header).parts
        for i in range(len(parts)):
            suffixes.setdefault("/".join(parts[i:]), set()).add("/".join(parts[:i]) or ".")
    # Stat each distinct search spelling once, not once per including file.
    cache: dict[tuple[str, str], str | None] = {}

    def locate(directory: str, spelling: str) -> str | None:
        key = (directory, spelling)
        if key not in cache:
            path = Path(directory) / spelling
            try:
                cache[key] = str(path.resolve()) if path.is_file() else None
            except OSError:
                cache[key] = None
        return cache[key]

    missing: set[str] = set()
    for rel, spelling, quoted in sorted(literals):
        if quoted and locate(str((root / rel).parent), spelling) is not None:
            continue
        if not any(locate(directory, spelling) is not None for directory in baseline):
            missing.add(spelling)
    proposed = {next(iter(suffixes[s])) for s in missing if len(suffixes.get(s, ())) == 1}
    rejected: set[str] = set()
    allowed_paths = {str(root / f) for f in files}
    for spelling in sorted(missing):
        providers = {d: target for d in proposed if (target := locate(str(root / d), spelling)) is not None}
        if len(set(providers.values())) > 1 or any(
            target not in allowed_paths for target in providers.values()
        ):
            rejected.update(providers)
    return sorted(proposed - rejected)
