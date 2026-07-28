"""One analysis layer, two renderings.

``understand`` (the committed ``episteme/``) and ``state`` (the printed report) read
the same repository and answer the same questions — but they used to each build the
graph themselves and each decide what was interesting. The result was the inversion
this module exists to fix: the *ephemeral* report computed sixteen sections while the
*committed* knowledge base rendered four, so the artifact a team reads and an AI tool
grounds on was the poorer of the two.

:func:`analyse` is the single pipeline both now go through — extract, fold migrations
into the schema, reconcile the data layer, ingest docs, profile the project, and
compute the metrics. What differs downstream is only *rendering*: one writes markdown
pages into the repo, the other prints a report.

**A rendering caveat worth knowing.** Not everything in :class:`CurrentState` belongs
in a committed artifact. ``recent_areas`` is computed from the last ~60 commits, so its
value shifts every time anything is committed — including the knowledge base itself.
Rendering it into ``episteme`` would make the bank go stale the moment it landed, and
``understand --check`` would fail forever after. Git-history metrics stay in the
ephemeral report; see ``knowledge/renderers.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.catalog.profile import ProjectProfile
    from orchestrator.knowledge.current_state import CurrentState
    from orchestrator.pkg.facts import FactBatch
    from orchestrator.pkg.stats import GraphStats
    from orchestrator.pkg.store import FactStore


@dataclass(frozen=True)
class Analysis:
    """Everything both comprehension surfaces need, computed once."""

    root: Path
    batch: FactBatch
    store: FactStore
    profile: ProjectProfile
    stats: GraphStats
    state: CurrentState
    greenfield: bool


def analyse(
    root: Path | str,
    *,
    refresh: bool = False,
    sql_dialect: str | None = None,
) -> Analysis:
    """Build the graph and compute every metric both surfaces render from."""
    from orchestrator.catalog.profile import ProjectProfile
    from orchestrator.knowledge import renderers
    from orchestrator.knowledge.current_state import compute_current_state
    from orchestrator.pkg.data_layer_link import link_data_layer
    from orchestrator.pkg.doc_link import link_docs
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.migrations import apply_migrations
    from orchestrator.pkg.persistence import load_or_extract
    from orchestrator.pkg.stats import summarise_store
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.layout import is_effectively_empty

    root_path = Path(root)
    # A pinned --dialect changes SQL extraction, so bypass the commit-keyed cache.
    fresh = refresh or sql_dialect is not None
    batch = (
        RepoCodeExtractor(sql_dialect=sql_dialect).extract(root_path) if fresh else load_or_extract(root_path)
    )
    # A4: fold ordered migrations into the authoritative current schema, then
    # A3: let that schema stand in for ORM-inferred entities/FKs. Both no-op
    # when the repo has no migrations / no .sql schema.
    batch = apply_migrations(batch, root_path)
    batch = link_data_layer(batch)
    # Fold the repo's text docs into the graph (Doc nodes + MENTIONS edges); no-op with no docs.
    batch = link_docs(batch, root_path)

    store = FactStore(batch)
    profile = ProjectProfile.from_repo(root_path)
    # Ask for a deep candidate list, not the top 10: the renderer keeps only first-party
    # symbols, and the raw ranking is dominated by stdlib/third-party ones (`json.dumps`,
    # `pytest.raises`). Ten raw candidates can filter down to nearly nothing. Costs
    # nothing — `summarise_store` counts every function either way and only slices at the
    # end. `renderers` re-slices to the display count.
    stats = summarise_store(store, top_n=renderers.HOTSPOT_CANDIDATES)
    state = compute_current_state(batch, profile, root=root_path)

    return Analysis(
        root=root_path,
        batch=batch,
        store=store,
        profile=profile,
        stats=stats,
        state=state,
        greenfield=is_effectively_empty(root_path),
    )


__all__ = ["Analysis", "analyse"]
