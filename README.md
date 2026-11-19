# hermes

Hosted [Hermes](https://github.com/NousResearch/hermes-agent) agent for loops and cron jobs,
running in the estate's k3s cluster and routed through LiteLLM tier keys.

- **Hermes** is pinned to v0.23.2.
- **Models** are reached only through LiteLLM tier keys. There is no subscription login and no
  direct provider key in this repository or its pod.
- **The pod is the sandbox.** No host mounts, a throwaway clone per run, non-root, a read-only
  root filesystem, and a NetworkPolicy that allows only LiteLLM and GitHub.
- **Delegation** maps the tier enum to LiteLLM groups and records the serving model of every
  subagent.

## Layout

| Path | What |
|---|---|
| `Dockerfile`, `docker/entrypoint.sh` | Hermes v0.23.2 (commit `ae2a81cc`, installed from its `uv.lock`), non-root uid 10001, read-only root filesystem; the entrypoint seeds `HERMES_HOME` from the image. |
| `config/config.yaml` | One custom provider per LiteLLM tier key, auxiliary models on `tier-free`, no subscription login, the tier plugin enabled. |
| `plugins/delegation-litellm-tiers/` | The delegation provider (tier enum to LiteLLM tier group, unknown tier refused) and the serving-model log. |
| `deploy/hermes.project.yml`, `deploy/env/` | The deploy-kit Project Intent: Vault keys, the edge to LiteLLM, writable paths, egress narrowed to GitHub. |
| `scripts/smoke.sh` | The cluster smoke test. |
| `tests/` | pytest, 100% line and branch coverage of the plugin. |

## Tiers and keys

| Tier | Provider in `config.yaml` | Vault key (`secret/data/hermes/hermes`) | Env var |
|---|---|---|---|
| `tier-free` | `litellm-tier-free` | `litellm_free_key` | `LITELLM_FREE_KEY` |
| `tier-vibe` | `litellm-tier-vibe` | `litellm_tier_vibe_key` | `LITELLM_TIER_VIBE_KEY` |
| `tier-go-cheap` | `litellm-tier-go-cheap` | `litellm_tier_go_cheap_key` | `LITELLM_TIER_GO_CHEAP_KEY` |
| `tier-strong-open` | `litellm-tier-strong-open` | `litellm_tier_strong_open_key` (optional; without it the provider is not configured and the tier is refused) | `LITELLM_TIER_STRONG_OPEN_KEY` |
| `tier-review` | `litellm-tier-review` | `litellm_tier_review_key` | `LITELLM_TIER_REVIEW_KEY` |

The tier names and what each key may reach are LiteLLM's (`catalog/routing.json`, `tier_groups`).
A subagent task names a tier; the plugin maps it to the provider and sends the tier name as the
model, because Hermes ignores a per-call model override (NousResearch/hermes-agent#17685). A task
that names no tier runs on `tier-free`; an unknown tier is refused before any child starts.

## Serving-model log

Each HTTP response to a model endpoint writes one JSON line to stdout, logged where it is received
and tagged with a request id generated per call, so overlapping calls (children, retries, auxiliary
tasks) are never mixed up:

```json
{"event":"serving_model","ts":1795025784.28,"request_id":"f27b...","session_id":"...","subagent":true,"host":"litellm...","requested_model":"tier-free","in_tier_map":true,"via_litellm":true,"litellm_model_id":"free/hy4-flash-free","attempted_fallbacks":0,"status":200,"duration_s":0.57}
```

`litellm_model_id` and `attempted_fallbacks` come from LiteLLM's `x-litellm-model-id` and
`x-litellm-attempted-fallbacks` response headers. A call to a model outside the tier map, or one that
did not go through LiteLLM, is logged too, with `in_tier_map` or `via_litellm` false. No prompt,
completion or key is logged.

## Configuration is owned by the image

`config/config.yaml` is rendered into `HERMES_HOME` on every start (`docker/entrypoint.sh`,
`docker/render_config.py`), so an edit made inside the pod does not survive a restart; session state in
`HERMES_HOME` is throwaway too. The renderer drops the `tier-strong-open` provider when its key is not
set, and exits non-zero, so Hermes does not start, if the result lacks a tier provider, the tiers
backend or a tier main model. The Project passes `sleep infinity` as `args`, never `command`, because
`command` would replace the entrypoint.

## Residual risk

This pod reads untrusted content (pull requests of public repositories) while it holds tier keys in its
environment. Its egress is narrowed to `api.github.com` and `github.com` on 443 (deploy-kit 1.12.0):
the Process reaches them only through its own allowlisting proxy (`hermes-egress`, squid), reads
`HTTPS_PROXY`, and keeps LiteLLM and the cluster's names on `NO_PROXY`, and its NetworkPolicy, applied
for this Process, leaves no other public route. A prompt injection can no longer send a key to an
arbitrary host, but it can still send one to GitHub (a comment, an issue or a gist, if a credential
allowed it), and DNS queries stay open as a covert channel. So:

- the pod holds **no GitHub credential**; pushes arrive with the Renovate loop (agent-kit#912), through
  the git credential helper only, not the process environment;
- mint every tier key with a budget and its tier's models only, so a leaked key is bounded and revocable.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m pytest --cov            # fails below 100%
node ~/Projects/JorisJonkers-dev/deploy-kit/dist/cli/index.js validate deploy/hermes.project.yml deploy/env
docker build -t hermes:local .
```

## Smoke test (needs the cluster)

CI cannot prove that the Project deploys, answers on a free tier or limits egress. After the
Project is deployed and the Vault secret is written:

```sh
scripts/smoke.sh                    # namespace hermes-system, deployment hermes (checks the config, an answer on
                                    # tier-free, the serving-model line, tier refusals and egress)
SMOKE_EGRESS=skip scripts/smoke.sh  # everything except the egress check
```

The egress check expects the narrowed egress: `github.com` and `api.github.com` answer through the
proxy the pod is given (`HTTPS_PROXY`), and a direct connection to any host, `example.com`,
`pypi.org`, `api.openai.com` or `openrouter.ai` included, fails, as does a request through the proxy
to a host it does not list. `SMOKE_EGRESS=skip` skips it. Run it once after the first rollout of the
host-narrowed egress: it is the live check the render's container proof (deploy-kit decision record
0154) leaves to the cluster.

The design is tracked in JorisJonkers-dev/agent-kit#911. The inference plan is in
JorisJonkers-dev/litellm#57 (`docs/free-inference.md`).

This repository is source available for viewing only. See [LICENSE](LICENSE) and
[CONTRIBUTING.md](CONTRIBUTING.md).
