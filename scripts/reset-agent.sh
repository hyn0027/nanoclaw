#!/usr/bin/env bash
# Reset every agent: delete all sessions, memory, logs and ontology runtime state, and switch
# off OneCLI policy groups and credential grants (Anthropic stays on; vault secrets are kept).
# Builds the host and the agent image first, so the reset runs the current code; a failing
# build stops it before anything is deleted.
#   scripts/reset-agent.sh [--yes]
set -euo pipefail
cd "$(dirname "$0")/.."

[ "${1:-}" = --yes ] || { read -r -p "Delete all agent history? [y/N] " ok; [ "$ok" = y ]; }

pnpm run build
./container/build.sh

q() { pnpm exec tsx scripts/q.ts data/v2.db "$1"; }
PLIST=$(ls ~/Library/LaunchAgents/com.nanoclaw*.plist | head -1)

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
sleep 3
pkill -f ontology_runtime.py || true
for c in $(docker ps --format '{{.Names}}' | grep '^ncl-' || true); do docker rm -f "$c" >/dev/null; done

for t in pending_questions pending_approvals session_claims delivery_attempts wake_signals sessions; do
  q "delete from $t"
done
q "update container_configs set mcp_servers = '{}', packages_apt = '[]', packages_npm = '[]', image_tag = NULL"

for D in data/v2-sessions/*/; do
  rm -rf "$D"sess-* "$D.context"
  find "$D.claude-shared" -mindepth 1 -maxdepth 1 ! -name settings.json ! -name skills -exec rm -rf {} + 2>/dev/null || true
done
rm -rf groups/*/memory groups/*/conversations logs/sessions logs/*.log logs/ontology-runtime \
  data/ontology-runtime data/package-locks ontology/observations ontology/local/credential-scopes.json
find data/onecli-capture -type f -delete

(cd ontology && .venv/bin/python - <<'EOF'
import json, urllib.request
from src.runtime.policies import api_key, NANOCLAW

KEY = api_key(NANOCLAW / ".env")
def call(method, path, body=None):
    req = urllib.request.Request("http://127.0.0.1:10256/v1" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=60).read()
    return json.loads(raw) if raw else None

state = call("GET", "/cedar-policy")
for g in state["groups"]:  # off, except the group that permits the model API
    if g["enabled"] and not any("api.anthropic.com" in p["policyText"] for p in g["policies"]):
        call("PATCH", f"/cedar-policy/groups/{g['id']}", {"enabled": False})
        print("policy group off:", g["name"])
model = {s["id"] for s in state["secrets"] if s["type"] == "anthropic"}
for s in state["secrets"]:
    if s["id"] not in model and s["workspaceShared"]:
        call("PUT", f"/cedar-policy/shared-credentials/{s['id']}", {"shared": False})
        print("unshared:", s["name"])
for a in state["agents"]:
    for s in call("GET", f"/agents/{a['id']}/grants")["secrets"]:
        if s["secretId"] not in model:
            call("DELETE", f"/agents/{a['id']}/grants/secrets/{s['secretId']}")
            print("grant removed:", a["name"], "->", s["name"])
EOF
)

docker restart onecli-gateway >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Reset done. Agents start fresh on their next message."
