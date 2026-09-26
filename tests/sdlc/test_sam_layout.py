"""Python layout follows an existing repository instead of scaffolding beside it (CB-764).

A SAM repository keeps each Lambda function in its template's `CodeUri` directory, with no
`__init__.py`. Layout detection saw no package, and `--layout auto` scaffolded
`src/cannabee_crud_apis/` and a root `pyproject.toml` into it — code nothing deploys, and a pytest
config for the whole repo. The test env never installed `boto3`, which Lambda provides.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.language_guidance import python_guidance
from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.sam import function_dirs
from orchestrator.sdlc.testenv import _project_dependencies

_TEMPLATE = """\
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Resources:
  LicenceScraperFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/licence_scraper/
      Handler: app.lambda_handler
      Role: !GetAtt ScraperRole.Arn
  CrudFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: "src/crud"   # quoted, commented
      Handler: app.handler
      Environment:
        Variables:
          DB_HOST: !Ref DbHost
  Packaged:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: s3://bucket/artifact.zip
"""


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cannabee(root: Path) -> None:
    _write(root, "template.yaml", _TEMPLATE)
    _write(root, "src/licence_scraper/app.py", "def lambda_handler(e, c):\n    return {}\n")
    _write(root, "src/licence_scraper/requirements.txt", "requests==2.32.3\nbeautifulsoup4\n")
    for name in ("app", "db", "models"):
        _write(root, f"src/crud/{name}.py", "x = 1\n")
    _write(root, "src/crud/requirements.txt", "# pinned\npsycopg2-binary\n")
    _write(root, "tests/test_check_email.py", "import boto3\n")


def test_function_dirs_are_the_local_code_uris(tmp_path: Path) -> None:
    _cannabee(tmp_path)
    assert function_dirs(tmp_path) == ["src/licence_scraper", "src/crud"]


def test_a_sam_repo_is_existing_not_scaffolded_and_the_busiest_function_wins(tmp_path: Path) -> None:
    _cannabee(tmp_path)

    layout = resolve_layout(tmp_path, mode="auto", language="python")

    assert (layout.mode, layout.framework, layout.source_dir) == ("existing", "aws-sam", "src/crud")
    assert layout.package_name == "crud"
    assert "most Python (3 file(s))" in layout.chosen_reason


def test_the_function_the_design_names_wins(tmp_path: Path) -> None:
    _cannabee(tmp_path)

    layout = resolve_layout(
        tmp_path, mode="auto", language="python", prefer_paths=["src/licence_scraper/app.py"]
    )

    assert layout.source_dir == "src/licence_scraper"
    assert "holds a file the design names" in layout.chosen_reason


def test_a_function_dir_that_is_not_an_identifier_has_no_package(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "template.yml",
        "R:\n  Type: AWS::Serverless::Function\n  Properties:\n    CodeUri: ny-state/\n",
    )
    _write(tmp_path, "ny-state/app.py", "x = 1\n")

    layout = resolve_layout(tmp_path, mode="auto", language="python")

    assert (layout.source_dir, layout.package_name) == ("ny-state", "")


def test_sam_guidance_forbids_a_new_package_and_names_the_function(tmp_path: Path) -> None:
    _cannabee(tmp_path)
    guidance = python_guidance(resolve_layout(tmp_path, mode="auto", language="python"))

    assert "AWS SAM repository" in guidance and "`src/crud/`" in guidance
    assert "Do NOT create a new package, a new top-level directory or a `pyproject.toml`" in guidance
    assert "`src/crud/requirements.txt`" in guidance


def test_function_requirements_and_the_runtime_boto3_are_declared(tmp_path: Path) -> None:
    _cannabee(tmp_path)

    deps = _project_dependencies(tmp_path)

    assert {"boto3", "requests==2.32.3", "beautifulsoup4", "psycopg2-binary"} <= set(deps)
    assert "# pinned" not in deps


def test_a_repo_without_a_sam_template_gets_no_boto3(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "requests\n")
    assert "boto3" not in _project_dependencies(tmp_path)


def test_top_level_modules_are_followed_not_given_a_package(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, "utils.py", "y = 2\n")
    _write(tmp_path, "setup.py", "")

    layout = resolve_layout(tmp_path, mode="auto", language="python")

    assert (layout.mode, layout.package_name, layout.source_dir) == ("existing", "", ".")
    assert "Put new modules at `<module>.py` in the repository root" in python_guidance(layout)


def test_an_explicit_new_layout_still_scaffolds(tmp_path: Path) -> None:
    _cannabee(tmp_path)
    assert resolve_layout(tmp_path, mode="new", language="python").mode == "new"
