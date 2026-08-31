"""PKG-grounded code review: the reviewer learns a diff's cross-file blast radius."""

from __future__ import annotations

from pathlib import Path

from orchestrator.codereview.github_client import ChangedFile, PRDiff
from orchestrator.codereview.grounding import PKGReviewGrounder
from orchestrator.codereview.reviewer import LLMReviewer, ReviewService
from orchestrator.codereview.verifiers import Severity
from orchestrator.core.llm import CompletionResult, Message

# A two-file repo: `total` (invoice.py) calls `calc_tax` (tax.py) across files.
TAX = "def calc_tax(items):\n    return 1\n"
INVOICE = "from pkg.tax import calc_tax\n\n\ndef total(items):\n    return calc_tax(items)\n"

# Diff modifies calc_tax's body (new-file line 2 sits inside its span, lines 1-2).
TAX_PATCH = "@@ -1,2 +1,2 @@\n def calc_tax(items):\n-    return 1\n+    return 2\n"


def _repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "tax.py").write_text(TAX, encoding="utf-8")
    (pkg / "invoice.py").write_text(INVOICE, encoding="utf-8")
    return tmp_path


def _diff() -> PRDiff:
    cf = ChangedFile(filename="pkg/tax.py", status="modified", additions=1, deletions=1, patch=TAX_PATCH)
    return PRDiff(repo="acme/app", pr_number=7, head_sha="deadbeef", files=(cf,))


def test_grounder_finds_cross_file_caller(tmp_path: Path) -> None:
    grounder = PKGReviewGrounder.from_repo(_repo(tmp_path))
    brief = grounder.brief_for_diff(_diff())
    assert "calc_tax" in brief
    assert "py:pkg.invoice.total" in brief  # the caller in the *other* file
    assert "pkg/invoice.py:5" in brief  # with the exact call-site line


def test_changed_lines_maps_added_lines(tmp_path: Path) -> None:
    grounder = PKGReviewGrounder.from_repo(_repo(tmp_path))
    assert grounder.changed_lines(_diff()) == {"pkg/tax.py": {2}}


class _CapturingLLM:
    """Fake LLMClient that records the user message and returns a clean review."""

    def __init__(self) -> None:
        self.last_user = ""

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: object | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: object = None,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        self.last_user = messages[-1].content
        return CompletionResult('{"summary": "ok", "findings": []}', model, 1, 1, 0.0, 1.0)


class _FixedGrounder:
    def brief_for_diff(self, diff: PRDiff) -> str:
        return "IMPACT: callers exist"


async def test_reviewer_injects_grounding_into_prompt() -> None:
    llm = _CapturingLLM()
    await LLMReviewer(llm, grounder=_FixedGrounder()).review(_diff())
    assert "IMPACT: callers exist" in llm.last_user
    assert "Diff:" in llm.last_user  # the original diff still present


async def test_reviewer_without_grounder_has_no_impact_section() -> None:
    llm = _CapturingLLM()
    await LLMReviewer(llm).review(_diff())
    assert "IMPACT" not in llm.last_user


# ---- anchored findings ----------------------------------------------------


def test_findings_for_diff_emits_anchored_warning(tmp_path: Path) -> None:
    grounder = PKGReviewGrounder.from_repo(_repo(tmp_path))
    findings = grounder.findings_for_diff(_diff())
    assert len(findings) == 1
    f = findings[0]
    assert f.verifier_id == "pkg.impact" and f.severity is Severity.WARNING
    assert f.path == "pkg/tax.py" and f.line == 2  # anchored to the changed line
    assert "py:pkg.invoice.total" in f.message and "backward-compatible" in f.message


class _FakeGitHub:
    """Minimal GitHubClient stand-in: returns a fixed diff for the review."""

    def __init__(self, diff: PRDiff) -> None:
        self._diff = diff

    async def fetch_pr_diff(self, *, installation_id: int, repo: str, pr_number: int) -> PRDiff:
        return self._diff


def _clean_llm() -> _CapturingLLM:
    return _CapturingLLM()  # returns {"summary":"ok","findings":[]}


async def test_review_service_posts_pkg_impact_comment(tmp_path: Path) -> None:
    grounder = PKGReviewGrounder.from_repo(_repo(tmp_path))
    diff = _diff()
    service = ReviewService(
        github=_FakeGitHub(diff),  # type: ignore[arg-type]
        llm_reviewer=LLMReviewer(_clean_llm()),
        verifiers=[],  # isolate the PKG contribution
        impact_source=grounder,
    )
    _, submission = await service.preview_pull_request(installation_id=1, repo="acme/app", pr_number=7)

    # The clean LLM + no verifiers means the only comment is the grounded impact one.
    impact_comments = [c for c in submission.comments if "called from" in c.body]
    assert len(impact_comments) == 1
    assert impact_comments[0].path == "pkg/tax.py" and impact_comments[0].line == 2
    assert "py:pkg.invoice.total" in impact_comments[0].body


# ---- PKGGroundingVerifier in the review chain -------------------------------


def test_grounding_verifier_flags_stale_fact_in_review(tmp_path: Path) -> None:
    from orchestrator.codereview.grounding import PKGGroundingVerifier
    from orchestrator.pkg import RepoCodeExtractor

    repo = _repo(tmp_path)
    batch = RepoCodeExtractor().extract(repo)  # knows calc_tax exists
    (repo / "pkg" / "tax.py").write_text("def other():\n    return 0\n", encoding="utf-8")

    verifier = PKGGroundingVerifier(batch, root=repo)
    findings = verifier.scan(_diff())  # diff touches pkg/tax.py

    stale_ids = {f.rule for f in findings}
    assert "stale_fact" in stale_ids
    assert any("py:pkg.tax.calc_tax" in f.message for f in findings)
    assert all(f.verifier_id == "pkg.grounding" for f in findings)


# ---- documentation drift on the review path (WI-2 phase 2) ------------------
#
# Two ways a PR creates drift, and the second is the one a doc-file filter cannot see:
# rename a symbol and the docs that still name it are nowhere in your diff. Both rules are
# attributable because a drift finding exists *only* when the graph has no such symbol.

# Renames calc_tax -> compute_tax: `calc_tax` leaves on a removed line.
RENAME_PATCH = "@@ -1,2 +1,2 @@\n-def calc_tax(items):\n+def compute_tax(items):\n     return 1\n"


def _drift_repo(tmp_path: Path, *, doc: str) -> Path:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text(doc, encoding="utf-8")
    return repo


def _verifier(repo: Path, batch_root: Path | None = None):  # type: ignore[no-untyped-def]
    from orchestrator.codereview.grounding import PKGGroundingVerifier
    from orchestrator.pkg import RepoCodeExtractor

    return PKGGroundingVerifier(RepoCodeExtractor().extract(batch_root or repo), root=repo)


def test_a_renamed_symbol_flags_the_doc_that_still_names_it(tmp_path: Path) -> None:
    """The case the narrow filter misses: the doc is not in the diff at all."""
    repo = _drift_repo(tmp_path, doc="# Guide\n\nCall `calc_tax` to price a line.\n")
    (repo / "pkg" / "tax.py").write_text("def compute_tax(items):\n    return 1\n", encoding="utf-8")

    cf = ChangedFile(filename="pkg/tax.py", status="modified", additions=1, deletions=1, patch=RENAME_PATCH)
    diff = PRDiff(repo="acme/app", pr_number=7, head_sha="deadbeef", files=(cf,))

    drift = [f for f in _verifier(repo).scan(diff) if f.rule == "doc_drift"]
    assert [f.path for f in drift] == ["README.md"]
    assert "calc_tax" in drift[0].message
    assert drift[0].severity is Severity.WARNING


def test_drift_the_pr_did_not_cause_stays_out_of_the_review(tmp_path: Path) -> None:
    """Pre-existing drift is not this author's to answer for."""
    repo = _drift_repo(tmp_path, doc="# Guide\n\nSee `long_gone_helper` for details.\n")

    # The diff touches tax.py's body and names nothing the doc claims.
    drift = [f for f in _verifier(repo).scan(_diff()) if f.rule == "doc_drift"]
    assert drift == []


def test_a_doc_the_pr_edited_is_flagged_even_with_no_code_change(tmp_path: Path) -> None:
    """Rule 1: the author wrote a claim that does not hold."""
    repo = _drift_repo(tmp_path, doc="# Guide\n\nUse `never_existed_at_all` first.\n")
    patch = "@@ -1,3 +1,3 @@\n # Guide\n \n+Use `never_existed_at_all` first.\n"
    cf = ChangedFile(filename="README.md", status="modified", additions=1, deletions=0, patch=patch)
    diff = PRDiff(repo="acme/app", pr_number=7, head_sha="deadbeef", files=(cf,))

    drift = [f for f in _verifier(repo).scan(diff) if f.rule == "doc_drift"]
    assert [f.path for f in drift] == ["README.md"]
    assert "never_existed_at_all" in drift[0].message


def test_the_diff_filter_is_applied_before_the_cap(tmp_path: Path) -> None:
    """Cap-then-filter would go quiet on exactly the repos that carry drift already."""
    from orchestrator.pkg import RepoCodeExtractor
    from orchestrator.pkg.verifier import GroundingVerifier

    repo = _repo(tmp_path)
    for i in range(25):  # 25 unrelated drifting docs, sorted before the one we want
        (repo / f"a{i:02d}.md").write_text(f"# A{i}\n\nSee `absent_symbol_{i}` here.\n", encoding="utf-8")
    (repo / "zz.md").write_text("# Z\n\nSee `calc_tax_helper` here.\n", encoding="utf-8")

    verifier = GroundingVerifier(RepoCodeExtractor().extract(repo))
    unfiltered = verifier.doc_findings(repo)
    assert len(unfiltered) == 20  # the cap, and zz.md is past it
    assert not any(f.file == "zz.md" for f in unfiltered)

    filtered = verifier.doc_findings(repo, files=["zz.md"])
    assert [f.file for f in filtered] == ["zz.md"]


def test_a_drift_finding_anchors_on_its_section(tmp_path: Path) -> None:
    """A comment at line 1 of a long document does not say where to look."""
    from orchestrator.pkg import RepoCodeExtractor
    from orchestrator.pkg.verifier import GroundingVerifier

    repo = _repo(tmp_path)
    (repo / "README.md").write_text(
        "# Title\n\nIntro prose.\n\n## Pricing\n\nCall `absent_helper` to price.\n", encoding="utf-8"
    )
    findings = GroundingVerifier(RepoCodeExtractor().extract(repo)).doc_findings(repo, files=["README.md"])
    assert [f.file for f in findings] == ["README.md"]
    line = findings[0].line
    assert line is not None and line > 1  # the ## Pricing heading, not the top of the file


async def test_review_service_carries_grounding_verifier(tmp_path: Path) -> None:
    from orchestrator.codereview.grounding import PKGGroundingVerifier
    from orchestrator.pkg import RepoCodeExtractor

    repo = _repo(tmp_path)
    batch = RepoCodeExtractor().extract(repo)
    (repo / "pkg" / "tax.py").write_text("def other():\n    return 0\n", encoding="utf-8")

    diff = _diff()
    service = ReviewService(
        github=_FakeGitHub(diff),  # type: ignore[arg-type]
        llm_reviewer=LLMReviewer(_clean_llm()),
        verifiers=[PKGGroundingVerifier(batch, root=repo)],
    )
    _, submission = await service.preview_pull_request(installation_id=1, repo="acme/app", pr_number=7)
    assert (
        any("stale" in c.body.lower() for c in submission.comments) or "stale" in submission.summary.lower()
    )
