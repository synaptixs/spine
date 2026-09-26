"""AWS SAM repositories: where the Python lives, and what it depends on (CB-764).

**Why this exists.** A SAM repository keeps each Lambda function in the directory its
``template.yaml`` names as ``CodeUri`` — ``src/licence_scraper/app.py`` beside a
``requirements.txt`` — usually with no ``__init__.py``. Layout detection only recognised a Python
*package*, found none, and in ``auto`` mode scaffolded ``src/cannabee_crud_apis/`` and a root
``pyproject.toml`` into a deployed repository: the new code went into a package nothing deploys,
and the new ``pyproject.toml`` rewrote pytest's settings for the whole repo. The test environment
read only root requirements, so the functions' dependencies — and ``boto3``, which the Lambda
runtime provides and function requirements therefore omit — were never installed.

Read with a line-oriented pattern rather than a YAML parser: SAM templates use CloudFormation
tags (``!Ref``, ``!GetAtt``) that a plain ``safe_load`` rejects, and only ``CodeUri`` is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

_TEMPLATE_NAMES = ("template.yaml", "template.yml")
_SERVERLESS_FUNCTION = "AWS::Serverless::Function"
_CODE_URI = re.compile(r"^[ \t]*CodeUri:[ \t]*['\"]?(?P<uri>[^'\"\s#]+)", re.MULTILINE)
#: Provided by every Python Lambda runtime, so function requirements leave it out — and tests
#: that import a handler need it installed all the same.
RUNTIME_PROVIDED = ("boto3",)


def sam_template(root: Path) -> Path | None:
    """The repository's SAM template, when one at the root declares a serverless function."""
    for name in _TEMPLATE_NAMES:
        path = root / name
        try:
            if path.is_file() and _SERVERLESS_FUNCTION in path.read_text(encoding="utf-8", errors="replace"):
                return path
        except OSError:
            continue
    return None


def function_dirs(root: Path) -> list[str]:
    """Repo-relative directories the template's functions deploy (``CodeUri``), in template order.

    Only local directories that exist: an ``s3://`` URI or a packaged artifact is not source,
    and ``.`` — the whole repository as one function — is not a directory to put code *in*.
    """
    template = sam_template(root)
    if template is None:
        return []
    found: list[str] = []
    resolved_root = root.resolve()
    text = template.read_text(encoding="utf-8", errors="replace")
    for match in _CODE_URI.finditer(text):
        uri = match["uri"].rstrip("/")
        if "://" in uri or uri in {"", "."}:
            continue
        target = (root / uri).resolve()
        if not target.is_dir() or not target.is_relative_to(resolved_root) or target == resolved_root:
            continue
        rel = target.relative_to(resolved_root).as_posix()
        if rel not in found:
            found.append(rel)
    return found


def function_requirements(root: Path) -> list[Path]:
    """Each function's own ``requirements.txt``, where it has one."""
    return [
        root / d / "requirements.txt"
        for d in function_dirs(root)
        if (root / d / "requirements.txt").is_file()
    ]


__all__ = ["RUNTIME_PROVIDED", "function_dirs", "function_requirements", "sam_template"]
