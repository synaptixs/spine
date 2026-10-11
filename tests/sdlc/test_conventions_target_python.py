"""B65 (SSPN-146): codegen is told the oldest Python the repository declares it supports.

In the SWE-bench pilot 5 of 20 instances failed because the model wrote ``str | None``,
``list[str]`` and parenthesised ``with`` for repositories whose tests run on Python 3.6 or 3.8:
the tests could not be imported and refine spent its passes on syntax. Codegen never asked
which Python the repository targets. These tests pin the fix: ``declared_min_python`` reads the
*declaration* (never executes anything), ``RepoConventions.min_python`` carries it, and
``prompt_block`` renders one deterministic TARGET PYTHON paragraph listing only the features
newer than that minimum, even when no source file was sampled.

The new names are reached through ``_declared`` / ``_conv`` / ``_field`` below so that the
file type-checks (``mypy src tests`` is a CI gate) while the API is still being added.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.codegen import LLMCodegenAdapter
from orchestrator.sdlc.conventions import RepoConventions, extract_conventions
from tests.sdlc.test_codegen import _SPEC, _files_response, _ScriptedLLM

_HEADER = "TARGET PYTHON"

# Anchors into the paragraph: the construct as the model would write it. They are the
# contract with the model, not the prose around them.
_WALRUS = "`:=`"
_POSITIONAL_ONLY = "positional-only"
_BUILTIN_GENERICS = "`list[str]`"
_UNION = "`X | Y`"
_MATCH = "`match`"
_PAREN_WITH = "parenthesised multi-item `with`"
_TOMLLIB = "`tomllib`"
_ALL_ANCHORS = (_WALRUS, _POSITIONAL_ONLY, _BUILTIN_GENERICS, _UNION, _MATCH, _PAREN_WITH, _TOMLLIB)


# ---- helpers ---------------------------------------------------------------


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _declared(root: Path) -> tuple[int, int] | None:
    module = importlib.import_module("orchestrator.sdlc.conventions")
    result: tuple[int, int] | None = module.declared_min_python(root)
    return result


def _conv(**fields: Any) -> RepoConventions:
    return RepoConventions(**fields)


def _field(conv: RepoConventions, name: str) -> Any:
    return getattr(conv, name)


def _classifiers_cfg(*versions: str) -> str:
    lines = "".join(f"    Programming Language :: Python :: {v}\n" for v in versions)
    return f"[metadata]\nname = demo\nclassifiers =\n{lines}"


def _block(root: Path) -> str:
    return extract_conventions(root).prompt_block()


def _paragraph(block: str) -> str:
    """The TARGET PYTHON paragraph alone, so a mention elsewhere cannot satisfy a test."""
    assert _HEADER in block, block
    return block[block.index(_HEADER) :].split("\n\n")[0]


# ---- declaration shapes ----------------------------------------------------


@pytest.mark.parametrize(
    ("specifier", "expected"),
    [
        (">=3.8", (3, 8)),
        (">=3.8,<4", (3, 8)),
        ("<4,>=3.9", (3, 9)),
        ("~=3.8", (3, 8)),
        ("==3.9.*", (3, 9)),
        (">=3.10.4", (3, 10)),
        (" >= 3.7 ", (3, 7)),
        (">=3.9, !=3.10.*", (3, 9)),
    ],
)
def test_pyproject_requires_python_specifier_shapes(
    tmp_path: Path, specifier: str, expected: tuple[int, int]
) -> None:
    _write(tmp_path / "pyproject.toml", f'[project]\nname = "demo"\nrequires-python = "{specifier}"\n')
    assert _declared(tmp_path) == expected


def test_setup_cfg_python_requires(tmp_path: Path) -> None:
    _write(tmp_path / "setup.cfg", "[options]\npython_requires = >=3.6\n")
    assert _declared(tmp_path) == (3, 6)


@pytest.mark.parametrize(
    "line",
    [
        'python_requires=">=3.7",',
        "python_requires='>=3.7',",
        'python_requires = ">=3.7",',
        'python_requires=">=3.7"',
    ],
)
def test_setup_py_literal_python_requires(tmp_path: Path, line: str) -> None:
    _write(tmp_path / "setup.py", f"from setuptools import setup\nsetup(\n    name='demo',\n    {line}\n)\n")
    assert _declared(tmp_path) == (3, 7)


def test_setup_py_format_template_has_no_digits_and_falls_through_to_classifiers(tmp_path: Path) -> None:
    # Django 3.0/3.1 build the specifier at run time; the digits live in a tuple, not in the call.
    _write(
        tmp_path / "setup.py",
        "REQUIRED_PYTHON = (3, 6)\n"
        "setup(\n    python_requires='>={}.{}'.format(*REQUIRED_PYTHON),\n"
        "    classifiers=[\n        'Programming Language :: Python :: 3.7',\n"
        "        'Programming Language :: Python :: 3.8',\n    ],\n)\n",
    )
    # Not (3, 6): the tuple is code, and only a literal specifier is read. Classifiers rank last.
    assert _declared(tmp_path) == (3, 7)


def test_setup_py_format_template_with_no_classifiers_declares_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "setup.py", "setup(python_requires='>={}.{}'.format(*REQUIRED_PYTHON))\n")
    assert _declared(tmp_path) is None


def test_setup_py_is_read_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    _write(
        tmp_path / "setup.py",
        f"open({str(marker)!r}, 'w').write('x')\nsetup(python_requires='>=3.8')\n",
    )
    assert _declared(tmp_path) == (3, 8)
    assert not marker.exists()


def test_lowest_classifier_wins_and_versions_sort_numerically(tmp_path: Path) -> None:
    # '3.10' < '3.9' as strings; the lowest supported version here is 3.9.
    _write(tmp_path / "setup.cfg", _classifiers_cfg("3.11", "3.10", "3.9"))
    assert _declared(tmp_path) == (3, 9)


def test_classifiers_in_pyproject_and_setup_py(tmp_path: Path) -> None:
    pyproject = tmp_path / "a" / "pyproject.toml"
    _write(
        pyproject,
        '[project]\nname = "x"\nclassifiers = [\n    "Programming Language :: Python :: 3.9",\n'
        '    "Programming Language :: Python :: 3.8",\n]\n',
    )
    assert _declared(pyproject.parent) == (3, 8)
    _write(
        tmp_path / "b" / "setup.py",
        "setup(classifiers=[\n"
        "    'Programming Language :: Python :: 3.7',\n"
        "    'Programming Language :: Python :: 3.6',\n])\n",
    )
    assert _declared(tmp_path / "b") == (3, 6)


def test_bare_major_classifiers_are_ignored(tmp_path: Path) -> None:
    # 'Python :: 3' and 'Python :: 3 :: Only' carry no minor version; Django ships both.
    _write(
        tmp_path / "setup.cfg",
        "[metadata]\nclassifiers =\n    Programming Language :: Python\n"
        "    Programming Language :: Python :: 3\n    Programming Language :: Python :: 3 :: Only\n"
        "    Programming Language :: Python :: Implementation :: CPython\n",
    )
    assert _declared(tmp_path) is None


def test_bare_major_classifier_does_not_hide_a_versioned_one(tmp_path: Path) -> None:
    _write(
        tmp_path / "setup.cfg",
        "[metadata]\nclassifiers =\n    Programming Language :: Python :: 3\n"
        "    Programming Language :: Python :: 3.8\n    Programming Language :: Python :: 3 :: Only\n",
    )
    assert _declared(tmp_path) == (3, 8)


def test_python_2_declarations_are_read_as_they_are(tmp_path: Path) -> None:
    _write(tmp_path / "setup.py", "setup(python_requires='>=2.7')\n")
    assert _declared(tmp_path) == (2, 7)
    _write(tmp_path / "other" / "setup.cfg", _classifiers_cfg("2.7", "3.4", "3.5", "3.6"))
    assert _declared(tmp_path / "other") == (2, 7)


def test_a_repo_that_declares_nothing_yields_none(tmp_path: Path) -> None:
    assert _declared(tmp_path) is None
    _write(tmp_path / "pyproject.toml", '[build-system]\nrequires = ["setuptools", "wheel"]\n')
    _write(tmp_path / "setup.cfg", "[metadata]\nname = demo\n")
    _write(tmp_path / "setup.py", "from setuptools import setup\nsetup()\n")
    assert _declared(tmp_path) is None


# ---- malformed input is skipped, never raised ------------------------------


def test_invalid_toml_is_skipped_and_the_next_source_is_read(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project\nrequires-python = >=3.9 oops\n")
    assert _declared(tmp_path) is None
    _write(tmp_path / "setup.cfg", "[options]\npython_requires = >=3.7\n")
    assert _declared(tmp_path) == (3, 7)


@pytest.mark.parametrize(
    "body",
    [
        "[project]\nrequires-python = 3.9\n",  # a float, not a string
        '[project]\nrequires-python = ""\n',
        '[project]\nrequires-python = "any"\n',
        '[project]\nrequires-python = [">=3.9"]\n',
        "requires-python = '>=3.9'\n",  # outside [project]
    ],
)
def test_unusable_requires_python_values_are_skipped(tmp_path: Path, body: str) -> None:
    _write(tmp_path / "pyproject.toml", body)
    _write(tmp_path / "setup.cfg", "[options]\npython_requires = >=3.7\n")
    assert _declared(tmp_path) == (3, 7)


def test_setup_cfg_without_a_section_header_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path / "setup.cfg", "python_requires = >=3.9\n")  # configparser rejects this
    assert _declared(tmp_path) is None


def test_undecodable_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_bytes(b"\xff\xfe\x00 python_requires='>=3.9'")
    (tmp_path / "pyproject.toml").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "setup.cfg").write_bytes(b"\x80\x81")
    assert _declared(tmp_path) is None


def test_a_directory_named_like_a_declaration_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "setup.py").mkdir()
    assert _declared(tmp_path) is None


# ---- precedence (D2) -------------------------------------------------------


def test_precedence_is_pyproject_then_setup_cfg_then_setup_py_then_classifiers(tmp_path: Path) -> None:
    _write(
        tmp_path / "setup.cfg",
        "[metadata]\nclassifiers =\n    Programming Language :: Python :: 3.5\n\n"
        "[options]\npython_requires = >=3.7\n",
    )
    _write(
        tmp_path / "setup.py",
        "setup(python_requires='>=3.6', classifiers=['Programming Language :: Python :: 3.4'])\n",
    )
    assert _declared(tmp_path) == (3, 7)  # setup.cfg beats setup.py and every classifier
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.9"\n')
    assert _declared(tmp_path) == (3, 9)  # pyproject beats all
    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = demo\n", encoding="utf-8")
    assert _declared(tmp_path) == (3, 6)  # a literal in setup.py beats classifiers


def test_a_classifier_older_than_the_declared_minimum_does_not_lower_it(tmp_path: Path) -> None:
    # sympy-style staleness: classifiers go out of date, the packaging contract does not.
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.9"\n')
    _write(tmp_path / "setup.cfg", _classifiers_cfg("2.7", "3.4"))
    assert _declared(tmp_path) == (3, 9)


def test_classifiers_are_pooled_across_files_when_nothing_else_declares(tmp_path: Path) -> None:
    _write(tmp_path / "setup.cfg", _classifiers_cfg("3.9"))
    _write(tmp_path / "setup.py", "setup(classifiers=['Programming Language :: Python :: 3.8'])\n")
    assert _declared(tmp_path) == (3, 8)


# ---- extract_conventions carries it ----------------------------------------


def test_extract_conventions_fills_min_python(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.8"\n')
    _write(tmp_path / "src" / "app" / "m.py", "x = 1\n")
    assert _field(extract_conventions(tmp_path), "min_python") == (3, 8)


def test_extract_conventions_fills_min_python_when_no_source_was_sampled(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.8"\n')
    conv = extract_conventions(tmp_path)
    assert conv.sampled == 0
    assert _field(conv, "min_python") == (3, 8)


def test_min_python_defaults_to_none(tmp_path: Path) -> None:
    assert _field(RepoConventions(), "min_python") is None
    assert _field(extract_conventions(tmp_path), "min_python") is None


# ---- rendering -------------------------------------------------------------


def test_python_36_lists_everything_newer_in_a_fixed_order() -> None:
    para = _paragraph(_conv(min_python=(3, 6), sampled=1).prompt_block())
    assert "3.6" in para
    for anchor in _ALL_ANCHORS:
        assert anchor in para, anchor
    # ascending by the release that introduced it
    order = [para.index(a) for a in (_WALRUS, _BUILTIN_GENERICS, _UNION, _TOMLLIB)]
    assert order == sorted(order)


@pytest.mark.parametrize(
    ("minimum", "absent", "present"),
    [
        ((3, 8), (_WALRUS, _POSITIONAL_ONLY), (_BUILTIN_GENERICS, _UNION, _MATCH, _PAREN_WITH, _TOMLLIB)),
        ((3, 9), (_WALRUS, _BUILTIN_GENERICS), (_UNION, _MATCH, _PAREN_WITH, _TOMLLIB)),
        ((3, 10), (_WALRUS, _BUILTIN_GENERICS, _UNION, _MATCH, _PAREN_WITH), (_TOMLLIB,)),
    ],
)
def test_only_features_newer_than_the_minimum_are_listed(
    minimum: tuple[int, int], absent: tuple[str, ...], present: tuple[str, ...]
) -> None:
    para = _paragraph(_conv(min_python=minimum, sampled=1).prompt_block())
    assert f"{minimum[0]}.{minimum[1]}" in para
    for anchor in present:
        assert anchor in para, anchor
    for anchor in absent:
        assert anchor not in para, anchor


def test_python_2_lists_every_python_3_feature() -> None:
    # D7: Python 2 idioms (print, unicode, six) are out of scope; the table is Python 3 only.
    para = _paragraph(_conv(min_python=(2, 7), sampled=1).prompt_block())
    for anchor in _ALL_ANCHORS:
        assert anchor in para, anchor


def test_builtin_generics_are_phrased_as_runtime_evaluated() -> None:
    # With `from __future__ import annotations`, `list[str]` as an annotation is fine on 3.7+.
    # The wording must not forbid what the future import allows.
    para = _paragraph(_conv(min_python=(3, 8), sampled=1).prompt_block())
    assert "runtime-evaluated" in para


def test_no_feature_newer_than_the_minimum_means_no_paragraph() -> None:
    plain = RepoConventions(line_length=120, sampled=1).prompt_block()
    for minimum in ((3, 12), (3, 13)):
        block = _conv(min_python=minimum, sampled=1, line_length=120).prompt_block()
        assert _HEADER not in block
        assert block == plain  # byte-identical to today's block


def test_spines_own_repo_gets_no_paragraph() -> None:
    root = Path(__file__).resolve().parents[2]
    assert _field(extract_conventions(root), "min_python") == (3, 12)
    assert _HEADER not in _block(root)


def test_paragraph_is_present_when_no_source_file_was_sampled() -> None:
    block = _conv(min_python=(3, 8), sampled=0).prompt_block()
    assert _HEADER in block
    assert _BUILTIN_GENERICS in block


def test_no_declaration_and_no_samples_still_renders_nothing() -> None:
    assert RepoConventions().prompt_block() == ""
    assert _conv(min_python=None, sampled=0).prompt_block() == ""


def test_paragraph_is_appended_to_the_observed_conventions_not_a_replacement(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nrequires-python = ">=3.8"\n\n[tool.ruff]\nline-length = 110\n',
    )
    for i in range(3):
        _write(tmp_path / "src" / "app" / f"m{i}.py", '"""Doc."""\n\nfrom __future__ import annotations\n')
    block = _block(tmp_path)
    assert "REPO CONVENTIONS" in block
    assert "110 characters" in block
    assert _HEADER in block


def test_block_is_byte_identical_across_runs_and_file_creation_order(tmp_path: Path) -> None:
    def build(root: Path, order: list[str]) -> None:
        for name in order:
            if name == "pyproject.toml":
                _write(root / name, '[project]\nrequires-python = ">=3.8,<4"\n')
            else:
                _write(root / "src" / "app" / name, '"""Doc."""\n\nx = 1\n')

    first, second = tmp_path / "one", tmp_path / "two"
    build(first, ["pyproject.toml", "a.py", "b.py"])
    build(second, ["b.py", "a.py", "pyproject.toml"])
    assert _block(first) == _block(first)
    assert _block(first) == _block(second)
    assert _HEADER in _block(first)


# ---- the five instances that failed in the pilot ---------------------------
# Declaration lines are verbatim from the SWE-bench instance images (probe run on 2026-10-11,
# ~/plans/swebench-pilot/py_decl_snippets.txt); the lines around them are elided.

_DJANGO_30_SETUP_PY = """\
CURRENT_PYTHON = sys.version_info[:2]
REQUIRED_PYTHON = (3, 6)
if CURRENT_PYTHON < REQUIRED_PYTHON:
    sys.stderr.write(\"\"\"
understand the python_requires classifier. Make sure you
\"\"\".format(*(REQUIRED_PYTHON + CURRENT_PYTHON)))
    sys.exit(1)
setup(
    python_requires='>={}.{}'.format(*REQUIRED_PYTHON),
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3 :: Only',
    ],
)
"""
_DJANGO_32_SETUP_PY = _DJANGO_30_SETUP_PY.split("setup(")[0]  # no setup() call: it moved to setup.cfg
_DJANGO_32_SETUP_CFG = (
    "[metadata]\nname = Django\nclassifiers =\n    Programming Language :: Python\n"
    "    Programming Language :: Python :: 3\n    Programming Language :: Python :: 3 :: Only\n"
    "    Programming Language :: Python :: 3.6\n    Programming Language :: Python :: 3.7\n"
    "    Programming Language :: Python :: 3.8\n\n[options]\npython_requires = >=3.6\n"
)
_DJANGO_40_SETUP_CFG = (
    "[metadata]\nname = Django\nclassifiers =\n    Programming Language :: Python\n"
    "    Programming Language :: Python :: 3\n    Programming Language :: Python :: 3 :: Only\n"
    "    Programming Language :: Python :: 3.8\n    Programming Language :: Python :: 3.9\n"
    "\n[options]\npython_requires = >=3.8\n"
)
# Representative, not verbatim: the probe found no declaration in Django 4.x's setup.py and
# pyproject.toml, only a build-system table.
_BUILD_ONLY_PYPROJECT = (
    '[build-system]\nrequires = ["setuptools>=40.8.0", "wheel"]\nbuild-backend = "setuptools.build_meta"\n'
)
_BUILD_ONLY_SETUP_PY = "from setuptools import setup\n\nsetup()\n"

_FAILING_INSTANCES: dict[str, tuple[dict[str, str], tuple[int, int]]] = {
    "django-11138": ({"setup.py": _DJANGO_30_SETUP_PY}, (3, 6)),
    "django-13279": ({"setup.py": _DJANGO_32_SETUP_PY, "setup.cfg": _DJANGO_32_SETUP_CFG}, (3, 6)),
    "django-13658": ({"setup.py": _DJANGO_32_SETUP_PY, "setup.cfg": _DJANGO_32_SETUP_CFG}, (3, 6)),
    "django-14434": (
        {
            "setup.py": _BUILD_ONLY_SETUP_PY,
            "setup.cfg": _DJANGO_40_SETUP_CFG,
            "pyproject.toml": _BUILD_ONLY_PYPROJECT,
        },
        (3, 8),
    ),
    "django-15368": (
        {
            "setup.py": _BUILD_ONLY_SETUP_PY,
            "setup.cfg": _DJANGO_40_SETUP_CFG,
            "pyproject.toml": _BUILD_ONLY_PYPROJECT,
        },
        (3, 8),
    ),
}


@pytest.mark.parametrize("instance", sorted(_FAILING_INSTANCES))
def test_the_pilots_failing_instances_are_told_to_avoid_what_broke_them(
    tmp_path: Path, instance: str
) -> None:
    files, expected = _FAILING_INSTANCES[instance]
    for name, body in files.items():
        _write(tmp_path / name, body)
    _write(tmp_path / "django" / "core" / "mod.py", "x = 1\n")
    assert _declared(tmp_path) == expected
    para = _paragraph(_block(tmp_path))
    # The constructs the refine summaries named: PEP 604 unions, builtin generics, paren `with`.
    for anchor in (_UNION, _BUILTIN_GENERICS, _PAREN_WITH):
        assert anchor in para, anchor


# ---- the paragraph reaches every code-writing prompt -----------------------


def _py36_repo(root: Path, *, with_sources: bool) -> Path:
    _write(root / "pyproject.toml", '[project]\nname = "demo"\nrequires-python = ">=3.6"\n')
    if with_sources:
        for i in range(3):
            _write(root / "src" / "app" / f"m{i}.py", '"""Doc."""\n\nx = 1\n')
    return root


async def _implement(adapter: LLMCodegenAdapter, root: Path) -> None:
    await adapter.implement(spec=_SPEC, path=str(root), issue_key="B-65")


async def _author_tests(adapter: LLMCodegenAdapter, root: Path) -> None:
    await adapter.author_tests(spec=_SPEC, path=str(root), issue_key="B-65")


async def _refine(adapter: LLMCodegenAdapter, root: Path) -> None:
    await adapter.refine(spec=_SPEC, path=str(root), issue_key="B-65", failures="E   assert 1 == 2")


async def _revise(adapter: LLMCodegenAdapter, root: Path) -> None:
    await adapter.revise(spec=_SPEC, path=str(root), issue_key="B-65", blockers=["criterion 1"])


_STAGES = {"implement": _implement, "author_tests": _author_tests, "refine": _refine, "revise": _revise}


@pytest.mark.parametrize("with_sources", [True, False], ids=["sampled", "unsampled"])
@pytest.mark.parametrize("stage", sorted(_STAGES))
async def test_target_python_reaches_the_prompt_of_every_code_writing_stage(
    tmp_path: Path, stage: str, with_sources: bool
) -> None:
    root = _py36_repo(tmp_path, with_sources=with_sources)
    llm = _ScriptedLLM([_files_response({"feature.py": "x = 1\n"})])
    await _STAGES[stage](LLMCodegenAdapter(llm), root)

    prompt = llm.calls[0][-1].content
    assert _HEADER in prompt
    assert "3.6" in _paragraph(prompt)
    assert _UNION in prompt
    assert prompt.count(_HEADER) == 1


@pytest.mark.parametrize("stage", sorted(_STAGES))
async def test_a_repo_that_requires_a_current_python_gets_no_paragraph_in_any_stage(
    tmp_path: Path, stage: str
) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\nrequires-python = ">=3.12"\n')
    _write(tmp_path / "src" / "app" / "m.py", '"""Doc."""\n\nx = 1\n')
    assert _declared(tmp_path) == (3, 12)  # read, and then found to need no warning
    llm = _ScriptedLLM([_files_response({"feature.py": "x = 1\n"})])
    await _STAGES[stage](LLMCodegenAdapter(llm), tmp_path)

    assert _HEADER not in llm.calls[0][-1].content
