#!/usr/bin/env bash
# Smoke-test the shared-server image the way an admin would meet it: it refuses to start with
# no token check, then with no repository list; with both, a call with no token gets 401, a
# signed-in call on an unlisted path is refused and audited, and a listed repository is mapped.
# Used by the `container` job in ci.yml and the release image job; runs locally the same way:
#
#   docker build -f deploy/Dockerfile -t spine-mcp:smoke . && bash deploy/smoke.sh spine-mcp:smoke
set -euo pipefail

image="${1:?usage: smoke.sh <image>}"
name="spine-smoke-$$"
port="${SMOKE_PORT:-18080}"
token="smoke-$(date +%s)-$$"
work="$(mktemp -d)"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
fail() {
  echo "SMOKE FAIL: $*" >&2
  docker logs "$name" 2>&1 | tail -20 >&2 || true
  exit 1
}

auth=(-e "ORCHESTRATOR_MCP_TOKEN=$token" -e "ORCHESTRATOR_MCP_RESOURCE_URL=http://127.0.0.1:$port/mcp")

echo "1. no token check -> refuses to start"
if out="$(docker run --rm "$image" 2>&1)"; then fail "started with no auth"; fi
grep -q "without auth" <<<"$out" || fail "no auth refusal in: $out"

echo "2. no repository list -> refuses to start"
if out="$(docker run --rm "${auth[@]}" "$image" 2>&1)"; then fail "started with no repository list"; fi
grep -q "ORCHESTRATOR_MCP_REPOS" <<<"$out" || fail "no list refusal in: $out"

echo "3. token check and list -> serves"
mkdir -p "$work/repos/app"
printf 'def greet():\n    return "hi"\n' >"$work/repos/app/app.py"
printf 'repos:\n  app: /repos/app\n' >"$work/repos.yaml"
chmod -R a+rX "$work"
docker run -d --name "$name" -p "127.0.0.1:$port:8080" "${auth[@]}" \
  -v "$work/repos:/repos:ro" -v "$work/repos.yaml:/srv/spine/.spine/repos.yaml:ro" "$image" >/dev/null

url="http://127.0.0.1:$port/mcp"
accept='Accept: application/json, text/event-stream'
json='Content-Type: application/json'
for _ in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$accept" -H "$json" -d '{}' "$url" || true)"
  [ "$code" != "000" ] && break
  sleep 1
done

code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$accept" -H "$json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "$url")"
[ "$code" = "401" ] || fail "a call with no token got $code, not 401"

init='{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
headers="$work/headers"
code="$(curl -s -D "$headers" -o /dev/null -w '%{http_code}' -X POST -H "$accept" -H "$json" \
  -H "Authorization: Bearer $token" -d "$init" "$url")"
[ "$code" = "200" ] || fail "initialize with the token got $code"
session="$(grep -i '^mcp-session-id:' "$headers" | cut -d' ' -f2 | tr -d '\r')"
sid=()
[ -n "$session" ] && sid=(-H "mcp-session-id: $session")
curl -s -o /dev/null -X POST -H "$accept" -H "$json" -H "Authorization: Bearer $token" "${sid[@]}" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' "$url"

call() {
  curl -s -X POST -H "$accept" -H "$json" -H "Authorization: Bearer $token" "${sid[@]}" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"map_repo\",\"arguments\":{\"repo_path\":\"$1\"}}}" "$url"
}

echo "4. an unlisted path is refused and audited"
out="$(call /etc)"
grep -q "not a repository this server serves" <<<"$out" || fail "/etc was not refused: $out"
sleep 1
logs="$(docker logs "$name" 2>&1)"  # captured first: grep -q on a pipe would SIGPIPE docker logs
grep -q '^{.*"event": "mcp_repo_refused"' <<<"$logs" || fail "no bare JSON audit line for the refusal"

echo "5. a listed repository is mapped"
out="$(call /repos/app)"
grep -q "not a repository this server serves" <<<"$out" && fail "/repos/app was refused: $out"
grep -qF 'files\": 1' <<<"$out" || fail "/repos/app did not map: ${out:0:600}"  # its one file

echo "SMOKE OK"
