"""PKG-grounded review context — the seam between the code reviewer and the PKG.

Block A's reviewer sees only the diff: it cannot know that a function whose
signature changed is called from three other files. ``PKGReviewGrounder`` closes
that gap. It builds (or is handed) a ``FactStore`` for the repo, maps the diff's
changed lines to their enclosing symbols, and emits an *impact brief* naming the
cross-file callers to check — which the reviewer prepends to its prompt.

Deterministic and read-only: the grounding is graph lookups, not an LLM call.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.codereview.diff_utils import iter_added_lines
from orchestrator.codereview.github_client import PRDiff
from orchestrator.codereview.verifiers import Finding, Severity
from orchestrator.pkg import (
    FactBatch,
    FactStore,
    GroundedRetriever,
    GroundingVerifier,
    RepoCodeExtractor,
    load_or_extract,
)


class PKGReviewGrounder:
    """Turns a ``PRDiff`` into a grounded impact brief using the repo's PKG."""

    def __init__(self, retriever: GroundedRetriever) -> None:
        self._retriever = retriever

    @classmethod
    def from_repo(
        cls, root: Path | str, *, use_cache: bool = True, cache_dir: Path | None = None
    ) -> PKGReviewGrounder:
        """Build the PKG for ``root`` and wrap it. Provenance paths are
        ``root``-relative, so pass the *repo root* to match GitHub diff paths.

        Reuses the commit-keyed fact cache by default (a clean tree at a cached
        SHA skips the repo walk); ``use_cache=False`` forces a fresh extraction.
        """
        batch = load_or_extract(root, cache_dir=cache_dir) if use_cache else RepoCodeExtractor().extract(root)
        return cls(GroundedRetriever(FactStore(batch)))

    def changed_lines(self, diff: PRDiff) -> dict[str, set[int]]:
        """New-file line numbers touched by each modified file in the diff."""
        out: dict[str, set[int]] = {}
        for f in diff.files:
            if f.status == "removed" or not f.patch:
                continue
            lines = {line_no for line_no, _ in iter_added_lines(f.patch)}
            if lines:
                out[f.filename] = lines
        return out

    def brief_for_diff(self, diff: PRDiff) -> str:
        """The cross-file impact brief, or '' when nothing downstream is at risk."""
        impacts = self._retriever.diff_impact(self.changed_lines(diff))
        return self._retriever.render(impacts, cross_file_only=True)

    def findings_for_diff(self, diff: PRDiff) -> list[Finding]:
        """Anchored WARNING findings: each changed symbol with cross-file callers.

        One finding per at-risk symbol, anchored to the lowest *changed* line in
        that symbol (guaranteed to be a diff line, so the comment posts inline).
        """
        findings: list[Finding] = []
        for file, lines in self.changed_lines(diff).items():
            by_symbol: dict[str, list[int]] = {}
            for line in sorted(lines):
                symbol = self._retriever.enclosing_symbol(file, line)
                if symbol is not None:
                    by_symbol.setdefault(symbol.id, []).append(line)
            for symbol_id, symbol_lines in by_symbol.items():
                impact = self._retriever.impact_of(symbol_id)
                if impact is None:
                    continue
                cross = impact.cross_file_callers()
                if not cross:
                    continue
                sample = "; ".join(f"{c.caller.id} ({c.at})" for c in cross[:3])
                more = f"; +{len(cross) - 3} more" if len(cross) > 3 else ""
                findings.append(
                    Finding(
                        verifier_id="pkg.impact",
                        rule="cross_file_callers",
                        severity=Severity.WARNING,
                        path=file,
                        line=min(symbol_lines),
                        message=(
                            f"`{impact.symbol.name}` is called from {len(cross)} site(s) in other "
                            f"files ({sample}{more}). Verify this change is backward-compatible."
                        ),
                    )
                )
        return findings


class PKGGroundingVerifier:
    """``CodeVerifier`` adapter for the PKG GroundingVerifier (Track 1.4).

    Plugs into the review chain next to Secrets/Security/Style. For each PR it
    checks the two ways the knowledge graph can lie about the changed files:
    SHACL shape violations (when a shapes file — e.g. ontomesh's generated
    ``_combined.ttl`` — is configured) and stale facts (the graph asserts a
    symbol the current source no longer defines). Findings are anchored to the
    fact's provenance; out-of-diff lines fold into the review body, which is
    exactly where a staleness warning belongs.
    """

    verifier_id = "pkg.grounding"

    def __init__(
        self,
        batch: FactBatch,
        *,
        root: Path,
        shapes_path: Path | str | None = None,
    ) -> None:
        self._verifier = GroundingVerifier(batch, shapes_path=shapes_path)
        self._root = root

    @classmethod
    def from_repo(cls, root: Path | str, *, shapes_path: Path | str | None = None) -> PKGGroundingVerifier:
        return cls(load_or_extract(root), root=Path(root), shapes_path=shapes_path)

    def scan(self, diff: PRDiff) -> list[Finding]:
        changed = [f.filename for f in diff.files if f.patch or f.status == "removed"]
        grounding = [
            *self._verifier.stale_findings(self._root, changed),
            *self._verifier.shacl_findings(),
        ]
        findings: list[Finding] = []
        for g in grounding:
            if g.rule == "shacl_violation" and g.file is not None and g.file not in changed:
                continue  # shape violations outside this PR's blast radius stay out of the review
            findings.append(
                Finding(
                    verifier_id=self.verifier_id,
                    rule=g.rule,
                    severity=Severity.WARNING,
                    path=g.file or (changed[0] if changed else ""),
                    line=g.line or 1,
                    message=g.message,
                )
            )
        return findings


__all__ = ["PKGGroundingVerifier", "PKGReviewGrounder"]
