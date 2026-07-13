"""Build the committed memory bank for a repo (Phase 0 `understand`).

Detects greenfield vs brownfield, extracts the PKG + profile, renders the
structural files deterministically, and writes them to ``<repo>/memory-bank/``
(override with ``$ORCHESTRATOR_MEMORY_BANK_DIR`` or ``out_dir``). LLM-synthesized
prose (brief / product-context) is Phase 2 and not produced here.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.knowledge import renderers


def memory_bank_dir(root: Path | str, out_dir: Path | str | None = None) -> Path:
    if out_dir is not None:
        return Path(out_dir)
    env = os.getenv("ORCHESTRATOR_MEMORY_BANK_DIR")
    return Path(env) if env else Path(root) / "memory-bank"


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
    stats = summarise_store(store)
    profile = ProjectProfile.from_repo(root_path)
    kind = "greenfield" if greenfield else "brownfield"
    grounded = store.summary().get("grounded_nodes", 0)
    emit(f"[understand] {kind} — {grounded} grounded nodes")

    files = {
        "README.md": renderers.render_index(root_path, greenfield=greenfield, summary=store.summary()),
        "architecture.md": renderers.render_architecture(store, stats, greenfield=greenfield),
        "domain-model.md": renderers.render_domain_model(store),
        "tech-context.md": renderers.render_tech_context(profile, greenfield=greenfield),
        "conventions.md": renderers.render_conventions(root_path),
        "glossary.md": renderers.render_glossary(store),
        "progress.md": renderers.render_progress_pointer(),
    }

    target = memory_bank_dir(root_path, out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (target / name).write_text(content, encoding="utf-8")
    emit(f"[understand] wrote {len(files)} files → {target}")

    return {
        "dir": str(target),
        "files": sorted(files),
        "greenfield": greenfield,
        "summary": store.summary(),
        "profile": profile.to_dict(),
    }


__all__ = ["build_memory_bank", "memory_bank_dir"]
