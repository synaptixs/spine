"""Absolute imports spelled the way the project runs: from the repo root, through `src`."""

from src.agents import agent
from src.services.limiter import limit


def run() -> int:
    """Calls one imported function and one function on an imported module."""
    return limit() + agent.act()
