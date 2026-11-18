#!/usr/bin/env bash
# Cluster smoke test for the hosted Hermes (agent-kit#911 acceptance). Run after the Project is
# deployed. It needs kubectl access to the cluster; nothing here runs in CI.
#
#   scripts/smoke.sh                 all checks
#
# The checks, in the order of the acceptance criteria:
#   1. the pod runs, non-root, with a read-only root filesystem and no host mounts;
#   2. Hermes answers on the free tier;
#   3. a task naming an unknown tier is refused;
#   4. the serving model of that call was logged as one JSON line;
#   5. egress to a host other than LiteLLM and GitHub fails.
set -euo pipefail

NS="${HERMES_NAMESPACE:-hermes-system}"
DEPLOY="${HERMES_DEPLOYMENT:-deploy/hermes}"
K=(kubectl -n "$NS")
X=("${K[@]}" exec "$DEPLOY" --)

pass() { printf 'ok   %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

# 1. the sandbox
sc="$("${K[@]}" get "$DEPLOY" -o jsonpath='{.spec.template.spec.containers[0].securityContext}')"
[[ "$sc" == *'"readOnlyRootFilesystem":true'* ]] || fail "root filesystem is not read-only: $sc"
[[ "$sc" == *'"runAsNonRoot":true'* ]] || fail "runAsNonRoot is not set: $sc"
hostpaths="$("${K[@]}" get "$DEPLOY" -o jsonpath='{.spec.template.spec.volumes[*].hostPath.path}')"
[[ -z "$hostpaths" ]] || fail "host mounts present: $hostpaths"
pass "non-root, read-only root filesystem, no host mounts"

# 2 and 4. An answer on the free tier, and the serving model of that call logged as one JSON line.
# Runs Hermes's agent in process (as `hermes chat -q` does inside the gateway), against the pod's own
# LiteLLM address and tier-free key, so the key, the route and the plugin's hooks are all proven.
# kubectl exec -i feeds the script on stdin.
out="$("${K[@]}" exec -i "$DEPLOY" -- python - <<'PY' 2>&1
import os
from hermes_cli.plugins import discover_plugins

discover_plugins()
from run_agent import AIAgent

agent = AIAgent(
    base_url=f"http://{os.environ['LITELLM_HOST']}:{os.environ['LITELLM_PORT']}/v1",
    api_key=os.environ["LITELLM_FREE_KEY"],
    provider="litellm-tier-free",
    model="tier-free",
    quiet_mode=True,
    platform="cli",
    skip_context_files=True,
    skip_memory=True,
    enabled_toolsets=[],
)
print("ANSWER:", agent.run_conversation("Reply with the single word: pong").get("final_response"))
PY
)" || fail "hermes did not run: $out"
grep -q 'ANSWER:.*[Pp]ong' <<<"$out" || fail "tier-free did not answer: $out"
pass "answered on tier-free"
line="$(grep '"event":"serving_model"' <<<"$out" | tail -1 || true)"
[[ -n "$line" ]] || fail "no serving_model line was logged: $out"
grep -q '"response_model":"[^"]' <<<"$line" || fail "the line names no serving model: $line"
grep -q '"litellm_model_id":"[^"]' <<<"$line" || fail "the line holds no x-litellm-model-id: $line"
pass "serving model logged: $line"

# 3. an unknown tier is refused. Loads the deployed plugin against the real Hermes, as Hermes does.
"${X[@]}" python - <<'PY' || fail "unknown tier was not refused"
import importlib.util, os, sys
from agent.delegation_provider import DelegationRouteError

plugin_dir = os.path.join(os.environ["HERMES_HOME"], "plugins", "delegation-litellm-tiers")
spec = importlib.util.spec_from_file_location(
    "delegation_litellm_tiers", os.path.join(plugin_dir, "__init__.py"), submodule_search_locations=[plugin_dir]
)
module = importlib.util.module_from_spec(spec)
sys.modules["delegation_litellm_tiers"] = module
spec.loader.exec_module(module)
provider = module.LiteLLMTiers()
assert provider.route_task({"goal": "x", "tier": "tier-vibe"}, {})["provider"] == "litellm-tier-vibe"
try:
    provider.route_task({"goal": "x", "tier": "tier-premium"}, {})
except DelegationRouteError as error:
    print("refused:", error)
else:
    raise SystemExit("tier-premium was not refused")
PY
pass "unknown tier refused"

# 5. egress to anything else fails. LiteLLM and GitHub are reachable; the rest is not.
# This fails today: deploy-kit can only declare `egress: internet`, and NetworkPolicy is render-only
# in v1. SMOKE_EGRESS=skip reports it without failing, until both change.
if [[ "${SMOKE_EGRESS:-enforce}" == skip ]]; then
  printf 'skip egress check (SMOKE_EGRESS=skip)\n'
  exit 0
fi
"${X[@]}" python - <<'PY' || fail "egress check failed"
import socket, sys
def reach(host, port=443):
    try:
        socket.create_connection((host, port), timeout=5).close()
        return True
    except OSError:
        return False
must = {"github.com": True, "api.github.com": True}
mustnt = ["example.com", "pypi.org", "api.openai.com", "openrouter.ai"]
bad = [h for h, want in must.items() if reach(h) != want] + [h for h in mustnt if reach(h)]
if bad:
    print("unexpected reachability:", bad)
    sys.exit(1)
PY
pass "egress to other hosts fails"
