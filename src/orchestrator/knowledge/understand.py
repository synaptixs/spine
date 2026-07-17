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
from pathlib import Path
from typing import Any

from orchestrator.knowledge import renderers
from orchestrator.knowledge.areas import AreaIndex

BANK_DIRNAME = "episteme"
_LEGACY_DIRNAME = "memory-bank"


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


def build_memory_bank(
    root: Path | str,
    *,
    out_dir: Path | str | None = None,
    refresh: bool = False,
    sql_dialect: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Render + write the memory bank; return a summary dict."""
    from orchestrator.catalog.profile import ProjectProfile
    from orchestrator.pkg.data_layer_link import link_data_layer
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.migrations import apply_migrations
    from orchestrator.pkg.persistence import load_or_extract
    from orchestrator.pkg.stats import summarise_store
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.layout import is_effectively_empty

    emit = log or (lambda _m: None)
    root_path = Path(root)

    greenfield = is_effectively_empty(root_path)
    # A pinned --dialect changes SQL extraction, so bypass the commit cache
    # (which is keyed only by HEAD sha).
    extractor = RepoCodeExtractor(sql_dialect=sql_dialect)
    fresh = refresh or sql_dialect is not None
    batch = extractor.extract(root_path) if fresh else load_or_extract(root_path)
    # A4: fold ordered migrations into the authoritative current schema, then
    # A3: let that schema stand in for ORM-inferred entities/FKs. Both no-op
    # when the repo has no migrations / no .sql schema.
    batch = apply_migrations(batch, root_path)
    batch = link_data_layer(batch)
    store = FactStore(batch)
    # Ask for a deep candidate list, not the top 10: the renderer keeps only first-party
    # symbols, and the raw ranking is dominated by stdlib/third-party ones (`json.dumps`,
    # `pytest.raises`). Ten raw candidates can filter down to nearly nothing. Costs
    # nothing — `summarise_store` counts every function either way and only slices at the
    # end. `renderers` re-slices to the display count.
    stats = summarise_store(store, top_n=renderers.HOTSPOT_CANDIDATES)
    profile = ProjectProfile.from_repo(root_path)
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

    files = {
        "README.md": renderers.render_index(
            root_path,
            greenfield=greenfield,
            summary=store.summary(),
            module_pages=len(paged),
            area_pages=len(paged_areas),
            entity_pages=len(paged_entities),
        ),
        "architecture.md": renderers.render_architecture(
            store,
            stats,
            greenfield=greenfield,
            src=src,
            page_of=page_of,
            areas=paged_areas,
            area_pages=area_page_of,
        ),
        "domain-model.md": renderers.render_domain_model(store, src=src, entity_pages=entity_page_of),
        "tech-context.md": renderers.render_tech_context(profile, greenfield=greenfield),
        "conventions.md": renderers.render_conventions(root_path),
        "glossary.md": renderers.render_glossary(store),
        "progress.md": renderers.render_progress_pointer(),
    }
    for m in paged:
        files[f"{renderers.MODULES_SUBDIR}/{slugs[m.name]}.md"] = renderers.render_module_page(
            store, m, src=src_sub, page_of=page_of, deps=deps
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

    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    reaped = _reap_orphans(target / renderers.MODULES_SUBDIR, {f"{s}.md" for s in slugs.values()})
    reaped += _reap_orphans(target / renderers.AREAS_SUBDIR, {f"{s}.md" for s in area_slugs.values()})
    reaped += _reap_orphans(target / renderers.ENTITIES_SUBDIR, {f"{s}.md" for s in entity_slugs.values()})
    emit(f"[understand] wrote {len(files)} files → {target}" + (f" (reaped {reaped})" if reaped else ""))

    return {
        "dir": str(target),
        "files": sorted(files),
        "greenfield": greenfield,
        "summary": store.summary(),
        "profile": profile.to_dict(),
    }


__all__ = ["BANK_DIRNAME", "build_memory_bank", "existing_bank_dir", "memory_bank_dir"]
