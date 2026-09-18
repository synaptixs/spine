"""Cross-surface registry contracts that the language-specific codegen tests cannot cover."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.catalog.catalog import CapabilityCatalog
from orchestrator.catalog.skills import NATIVE_SKILLS
from orchestrator.sdlc.feature_runner import _resolve_language
from orchestrator.sdlc.preflight import PhpPreflightRunner, SubprocessPreflightRunner, make_preflight_runner
from orchestrator.sdlc.toolchains import TOOLCHAINS


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        (set(), "python"),
        ({"sql"}, "python"),
        ({"perl"}, "perl"),
        ({"python", "java", "php"}, "python"),
        ({"java", "typescript"}, "java"),
        ({"typescript", "csharp"}, "typescript"),
        ({"csharp", "php"}, "csharp"),
        ({"php", "go"}, "php"),
        ({"go", "cpp"}, "go"),
        ({"cpp", "c"}, "cpp"),
        ({"c"}, "c"),
    ],
)
def test_auto_language_precedence_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, languages: set[str], expected: str
) -> None:
    # Equal counts — the precedence this test pins is the tie rule, which `detect_language`
    # keeps: Python first, then `auto_priority`. The resolver now reads counts, not a set.
    monkeypatch.setattr(
        "orchestrator.catalog.profile.language_file_counts",
        lambda root: dict.fromkeys(languages, 1),
    )
    assert _resolve_language(tmp_path, "auto") == expected


def test_every_languages_conventions_capability_resolves_to_a_skill() -> None:
    """Replaces a check on `Toolchain.conventions_skill_id`, removed in P10 as dead code.

    The invariant it guarded is real and belongs on the path codegen actually uses: the
    planner selects by capability, so a `<language>-conventions` capability that resolves to
    nothing reaches a run as an id with no text behind it — guidance that silently is not
    there. Not every language has one (SQL ships without), so a missing capability is fine;
    a *registered* one that resolves to nothing is not.
    """
    catalog = CapabilityCatalog.from_sources()
    skills = {skill.id: skill for skill in NATIVE_SKILLS}
    checked: list[str] = []
    problems: list[str] = []
    for language in TOOLCHAINS:
        capability = catalog.get(f"{language}-conventions")
        if capability is None:
            continue  # not every language has one — SQL ships without, and that is fine
        checked.append(language)
        skill = skills.get(capability.id)
        if skill is None:
            problems.append(f"{capability.id}: selected by the planner, no skill behind the id")
        elif not skill.guidance.strip():
            problems.append(f"{capability.id}: a skill with no guidance is an empty prompt")
        if language not in (capability.selector.languages or frozenset()):
            problems.append(f"{capability.id}: selector does not name {language}, so it is never chosen")
    assert problems == []
    # Non-vacuity. The previous version of this test filtered the catalog by membership of
    # `_SEED` — and the catalog is *built* from `_SEED`, so `dangling` was unconditionally
    # empty and the body could have been deleted without failing anything.
    assert len(checked) >= len(TOOLCHAINS) - 1, f"only {checked} were examined"


def test_preflight_factory_preserves_interpreter_selection() -> None:
    python = make_preflight_runner("python", executable="/custom/python")
    php = make_preflight_runner("php", executable="/custom/php")
    assert isinstance(python, SubprocessPreflightRunner)
    assert python._python == "/custom/python"
    assert isinstance(php, PhpPreflightRunner)
    assert php._php == "/custom/php"


# ---- what a repository *is* — counts, not presence (CB-686) -------------------------------


def test_a_stray_python_file_does_not_make_a_typescript_app_python() -> None:
    from orchestrator.sdlc.toolchains import detect_language

    assert detect_language({"typescript": 300, "python": 5}) == "typescript"
    assert detect_language({"python": 40, "javascript": 3}) == "python"
    # A C++ tree with a build script is a C++ repository. What must not happen is the vendored
    # `ios/Pods` counting at all — that is the ignore rule's job, upstream of this one.
    assert detect_language({"cpp": 500, "python": 2}) == "cpp"
    assert detect_language({"sql": 20}) == "python"  # nothing a toolchain exists for → the default
    assert detect_language({}) == "python"


def test_ties_keep_the_old_order_python_then_priority() -> None:
    from orchestrator.sdlc.toolchains import detect_language

    assert detect_language({"python": 1, "go": 1}) == "python"
    assert detect_language({"typescript": 2, "java": 2}) == "java"  # java's auto_priority is lower


def test_resolve_language_prefers_the_graph_when_one_is_in_hand(tmp_path: Path) -> None:
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
    from orchestrator.sdlc.toolchains import resolve_language

    (tmp_path / "build.py").write_text("x = 1\n", encoding="utf-8")
    b = FactBatch()
    for i in range(3):
        b.add_node(
            Node(f"ts:app/f{i}", NodeKind.MODULE, f"app/f{i}", "typescript", Provenance(f"app/f{i}.ts", 1))
        )
    b.add_node(Node("py:build", NodeKind.MODULE, "build", "python", Provenance("build.py", 1)))
    assert resolve_language(tmp_path, "auto", store=FactStore(b)) == "typescript"
    assert resolve_language(tmp_path, "auto") == "python"  # the tree alone holds one .py
    assert resolve_language(tmp_path, "go") == "go"


def test_the_react_native_shape_resolves_to_typescript_from_the_tree(tmp_path: Path) -> None:
    from orchestrator.sdlc.toolchains import resolve_language

    (tmp_path / "src").mkdir()
    for i in range(4):
        (tmp_path / "src" / f"s{i}.tsx").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "postinstall.py").write_text("x = 1\n", encoding="utf-8")
    assert resolve_language(tmp_path, "auto") == "typescript"


def test_a_tie_between_two_toolchains_is_broken_by_name_not_by_walk_order() -> None:
    """`cpp` and `kotlin` both sit at auto_priority 5, so an equal count fell through to the
    order the walk filled the counts dict — adding one file flipped the scaffold silently."""
    from orchestrator.sdlc.toolchains import detect_language

    assert detect_language({"kotlin": 40, "cpp": 40}) == detect_language({"cpp": 40, "kotlin": 40})
