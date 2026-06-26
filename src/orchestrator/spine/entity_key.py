"""The Entity Key contract — Spine's universal join (Phase 0).

``Component_vX::Region::Interface`` is the one identity every stage shares:
ontomesh mints it (entity identity), infodrift scopes drift by it, the
orchestrator's PKG carries it on mapped code nodes. One key threads
domain → code → deployment → drift, so a single lineage is queryable end to end.

Examples (from the infodrift reference domains)::

    AMF_v2::RegionA::N11
    FraudDetector_v5::APAC::CardTransactions
"""

from __future__ import annotations

from dataclasses import dataclass

_SEP = "::"
_VER = "_v"


class EntityKeyError(ValueError):
    """A string did not parse as a valid entity key."""


@dataclass(frozen=True)
class EntityKey:
    """A deployment unit's identity: component + version + region + interface.

    ``version`` is kept as a string (``"2"``, ``"5"``, ``"2.1"``) — versions are
    identifiers, not numbers to do arithmetic on.
    """

    component: str
    version: str
    region: str
    interface: str

    def __post_init__(self) -> None:
        for field_name in ("component", "version", "region", "interface"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise EntityKeyError(f"entity-key {field_name} must be a non-empty string")
            if _SEP in value:
                raise EntityKeyError(f"entity-key {field_name} must not contain {_SEP!r}: {value!r}")

    def __str__(self) -> str:
        return self.format()

    def format(self) -> str:
        """Render the canonical ``Component_vX::Region::Interface`` string."""
        return f"{self.component}{_VER}{self.version}{_SEP}{self.region}{_SEP}{self.interface}"

    @property
    def component_version(self) -> str:
        """The ``Component_vX`` head, without region/interface."""
        return f"{self.component}{_VER}{self.version}"

    @classmethod
    def parse(cls, text: str) -> EntityKey:
        """Parse a canonical entity-key string, or raise ``EntityKeyError``."""
        if not isinstance(text, str):
            raise EntityKeyError(f"entity key must be a string, got {type(text).__name__}")
        parts = text.split(_SEP)
        if len(parts) != 3:
            raise EntityKeyError(f"entity key must have exactly 3 {_SEP!r}-separated parts: {text!r}")
        comp_ver, region, interface = parts
        if _VER not in comp_ver:
            raise EntityKeyError(f"entity key head must contain {_VER!r} (Component_vX): {comp_ver!r}")
        # Split on the LAST _v so a component name may itself contain it.
        component, version = comp_ver.rsplit(_VER, 1)
        return cls(component=component, version=version, region=region, interface=interface)

    @classmethod
    def is_valid(cls, text: str) -> bool:
        """True when ``text`` parses as a canonical entity key."""
        try:
            cls.parse(text)
        except EntityKeyError:
            return False
        return True


__all__ = ["EntityKey", "EntityKeyError"]
