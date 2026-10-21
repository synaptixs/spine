"""Why a test the run wrote is failing, in a closed set of causes (test-trust P4a/P4b, SSPN-228).

Deterministic and model-free, like ``own_tests``: it reads the runner's output and names one of four
causes, the line that shows it, and one sentence a person (or a repair turn) can act on. The class is what
the question to a person carries and what the intervention report is keyed by, so the set is closed and a
new cause is a code change, not a free-text label.

Order matters: an app that is not installed explains the assertions after it, and an import that fails
explains everything the file would have tested, so those are looked for first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["CAUSES", "Diagnosis", "diagnose"]

CAUSES: tuple[str, ...] = ("import_error", "missing_app", "wrong_expectation", "other")

_EVIDENCE_LIMIT = 200

_SUGGESTIONS = {
    "import_error": (
        "The test imports something the runner cannot import; rewrite it to use only what the project's own "
        "tests import, or remove the import."
    ),
    "missing_app": (
        "The test defines models outside an installed app; give them an app the settings already install, or "
        "set an explicit app_label."
    ),
    "wrong_expectation": (
        "The test asserts something the ticket does not ask for; correct the expectation to what the ticket "
        "requires, or drop that test."
    ),
    "other": "The cause is not one the system can name; read the failure and say what the test should do.",
}

# (cause, pattern) in priority order; the first pattern that matches a line of the output decides.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("missing_app", re.compile(r"(?:isn't|is not) in an application in INSTALLED_APPS|explicit app_label")),
    (
        "import_error",
        re.compile(r"ModuleNotFoundError|ImportError|Failed to import test module|No module named"),
    ),
    ("wrong_expectation", re.compile(r"\bAssertionError\b|^E?\s*assert\b")),
)


@dataclass(frozen=True)
class Diagnosis:
    cause: str
    #: The line of the output that showed it, clipped; empty when nothing matched.
    evidence: str
    suggestion: str


def diagnose(output: str) -> Diagnosis:
    """The cause class of the failure in ``output`` (``other`` when none of the named causes shows)."""
    lines = [ln.strip() for ln in output.splitlines()]
    for cause, pattern in _RULES:
        # The most specific line of that cause: the one that names the module or the label, not the wrapper.
        hits = [ln for ln in lines if pattern.search(ln)]
        if hits:
            best = next((ln for ln in hits if "No module named" in ln or "INSTALLED_APPS" in ln), hits[0])
            return Diagnosis(cause, best[:_EVIDENCE_LIMIT], _SUGGESTIONS[cause])
    first = next((ln for ln in lines if re.match(r"\w*(Error|Exception)\b", ln)), "")
    return Diagnosis("other", first[:_EVIDENCE_LIMIT], _SUGGESTIONS["other"])
