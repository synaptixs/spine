const form = document.getElementById('login');
const msg = document.getElementById('msg');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const key = document.getElementById('key').value.trim();
  if (!key) return;
  msg.textContent = 'Signing in…';
  try {
    const r = await fetch('/login', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({api_key: key}),
    });
    if (r.ok) { window.location = '/app'; return; }
    msg.innerHTML = '<span class="err">Invalid API key.</span>';
  } catch (err) {
    msg.innerHTML = '<span class="err">Sign-in failed.</span>';
  }
});
