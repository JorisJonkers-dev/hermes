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
| `deploy/hermes.project.yml`, `deploy/env/` | The deploy-kit Project Intent: Vault keys, the edge to LiteLLM, writable paths, internet egress for GitHub. |
| `scripts/smoke.sh` | The cluster smoke test. |
| `tests/` | pytest, 100% line and branch coverage of the plugin. |

## Tiers and keys

| Tier | Provider in `config.yaml` | Vault key (`secret/data/hermes/hermes`) | Env var |
|---|---|---|---|
| `tier-free` | `litellm-tier-free` | `litellm_free_key` | `LITELLM_FREE_KEY` |
| `tier-vibe` | `litellm-tier-vibe` | `litellm_tier_vibe_key` | `LITELLM_TIER_VIBE_KEY` |
| `tier-go-cheap` | `litellm-tier-go-cheap` | `litellm_tier_go_cheap_key` | `LITELLM_TIER_GO_CHEAP_KEY` |
| `tier-strong-open` | `litellm-tier-strong-open` | `litellm_tier_strong_open_key` (optional) | `LITELLM_TIER_STRONG_OPEN_KEY` |
| `tier-review` | `litellm-tier-review` | `litellm_tier_review_key` | `LITELLM_TIER_REVIEW_KEY` |

The tier names and what each key may reach are LiteLLM's (`catalog/routing.json`, `tier_groups`).
A subagent task names a tier; the plugin maps it to the provider and sends the tier name as the
model, because Hermes ignores a per-call model override (NousResearch/hermes-agent#17685). A task
that names no tier runs on `tier-free`; an unknown tier is refused before any child starts.

## Serving-model log

Each model call of a tier provider writes one JSON line to stdout:

```json
{"event":"serving_model","ts":1795025784.28,"session_id":"...","subagent":true,"tier":"tier-free","requested_model":"tier-free","response_model":"hy4-flash-free","litellm_model_id":"free/hy4-flash-free","attempted_fallbacks":0,"finish_reason":"stop","duration_s":0.57}
```

`litellm_model_id` and `attempted_fallbacks` come from LiteLLM's `x-litellm-model-id` and
`x-litellm-attempted-fallbacks` response headers. No prompt, completion or key is logged.

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
scripts/smoke.sh                    # namespace hermes-system, deployment hermes
SMOKE_EGRESS=skip scripts/smoke.sh  # everything except the egress check
```

The egress check fails today, on purpose: deploy-kit can declare only `egress: internet` (every
public address), not GitHub alone, and its NetworkPolicy is render-only in v1. See the open
points in the pull request.

The design is tracked in JorisJonkers-dev/agent-kit#911. The inference plan is in
JorisJonkers-dev/litellm#57 (`docs/free-inference.md`).

This repository is source available for viewing only. See [LICENSE](LICENSE) and
[CONTRIBUTING.md](CONTRIBUTING.md).
