"""Compiler errors lead the refine prompt, and refine edits only what something names (NSS-1243).

`dotnet test` output was cut to its last 4,000 characters. On a Blazor project with 138 warnings
that tail was warnings and a summary; the error the model had just caused was printed first and
cut first. Refine read the cascade that remained and, in three runs of three, edited a correct
`_Imports.razor` — once down to a bare `.Ui`.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from orchestrator.sdlc.codegen import _SYMBOLS_IN_ERRORS, CodegenError, apply_files
from orchestrator.sdlc.diagnostics import (
    digest_dotnet_output,
    dotnet_blocker,
    dotnet_errors,
    dotnet_failing_tests,
    paths_named,
    strip_failure_blocks,
)

_CAP = 4000


def _build_log(root: Path, *, cascade: int = 40, warnings: int = 138) -> str:
    """A `dotnet test` log shaped like the field's: the cause, then its cascade, warnings, recap."""
    web = f"{root}/WebApp"
    proj = f"[{web}/acme-order-portal.csproj]"
    cause = (
        f"{web}/Shared/Components/OilStatus.razor.cs(1,7): error CS1003: Syntax error, ',' expected {proj}"
    )
    knock_on = [
        f"{web}/_Imports.razor({9 + i},7): error CS0234: The type or namespace name 'Ui{i}' does not "
        f"exist in the namespace 'Acme.Order.Portal.Features.Home' {proj}"
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


# --- an error with no position, and a failure with no error (B73) -----------------------------
#
# `dotnet_errors` read only `path(line,col): error CODE:`. A restore failure (`NU1301`), an MSBuild
# task failure (`MSB4018`) and a bare `error CS...` line carry no position, matched nothing, and
# left `digest_dotnet_output` to hand refine a 4,000-character tail of warnings. Names below are
# invented; none is a field repository's.

_WARNING = (
    "/w/WebApp/F{i}.cs({i},5): warning CS8618: Non-nullable property 'P{i}' must contain a "
    "non-null value when exiting constructor. [/w/WebApp/App.csproj]"
)


def _warnings(n: int = 138) -> list[str]:
    return [_WARNING.format(i=i + 1) for i in range(n)]


def test_an_error_with_no_position_is_still_an_error(tmp_path: Path) -> None:
    log = "\n".join(
        [
            f"{tmp_path}/WebApp/App.csproj : error NU1301: Failed to retrieve information about "
            f"'Acme.Widgets' from remote source 'https://feed.example/index.json'. [{tmp_path}/s.sln]",
            "MSBUILD : error MSB1009: Project file does not exist.",
            "error CS0103: The name 'Foo' does not exist",
        ]
    )

    errors = dotnet_errors(log, tmp_path)

    assert [(e.path, e.line, e.code) for e in errors] == [
        ("WebApp/App.csproj", 0, "NU1301"),
        ("MSBUILD", 0, "MSB1009"),
        ("", 0, "CS0103"),
    ]
    assert not errors[0].message.endswith("]")
    assert errors[0].render().startswith("WebApp/App.csproj: error NU1301:")  # no "(0,0)"
    assert errors[2].render().startswith("error CS0103:")


def test_assertion_text_that_says_error_is_not_a_build_error() -> None:
    log = "\n".join(
        [
            "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]",
            "  Error Message:",
            "   Assert.Equal() Failure: error CS0001: not a build error",
            "   Expected: error NU1301: also not one",
        ]
    )

    assert dotnet_errors(log) == []


def test_a_position_less_error_is_not_lost_among_the_warnings_after_it() -> None:
    """The measured worst case: the cause printed first, 138 warnings after it, a 4,000-char tail."""
    cause = "/w/WebApp/App.csproj : error MSB4018: The task failed unexpectedly. [/w/s.sln]"
    log = "\n".join([cause, *_warnings(), "Build FAILED.", *_warnings(), "    1 Error(s)"])
    assert "MSB4018" not in log[-_CAP:]  # what refine was shown today

    digest = digest_dotnet_output(log, Path("/w"), cap=_CAP, returncode=1)

    assert len(digest) <= _CAP
    assert digest.startswith("COMPILER ERRORS")
    assert "WebApp/App.csproj: error MSB4018" in digest.splitlines()[1]


def test_warning_lines_are_dropped_from_the_tail() -> None:
    log = "\n".join(
        [
            *_warnings(),
            "/w/WebApp/A.cs(2,7): error CS1003: Syntax error, ',' expected [/w/W.csproj]",
            *_warnings(),
        ]
    )

    digest = digest_dotnet_output(log, Path("/w"), cap=_CAP, returncode=1)

    assert "warning CS" not in digest
    assert "error CS1003" in digest


def test_a_failure_the_parser_cannot_read_says_so_and_shows_no_warnings() -> None:
    log = "\n".join([*_warnings(), "Something unexpected happened.", *_warnings()])

    digest = digest_dotnet_output(log, None, cap=_CAP, returncode=1)

    assert digest.startswith("UNPARSED FAILURE")
    assert "warnings are not the cause" in digest.splitlines()[0]
    assert "warning CS" not in digest
    assert "Something unexpected happened." in digest  # the rest of the output is still shown


def test_output_the_parser_can_read_is_not_called_unparsed() -> None:
    """Exit 0, or no exit code given, never gets the notice; only a failure nothing explains."""
    log = "\n".join([*_warnings(5), "Passed!  - Failed: 0, Passed: 12"])

    assert not digest_dotnet_output(log, None, cap=_CAP, returncode=0).startswith("UNPARSED")
    assert not digest_dotnet_output(log, None, cap=_CAP).startswith("UNPARSED")
    assert "warning CS" not in digest_dotnet_output(log, None, cap=_CAP)


def test_failing_tests_lead_the_digest_when_no_build_error_explains_the_failure() -> None:
    log = "\n".join(
        [
            *_warnings(),
            "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]",
            "  Error Message:",
            "   Assert.Equal() Failure: Expected: Yes Actual: No",
            "Failed!  - Failed:     1, Passed:    40, Skipped:     0, Total:    41",
        ]
    )

    digest = digest_dotnet_output(log, None, cap=_CAP, returncode=1)

    assert digest.startswith("FAILED TESTS (1)")
    assert "Acme.Widgets.Tests.ParserTests.Rejects" in digest.splitlines()[1]
    assert "Expected: Yes Actual: No" in digest
    assert not digest.startswith("UNPARSED")


def test_failing_test_names_are_read_once_each_without_their_duration() -> None:
    log = "\n".join(
        [
            "  Passed Acme.Widgets.Tests.ParserTests.Accepts [1 ms]",
            "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]",
            '  Failed Acme.Widgets.Tests.ParserTests.Mixed(input: "a b", n: 2) [< 1 ms]',
            "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]",  # the recap prints it again
            "  Failed to restore /w/WebApp/App.csproj (in 6.82 sec).",
            "   Failed to parse the payload",  # indented message text, not a test
            "      Failed validation of the payload",  # three or more spaces: message text, not a test
            "Failed!  - Failed:     2, Passed:    40, Skipped:     0, Total:    42",
            "    Failed: 2",
        ]
    )

    assert dotnet_failing_tests(log) == (
        "Acme.Widgets.Tests.ParserTests.Rejects",
        'Acme.Widgets.Tests.ParserTests.Mixed(input: "a b", n: 2)',
    )
    assert dotnet_failing_tests("Passed!  - Failed: 0, Passed: 12") == ()


@pytest.mark.parametrize(
    ("log", "kind"),
    [
        (
            "/w/App.csproj : error NU1301: Failed to retrieve information about 'X' ... 401 (Unauthorized)",
            "restore",
        ),
        ("/w/App.csproj : error NU1101: Unable to find package Acme.Widgets. No packages exist", "restore"),
        ("/w/App.csproj : error NETSDK1045: The current .NET SDK does not support .NET 8.0.", "restore"),
        (
            "/w/App.csproj : error NETSDK1141: Unable to resolve the .NET SDK version in global.json",
            "restore",
        ),
        ("MSBUILD : error MSB1003: Specify a project or solution file. The directory has none.", "project"),
        ("A compatible .NET SDK was not found.", "sdk"),
        ("You must install or update .NET to run this application.", "sdk"),
    ],
)
def test_an_environment_failure_is_named_as_one(log: str, kind: str) -> None:
    blocker = dotnet_blocker(log)

    assert blocker is not None
    assert blocker.kind == kind
    assert blocker.detail  # a sentence the stop message can show


def test_a_timeout_is_an_environment_failure() -> None:
    blocker = dotnet_blocker("dotnet test run timed out", timed_out=True)

    assert blocker is not None and blocker.kind == "timeout"


@pytest.mark.parametrize(
    "log",
    [
        "/w/WebApp/A.cs(2,7): error CS1003: Syntax error, ',' expected",
        "error CS0103: The name 'Foo' does not exist",
        "/w/WebApp/App.csproj : warning NU1603: Acme.Widgets depends on Acme.Core (>= 1.0) but 1.1 was used",
        "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]",
        "",
    ],
)
def test_a_code_failure_or_a_warning_is_not_an_environment_failure(log: str) -> None:
    assert dotnet_blocker(log) is None


def test_only_a_feed_failure_is_one_no_edit_to_the_project_can_cause() -> None:
    """NU1301-NU1303 are about reaching a feed; NU1101 is a package that does not resolve."""
    feed = dotnet_blocker("/w/App.csproj : error NU1301: Failed to retrieve information about 'X'")
    unauthorised = dotnet_blocker("/w/App.csproj : error NU1303: Unable to load the service index")
    resolve = dotnet_blocker("/w/App.csproj : error NU1101: Unable to find package Acme.Widgets")

    assert feed is not None and feed.feed and feed.kind == "restore"
    assert unauthorised is not None and unauthorised.feed
    assert resolve is not None and not resolve.feed and resolve.kind == "restore"


_BLOCKS = "\n".join(
    [
        "  Passed Acme.Widgets.Tests.ParserTests.Accepts [1 ms]",
        "  Failed Acme.Widgets.Tests.ParserTests.Old [3 ms]",
        "  Error Message:",
        "   Assert.Equal() Failure: old and broken",
        "  Stack Trace:",
        "     at Acme.Widgets.Tests.ParserTests.Old() in /w/ParserTests.cs:line 9",
        "",
        "  Failed Acme.Widgets.Tests.ParserTests.New [2 ms]",
        "  Error Message:",
        "   Assert.Equal() Failure: the change broke this",
        "Failed!  - Failed:     2, Passed:     1, Skipped:     0, Total:     3",
    ]
)


def test_a_failure_block_is_removed_with_its_message_and_stack_and_nothing_else() -> None:
    stripped = strip_failure_blocks(_BLOCKS, ["Acme.Widgets.Tests.ParserTests.Old"])

    assert "old and broken" not in stripped and "ParserTests.cs:line 9" not in stripped
    assert "ParserTests.Old" not in stripped
    assert "the change broke this" in stripped  # the other block, whole
    assert "Failed Acme.Widgets.Tests.ParserTests.New" in stripped
    assert "Passed Acme.Widgets.Tests.ParserTests.Accepts" in stripped
    assert stripped.rstrip().endswith("Total:     3")  # the summary is not a block


def test_stripping_nothing_changes_nothing() -> None:
    assert strip_failure_blocks(_BLOCKS, []) == _BLOCKS
    assert strip_failure_blocks(_BLOCKS, ["Acme.Widgets.Tests.Missing"]) == _BLOCKS


# --- what real output looks like (B73 review): fixtures captured from real xunit runs ---------

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "dotnet"
_OLD_FIVE = (
    'OldTests.Th(a: 1, b: "x")',
    'OldTests.Th(a: 2, b: "y [z]")',
    "OldTests.Sum",
    "OldTests.StrDiff",
    "OldTests.Prints",
)


def _real(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_real_failing_tests_are_named_whole_including_quotes_and_brackets() -> None:
    assert dotnet_failing_tests(_real("xunit_failures.txt")) == _OLD_FIVE


def test_the_same_output_with_windows_line_endings_names_the_same_tests() -> None:
    crlf = _real("xunit_failures.txt").replace("\n", "\r\n")

    assert dotnet_failing_tests(crlf) == _OLD_FIVE  # not 'OldTests.Sum [< 1 ms]'


def test_message_text_that_looks_like_a_compiler_error_is_not_one_when_the_tests_ran() -> None:
    """A healthy build whose failing tests print `error CS0103: …` was reported as a red build."""
    out = _real("xunit_diagnostic_text.txt")

    assert dotnet_errors(out) == []
    assert dotnet_failing_tests(out) == ("DiagTests.Plain", "DiagTests.Snippet")
    assert digest_dotnet_output(out, None, cap=_CAP, returncode=1).startswith("FAILED TESTS (2)")


def test_an_error_line_with_words_before_the_path_is_not_a_position_less_error() -> None:
    assert dotnet_errors("Message: path : error ABC123: boom") == []
    assert dotnet_errors("   Expected: x : error CS0001: y") == []


def test_blank_lines_do_not_make_the_error_reader_quadratic() -> None:
    log = "\n" * 30_000 + "/w/App.csproj : error MSB4018: The task failed unexpectedly."

    started = time.perf_counter()
    errors = dotnet_errors(log)

    assert [e.code for e in errors] == ["MSB4018"]
    assert time.perf_counter() - started < 1.5  # 30,000 blank lines took ~10 s before


def test_stripping_every_old_failure_leaves_no_trace_of_it() -> None:
    """xunit prints `Expected:` / `Actual:` at column 0; the block used to end at the first of them."""
    stripped = strip_failure_blocks(_real("xunit_failures.txt"), _OLD_FIVE)

    for leftover in ("Expected:", "Actual:", "Stack Trace", "Tests.cs", "[FAIL]", "Values differ"):
        assert leftover not in stripped, leftover
    assert "Failed!  - Failed:     5, Passed:     1" in stripped  # the summary stays


def test_stripping_some_old_failures_keeps_the_others_whole() -> None:
    stripped = strip_failure_blocks(
        _real("xunit_failures.txt"), ["OldTests.Sum", 'OldTests.Th(a: 2, b: "y [z]")']
    )

    assert "Failed OldTests.Sum" not in stripped and "OldTests.Sum [FAIL]" not in stripped
    assert 'b: "y [z]")' not in stripped
    # the other blocks and lines are untouched, message and stack included
    assert 'Failed OldTests.Th(a: 1, b: "x")' in stripped
    assert "Failed OldTests.StrDiff" in stripped and 'Expected: "a\\nb"' in stripped
    assert "OldTests.Prints [FAIL]" in stripped and "bad: error CS0103: x" in stripped


def test_stripped_real_output_names_no_file_for_refine_to_edit(tmp_path: Path) -> None:
    (tmp_path / "Tests.cs").write_text("// test file\n", encoding="utf-8")
    out = _real("xunit_failures.txt").replace("/w/", f"{tmp_path}/")

    assert paths_named(out, tmp_path) == ["Tests.cs"]  # unstripped, the file is named
    assert paths_named(strip_failure_blocks(out, _OLD_FIVE), tmp_path) == []


def test_a_restore_error_quoted_in_test_output_is_not_an_environment_failure() -> None:
    """A test that asserts on a NuGet message must not make a healthy run look blocked."""
    out = "\n".join(
        [
            "  Failed Acme.Widgets.Tests.FeedTests.Reports [3 ms]",
            "  Error Message:",
            "   expected: /w/App.csproj : error NU1301: Failed to retrieve information",
            "Failed!  - Failed:     1, Passed:    40, Skipped:     0, Total:    41",
        ]
    )

    assert dotnet_blocker(out) is None
