"""Load a local ``.env`` into ``os.environ``.

pydantic-settings reads ``.env`` directly into its config objects, but it
does *not* populate ``os.environ`` — so libraries that read the process
environment (LiteLLM looking for ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``)
and plain ``os.getenv`` calls don't see ``.env`` values. The CLI + live
probes call ``load_local_env`` at startup to bridge that gap.

Deliberately minimal (no python-dotenv dependency) and uses
``setdefault`` so a real exported environment variable always wins over
the file.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: str | Path = ".env") -> int:
    """Populate ``os.environ`` from a ``.env`` file. Returns keys set.

    Lines are ``KEY=VALUE``; blanks and ``#`` comments are skipped;
    surrounding single/double quotes on the value are stripped. Existing
    environment variables are never overwritten.
    """
    p = Path(path)
    if not p.is_file():
        return 0
    set_count = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            set_count += 1
    return set_count
