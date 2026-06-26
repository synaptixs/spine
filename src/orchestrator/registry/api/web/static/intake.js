const f = document.getElementById('f');
const out = document.getElementById('out');
const statusEl = document.getElementById('status');
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const list = xs => (xs && xs.length)
  ? '<ul>' + xs.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>' : '';

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  const source = document.getElementById('source').value.trim();
  if (!source) return;
  out.innerHTML = '';
  statusEl.textContent = 'Analyzing…';
  document.getElementById('go').disabled = true;
  try {
    const r = await fetch('/v1/intake/preview', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({source}),
    });
    const data = await r.json();
    if (!r.ok) {
      statusEl.innerHTML = '<span class="err">' + esc(data.detail || r.status) + '</span>';
      return;
    }
    statusEl.textContent = '';
    render(data);
  } catch (err) {
    statusEl.innerHTML = '<span class="err">' + esc(err) + '</span>';
  } finally {
    document.getElementById('go').disabled = false;
  }
});

function render(d) {
  let h = '';
  h += '<p>' + d.documents + ' document(s) read' + (d.truncated ? ' (truncated)' : '') + '. ';
  h += d.blocked
    ? '<span class="blocked">Blocked: gaps gate intent approval.</span>'
    : 'No blocking gaps.';
  h += '</p>';

  if (d.gaps.length) {
    h += '<h2>Gaps</h2>';
    for (const g of d.gaps) {
      h += '<div class="gap ' + esc(g.severity) + '"><span class="badge">' + esc(g.severity)
         + '</span> <strong>' + esc(g.intent) + '</strong>: ' + esc(g.message) + '</div>';
    }
  }

  h += '<h2>Specs (' + d.specs.length + ')</h2>';
  for (const s of d.specs) {
    h += '<div class="card"><strong>' + esc(s.title) + '</strong>'
       + (s.estimate ? ' <span class="badge">' + esc(s.estimate) + '</span>' : '');
    if (s.summary) h += '<p>' + esc(s.summary) + '</p>';
    if (s.user_story) h += '<p><em>' + esc(s.user_story) + '</em></p>';
    if (s.acceptance_criteria.length) h += '<p>Acceptance criteria:</p>' + list(s.acceptance_criteria);
    if (s.dependencies.length) h += '<p>Dependencies:</p>' + list(s.dependencies);
    h += '</div>';
  }
  out.innerHTML = h;
}
