"""Policy-as-code for the agentic loop (Bet 2a).

A declarative policy, evaluated before every tool call, that says what the agent
may do: allow / deny / require-approval per tool, with path scoping for file
writes and glob matching for MCP tools. Not a second ``eval()`` — just
predicates over the tool name + arguments. Loaded from a YAML/JSON file
(``orchestrator``'s usual no-code-config shape).

``require_approval`` is treated as a (recorded) refusal in 2a — a real mid-loop
human pause is 2c. Either way the decision is surfaced and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


class PolicyAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ToolRule:
    """A rule for one tool (or glob). ``paths`` (file tools) scopes ``action``:
    in-scope paths get ``action``; anything else gets ``else_action``."""

    action: PolicyAction
    paths: tuple[str, ...] | None = None
    else_action: PolicyAction = PolicyAction.DENY


@dataclass(frozen=True)
class Decision:
    action: PolicyAction
    reason: str

    @property
    def allowed(self) -> bool:
        return self.action is PolicyAction.ALLOW


@dataclass
class Policy:
    """Per-tool rules + a default for anything unlisted."""

    rules: dict[str, ToolRule] = field(default_factory=dict)
    default: PolicyAction = PolicyAction.ALLOW
    budget_usd: float | None = None

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> Decision:
        rule = self._match(tool_name)
        if rule is None:
            return Decision(self.default, "no matching rule (default)")
        if rule.paths is not None:
            paths = [str(f.get("path", "")) for f in (arguments.get("files") or []) if isinstance(f, dict)]
            if paths and all(any(fnmatch(p, g) for g in rule.paths) for p in paths):
                return Decision(rule.action, f"all paths within {list(rule.paths)}")
            return Decision(rule.else_action, f"path(s) outside {list(rule.paths)}")
        return Decision(rule.action, f"rule for {tool_name!r}")

    def _match(self, tool_name: str) -> ToolRule | None:
        if tool_name in self.rules:
            return self.rules[tool_name]
        for pattern, rule in self.rules.items():
            if "*" in pattern and fnmatch(tool_name, pattern):
                return rule
        return None

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> Policy:
        rules: dict[str, ToolRule] = {}
        for name, raw in (doc.get("tools") or {}).items():
            rules[name] = _parse_rule(raw)
        default = PolicyAction(doc.get("default", "allow"))
        budget = doc.get("budget_usd")
        return cls(rules=rules, default=default, budget_usd=float(budget) if budget is not None else None)

    @classmethod
    def from_file(cls, path: str | Path) -> Policy:
        import json

        text = Path(path).read_text(encoding="utf-8")
        try:
            import yaml

            doc = yaml.safe_load(text)
        except ImportError:
            doc = json.loads(text)
        return cls.from_dict(doc or {})


def _parse_rule(raw: Any) -> ToolRule:
    if isinstance(raw, str):
        return ToolRule(action=PolicyAction(raw))
    if isinstance(raw, dict):
        if "action" in raw:
            action = PolicyAction(raw["action"])
        else:
            action = PolicyAction.ALLOW if raw.get("allow") else PolicyAction.DENY
        paths = tuple(raw["paths"]) if raw.get("paths") else None
        else_action = PolicyAction(raw.get("else", "deny"))
        return ToolRule(action=action, paths=paths, else_action=else_action)
    raise ValueError(f"invalid tool rule: {raw!r}")


__all__ = ["Decision", "Policy", "PolicyAction", "ToolRule"]
