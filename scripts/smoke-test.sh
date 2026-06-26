#!/usr/bin/env bash
# End-to-end smoke test for the foundation.
#
# What it does:
#   1. Verifies prerequisites (docker, uv, ANTHROPIC_API_KEY or OPENAI_API_KEY).
#   2. Brings up Postgres via docker-compose.dev.yml (idempotent).
#   3. Runs Alembic migrations.
#   4. Boots the registry+task API on :8000 in the background.
#   5. Registers and publishes the research_agent template.
#   6. Submits a task with no template pinned (exercises the planner).
#   7. Submits a task with template pinned (exercises the direct path).
#   8. Tears down the API process. Postgres stays up.
#
# Usage:
#   ANTHROPIC_API_KEY=sk-ant-... ./scripts/smoke-test.sh
#   OPENAI_API_KEY=sk-...        ./scripts/smoke-test.sh
#
# To stop the local Postgres afterwards:
#   docker compose -f docker-compose.dev.yml down

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

API_URL="${ORCHESTRATOR_API_URL:-http://localhost:8000}"
API_KEY="${ORCHESTRATOR_API_KEY:-dev-key}"

red() { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
blue() { printf "\033[34m%s\033[0m\n" "$*"; }

step() { blue ">>> $*"; }
ok() { green "    ok"; }
fail() { red "    FAIL: $*"; exit 1; }

# --- prerequisites --------------------------------------------------------
step "checking prerequisites"
command -v docker >/dev/null || fail "docker not found on PATH"
command -v uv >/dev/null || fail "uv not found on PATH"
command -v curl >/dev/null || fail "curl not found on PATH"
command -v jq >/dev/null || fail "jq not found on PATH (brew install jq)"
if [[ -z "${ANTHROPIC_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
    fail "set ANTHROPIC_API_KEY or OPENAI_API_KEY before running"
fi
ok

# --- postgres -------------------------------------------------------------
step "ensuring Postgres is up"
docker compose -f docker-compose.dev.yml up -d postgres >/dev/null
# wait for healthcheck
for _ in {1..30}; do
    if docker compose -f docker-compose.dev.yml exec -T postgres pg_isready -U orchestrator -d orchestrator >/dev/null 2>&1; then
        ok
        break
    fi
    sleep 1
done

# --- migrations -----------------------------------------------------------
step "applying Alembic migrations"
uv run alembic upgrade head >/dev/null
ok

# --- start the API --------------------------------------------------------
step "starting registry+task API on :8000"
LOG_FILE="$REPO_ROOT/.smoke-api.log"
: > "$LOG_FILE"   # truncate on each run
ORCHESTRATOR_API_KEY="$API_KEY" \
    uv run uvicorn 'orchestrator.registry.api.app:create_app' \
    --factory --host 127.0.0.1 --port 8000 --log-level info >"$LOG_FILE" 2>&1 &
API_PID=$!
on_exit() {
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
}
trap on_exit EXIT
dump_log_on_failure() {
    red "    last 40 lines of API log:"
    tail -n 40 "$LOG_FILE" | sed 's/^/      /'
}

for _ in {1..30}; do
    if curl -sf "$API_URL/healthz" >/dev/null 2>&1; then
        ok
        break
    fi
    sleep 0.5
done
curl -sf "$API_URL/healthz" >/dev/null || { red "    API failed to start:"; cat "$LOG_FILE"; exit 1; }

# --- register + publish the research_agent --------------------------------
step "registering research_agent template"
REGISTER_RESP=$(
    uv run orchestrator template register examples/templates/research_agent.yaml 2>&1
) || true
if echo "$REGISTER_RESP" | grep -q '"status": "draft"\|exists'; then
    ok
else
    red "    unexpected response:"
    echo "$REGISTER_RESP"
    exit 1
fi

step "publishing research_agent@0.1.0"
uv run orchestrator template publish agent.research 0.1.0 >/dev/null 2>&1 \
    || true  # may already be published from a prior run
PUBLISHED=$(curl -sf "$API_URL/v1/agent-templates/agent.research/0.1.0" \
    -H "X-API-Key: $API_KEY" | jq -r .status)
[[ "$PUBLISHED" == "published" ]] && ok || fail "template not published, got $PUBLISHED"

# Submit a task with --max-time so we fail loud instead of hanging. Anthropic
# Opus calls regularly take 20-60 seconds; the timeout is generous.
TASK_TIMEOUT=180

submit_task() {
    local label="$1" body="$2"
    step "$label"
    local resp http_code
    resp=$(curl -s --max-time "$TASK_TIMEOUT" -w "\n%{http_code}" \
        -X POST "$API_URL/v1/tasks" \
        -H "X-API-Key: $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$body") || {
        red "    curl failed (timeout or connection error)"
        dump_log_on_failure
        exit 1
    }
    http_code=$(printf '%s' "$resp" | tail -n1)
    resp=$(printf '%s' "$resp" | sed '$d')
    if [[ "$http_code" != "200" ]]; then
        red "    HTTP $http_code: $resp"
        dump_log_on_failure
        exit 1
    fi
    printf '%s\n' "$resp" | jq '{task_id, trace_id, template,
        verifier_outcome: .verifier.outcome,
        planner_justification,
        confidence: .output.confidence,
        findings: .output.findings,
        cost_usd: (.output.cost_usd // null)}'
    [[ "$(printf '%s' "$resp" | jq -r .verifier.outcome)" == "pass" ]] || {
        red "    verifier did not pass: $(printf '%s' "$resp" | jq -c .verifier)"
        dump_log_on_failure
        exit 1
    }
    ok
}

submit_task "submitting task #1 — planner picks the template" \
    '{"objective": "Do antibiotics work against viral infections?"}'

submit_task "submitting task #2 — caller pins the template" \
    '{"objective": "Summarize how an mRNA vaccine works in two sentences.",
      "template": {"id": "agent.research", "version": "0.1.0"}}'

# --- audit log sanity check ----------------------------------------------
step "verifying audit log captured both submissions"
AUDIT_COUNT=$(docker compose -f docker-compose.dev.yml exec -T postgres \
    psql -U orchestrator -d orchestrator -tA \
    -c "SELECT count(*) FROM audit_log WHERE action = 'task_submit'")
[[ "$AUDIT_COUNT" -ge 2 ]] && ok \
    || fail "expected >=2 task_submit rows, got $AUDIT_COUNT"

green ""
green "Foundation smoke test passed."
green "  API:      $API_URL"
green "  Docs:     $API_URL/docs"
green "  API log:  $LOG_FILE  (overwritten on next run)"
