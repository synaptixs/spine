// Shared client helpers for the capability-job pages (understand / state / graph).
// Starts a job, streams live progress messages over SSE (best-effort), and polls
// /v1/jobs/{id} as the source of truth for completion. Exposes window.spine.
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function apiJSON(path, opts) {
    const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
    if (res.status === 401) { window.location = "/login"; throw new Error("session expired"); }
    if (!res.ok) throw new Error("HTTP " + res.status + " on " + path);
    return res.status === 204 ? null : res.json();
  }

  async function fetchArtifact(jobId) {
    const res = await fetch("/v1/jobs/" + encodeURIComponent(jobId) + "/artifact");
    if (res.status === 401) { window.location = "/login"; throw new Error("session expired"); }
    if (!res.ok) throw new Error("no artifact (HTTP " + res.status + ")");
    return res.text();
  }

  // Start a capability job; call onProgress(msg) for each streamed stage; resolve
  // with the completed job summary, or reject with the recorded error.
  async function runJob(startPath, body, onProgress) {
    const started = await apiJSON(startPath, { method: "POST", body: JSON.stringify(body || {}) });
    const jobId = started.job_id;
    let es = null;
    try {
      es = new EventSource("/v1/stream?run_id=" + encodeURIComponent(jobId));
      es.addEventListener("run.stage", (ev) => {
        try { const d = JSON.parse(ev.data); const m = d.payload && d.payload.after && d.payload.after.message; if (m && onProgress) onProgress(m); } catch (e) { /* ignore */ }
      });
    } catch (e) { /* SSE unavailable — polling still drives completion */ }
    try {
      for (;;) {
        await sleep(1200);
        const job = await apiJSON("/v1/jobs/" + encodeURIComponent(jobId));
        if (job.state === "completed") return job;
        if (job.state === "failed") throw new Error(job.error || "job failed");
      }
    } finally { if (es) es.close(); }
  }

  window.spine = { apiJSON, fetchArtifact, runJob, sleep };
})();
