"""OrchestratorState: the typed LangGraph state shared across all nodes.

Each channel is annotated with a reducer that combines a node's update
with the prior value. The reducers are plain callables so the schema
can be unit-tested without importing LangGraph; the framework
introspects the same Annotated metadata at graph-build time.

Reducer semantics:
- `merge_dict`     - shallow dict merge; later writes win on key conflict.
                     Used for node_outputs, artifacts, budget_consumed.
- `append_list`    - append b's items to a; preserves order.
                     Used for claims, confidence_history, tool_call_log,
                     approval_requests.
- `set_once`       - the first non-empty value wins; later non-matching
                     writes raise. Used for task_metadata.
- `write_once_glossary` - keys may be added, but never redefined.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, TypedDict, TypeVar

T = TypeVar("T")


def merge_dict(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = dict(a or {})
    out.update(b or {})
    return out


def append_list(a: Sequence[T] | None, b: Sequence[T] | None) -> list[T]:
    return [*(a or []), *(b or [])]


def set_once(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> dict[str, Any]:
    if a and b and dict(a) != dict(b):
        raise ValueError("set_once channel cannot be modified after initial set")
    return dict(a or b or {})


def write_once_glossary(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = dict(a or {})
    for key, value in (b or {}).items():
        if key in out and out[key] != value:
            raise ValueError(f"glossary term {key!r} cannot be redefined")
        out[key] = value
    return out


class OrchestratorState(TypedDict, total=False):
    """LangGraph state shared across orchestrator nodes."""

    task_metadata: Annotated[dict[str, Any], set_once]
    task_glossary: Annotated[dict[str, Any], write_once_glossary]

    node_outputs: Annotated[dict[str, Any], merge_dict]
    artifacts: Annotated[dict[str, Any], merge_dict]

    claims: Annotated[list[dict[str, Any]], append_list]
    confidence_history: Annotated[list[dict[str, Any]], append_list]
    tool_call_log: Annotated[list[dict[str, Any]], append_list]
    approval_requests: Annotated[list[dict[str, Any]], append_list]

    replan_count: int
    budget_consumed: Annotated[dict[str, Any], merge_dict]
    current_node_id: str | None
