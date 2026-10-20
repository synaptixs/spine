"""B65 (SSPN-146): codegen is told the oldest Python the repository declares it supports.

In the SWE-bench pilot 5 of 20 instances failed because the model wrote ``str | None``,
``list[str]`` and parenthesised ``with`` for repositories whose tests run on Python 3.6 or 3.8:
the tests could not be imported and refine spent its passes on syntax. Codegen never asked
which Python the repository targets. These tests pin the fix: ``declared_min_python`` reads the
*declaration* (never executes anything), ``RepoConventions.min_python`` carries it, and
``prompt_block`` renders one deterministic TARGET PYTHON paragraph listing only the features
newer than that minimum, even when no source file was sampled.

Written first and seen failing against the unchanged product (66 of 66); the new names were
then reached lazily so the file type-checked, and are imported directly now that they exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.codegen import LLMCodegenAdapter
from orchestrator.sdlc.conventions import RepoConventions, declared_min_python, extract_conventions
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
        (">3.8", (3, 8)),
        ("===3.9.1", (3, 9)),
    ],
)
def test_pyproject_requires_python_specifier_shapes(
    tmp_path: Path, specifier: str, expected: tuple[int, int]
) -> None:
    _write(tmp_path / "pyproject.toml", f'[project]\nname = "demo"\nrequires-python = "{specifier}"\n')
    assert declared_min_python(tmp_path) == expected


def test_setup_cfg_python_requires(tmp_path: Path) -> None:
    _write(tmp_path / "setup.cfg", "[options]\npython_requires = >=3.6\n")
    assert declared_min_python(tmp_path) == (3, 6)


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
    assert declared_min_python(tmp_path) == (3, 7)


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
    assert declared_min_python(tmp_path) == (3, 7)


def test_setup_py_format_template_with_no_classifiers_declares_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "setup.py", "setup(python_requires='>={}.{}'.format(*REQUIRED_PYTHON))\n")
    assert declared_min_python(tmp_path) is None


def test_setup_py_is_read_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    _write(
        tmp_path / "setup.py",
        f"open({str(marker)!r}, 'w').write('x')\nsetup(python_requires='>=3.8')\n",
    )
    assert declared_min_python(tmp_path) == (3, 8)
    assert not marker.exists()


def test_prose_and_comments_in_setup_py_are_not_declarations(tmp_path: Path) -> None:
    # Django's setup.py talks about "the python_requires classifier" in an error message, and a
    # commented-out line is not a declaration either. Only `python_requires = "<literal>"` counts.
    _write(
        tmp_path / "setup.py",
        "# python_requires='>=2.7'\n"
        'MESSAGE = """understand the python_requires classifier. Make sure you"""\n'
        "setup(python_requires='>=3.9')\n",
    )
    assert declared_min_python(tmp_path) == (3, 9)
    _write(
        tmp_path / "other" / "setup.py",
        "# python_requires='>=2.7'\nMESSAGE = 'the python_requires classifier'\n",
    )
    assert declared_min_python(tmp_path / "other") is None


def test_the_highest_lower_bound_is_the_floor(tmp_path: Path) -> None:
    # `!=3.0.*` and `<4` say nothing about the floor; two lower bounds intersect at the higher.
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = "!=3.0.*,>=3.5,>=3.7,<4"\n')
    assert declared_min_python(tmp_path) == (3, 7)


def test_lowest_classifier_wins_and_versions_sort_numerically(tmp_path: Path) -> None:
    # '3.10' < '3.9' as strings; the lowest supported version here is 3.9.
    _write(tmp_path / "setup.cfg", _classifiers_cfg("3.11", "3.10", "3.9"))
    assert declared_min_python(tmp_path) == (3, 9)


def test_classifiers_in_pyproject_and_setup_py(tmp_path: Path) -> None:
    pyproject = tmp_path / "a" / "pyproject.toml"
    _write(
        pyproject,
        '[project]\nname = "x"\nclassifiers = [\n    "Programming Language :: Python :: 3.9",\n'
        '    "Programming Language :: Python :: 3.8",\n]\n',
    )
    assert declared_min_python(pyproject.parent) == (3, 8)
    _write(
        tmp_path / "b" / "setup.py",
        "setup(classifiers=[\n"
        "    'Programming Language :: Python :: 3.7',\n"
        "    'Programming Language :: Python :: 3.6',\n])\n",
    )
    assert declared_min_python(tmp_path / "b") == (3, 6)


def test_bare_major_classifiers_are_ignored(tmp_path: Path) -> None:
    # 'Python :: 3' and 'Python :: 3 :: Only' carry no minor version; Django ships both.
    _write(
        tmp_path / "setup.cfg",
        "[metadata]\nclassifiers =\n    Programming Language :: Python\n"
        "    Programming Language :: Python :: 3\n    Programming Language :: Python :: 3 :: Only\n"
        "    Programming Language :: Python :: Implementation :: CPython\n",
    )
    assert declared_min_python(tmp_path) is None


def test_bare_major_classifier_does_not_hide_a_versioned_one(tmp_path: Path) -> None:
    _write(
        tmp_path / "setup.cfg",
        "[metadata]\nclassifiers =\n    Programming Language :: Python :: 3\n"
        "    Programming Language :: Python :: 3.8\n    Programming Language :: Python :: 3 :: Only\n",
    )
    assert declared_min_python(tmp_path) == (3, 8)


def test_python_2_declarations_are_read_as_they_are(tmp_path: Path) -> None:
    _write(tmp_path / "setup.py", "setup(python_requires='>=2.7')\n")
    assert declared_min_python(tmp_path) == (2, 7)
    _write(tmp_path / "other" / "setup.cfg", _classifiers_cfg("2.7", "3.4", "3.5", "3.6"))
    assert declared_min_python(tmp_path / "other") == (2, 7)


def test_a_repo_that_declares_nothing_yields_none(tmp_path: Path) -> None:
    assert declared_min_python(tmp_path) is None
    _write(tmp_path / "pyproject.toml", '[build-system]\nrequires = ["setuptools", "wheel"]\n')
    _write(tmp_path / "setup.cfg", "[metadata]\nname = demo\n")
    _write(tmp_path / "setup.py", "from setuptools import setup\nsetup()\n")
    assert declared_min_python(tmp_path) is None


# ---- malformed input is skipped, never raised ------------------------------


def test_invalid_toml_is_skipped_and_the_next_source_is_read(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project\nrequires-python = >=3.9 oops\n")
    assert declared_min_python(tmp_path) is None
    _write(tmp_path / "setup.cfg", "[options]\npython_requires = >=3.7\n")
    assert declared_min_python(tmp_path) == (3, 7)


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
    assert declared_min_python(tmp_path) == (3, 7)


def test_setup_cfg_without_a_section_header_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path / "setup.cfg", "python_requires = >=3.9\n")  # configparser rejects this
    assert declared_min_python(tmp_path) is None


def test_undecodable_pyproject_and_setup_cfg_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "setup.cfg").write_bytes(b"\x80\x81")
    assert declared_min_python(tmp_path) is None


def test_a_latin1_setup_py_with_a_coding_cookie_is_still_read(tmp_path: Path) -> None:
    # PEP 263 files exist in the wild (a non-ASCII author name); skipping one loses its declaration.
    body = "# -*- coding: latin-1 -*-\nsetup(author='Jos\xe9', python_requires='>=3.8')\n"
    (tmp_path / "setup.py").write_bytes(body.encode("latin-1"))
    assert declared_min_python(tmp_path) == (3, 8)


def test_a_utf8_bom_does_not_hide_a_declaration(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_bytes(b"\xef\xbb\xbf" + b'[project]\nrequires-python = ">=3.8"\n')
    (tmp_path / "setup.cfg").write_bytes(b"\xef\xbb\xbf" + b"[options]\npython_requires = >=3.12\n")
    assert declared_min_python(tmp_path) == (3, 8)  # pyproject, not the lower-ranked setup.cfg


def test_a_directory_named_like_a_declaration_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "setup.py").mkdir()
    assert declared_min_python(tmp_path) is None


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
    assert declared_min_python(tmp_path) == (3, 7)  # setup.cfg beats setup.py and every classifier
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.9"\n')
    assert declared_min_python(tmp_path) == (3, 9)  # pyproject beats all
    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = demo\n", encoding="utf-8")
    assert declared_min_python(tmp_path) == (3, 6)  # a literal in setup.py beats classifiers


def test_a_classifier_older_than_the_declared_minimum_does_not_lower_it(tmp_path: Path) -> None:
    # sympy-style staleness: classifiers go out of date, the packaging contract does not.
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.9"\n')
    _write(tmp_path / "setup.cfg", _classifiers_cfg("2.7", "3.4"))
    assert declared_min_python(tmp_path) == (3, 9)


def test_classifiers_are_pooled_across_files_when_nothing_else_declares(tmp_path: Path) -> None:
    _write(tmp_path / "setup.cfg", _classifiers_cfg("3.9"))
    _write(tmp_path / "setup.py", "setup(classifiers=['Programming Language :: Python :: 3.8'])\n")
    assert declared_min_python(tmp_path) == (3, 8)


# ---- extract_conventions carries it ----------------------------------------


def test_extract_conventions_fills_min_python(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.8"\n')
    _write(tmp_path / "src" / "app" / "m.py", "x = 1\n")
    assert extract_conventions(tmp_path).min_python == (3, 8)


def test_extract_conventions_fills_min_python_when_no_source_was_sampled(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.8"\n')
    conv = extract_conventions(tmp_path)
    assert conv.sampled == 0
    assert conv.min_python == (3, 8)


def test_min_python_defaults_to_none(tmp_path: Path) -> None:
    assert RepoConventions().min_python is None
    assert extract_conventions(tmp_path).min_python is None


# ---- rendering -------------------------------------------------------------


def test_python_36_lists_everything_newer_in_a_fixed_order() -> None:
    para = _paragraph(RepoConventions(min_python=(3, 6), sampled=1).prompt_block())
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
    para = _paragraph(RepoConventions(min_python=minimum, sampled=1).prompt_block())
    assert f"{minimum[0]}.{minimum[1]}" in para
    for anchor in present:
        assert anchor in para, anchor
    for anchor in absent:
        assert anchor not in para, anchor


def test_python_2_lists_every_python_3_feature() -> None:
    # D7: Python 2 idioms (print, unicode, six) are out of scope; the table is Python 3 only.
    para = _paragraph(RepoConventions(min_python=(2, 7), sampled=1).prompt_block())
    for anchor in _ALL_ANCHORS:
        assert anchor in para, anchor


def test_builtin_generics_are_phrased_as_runtime_evaluated() -> None:
    # With `from __future__ import annotations`, `list[str]` as an annotation is fine on 3.7+.
    # The wording must not forbid what the future import allows.
    para = _paragraph(RepoConventions(min_python=(3, 8), sampled=1).prompt_block())
    generics = para[para.index(_BUILTIN_GENERICS) - 70 : para.index(_BUILTIN_GENERICS) + 20]
    assert "runtime-evaluated" in generics, generics


def test_no_feature_newer_than_the_minimum_means_no_paragraph() -> None:
    plain = RepoConventions(line_length=120, sampled=1).prompt_block()
    for minimum in ((3, 12), (3, 13)):
        block = RepoConventions(min_python=minimum, sampled=1, line_length=120).prompt_block()
        assert _HEADER not in block
        assert block == plain  # byte-identical to today's block


def test_spines_own_repo_gets_no_paragraph() -> None:
    root = Path(__file__).resolve().parents[2]
    declared = extract_conventions(root).min_python
    assert declared is not None and declared >= (3, 12)  # read from Spine's own pyproject
    assert _HEADER not in _block(root)


def test_paragraph_is_present_when_no_source_file_was_sampled() -> None:
    block = RepoConventions(min_python=(3, 8), sampled=0).prompt_block()
    assert _HEADER in block
    assert _BUILTIN_GENERICS in block


def test_no_declaration_and_no_samples_still_renders_nothing() -> None:
    assert RepoConventions().prompt_block() == ""
    assert RepoConventions(min_python=None, sampled=0).prompt_block() == ""


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
    assert declared_min_python(tmp_path) == expected
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
    assert declared_min_python(tmp_path) == (3, 12)  # read, and then found to need no warning
    llm = _ScriptedLLM([_files_response({"feature.py": "x = 1\n"})])
    await _STAGES[stage](LLMCodegenAdapter(llm), tmp_path)

    assert _HEADER not in llm.calls[0][-1].content


# ---- review findings (B65 round 1): where the paragraph goes, and inputs that misled the parser ----


def test_understand_page_does_not_carry_the_codegen_instruction(tmp_path: Path) -> None:
    # `conventions.md` is for humans: the instruction is for the model, and rendered after the last
    # bullet with no blank line it read as part of "keep lines within 88 characters".
    from orchestrator.knowledge.renderers import render_conventions

    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.8"\n')
    for i in range(2):
        _write(tmp_path / "src" / "app" / f"m{i}.py", '"""Doc."""\n\nx = 1\n')
    page = render_conventions(tmp_path)
    assert "Do not use" not in page
    assert _HEADER not in page
    assert "keep lines within" in page  # the observed rules are still there


def test_understand_page_for_an_unsampled_repo_still_says_so(tmp_path: Path) -> None:
    from orchestrator.knowledge.renderers import render_conventions

    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.8"\n')
    assert "_No conventions sampled yet._" in render_conventions(tmp_path)


def test_prompt_block_can_leave_the_paragraph_out() -> None:
    conv = RepoConventions(min_python=(3, 6), sampled=1)
    assert _HEADER in conv.prompt_block()
    assert _HEADER not in conv.prompt_block(target_python=False)
    assert RepoConventions(min_python=(3, 6), sampled=0).prompt_block(target_python=False) == ""
    plain = RepoConventions(sampled=1).prompt_block()
    assert conv.prompt_block(target_python=False) == plain


def test_only_the_python_toolchain_gets_the_python_paragraph(tmp_path: Path) -> None:
    # A maturin/PyO3 or TypeScript-plus-Python-tooling repo must not tell a Rust or TypeScript
    # prompt that `match` and `tomllib` are unavailable.
    from orchestrator.sdlc.toolchains import TOOLCHAINS

    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.8"\n')
    assert _HEADER in TOOLCHAINS["python"].conventions(tmp_path, None)
    others = sorted(set(TOOLCHAINS) - {"python"})
    assert others  # the loop below is not vacuous
    for language in others:
        assert _HEADER not in TOOLCHAINS[language].conventions(tmp_path, None), language


@pytest.mark.parametrize(
    "body",
    [
        "x = 1  # python_requires='>=2.7'\nsetup(python_requires='>=3.9')\n",
        '"""Notes: python_requires=">=2.6" was dropped."""\nsetup(python_requires=">=3.9")\n',
        "setup(\n    # python_requires='>=2.7',\n    python_requires='>=3.9',\n)\n",
    ],
    ids=["trailing-comment", "docstring", "commented-line"],
)
def test_a_literal_inside_a_comment_or_docstring_is_not_the_declaration(tmp_path: Path, body: str) -> None:
    _write(tmp_path / "setup.py", body)
    assert declared_min_python(tmp_path) == (3, 9)


def test_a_docstring_alone_declares_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "setup.py", '"""python_requires=">=2.6" is old advice."""\nsetup()\n')
    assert declared_min_python(tmp_path) is None


def test_a_computed_setup_py_value_is_not_a_literal(tmp_path: Path) -> None:
    for expr in ('">=3.6" + EXTRA', '">=3.6" if OLD else ">=3.8"', '">=%d.%d" % V', 'f">={major}.6"', "V"):
        _write(tmp_path / "setup.py", f"setup(python_requires={expr})\n")
        assert declared_min_python(tmp_path) is None, expr


def test_python_requires_must_be_assigned_not_compared(tmp_path: Path) -> None:
    _write(tmp_path / "setup.py", 'check(python_requires == ">=2.6")\n')
    assert declared_min_python(tmp_path) is None


def test_a_py2_style_setup_py_is_still_read(tmp_path: Path) -> None:
    # Not valid Python 3 (a print statement), so it cannot be `ast.parse`d; the literal must still count.
    _write(tmp_path / "setup.py", "print 'building'\nsetup(python_requires='>=2.7')\n")
    assert declared_min_python(tmp_path) == (2, 7)


@pytest.mark.parametrize("name", ["setup.py", "setup.cfg", "pyproject.toml"])
def test_a_commented_out_classifier_does_not_lower_the_floor(tmp_path: Path, name: str) -> None:
    live, dead = "Programming Language :: Python :: 3.10", "Programming Language :: Python :: 2.7"
    bodies = {
        "setup.py": f"setup(classifiers=[\n    # '{dead}',\n    '{live}',\n])\n",
        "setup.cfg": f"[metadata]\nclassifiers =\n    # {dead}\n    {live}\n",
        "pyproject.toml": f'[project]\nclassifiers = [\n    # "{dead}",\n    "{live}",\n]\n',
    }
    _write(tmp_path / name, bodies[name])
    assert declared_min_python(tmp_path) == (3, 10)


@pytest.mark.parametrize(
    "body",
    ["[project]\nx = " + "9" * 5000 + "\n", "x = " + "[" * 100000 + "\n", 'project = "not a table"\n'],
    ids=["huge-integer", "deeply-nested", "project-not-a-table"],
)
def test_hostile_pyproject_is_skipped_not_raised(tmp_path: Path, body: str) -> None:
    _write(tmp_path / "pyproject.toml", body)
    _write(tmp_path / "setup.cfg", "[options]\npython_requires = >=3.7\n")
    assert declared_min_python(tmp_path) == (3, 7)


def test_absurdly_long_version_numbers_are_skipped_not_raised(tmp_path: Path) -> None:
    _write(tmp_path / "setup.cfg", "[options]\npython_requires = >=3." + "9" * 5000 + "\n")
    assert declared_min_python(tmp_path) is None
    _write(
        tmp_path / "setup.py",
        "setup(classifiers=['Programming Language :: Python :: 3." + "9" * 5000 + "'])\n",
    )
    assert declared_min_python(tmp_path) is None


# Every row of the feature table, pinned by an anchor and the release that introduces it. For
# a floor one minor below the row the anchor must be listed; at the row's own release it must
# not. The anchors are written here independently of the table so a row that moves, vanishes
# or is attributed to the wrong release fails.
_TABLE_ROWS = [
    ((3, 6), "f-strings"),
    ((3, 6), "variable annotations"),
    ((3, 7), "`from __future__ import annotations`"),
    ((3, 7), "`subprocess.run(text=...)`"),
    ((3, 7), "`capture_output=...`"),
    ((3, 8), "the walrus `:=`"),
    ((3, 8), "positional-only `/` parameters"),
    ((3, 8), "the f-string `=` specifier"),
    ((3, 8), "`typing.Literal`"),
    ((3, 8), "`Protocol`"),
    ((3, 8), "`TypedDict`"),
    ((3, 8), "`Final`"),
    ((3, 8), "`functools.cached_property`"),
    ((3, 9), "`list[str]`"),
    ((3, 9), "`dict | dict`"),
    ((3, 9), "`str.removeprefix`"),
    ((3, 9), "`zoneinfo`"),
    ((3, 10), "`X | Y`"),
    ((3, 10), "`match`"),
    ((3, 10), "parenthesised multi-item `with`"),
    ((3, 10), "`zip(strict=...)`"),
    ((3, 11), "`tomllib`"),
    ((3, 11), "`except*`"),
    ((3, 11), "`typing.Self`"),
    ((3, 11), "`enum.StrEnum`"),
    ((3, 12), "`type X = ...`"),
    ((3, 12), "`def f[T]`"),
]


@pytest.mark.parametrize(("release", "anchor"), _TABLE_ROWS, ids=[a for _, a in _TABLE_ROWS])
def test_every_table_row_is_listed_below_its_release_and_not_at_it(
    release: tuple[int, int], anchor: str
) -> None:
    below = RepoConventions(min_python=(release[0], release[1] - 1), sampled=1).prompt_block()
    at = RepoConventions(min_python=release, sampled=1).prompt_block()
    assert anchor in below, below
    assert anchor not in at, at


def test_a_floor_below_3_6_is_told_about_f_strings() -> None:
    para = _paragraph(RepoConventions(min_python=(3, 5), sampled=1).prompt_block())
    assert "f-strings" in para
    assert "variable annotations" in para


def test_a_3_6_floor_is_told_the_older_spelling_of_subprocess_text() -> None:
    """B70 (SSPN-153): a test written for a 3.6 repository used `subprocess.run(text=True)`, which 3.6
    rejects at call time (a TypeError), not at import time, so no syntax check could see it. The row
    carries the older spelling because the paragraph's generic tail only names typing and `with`."""
    para = _paragraph(RepoConventions(min_python=(3, 6), sampled=1).prompt_block())
    row = para[para.index("`subprocess.run(text=...)`") :].split(";")[0]
    assert "`capture_output=...`" in row, row
    assert "`universal_newlines=True`" in row, row
    assert "`stdout=subprocess.PIPE, stderr=subprocess.PIPE`" in row, row
    at_3_7 = RepoConventions(min_python=(3, 7), sampled=1).prompt_block()
    assert "subprocess" not in at_3_7, at_3_7


def test_unions_are_phrased_as_runtime_evaluated_too() -> None:
    # `str | None` annotations are legal on 3.7-3.9 under `from __future__ import annotations`.
    para = _paragraph(RepoConventions(min_python=(3, 8), sampled=1).prompt_block())
    union = para[para.index(_UNION) : para.index(_UNION) + 90]
    assert "runtime-evaluated" in union, union


# ---- review round 2 ----


@pytest.mark.parametrize(
    "prose",
    [
        '"""Drops Programming Language :: Python :: 2.7 support."""',
        'MESSAGE = "remove Programming Language :: Python :: 3.5 first"',
        "DROPPED = ['Programming Language :: Python :: 2.7']  # not a list of supported versions",
    ],
    ids=["docstring", "message", "removal-list"],
)
def test_a_string_that_merely_mentions_a_classifier_does_not_set_the_floor(
    tmp_path: Path, prose: str
) -> None:
    # Only a string that IS a classifier counts; prose about one is not a declaration.
    live = "'Programming Language :: Python :: 3.9'"
    _write(tmp_path / "setup.py", f"{prose}\nsetup(classifiers=[{live}])\n")
    assert declared_min_python(tmp_path) == (3, 9)
    _write(tmp_path / "other" / "setup.py", f"{prose}\nsetup()\n")
    assert declared_min_python(tmp_path / "other") is None


def test_a_tokenizer_error_later_in_setup_py_does_not_lose_an_earlier_literal(tmp_path: Path) -> None:
    for tail in ("foo(\n", "x = 'unterminated\n", "if 1:\n        a\n    b\n"):
        _write(tmp_path / "setup.py", f"setup(python_requires='>=3.8')\n{tail}")
        assert declared_min_python(tmp_path) == (3, 8), repr(tail)


def test_a_trailing_comment_after_the_literal_is_fine(tmp_path: Path) -> None:
    _write(tmp_path / "setup.py", "setup(python_requires='>=3.9'  # keep in sync\n)\n")
    assert declared_min_python(tmp_path) == (3, 9)
    _write(tmp_path / "b" / "setup.py", "python_requires = '>=3.9'  # keep in sync\n")
    assert declared_min_python(tmp_path / "b") == (3, 9)


def test_setup_cfg_with_repeated_keys_and_sections_is_still_read(tmp_path: Path) -> None:
    # A repeated [flake8] section or option is common in a hand-edited setup.cfg.
    _write(
        tmp_path / "setup.cfg",
        "[flake8]\nmax-line-length = 100\n\n[flake8]\nignore = E501\nignore = W503\n\n"
        "[options]\npython_requires = >=3.8\n",
    )
    assert declared_min_python(tmp_path) == (3, 8)


def test_an_absurd_version_in_one_clause_does_not_hide_the_others(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", f'[project]\nrequires-python = ">=3.8,>=3.{"9" * 5000}"\n')
    assert declared_min_python(tmp_path) == (3, 8)


def test_poetry_classifiers_are_read(tmp_path: Path) -> None:
    _write(
        tmp_path / "pyproject.toml",
        '[tool.poetry]\nname = "x"\nclassifiers = ["Programming Language :: Python :: 3.7"]\n',
    )
    assert declared_min_python(tmp_path) == (3, 7)
