"""The validator that guards the design seam — the clause that makes promoting it legal.

Every model output in this pipeline has a deterministic check downstream: intake's spec is
judged by `assess()`, implement's code by tests plus preflight plus the fit check, review's
fixes by re-running the tests. `design` has none, and it is safe today only because it calls no
model. The moment `_llm_design` is wired, model-written design text flows into codegen as
`ctx.plan` unchecked, and a hallucinated path or invented symbol rides into the change.

So the determinism boundary's promotion rule requires **a validator on the output edge** before
a node may become a model node. This is that validator.

## What it can enforce, and what it must not

`impact.unverified_references` already computes *"design-named paths absent from the graph"* and
**reports** them. Making that enforcing as-is would break every legitimate change: a design that
creates `src/orchestrator/sdlc/new_thing.py` names a path the graph has never heard of, and it
is right to.

The design dict cannot say which references it *adds* and which it *modifies* — `interfaces` is
documented as "signatures/types to **add or change**" — so any rule of the form "everything named
must already exist" produces a false refusal on every create.

**The rule that survives that:** you can build a new house on a real street; you cannot build one
on a street that does not exist.

| Named | Verdict |
|---|---|
| `report.py` — a file the graph holds | fine |
| `sdlc/new_thing.py` — new file, **existing** directory | fine, it is being created |
| `made_up_pkg/thing.py` — parent directory exists nowhere | **fabrication** |
| `orchestrator.sdlc.design.NewHelper` — new symbol in a **real** module | fine |
| `orchestrator.invented.Thing` — module prefix resolves to nothing | **fabrication** |

Only the fabrications refuse. Everything else stays reported, exactly as today.

**Greenfield is suppressed**, on the same grounds `unverified_references` suppresses it: when the
graph holds no grounded nodes, everything is legitimately absent and flagging it all would be
noise rather than signal.

Deterministic — the graph and the filesystem answer, no model. Kept whether or not `_llm_design`
is ever switched on: it costs nothing and guards the seam permanently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import NodeKind

__all__ = ["DesignFinding", "DesignValidation", "validate_design"]

# Fields a design may name code in. `approach` and `test_strategy` are prose and are not mined:
# a sentence mentioning a module is not a claim to touch it, and treating it as one would refuse
# designs for describing the repository correctly.
_REFERENCE_FIELDS = ("files_to_touch", "interfaces", "data_changes")


@dataclass(frozen=True)
class DesignFinding:
    """One reference the design invented."""

    field: str  # which design field named it
    named: str  # the reference as the design wrote it
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "named": self.named, "detail": self.detail}


@dataclass(frozen=True)
class DesignValidation:
    """The verdict on one design."""

    findings: tuple[DesignFinding, ...] = ()
    grounded: bool = True
    reported: tuple[str, ...] = ()  # absent-but-legal references, carried for the reader

    @property
    def ok(self) -> bool:
        """A design with no fabricated references. An ungrounded repo always passes."""
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "grounded": self.grounded,
            "findings": [f.to_dict() for f in self.findings],
            "reported": list(self.reported),
        }

    def render(self) -> str:
        if not self.grounded:
            return (
                "_Design references unchecked: the graph holds no grounded nodes, so every "
                "reference is legitimately absent._"
            )
        if self.ok:
            reported = f" {len(self.reported)} reference(s) are new, which is legal." if self.reported else ""
            return f"_Every reference the design names resolves, or is new in a real place.{reported}_"
        lines = ["**The design names code that does not exist:**", ""]
        lines += [f"- `{f.named}` ({f.field}) — {f.detail}" for f in self.findings]
        return "\n".join(lines)


def _known_dirs(store: FactStore, root: Path | None) -> set[str]:
    """Directories the repository actually has, from the graph and from disk.

    Both, because the graph only knows directories that contain extracted source: a design
    placing a file in `docs/` or `scripts/` is naming a real place the graph cannot see.
    """
    dirs: set[str] = {""}
    for node in store.nodes:
        if node.provenance is None:
            continue
        parts = PurePosixPath(node.provenance.file.replace("\\", "/")).parts
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    if root is not None:
        with_root = Path(root)
        for path in with_root.rglob("*"):
            if path.is_dir() and not any(p.startswith(".") for p in path.parts):
                dirs.add(path.relative_to(with_root).as_posix())
    return dirs


def _module_names(store: FactStore) -> set[str]:
    return {n.name.lower() for n in store.nodes if n.kind is NodeKind.MODULE}


def _looks_like_path(ref: str) -> bool:
    return "/" in ref or ref.endswith((".py", ".ts", ".tsx", ".java", ".cs", ".go", ".sql", ".c", ".h"))


def _check_path(ref: str, *, dirs: set[str]) -> str:
    """ "" when the path is fine; otherwise why it is a fabrication."""
    parent = PurePosixPath(ref.replace("\\", "/")).parent.as_posix()
    parent = "" if parent == "." else parent
    if parent in dirs:
        return ""
    return f"no directory `{parent}` exists in this repository, so the file cannot be created there"


def _check_symbol(ref: str, *, store: FactStore, modules: set[str]) -> str:
    """ "" when the symbol is fine; otherwise why its address is invented.

    Only dotted references are judged. A bare name (`OrderTotals`, `def render(x: int)`) says
    nothing about *where* it lives, so it is a new symbol somewhere and not checkable here.
    """
    text = ref.strip().strip("`")
    if "." not in text or " " in text or "(" in text:
        return ""
    prefix = text.rsplit(".", 1)[0].lower()
    if prefix in modules or store.find(text.rsplit(".", 1)[-1]):
        return ""
    if any(m.endswith(prefix) or prefix.endswith(m) for m in modules):
        return ""
    return f"no module `{prefix}` exists in this repository, so the symbol has no address"


def validate_design(
    design: dict[str, Any],
    *,
    store: FactStore,
    root: Path | str | None = None,
) -> DesignValidation:
    """Judge a design's references against the graph and the filesystem. Deterministic."""
    grounded = store.summary().get("grounded_nodes", 0) > 0
    if not grounded:
        return DesignValidation(grounded=False)

    dirs = _known_dirs(store, Path(root) if root else None)
    modules = _module_names(store)
    known_files = {n.provenance.file for n in store.nodes if n.provenance is not None}

    findings: list[DesignFinding] = []
    reported: list[str] = []
    for field in _REFERENCE_FIELDS:
        for raw in design.get(field) or []:
            ref = str(raw).strip()
            if not ref:
                continue
            if _looks_like_path(ref):
                if ref in known_files or (root is not None and (Path(root) / ref).exists()):
                    continue
                problem = _check_path(ref, dirs=dirs)
            else:
                problem = _check_symbol(ref, store=store, modules=modules)
                if not problem and "." in ref:
                    continue
            if problem:
                findings.append(DesignFinding(field=field, named=ref, detail=problem))
            else:
                reported.append(ref)
    # Sorted: findings are compared between runs and rendered into artifacts, and dict/set
    # iteration upstream would otherwise order them by hash.
    return DesignValidation(
        findings=tuple(sorted(findings, key=lambda f: (f.field, f.named))),
        grounded=True,
        reported=tuple(sorted(set(reported))),
    )
