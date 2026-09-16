#!/usr/bin/env python3
"""The real-repository smoke test as a script (§8.2 of `docs/specs/templates/language-track.md`).

CONTRIBUTING.md names "a real-repository smoke test for front-end changes" as part of the
maintainer review checklist for a language-track PR; until now it was three or four
commands typed by hand against a scratch clone, re-derived slightly differently by
whoever ran it. This is that loop as one script, reusable by every future front-end:

    python scripts/validate-frontend.py <language> <git-url> [<git-url> ...]
    python scripts/validate-frontend.py perl https://github.com/mojolicious/mojo \
        https://github.com/exiftool/exiftool

For each URL: shallow-clones it to a scratch temp dir — the same SSRF-guarded,
host-allow-listed resolution `pkg`/`state`/`understand` use under the CLI
(`resolve_repo_source` + `materialize_repo_source`), so a URL this refuses is a URL the
rest of Spine would refuse too — extracts, verifies, and reads the current-state summary,
then deletes the clone. Prints, per repo:

- `pkg extract`'s summary line (grounded/external nodes, edges, per-kind edge counts)
- node counts by (language, kind) — a multi-language repo (a Perl distribution's test
  fixtures under `t/`, say) should not silently show up as one language's numbers
- `pkg verify`'s report (errors/warnings; non-zero exit if any error)
- the `state` stack line equivalent: languages detected, framework, repo size, whether a
  call graph is available
- the top unresolved import targets — `IMPORTS` edges landing on an external placeholder,
  grouped by target name, most-referenced first; the number `pkg verify`'s external-ratio
  warning already gates is a percentage, this is *which* targets make it up

Nothing here writes anything outside the scratch clone (no `understand`/`episteme`
synthesis — that's a separate manual step in `perl-support-roadmap.md` §3.5, not part of
this script's scope per §8.2's own definition). `--language` only labels the report; every
front-end registered in the repo runs, same as a real `pkg extract` — a mono-language claim
that turns out to be multi-language *is* a finding.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def validate_one(language: str, url: str) -> bool:
    """Returns whether the repo passed `pkg verify` with no errors."""
    from orchestrator.knowledge.current_state import load_current_state
    from orchestrator.pkg import FactStore, RepoCodeExtractor
    from orchestrator.pkg.facts import EdgeKind, NodeKind
    from orchestrator.pkg.verify import verify_batch
    from orchestrator.registry.api.config import Settings
    from orchestrator.registry.api.workspace import (
        RepoPathError,
        RepoSourceError,
        materialize_repo_source,
        resolve_repo_source,
    )

    print(f"\n=== {language}: {url} ===")
    try:
        source = resolve_repo_source(url, Settings(repo_allow_any_local=True))
    except (RepoSourceError, RepoPathError) as exc:
        print(f"  REFUSED: {exc}")
        return False

    # A per-repo boundary, deliberately broad: this script's whole purpose is an
    # unattended run across a list of real repos (§8.2), so a clone timeout, a private
    # repo gone missing, or an extraction edge case on one URL must report and move on to
    # the next — not take the rest of the list down with a raw traceback. `main()`'s loop
    # relies on this function never raising.
    try:
        with materialize_repo_source(source, log=lambda m: print(f"  {m}")) as repo:
            extractor = RepoCodeExtractor()
            started = time.perf_counter()
            batch = extractor.extract(repo)
            elapsed = time.perf_counter() - started
            store = FactStore(batch)
            summary = store.summary()
            print(
                f"  extract: {summary['grounded_nodes']} grounded nodes, "
                f"{summary['external_nodes']} external, {summary['edges']} edges"
            )
            if extractor.clang_report.total_tus:
                print("  " + extractor.clang_report.summary())
                ids = {n.id for n in batch.nodes}
                grounded = {n.id for n in batch.nodes if n.grounded}
                cc_nodes = [n for n in batch.nodes if n.language in {"c", "cpp"}]
                cc_edges = [e for e in batch.edges if e.src.startswith(("c:", "cpp:"))]
                calls = [e for e in cc_edges if e.kind is EdgeKind.CALLS]
                missing = {i for e in batch.edges for i in (e.src, e.dst) if i not in ids}
                metrics = {
                    "extraction_seconds": round(elapsed, 3),
                    "nodes": len(batch.nodes),
                    "edges": len(batch.edges),
                    "dangling_ids": len(missing),
                    "dangling_edges": sum(e.src not in ids or e.dst not in ids for e in batch.edges),
                    "cc_nodes": len(cc_nodes),
                    "cc_grounded": sum(n.grounded for n in cc_nodes),
                    "cc_edges": len(cc_edges),
                    "cc_calls": len(calls),
                    "cc_calls_ungrounded": sum(e.dst not in grounded for e in calls),
                    "header_types": sum(
                        n.kind is NodeKind.TYPE
                        and n.provenance is not None
                        and n.provenance.file.endswith(".h")
                        for n in cc_nodes
                    ),
                    "unsupported_source_nodes": sum(
                        n.provenance is not None and n.provenance.file.endswith((".cu", ".mm"))
                        for n in cc_nodes
                    ),
                    "resolved_sites": extractor.clang_report.resolved,
                    "pending_sites": extractor.clang_report.pending,
                    "parsed_tus": extractor.clang_report.parsed_tus,
                    "total_tus": extractor.clang_report.total_tus,
                    "diagnostic_tus": extractor.clang_report.diagnostic_tus,
                    "failed_tus": extractor.clang_report.failed_tus,
                    "unresolved_reasons": extractor.clang_report.unresolved_reasons,
                }
                print("  semantic metrics: " + json.dumps(metrics, sort_keys=True))
            per_kind = {k[len("edges_") :]: v for k, v in summary.items() if k.startswith("edges_")}
            if per_kind:
                print("    " + "  ".join(f"{k.upper()} {v}" for k, v in per_kind.items()))

            by_lang_kind = Counter((n.language or "?", n.kind.value) for n in batch.nodes if not n.external)
            print("  nodes by language:")
            for (lang, kind), count in sorted(by_lang_kind.items()):
                print(f"    {lang:12s} {kind:10s} {count}")

            report = verify_batch(batch, repo)
            for issue in report.issues:
                print(f"  [{issue.severity}] {issue.check}: {issue.message}")
            print(
                f"  pkg verify: {'OK' if report.ok else 'FAILED'} — "
                f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
            )

            state, _ = load_current_state(repo, refresh=True)
            counted = " · ".join(f"{v} {k.lower()}s" for k, v in state.counts.items() if v)
            print(
                f"  state: languages={list(state.languages)} framework={state.framework or '—'} "
                f"size={state.namespaces} namespaces (~{state.areas} areas) · {counted} "
                f"call_graph={'available' if state.has_calls else 'not available'}"
            )

            unresolved: Counter[str] = Counter()
            by_id = {n.id: n for n in batch.nodes}
            for e in batch.edges:
                if e.kind is not EdgeKind.IMPORTS:
                    continue
                dst = by_id.get(e.dst)
                if dst is not None and dst.external:
                    unresolved[dst.name] += 1
            if unresolved:
                print("  top unresolved import targets:")
                for name, count in unresolved.most_common(10):
                    print(f"    {count:4d}  {name}")

            return bool(report.ok)
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("language", help="Label for the report (the extraction itself is language-agnostic).")
    parser.add_argument("urls", nargs="+", help="One or more git URLs to validate.")
    args = parser.parse_args()

    all_ok = True
    for url in args.urls:
        all_ok = validate_one(args.language, url) and all_ok

    print(f"\nvalidate-frontend: {'OK' if all_ok else 'FAILED'} — {len(args.urls)} repo(s) checked.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
