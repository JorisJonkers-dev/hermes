#!/usr/bin/env bash
# Cluster smoke test for the hosted Hermes (agent-kit#911 acceptance). Run after the Project is
# deployed. It needs kubectl access to the cluster; nothing here runs in CI.
#
#   scripts/smoke.sh                    all checks
#   SMOKE_EGRESS=skip scripts/smoke.sh  all but the egress check
#
# Every check that runs code in the pod feeds it on stdin (`kubectl exec -i`), prints a sentinel
# line when, and only when, the thing it checks holds, and the script requires that line. A check
# whose code never ran therefore fails instead of passing.
#
#   1. the pod is non-root, read-only, with no host mounts;
#   2. the config in the pod is the image's: the tier providers, the tiers backend;
#   3. Hermes answers on the free tier, with the provider, URL and key taken from config.yaml;
#   4. the serving model of that call was logged as one JSON line;
#   5. an unknown tier is refused, and tier-strong-open is refused when its key is not set;
#   6. egress is narrowed: GitHub answers through the pod's proxy, anything else fails, direct or proxied.
set -euo pipefail

NS="${HERMES_NAMESPACE:-hermes-system}"
DEPLOY="${HERMES_DEPLOYMENT:-deploy/hermes}"
K=(kubectl -n "$NS")
PY=("${K[@]}" exec -i "$DEPLOY" -- python -)

pass() { printf 'ok   %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }
# expect <sentinel> <output> <what failed>
expect() { grep -q "^$1\$" <<<"$2" || fail "$3: $2"; }

# 1. the sandbox
sc="$("${K[@]}" get "$DEPLOY" -o jsonpath='{.spec.template.spec.containers[0].securityContext}')"
[[ "$sc" == *'"readOnlyRootFilesystem":true'* ]] || fail "root filesystem is not read-only: $sc"
[[ "$sc" == *'"runAsNonRoot":true'* ]] || fail "runAsNonRoot is not set: $sc"
hostpaths="$("${K[@]}" get "$DEPLOY" -o jsonpath='{.spec.template.spec.volumes[*].hostPath.path}')"
[[ -z "$hostpaths" ]] || fail "host mounts present: $hostpaths"
pass "non-root, read-only root filesystem, no host mounts"

# 2. the pod runs the image's entrypoint, which renders the config: the tier providers are there
out="$("${PY[@]}" <<'PY' 2>&1 || true
import os

from ruamel.yaml import YAML

config = YAML(typ="safe").load(open(os.path.join(os.environ["HERMES_HOME"], "config.yaml")))
providers = set(config["providers"])
need = {"litellm-tier-free", "litellm-tier-vibe", "litellm-tier-go-cheap", "litellm-tier-review"}
assert need <= providers, providers
assert config["delegation"]["backend"] == "litellm-tiers"
assert config["model"]["provider"] in providers
print("CONFIG_OK")
PY
)"
expect CONFIG_OK "$out" "the config in the pod is not the image's"
pass "config.yaml holds the tier providers and the tiers backend"

# 3 and 4. An answer on the free tier, and the serving model of that call logged as one JSON line.
# The URL and key come from config.yaml through Hermes's own provider resolver, so a wrong provider
# entry, key variable or address fails here. The agent runs in process, with the plugin loaded.
out="$("${PY[@]}" <<'PY' 2>&1 || true
from hermes_cli.plugins import discover_plugins
from hermes_cli.runtime_provider import resolve_runtime_provider

discover_plugins()
from run_agent import AIAgent

runtime = resolve_runtime_provider(requested="litellm-tier-free")
agent = AIAgent(
    base_url=runtime["base_url"],
    api_key=runtime["api_key"],
    provider="litellm-tier-free",
    model="tier-free",
    quiet_mode=True,
    platform="cli",
    skip_context_files=True,
    skip_memory=True,
    enabled_toolsets=[],
)
answer = str(agent.run_conversation("Reply with the single word: pong").get("final_response"))
if "pong" in answer.lower():
    print("ANSWER_OK")
PY
)"
expect ANSWER_OK "$out" "tier-free did not answer"
pass "answered on tier-free"
line="$(grep '"event":"serving_model"' <<<"$out" | tail -1 || true)"
[[ -n "$line" ]] || fail "no serving_model line was logged: $out"
grep -q '"in_tier_map":true' <<<"$line" || fail "the call was not on a tier: $line"
grep -q '"litellm_model_id":"[^"]' <<<"$line" || fail "the line holds no x-litellm-model-id: $line"
pass "serving model logged: $line"

# 5. an unknown tier is refused. Loads the deployed plugin against the real Hermes, as Hermes does.
out="$("${PY[@]}" <<'PY' 2>&1 || true
import importlib.util
import os
import sys

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
except DelegationRouteError:
    refused_unknown = True
os.environ.pop("LITELLM_TIER_STRONG_OPEN_KEY", None)
try:
    provider.route_task({"goal": "x", "tier": "tier-strong-open"}, {})
except DelegationRouteError as error:
    if refused_unknown and "not enabled" in str(error):
        print("REFUSED_OK")
PY
)"
expect REFUSED_OK "$out" "unknown tier was not refused"
pass "unknown tier refused"

# 6. egress is narrowed to GitHub, through the Process's own proxy (deploy-kit host-narrowed egress).
# The pod holds HTTPS_PROXY. A declared host connects through it; an undeclared host is refused by
# it (CONNECT 403); a direct connection to any public address has no route; LiteLLM stays reachable
# without it (NO_PROXY). SMOKE_EGRESS=skip reports the check without running it.
if [[ "${SMOKE_EGRESS:-enforce}" == skip ]]; then
  printf 'skip egress check (SMOKE_EGRESS=skip)\n'
  exit 0
fi
out="$("${PY[@]}" <<'PY' 2>&1 || true
import os
import socket
import urllib.parse


def direct(host, port=443):
    try:
        socket.create_connection((host, port), timeout=5).close()
        return True
    except OSError:
        return False


def through_proxy(host, port=443):
    """The proxy's answer to a CONNECT, as a status code, or None when it was not reached."""
    proxy = urllib.parse.urlparse(os.environ.get("HTTPS_PROXY", ""))
    if not proxy.hostname:
        return None
    try:
        with socket.create_connection((proxy.hostname, proxy.port or 3128), timeout=10) as sock:
            sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
            return int(sock.recv(256).split(b" ", 2)[1])
    except (OSError, ValueError, IndexError):
        return None


problems = []
if not os.environ.get("HTTPS_PROXY"):
    problems.append("HTTPS_PROXY is not set")
for host in ("github.com", "api.github.com"):
    if through_proxy(host) != 200:
        problems.append(f"{host} not reachable through the proxy")
for host in ("example.com", "pypi.org", "api.openai.com", "openrouter.ai"):
    if through_proxy(host) != 403:
        problems.append(f"{host} not refused by the proxy")
    if direct(host):
        problems.append(f"{host} reachable directly")
for host in ("github.com", "api.github.com"):
    if direct(host):
        problems.append(f"{host} reachable directly, past the proxy")
print("EGRESS_OK" if not problems else f"unexpected egress: {problems}")
PY
)"
expect EGRESS_OK "$out" "egress is not narrowed to GitHub through the proxy"
pass "egress limited to GitHub through the proxy"
