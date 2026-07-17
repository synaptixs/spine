"""md.js — the dependency-free markdown + mermaid renderer for the web UI.

The frontend has no build step and no npm, so there is no JS test harness to hook into.
These drive `md.js` through `node` instead and skip when it is absent (the same shape as
the toolchain-gated extractor tests). CI has node; a contributor without it still gets a
green local run.

Why this file exists at all: `md.js` renders our generated mermaid to inline SVG by hand
rather than vendoring mermaid (~2.6MB against a ~100KB frontend that ships inside the pip
wheel, and must work air-gapped). Hand-rolled means the edge cases below are ours to keep.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

MD_JS = Path("src/orchestrator/registry/api/web/static/md.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _render(markdown: str) -> str:
    """Run window.renderMarkdown(markdown) under node and return the HTML."""
    script = textwrap.dedent(f"""
        const fs = require("fs");
        global.window = {{}};
        eval(fs.readFileSync({json.dumps(str(MD_JS))}, "utf8"));
        process.stdout.write(global.window.renderMarkdown({json.dumps(markdown)}));
    """)
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return out.stdout


def _mermaid(body: str) -> str:
    return f"```mermaid\n{body}\n```"


def test_mermaid_renders_to_inline_svg_not_a_code_block() -> None:
    """The bug this fixes: every fence became <pre><code>, so our one diagram showed
    as raw arrow syntax in our own UI while looking fine on GitHub."""
    html = _render(_mermaid('flowchart LR\n  n0["a"]\n  n1["b"]\n  n0 --> n1'))
    assert "<svg" in html and "<pre>" not in html
    assert html.count("<rect") == 2


def test_mermaid_renders_the_state_shape_subgraphs_and_weighted_edges() -> None:
    """`state --lens developer` emits subgraph zones, `<br/>` in labels, and |count| edges."""
    html = _render(
        _mermaid('flowchart LR\n  subgraph z["Source"]\n    a["src/api<br/>3 types"]\n  end\n  a -->|12| a')
    )
    assert "<svg" in html
    assert ">12<" in html  # edge label survives
    assert html.count("<tspan") == 2  # <br/> became two lines
    assert "Source" in html  # zone surfaced in the caption


def test_unknown_mermaid_construct_falls_back_to_a_code_block() -> None:
    """We only claim the subset we generate. No picture beats a wrong picture."""
    html = _render(_mermaid("flowchart LR\n  a{{hexagon}}\n  a --> b"))
    assert "<pre>" in html and "<svg" not in html


def test_non_mermaid_fences_are_untouched() -> None:
    assert "<pre>" in _render("```python\nx = 1\n```")


def test_mermaid_labels_are_escaped() -> None:
    """Labels reach the renderer from generated docs; they must never inject markup."""
    html = _render(_mermaid('flowchart LR\n  a["<img src=x onerror=alert(1)>"]\n  a --> a'))
    assert "<img" not in html and "&lt;img" in html


def test_mutual_dependency_stays_inside_the_viewbox() -> None:
    """A 2-node cycle ratchets raw column indices to {2,3}; sizing by column *count*
    while positioning by index pushed both nodes outside the viewBox, clipping them.
    Mutual imports between areas are real, so this is not a theoretical case."""
    html = _render(_mermaid('flowchart LR\n  n0["a"]\n  n1["b"]\n  n0 --> n1\n  n1 --> n0'))
    import re

    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', html)
    assert vb is not None
    width, height = float(vb.group(1)), float(vb.group(2))
    rects = [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', html
        )
    ]
    assert rects
    for x, y, w, h in rects:
        assert x >= 0 and y >= 0
        assert x + w <= width, "node clipped off the right edge"
        assert y + h <= height, "node clipped off the bottom"
