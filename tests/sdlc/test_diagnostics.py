"""Compiler errors lead the refine prompt, and refine edits only what something names (NSS-1243).

`dotnet test` output was cut to its last 4,000 characters. On a Blazor project with 138 warnings
that tail was warnings and a summary; the error the model had just caused was printed first and
cut first. Refine read the cascade that remained and, in three runs of three, edited a correct
`_Imports.razor` — once down to a bare `.Ui`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.codegen import _SYMBOLS_IN_ERRORS, CodegenError, apply_files
from orchestrator.sdlc.diagnostics import digest_dotnet_output, dotnet_errors, paths_named

_CAP = 4000


def _build_log(root: Path, *, cascade: int = 40, warnings: int = 138) -> str:
    """A `dotnet test` log shaped like the field's: the cause, then its cascade, warnings, recap."""
    web = f"{root}/WebApp"
    proj = f"[{web}/commercial-secondary-sales.csproj]"
    cause = (
        f"{web}/Shared/Components/OilStatus.razor.cs(1,7): error CS1003: Syntax error, ',' expected {proj}"
    )
    knock_on = [
        f"{web}/_Imports.razor({9 + i},7): error CS0234: The type or namespace name 'Ui{i}' does not "
        f"exist in the namespace 'Commercial.Secondary.Sales.Features.Home' {proj}"
        for i in range(cascade)
    ]
    noise = [
        f"{web}/Features/F{i}.cs({i},1): warning CS8618: Non-nullable property 'P{i}' must contain a "
        f"non-null value when exiting constructor. {proj}"
        for i in range(warnings)
    ]
    # MSBuild prints every error again in its closing recap, after the warnings.
    return "\n".join(
        ["Determining projects to restore...", cause, *knock_on, *noise, "Build FAILED.", cause, *knock_on]
    )


def test_errors_are_read_from_the_whole_output_in_order_and_once(tmp_path: Path) -> None:
    errors = dotnet_errors(_build_log(tmp_path, cascade=1), tmp_path)

    assert [(e.path, e.line, e.code) for e in errors] == [
        ("WebApp/Shared/Components/OilStatus.razor.cs", 1, "CS1003"),
        ("WebApp/_Imports.razor", 9, "CS0234"),
    ]
    assert not errors[0].message.endswith("]")  # the `[project.csproj]` suffix is dropped


def test_the_digest_keeps_the_cause_a_plain_tail_loses(tmp_path: Path) -> None:
    log = _build_log(tmp_path)
    assert "CS1003" not in log[-_CAP:]  # the field's failure: the tail is all cascade

    digest = digest_dotnet_output(log, tmp_path, cap=_CAP)

    assert len(digest) <= _CAP
    assert digest.startswith("COMPILER ERRORS (first 20 of 41)")
    assert "WebApp/Shared/Components/OilStatus.razor.cs(1,7): error CS1003" in digest.splitlines()[1]
    assert "warning CS" not in digest.split("--- end of the build output ---")[0]


def test_output_with_no_errors_is_the_plain_tail() -> None:
    log = "Passed!  - Failed: 0, Passed: 12\n" * 400
    assert digest_dotnet_output(log, None, cap=_CAP) == log[-_CAP:]


def test_paths_under_a_symlinked_root_are_made_relative(tmp_path: Path) -> None:
    """macOS reports /tmp worktrees as /private/tmp: both spellings must resolve."""
    real = tmp_path / "real"
    (real / "WebApp").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    line = f"{real}/WebApp/A.cs(2,1): error CS0246: The type or namespace name 'X' could not be found"

    assert dotnet_errors(line, link)[0].path == "WebApp/A.cs"


def test_paths_named_finds_existing_files_only(tmp_path: Path) -> None:
    (tmp_path / "WebApp").mkdir()
    (tmp_path / "WebApp" / "A.cs").write_text("class A {}\n", encoding="utf-8")
    text = (
        f"{tmp_path}/WebApp/A.cs(1,1): error CS1\n"
        '  File "WebApp/A.cs", line 3\n'
        "WebApp/Missing.cs(1,1): error CS2\n"
        "/etc/passwd.cs(1,1): error CS3\n"
    )

    assert paths_named(text, tmp_path) == ["WebApp/A.cs"]


def test_single_quoted_symbols_are_looked_up_too() -> None:
    text = "error CS0246: The type or namespace name 'OilStatusHelper' could not be found; \"Other\" too"
    assert [name for _q, name in _SYMBOLS_IN_ERRORS.findall(text)] == ["OilStatusHelper", "Other"]


# --- the refine edit guard -------------------------------------------------------------------


def _repo(tmp_path: Path) -> dict[Path, list[Path]]:
    """A worktree with pre-existing files, and a session that has written one of its own."""
    for rel in ("WebApp/Unnamed.cs", "WebApp/Named.cs", "WebApp/App.csproj", "WebApp/Mine.cs"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("// original\n", encoding="utf-8")
    return {tmp_path.resolve(): [(tmp_path / "WebApp" / "Mine.cs").resolve()]}


def _edit(rel: str) -> dict[str, object]:
    return {"path": rel, "edits": [{"find": "// original", "replace": "// changed"}]}


def test_refine_may_not_edit_a_file_nothing_names(tmp_path: Path) -> None:
    tracker = _repo(tmp_path)

    with pytest.raises(CodegenError) as caught:
        apply_files(
            [_edit("WebApp/Unnamed.cs")],
            tmp_path,
            written_tracker=tracker,
            grounded=True,
            editable_existing=frozenset({"WebApp/Named.cs"}),
        )

    assert "WebApp/Unnamed.cs" in caught.value.empty_summary  # routed to a corrective retry
    assert (tmp_path / "WebApp" / "Unnamed.cs").read_text(encoding="utf-8") == "// original\n"


def test_refine_may_edit_named_own_and_project_files(tmp_path: Path) -> None:
    tracker = _repo(tmp_path)

    change = apply_files(
        [
            _edit("WebApp/Named.cs"),
            _edit("WebApp/Mine.cs"),
            _edit("WebApp/App.csproj"),
            _edit("WebApp/Unnamed.cs"),
        ],
        tmp_path,
        written_tracker=tracker,
        grounded=True,
        editable_existing=frozenset({"WebApp/Named.cs"}),
    )

    assert sorted(Path(f).name for f in change.files) == ["App.csproj", "Mine.cs", "Named.cs"]
    assert "refused: WebApp/Unnamed.cs" in change.summary


def test_without_a_tracked_session_the_guard_stands_aside(tmp_path: Path) -> None:
    """A fresh adapter resuming a worktree cannot tell its own files from anyone else's."""
    _repo(tmp_path)

    change = apply_files(
        [_edit("WebApp/Unnamed.cs")],
        tmp_path,
        written_tracker={},
        grounded=True,
        editable_existing=frozenset(),
    )

    assert [Path(f).name for f in change.files] == ["Unnamed.cs"]
