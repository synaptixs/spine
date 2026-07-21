"""Shareable report — one self-contained HTML file rendered from a ``CurrentState``.

The engineering-grade counterpart to a comprehension tool's ``graph.html``: a single
file a team lead opens in a browser and forwards, with **no platform install**. It packages
the analysis ``current_state`` already computes (blast-radius hotspots, coupling,
god-components, test-coverage gaps, security surface, churn, recommendations) into a
skim-then-drill document.

Design constraints (see ``docs/specs/shareable-report-spec.md``):

- **Self-contained** — all CSS inline in a ``<style>``; zero external requests (invariant #5).
  A saved copy keeps its styling and fetches nothing.
- **Deterministic, no LLM** — same ``CurrentState`` in → same document out (invariant #2). The
  commit SHA is the report's identity; the timestamp is metadata and can be omitted for
  byte-stable diffs.
- **Theme-aware** — light/dark via ``prefers-color-scheme``; it's a page people open.
- **Pure** — ``render_report_html`` does no I/O and is unit-testable on a synthetic state.

Phase 1 (this module) renders every ``CurrentState`` section as tables/prose with a
module-list stand-in for the architecture diagram. Phase 2 replaces that stand-in with a
deterministic inline-SVG layout (``report_svg.py``) and the ``impact_across`` blast-radius
spotlight.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

from orchestrator.knowledge.current_state import _app_type, _overview, architecture_graph
from orchestrator.knowledge.report_svg import architecture_svg
from orchestrator.pkg.facts import NodeKind

if TYPE_CHECKING:
    from orchestrator.knowledge.current_state import CurrentState
    from orchestrator.pkg.store import FactStore

# Sections §4–§7 (blast radius, risk/health, coverage, security) carry developer jargon; the
# stakeholder lens keeps only §1–§3 and §8–§9, in plain language — mirrors the markdown split.
_STAKEHOLDER_LENS = "stakeholder"


def _e(text: object) -> str:
    """HTML-escape any value for safe interpolation into the document."""
    return html.escape(str(text), quote=True)


def _prose(text: str) -> str:
    """Escape, then render the only inline markdown the shared prose uses: ``**bold**``.
    `html.escape` leaves ``*`` untouched, so the conversion is safe after escaping."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _e(text))


def _node_count(state: CurrentState) -> int:
    return sum(state.counts.values())


# --- sections -------------------------------------------------------------------------


def _header(
    state: CurrentState, repo_name: str, sha: str | None, timestamp: str | None, grounded: int, edges: int
) -> str:
    """Provenance banner — establishes 'this is your real code, at this commit'."""
    langs = ", ".join(state.languages) or "—"
    chips = [
        ("languages", langs),
        ("nodes", f"{_node_count(state):,}"),
        ("grounded", f"{grounded:,}"),
        ("edges", f"{edges:,}"),
        ("files", f"{state.modules:,}"),
    ]
    if sha:
        chips.insert(0, ("commit", sha[:12]))
    chip_html = "".join(
        f'<span class="chip"><span class="chip-k">{_e(k)}</span><span class="chip-v">{_e(v)}</span></span>'
        for k, v in chips
    )
    meta = f'<p class="generated">Generated {_e(timestamp)}</p>' if timestamp else ""
    return (
        '<header class="report-header">'
        f"<h1>{_e(repo_name)}</h1>"
        f'<p class="subtitle">Codebase intelligence report · {_e(_app_type(state))}</p>'
        f'<div class="chips">{chip_html}</div>'
        f"{meta}"
        "</header>"
    )


def _section(title: str, subtitle: str, body: str) -> str:
    sub = f'<p class="section-sub">{_e(subtitle)}</p>' if subtitle else ""
    return f"<section><h2>{_e(title)}</h2>{sub}{body}</section>"


def _overview_section(state: CurrentState) -> str:
    return _section("Overview", "", f'<p class="lede">{_prose(_overview(state))}</p>')


def _architecture_section(state: CurrentState) -> str:
    """The deterministic inline-SVG architecture diagram (zones → components, weighted
    arrows), plus the strongest dependency edges as a table beneath it."""
    if not (state.area_types or state.area_funcs):
        return ""
    svg = architecture_svg(state)
    shown = len(architecture_graph(state)[0])
    total = len(set(state.area_types) | set(state.area_funcs))
    body = f'<div class="arch-wrap">{svg}</div>' if svg else ""
    if total > shown:
        body += f'<p class="section-sub">Showing the top {shown} of {total} components.</p>'
    if state.coupling:
        rows = "".join(
            f"<tr><td><code>{_e(a)}</code></td><td>→</td>"
            f'<td><code>{_e(b)}</code></td><td class="num">{c}</td></tr>'
            for (a, b), c in state.coupling.most_common(10)
        )
        body += (
            '<h3 class="sub">Component dependencies (strongest)</h3>'
            "<table><thead><tr><th>From</th><th></th><th>To</th>"
            f'<th class="num">Strength</th></tr></thead><tbody>{rows}</tbody></table>'
        )
    return _section(
        "Architecture",
        "Components grouped by zone; arrows are dependency strength (import / #include count).",
        body,
    )


def _top_hotspot_id(state: CurrentState, store: FactStore | None) -> str | None:
    """The node id of the top call-hotspot, for graph queries.

    ``call_hotspots`` keeps only names, so several distinct functions can share the top
    name; resolve to the *same* node the hotspot metric ranked — the same-named function
    with the most callers — not just the first match, so the spotlight and the hotspot
    table describe one node."""
    if store is None or not state.call_hotspots:
        return None
    candidates = [n for n in store.find(state.call_hotspots[0][0]) if n.kind is NodeKind.FUNCTION]
    if not candidates:
        return None
    # Rank by caller count (the hotspot metric); id as a deterministic tiebreak.
    return max(candidates, key=lambda n: (len(store.callers_of(n.id)), n.id)).id


def _spotlight(state: CurrentState, store: FactStore | None) -> str:
    """The differentiator sentence. With a graph, quantify the *cross-layer* blast radius
    (dependents + files touched) via ``impact_across``; without one, fall back to the raw
    call-site count. Deterministic either way — no target-picking beyond the top hotspot."""
    top_name, top_calls = state.call_hotspots[0]
    tid = _top_hotspot_id(state, store)
    if store is not None and tid is not None:
        impacted = store.impact_across(tid)
        if impacted:
            files = {n.provenance.file for n, _ in impacted if n.provenance}
            return (
                f'<p class="spotlight">Changing <code>{_e(top_name)}</code> ripples out to '
                f"<strong>{len(impacted)}</strong> dependents across <strong>{len(files)}</strong> "
                "files — the widest blast radius in the graph.</p>"
            )
    return (
        f'<p class="spotlight">Changing <code>{_e(top_name)}</code> ripples out to '
        f"<strong>{top_calls}</strong> call sites — the most-depended-upon code in the graph.</p>"
    )


def _blast_radius_section(state: CurrentState, store: FactStore | None) -> str:
    if not state.call_hotspots:
        return ""
    rows = "".join(
        f'<tr><td><code>{_e(n)}</code></td><td class="num">{c}</td></tr>' for n, c in state.call_hotspots
    )
    body = (
        _spotlight(state, store) + "<table><thead><tr><th>Function</th>"
        '<th class="num">Called from (sites)</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )
    return _section(
        "Blast-radius hotspots",
        "The functions most other code relies on — where a change reaches furthest.",
        body,
    )


def _risk_section(state: CurrentState) -> str:
    parts: list[str] = []
    if state.layers:
        rows = "".join(
            f'<tr><td>{_e(lyr)}</td><td class="num">{c}</td></tr>' for lyr, c in state.layers.most_common()
        )
        parts.append(
            '<h3 class="sub">Layers</h3>'
            '<table><thead><tr><th>Layer</th><th class="num">Components</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
        )
    if state.hotspots:
        rows = "".join(
            f'<tr><td><code>{_e(n)}</code></td><td class="num">{c}</td><td>{_e(loc)}</td></tr>'
            for n, c, loc in state.hotspots
        )
        dist = " · ".join(f"{_e(k)}: {v}" for k, v in state.size_dist.most_common())
        parts.append(
            f'<h3 class="sub">God-components &amp; complexity</h3>'
            f'<p class="section-sub">Size distribution — {dist}</p>'
            '<table><thead><tr><th>Largest component</th><th class="num">Members</th>'
            f"<th>Location</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    if state.external_deps:
        chips = "".join(
            f'<span class="dep">{_e(d)} <span class="dep-c">{c}</span></span>'
            for d, c in state.external_deps.most_common(12)
        )
        parts.append(f'<h3 class="sub">External-dependency surface</h3><div class="deps">{chips}</div>')
    if not parts:
        return ""
    return _section(
        "Risk & health", "Coupling, oversized components, and the third-party surface.", "".join(parts)
    )


def _blast_coverage(state: CurrentState, store: FactStore | None) -> str:
    """Symbols in the *top hotspot's* blast radius that no test reaches — the "what's
    untested where it matters most" a concept map can't show. Reuses C8's regression plan
    (``build_regression_plan``) at the top call-hotspot; empty without a call graph."""
    tid = _top_hotspot_id(state, store)
    if store is None or tid is None:
        return ""
    from orchestrator.sdlc.coverage import build_regression_plan

    plan = build_regression_plan(store, tid)
    if not plan.call_graph_available or not plan.impacted:
        return ""
    target = f"<code>{_e(plan.target)}</code>"
    uncovered = [it for it in plan.impacted if not it.covered]
    if not uncovered:
        return (
            f'<p class="section-sub">✅ All {len(plan.impacted)} symbols in the blast radius of '
            f"{target} are reached by a test.</p>"
        )
    shown = uncovered[:12]
    items = "".join(
        f'<li><span class="area"><code>{_e(it.name)}</code></span>'
        f'<span class="area-m">{_e(it.where)}</span></li>'
        for it in shown
    )
    more = (
        f'<p class="section-sub">…and {len(uncovered) - len(shown)} more.</p>'
        if len(uncovered) > len(shown)
        else ""
    )
    return (
        f'<h3 class="sub">Untested in the blast radius of {target}</h3>'
        f'<p class="section-sub">{len(uncovered)} of {len(plan.impacted)} impacted symbols have no '
        f'covering test — a change here could break them silently.</p><ul class="gaps">{items}</ul>{more}'
    )


def _coverage_section(state: CurrentState, store: FactStore | None) -> str:
    tested = f"<strong>{state.tested_areas} of {state.areas} areas</strong> have any test type."
    untested = ""
    if state.untested_top:
        items = "".join(
            f'<li><span class="area">{_e(a)}</span>'
            f'<span class="area-m">{c} types, no covering test</span></li>'
            for a, c in state.untested_top
        )
        untested = f'<p class="section-sub">Largest untested areas:</p><ul class="gaps">{items}</ul>'
    return _section(
        "Test-coverage gaps",
        "What a change could break silently — areas with no covering test.",
        f"<p>{tested}</p>{untested}{_blast_coverage(state, store)}",
    )


def _security_section(state: CurrentState) -> str:
    if not state.auth_surface:
        return ""
    chips = "".join(f'<span class="dep">{_e(n)}</span>' for n in state.auth_surface)
    note = (
        '<p class="section-sub">⚠️ Attribute-level auth ([Authorize]/decorators) isn\'t extracted '
        "yet — endpoint access rules can't be confirmed from the graph alone.</p>"
    )
    return _section(
        "Security surface",
        f"{len(state.auth_surface)} auth/security-related types (by name).",
        f'<div class="deps">{chips}</div>{note}',
    )


def _activity_section(state: CurrentState) -> str:
    if not state.recent_areas:
        return ""
    items = "".join(
        f'<li><span class="area">{_e(a)}</span><span class="area-m">{c} changes</span></li>'
        for a, c in state.recent_areas
    )
    return _section(
        "Recent activity",
        "Where change concentrates (last ~60 commits).",
        f'<ul class="gaps">{items}</ul>',
    )


def _recommendations_section(state: CurrentState) -> str:
    if not state.recommendations:
        return ""
    items = "".join(
        f'<li><span class="pri pri-{_e(pri).lower()}">{_e(pri)}</span>{_e(text)}</li>'
        for pri, text in state.recommendations
    )
    return _section(
        "Recommendations",
        "Prioritized, deterministic next actions.",
        f'<ol class="recs">{items}</ol>',
    )


def _toolbar() -> str:
    """A sticky search box. Filtering is done entirely client-side by ``_SCRIPT`` over the
    already-inlined data — the layout stays precomputed; JS only shows/hides (invariant #3,
    #4: no build step, nothing fetched). Degrades to a plain (inert) box with JS disabled."""
    return (
        '<div class="toolbar">'
        '<input id="report-search" type="search" autocomplete="off" spellcheck="false" '
        'placeholder="Filter components, functions, dependencies…" '
        'aria-label="Filter the report">'
        '<span class="toolbar-count" id="report-count" aria-live="polite"></span>'
        "</div>"
    )


def _footer(state: CurrentState) -> str:
    caveat = "Heuristic synthesis from naming + structure. "
    if not state.has_calls:
        caveat += "Call graph not yet available for this language. "
    caveat += f"{state.generated} generated types flagged and excluded from hotspots."
    return (
        "<footer><p>Deterministic snapshot rendered from the Program Knowledge Graph — no LLM, "
        f"nothing fetched. {_e(caveat)}</p></footer>"
    )


# --- document -------------------------------------------------------------------------


def render_report_html(
    state: CurrentState,
    *,
    repo_name: str = "repository",
    sha: str | None = None,
    timestamp: str | None = None,
    lens: str = "developer",
    grounded: int = 0,
    edges: int = 0,
    store: FactStore | None = None,
) -> str:
    """Render a ``CurrentState`` as one self-contained, theme-aware HTML document.

    Pure: no I/O, deterministic for a given ``state`` (the ``timestamp`` is the only
    non-reproducible input — omit it for byte-stable diffs). When a ``store`` is supplied the
    blast-radius spotlight and coverage gaps are quantified from the graph (``impact_across`` /
    ``build_regression_plan``); without one they degrade to the ``state``-only signals. The
    stakeholder ``lens`` drops the jargon-heavy sections (blast radius, risk/health, coverage,
    security).
    """
    body = [_header(state, repo_name, sha, timestamp, grounded, edges), _toolbar(), _overview_section(state)]
    body.append(_architecture_section(state))
    if lens != _STAKEHOLDER_LENS:
        body.append(_blast_radius_section(state, store))
        body.append(_risk_section(state))
        body.append(_coverage_section(state, store))
        body.append(_security_section(state))
    body.append(_activity_section(state))
    body.append(_recommendations_section(state))
    body.append(_footer(state))
    main = "\n".join(part for part in body if part)
    title = f"{_e(repo_name)} — codebase report"
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{_CSS}</style>\n</head>\n"
        f'<body><main class="report">{main}</main>\n<script>{_SCRIPT}</script>\n</body>\n</html>\n'
    )


_CSS = """
:root{
  --bg:#f7f8fa;--card:#fff;--fg:#1a1d24;--muted:#5c6370;
  --line:#e3e6eb;--accent:#3b6ef5;--chip:#eef1f6;--code:#f0f2f5;
  --hi:#8a4b00;--hi-bg:#fff5e6;--pri-p0:#c0362c;--pri-p1:#b06a00;--pri-p2:#2f7d4f;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0f1115;--card:#171a21;--fg:#e6e9ef;--muted:#9aa2b1;--line:#262b35;
    --accent:#6d97ff;--chip:#20252f;--code:#1c212b;--hi:#ffcf8a;--hi-bg:#2a2013;
    --pri-p0:#ff6b5e;--pri-p1:#e0a144;--pri-p2:#5fce8f;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.report{max-width:960px;margin:0 auto;padding:2rem 1.25rem 4rem}
code{background:var(--code);padding:.1em .35em;border-radius:4px;
  font:.86em/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h1{font-size:1.9rem;margin:0 0 .2rem}
h2{font-size:1.3rem;margin:0 0 .3rem;padding-bottom:.35rem;border-bottom:2px solid var(--line)}
h3{font-size:1rem;margin:1.2rem 0 .5rem}
h3.sub{color:var(--muted);text-transform:uppercase;letter-spacing:.04em;font-size:.78rem}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:1.25rem 1.4rem;margin:1rem 0}
.report-header{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:1.5rem 1.4rem;margin-bottom:1rem}
.subtitle{color:var(--muted);margin:.1rem 0 1rem}
.section-sub{color:var(--muted);font-size:.9rem;margin:.1rem 0 .8rem}
.lede{font-size:1.05rem;line-height:1.6}
.chips{display:flex;flex-wrap:wrap;gap:.5rem}
.chip{display:inline-flex;flex-direction:column;background:var(--chip);
  border-radius:8px;padding:.4rem .7rem;min-width:64px}
.chip-k{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.chip-v{font-weight:600;font-size:.95rem}
.generated{color:var(--muted);font-size:.8rem;margin:1rem 0 0}
table{width:100%;border-collapse:collapse;margin:.5rem 0;font-size:.9rem}
th,td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:.8rem;text-transform:uppercase;letter-spacing:.03em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.zones{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:.8rem}
.zone{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:.8rem .9rem}
.zone h3{margin:0 0 .5rem;font-size:.95rem}
.zone ul,.gaps{list-style:none;margin:0;padding:0}
.zone li,.gaps li{display:flex;justify-content:space-between;gap:.5rem;
  padding:.25rem 0;border-bottom:1px solid var(--line)}
.zone li:last-child,.gaps li:last-child{border-bottom:0}
.area{font-weight:500}.area-m{color:var(--muted);font-size:.82rem;white-space:nowrap}
.spotlight{background:var(--hi-bg);color:var(--hi);border-radius:10px;
  padding:.8rem 1rem;margin:.2rem 0 1rem;font-size:1rem}
.spotlight code{background:rgba(0,0,0,.08)}
.deps{display:flex;flex-wrap:wrap;gap:.4rem;margin:.3rem 0}
.dep{background:var(--chip);border-radius:6px;padding:.25rem .55rem;font-size:.85rem}
.dep-c{color:var(--muted);font-size:.78rem}
.recs{margin:.3rem 0;padding-left:0;list-style:none;counter-reset:r}
.recs li{padding:.5rem 0;border-bottom:1px solid var(--line);display:flex;gap:.6rem;align-items:baseline}
.recs li:last-child{border-bottom:0}
.pri{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
  padding:.15rem .45rem;border-radius:5px;background:var(--chip);white-space:nowrap}
.pri-p0{color:var(--pri-p0)}.pri-p1{color:var(--pri-p1)}.pri-p2{color:var(--pri-p2)}
.arch-wrap{overflow-x:auto;margin:.3rem 0 1rem;padding-bottom:.3rem}
svg.arch{display:block;min-width:520px;max-width:100%;height:auto}
.arch-zone{fill:var(--bg);stroke:var(--line);stroke-width:1}
.arch-zone-label{fill:var(--muted);font-size:12px;font-weight:600;
  text-transform:uppercase;letter-spacing:.04em}
.arch-node rect{fill:var(--card);stroke:var(--line);stroke-width:1.5}
.arch-name{fill:var(--fg);font-size:12.5px;font-weight:600}
.arch-meta{fill:var(--muted);font-size:10.5px}
.arch-edge{stroke:var(--accent);stroke-width:1.5;fill:none;opacity:.55}
.arch-head{fill:var(--accent);opacity:.7}
.arch-weight{fill:var(--muted);font-size:10px;font-variant-numeric:tabular-nums}
.arch-node{transition:opacity .12s}
.arch-node.dim{opacity:.16}
.toolbar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:.7rem;
  background:var(--bg);padding:.7rem 0;margin:.2rem 0 .4rem}
#report-search{flex:1;font:15px/1.4 inherit;color:var(--fg);background:var(--card);
  border:1px solid var(--line);border-radius:9px;padding:.55rem .8rem}
#report-search:focus{outline:2px solid var(--accent);outline-offset:1px}
.toolbar-count{color:var(--muted);font-size:.82rem;white-space:nowrap}
tr[hidden],li[hidden],.dep[hidden]{display:none!important}
section.dim{display:none}
footer{color:var(--muted);font-size:.82rem;margin-top:1.5rem;text-align:center}
"""

# Client-side filter. Reads the already-rendered text (no data duplicated), hides
# non-matching rows / list items / dep chips, dims non-matching SVG components, and hides a
# section once all its filter targets are hidden. Layout is untouched — this only toggles
# visibility (invariant #3). Vanilla JS, no deps, no build step (invariant #4).
_SCRIPT = """
(function(){
  var q=document.getElementById('report-search'),c=document.getElementById('report-count');
  if(!q)return;
  var ROW='table tbody tr, .gaps li, .recs li, .deps .dep';
  var rows=[].slice.call(document.querySelectorAll(ROW.replace(/(^|,)/g,'$1.report ')));
  var nodes=[].slice.call(document.querySelectorAll('.report svg.arch .arch-node'));
  var secs=[].slice.call(document.querySelectorAll('.report section'));
  function nodeName(g){var t=g.querySelector('.arch-name');return t?t.textContent.toLowerCase():'';}
  function apply(){
    var term=q.value.trim().toLowerCase(),shown=0;
    rows.forEach(function(r){
      var hit=!term||r.textContent.toLowerCase().indexOf(term)>=0;
      r.hidden=!hit; if(hit)shown++;
    });
    nodes.forEach(function(g){
      g.classList.toggle('dim',!!term&&nodeName(g).indexOf(term)<0);
    });
    secs.forEach(function(s){
      var t=s.querySelectorAll(ROW);
      if(!t.length){return;}
      var vis=0;
      for(var i=0;i<t.length;i++){if(!t[i].hidden)vis++;}
      s.classList.toggle('dim',!vis);
    });
    c.textContent=term?(shown+' match'+(shown===1?'':'es')):'';
  }
  q.addEventListener('input',apply);
  q.addEventListener('keydown',function(e){if(e.key==='Escape'){q.value='';apply();}});
})();
"""

__all__ = ["render_report_html"]
