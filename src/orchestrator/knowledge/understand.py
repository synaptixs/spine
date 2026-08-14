"""Build the committed knowledge base for a repo (Phase 0 `understand`).

Detects greenfield vs brownfield, extracts the PKG + profile, renders the
structural files deterministically, and writes them to ``<repo>/episteme/``
(override with ``$ORCHESTRATOR_MEMORY_BANK_DIR`` or ``out_dir``). LLM-synthesized
prose (brief / product-context) is Phase 2 and not produced here.

The directory is named ``episteme`` — knowledge grounded in evidence, as opposed
to *doxa*, opinion — because that is exactly the contract every rendered file
carries: the PKG is the source of truth and hand edits are advisory. The public
identifiers (``memory_bank_dir``, ``$ORCHESTRATOR_MEMORY_BANK_DIR``,
``read_memory_bank``) keep their names; they are published contracts, and the
brand/artifact split mirrors "the product is Spine, the package is orchestrator".
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.knowledge import renderers
from orchestrator.knowledge.areas import AreaIndex

BANK_DIRNAME = "episteme"
_LEGACY_DIRNAME = "memory-bank"


def spine_version() -> str:
    """The installed Spine version, for the provenance stamp."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("synaptixs-spine")
    except PackageNotFoundError:  # running from a source tree without an install
        return "unknown"


def memory_bank_dir(root: Path | str, out_dir: Path | str | None = None) -> Path:
    """Where ``understand`` *writes* the knowledge base (always the canonical name)."""
    if out_dir is not None:
        return Path(out_dir)
    env = os.getenv("ORCHESTRATOR_MEMORY_BANK_DIR")
    return Path(env) if env else Path(root) / BANK_DIRNAME


def existing_bank_dir(root: Path | str, out_dir: Path | str | None = None) -> Path:
    """Where a *reader* should look — the canonical dir, else a legacy ``memory-bank/``.

    Repos analysed before the rename have a committed ``memory-bank/``; without
    this fallback they'd silently report "no knowledge yet" after an upgrade. An
    explicit ``out_dir``/env override always wins (no surprise fallback), and the
    legacy directory is never migrated or deleted — it is the user's committed
    content, so we read it and leave it alone.
    """
    target = memory_bank_dir(root, out_dir)
    if target.is_dir() or out_dir is not None or os.getenv("ORCHESTRATOR_MEMORY_BANK_DIR"):
        return target
    legacy = Path(root) / _LEGACY_DIRNAME
    return legacy if legacy.is_dir() else target


def _source_prefix(root: Path, target: Path) -> str | None:
    """Relative path from the bank dir back to the repo root, for source links.

    ``None`` when the bank lives outside the repo (``--out /tmp/x``, a transient
    clone): a link would be machine-specific or broken, so callers degrade to
    plain text instead. Never emit a link we can't stand behind.
    """
    try:
        root_r, target_r = root.resolve(), target.resolve()
        target_r.relative_to(root_r)
    except (ValueError, OSError):
        return None
    up = os.path.relpath(root_r, target_r)
    return "" if up == "." else up.replace(os.sep, "/") + "/"


def _reap_orphans(page_dir: Path, keep: set[str]) -> int:
    """Delete generated pages this run didn't produce; return how many.

    The fixed seven docs never needed this, but the module page set is *dynamic*:
    delete or rename a module and its page would linger forever, describing code that
    no longer exists. That is worse than having no page — it's a confident lie. Only
    ``.md`` files directly under the generated page dir are touched, so a stray hand-
    written file elsewhere in the bank is never at risk.
    """
    if not page_dir.is_dir():
        return 0
    reaped = 0
    for stale in page_dir.glob("*.md"):
        if stale.name not in keep:
            stale.unlink()
            reaped += 1
    if not any(page_dir.iterdir()):
        page_dir.rmdir()
    return reaped


@dataclass(frozen=True)
class RenderedBank:
    """A fully-rendered knowledge base, not yet written anywhere.

    Separating render from write is what lets ``understand --check`` compare the
    committed bank against what the code says *now* without touching the checkout —
    one rendering path, so the check can never disagree with the build for reasons
    of its own.
    """

    target: Path
    files: dict[str, str]
    # Generated-page dirs → the basenames this render produced, for orphan reaping
    # (build) and orphan reporting (check).
    page_dirs: dict[str, set[str]] = field(default_factory=dict)
    greenfield: bool = False
    summary: dict[str, int] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)


def render_memory_bank(
    root: Path | str,
    *,
    out_dir: Path | str | None = None,
    refresh: bool = False,
    sql_dialect: str | None = None,
    intents: bool = False,
    log: Callable[[str], None] | None = None,
) -> RenderedBank:
    """Render every page of the knowledge base in memory (no writes)."""
    from orchestrator.knowledge.analysis import analyse
    from orchestrator.knowledge.insights import onboarding_path
    from orchestrator.pkg.persistence import repo_state
    from orchestrator.sdlc.coverage import CoverageIndex

    emit = log or (lambda _m: None)
    root_path = Path(root)

    # One analysis layer, two renderings: `state` computes exactly this and prints it
    # (see `knowledge/analysis.py`). Everything below is rendering.
    analysis = analyse(root_path, refresh=refresh, sql_dialect=sql_dialect, intents=intents)
    store, stats, profile = analysis.store, analysis.stats, analysis.profile
    greenfield = analysis.greenfield
    kind = "greenfield" if greenfield else "brownfield"
    grounded = store.summary().get("grounded_nodes", 0)
    emit(f"[understand] {kind} — {grounded} grounded nodes")

    # Source links are relative to where the bank lands, so resolve the target first.
    # Module pages sit one level deeper, so they get their own prefix.
    target = memory_bank_dir(root_path, out_dir)
    src = _source_prefix(root_path, target)
    src_sub = _source_prefix(root_path, target / renderers.MODULES_SUBDIR)

    paged = renderers.select_module_pages(store)
    slugs = renderers.module_page_slugs([m.name for m in paged])
    page_of = {m.id: slugs[m.name] for m in paged}

    deps = renderers.ModuleDeps(store, AreaIndex(store))
    all_areas = renderers.collect_areas(store, deps)
    paged_areas = renderers.select_area_pages(all_areas)
    area_slugs = renderers.module_page_slugs([a.name for a in paged_areas])
    area_page_of = {a.name: area_slugs[a.name] for a in paged_areas}

    data = renderers.DataLayer(store)
    paged_entities = renderers.select_entity_pages(store)
    entity_slugs = renderers.module_page_slugs([e.name for e in paged_entities])
    entity_page_of = {e.id: entity_slugs[e.name] for e in paged_entities}

    # Provenance facts for the stamp, gathered here (invariant #1: the renderer
    # renders facts, it doesn't go and find them).
    sha, dirty = repo_state(root_path)
    files = {
        "README.md": renderers.render_index(
            root_path,
            greenfield=greenfield,
            summary=store.summary(),
            module_pages=len(paged),
            area_pages=len(paged_areas),
            entity_pages=len(paged_entities),
            stamp=renderers.render_stamp(commit=sha, dirty=dirty, version=spine_version()),
            api_surface=renderers.has_api_surface(store),
            onboarding=renderers.render_onboarding(
                onboarding_path(store, deps.importers, analysis.state.entry_points), src
            ),
        ),
        "architecture.md": renderers.render_architecture(
            store,
            stats,
            greenfield=greenfield,
            src=src,
            page_of=page_of,
            areas=paged_areas,
            area_pages=area_page_of,
            state=analysis.state,
            deps=deps,
        ),
        "domain-model.md": renderers.render_domain_model(store, src=src, entity_pages=entity_page_of),
        "tech-context.md": renderers.render_tech_context(
            profile, greenfield=greenfield, state=analysis.state, root=root_path
        ),
        "conventions.md": renderers.render_conventions(root_path, store),
        "glossary.md": renderers.render_glossary(store, src=src),
        "progress.md": renderers.render_progress_pointer(analysis.state),
        "symbol-index.md": renderers.render_symbol_index(store, page_of=page_of, src=src),
    }
    # Only for repos that actually expose routes — see `render_api_surface`.
    if renderers.has_api_surface(store):
        files["api-surface.md"] = renderers.render_api_surface(store, src=src)
    # Indexed once for the whole repo: per-module blast radius and test reachability
    # would otherwise rescan every call edge on every page.
    cov = CoverageIndex(store)
    for m in paged:
        files[f"{renderers.MODULES_SUBDIR}/{slugs[m.name]}.md"] = renderers.render_module_page(
            store, m, src=src_sub, page_of=page_of, deps=deps, cov=cov
        )
    for a in paged_areas:
        files[f"{renderers.AREAS_SUBDIR}/{area_slugs[a.name]}.md"] = renderers.render_area_page(
            a,
            total_areas=len(all_areas),
            src=src_sub,
            area_pages=area_page_of,
            module_pages=page_of,
        )
    for e in paged_entities:
        files[f"{renderers.ENTITIES_SUBDIR}/{entity_slugs[e.name]}.md"] = renderers.render_entity_page(
            store, e, src=src_sub, entity_pages=entity_page_of, data=data
        )

    return RenderedBank(
        target=target,
        files=files,
        page_dirs={
            renderers.MODULES_SUBDIR: {f"{s}.md" for s in slugs.values()},
            renderers.AREAS_SUBDIR: {f"{s}.md" for s in area_slugs.values()},
            renderers.ENTITIES_SUBDIR: {f"{s}.md" for s in entity_slugs.values()},
        },
        greenfield=greenfield,
        summary=store.summary(),
        profile=profile.to_dict(),
    )


def build_memory_bank(
    root: Path | str,
    *,
    out_dir: Path | str | None = None,
    refresh: bool = False,
    sql_dialect: str | None = None,
    intents: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Render + write the memory bank; return a summary dict."""
    emit = log or (lambda _m: None)
    bank = render_memory_bank(
        root, out_dir=out_dir, refresh=refresh, sql_dialect=sql_dialect, intents=intents, log=log
    )
    target = bank.target
    target.mkdir(parents=True, exist_ok=True)
    for name, content in bank.files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    reaped = sum(_reap_orphans(target / sub, keep) for sub, keep in bank.page_dirs.items())
    emit(f"[understand] wrote {len(bank.files)} files → {target}" + (f" (reaped {reaped})" if reaped else ""))

    return {
        "dir": str(target),
        "files": sorted(bank.files),
        "greenfield": bank.greenfield,
        "summary": bank.summary,
        "profile": bank.profile,
    }


@dataclass(frozen=True)
class BankCheck:
    """The verdict of ``understand --check``: is the committed episteme still true?

    ``missing``/``stale``/``orphaned`` are repo-relative page names, so the message
    can name what to look at rather than just failing.
    """

    bank_dir: Path
    # Set when the check couldn't run at all (no bank on disk) — distinct from a bank
    # that exists and disagrees.
    absent: bool = False
    missing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    orphaned: tuple[str, ...] = ()
    commit: str | None = None
    dirty: bool = False

    @property
    def ok(self) -> bool:
        return not (self.absent or self.missing or self.stale or self.orphaned)

    def summary_line(self) -> str:
        if self.absent:
            return f"No knowledge base at {self.bank_dir} — run `orchestrator understand .` to build one."
        if self.ok:
            if self.dirty:
                return "episteme is current — it matches the working tree (which has uncommitted changes)."
            where = f" at commit {self.commit[:12]}" if self.commit else ""
            return f"episteme is current — it matches the code{where}."
        counts = [
            f"{len(self.stale)} out of date" if self.stale else "",
            f"{len(self.missing)} missing" if self.missing else "",
            f"{len(self.orphaned)} describing code that's gone" if self.orphaned else "",
        ]
        return "episteme is stale — " + ", ".join(c for c in counts if c) + "."


def check_memory_bank(
    root: Path | str,
    *,
    out_dir: Path | str | None = None,
    refresh: bool = False,
    sql_dialect: str | None = None,
    intents: bool = False,
    log: Callable[[str], None] | None = None,
) -> BankCheck:
    """Compare the committed knowledge base against what the code says now.

    Re-renders and diffs page by page. The comparison ignores the fenced provenance
    stamp: committing the episteme itself creates a new commit, so a bank is always
    one commit "behind" the moment it lands — content, not the stamp, is what proves
    currency. A dirty working tree therefore reads as stale, which is honest: the
    committed pages genuinely don't describe the code as it stands.
    """
    from orchestrator.pkg.persistence import repo_state

    root_path = Path(root)
    bank_dir = existing_bank_dir(root_path, out_dir)
    sha, dirty = repo_state(root_path)
    if not bank_dir.is_dir():
        return BankCheck(bank_dir=bank_dir, absent=True, commit=sha, dirty=dirty)

    rendered = render_memory_bank(
        root_path, out_dir=bank_dir, refresh=refresh, sql_dialect=sql_dialect, intents=intents, log=log
    )
    missing: list[str] = []
    stale: list[str] = []
    for name, expected in rendered.files.items():
        path = bank_dir / name
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError:
            missing.append(name)
            continue
        if renderers.strip_stamp(actual) != renderers.strip_stamp(expected):
            stale.append(name)

    # Pages for code that no longer exists — the same set `build` would reap.
    orphaned: list[str] = []
    for sub, keep in rendered.page_dirs.items():
        page_dir = bank_dir / sub
        if not page_dir.is_dir():
            continue
        orphaned += [f"{sub}/{p.name}" for p in page_dir.glob("*.md") if p.name not in keep]

    return BankCheck(
        bank_dir=bank_dir,
        missing=tuple(sorted(missing)),
        stale=tuple(sorted(stale)),
        orphaned=tuple(sorted(orphaned)),
        commit=sha,
        dirty=dirty,
    )


__all__ = [
    "BANK_DIRNAME",
    "BankCheck",
    "RenderedBank",
    "build_memory_bank",
    "check_memory_bank",
    "existing_bank_dir",
    "memory_bank_dir",
    "render_memory_bank",
    "spine_version",
]
