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
| `deploy/hermes.project.yml`, `deploy/env/` | The deploy-kit Project Intent: Vault keys, the edge to LiteLLM, writable paths, internet egress. |
| `loops/renovate_repair/` | The Renovate repair loop (agent-kit#912): classifier, merge/comment/repair decisions, GitHub client, fixer, CLI. |
| `scripts/smoke.sh` | The cluster smoke test. |
| `tests/` | pytest, 100% line and branch coverage of the plugin and the loops. |

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
environment, and its egress is every public address: deploy-kit cannot yet name GitHub alone, and its
NetworkPolicy is render-only. A prompt injection could therefore send a key out. Until that is fixed:

- the idle pod holds **no GitHub credential**. The Renovate loop's token is an optional file grant of its
  own scheduled process, written to Vault only after deploy-kit#874 narrows egress to GitHub, and read
  by the loop's `git push` through a credential helper, never from the environment;
- mint every tier key with a budget and its tier's models only, so a leaked key is bounded and revocable.

## Renovate repair loop

`python3 -m loops.renovate_repair` sweeps the open Renovate pull requests (author `renovate[bot]`) of the
public repositories of `JorisJonkers-dev` (agent-kit#912):

| Pull request | What the loop does |
|---|---|
| A repository that is `private`, or whose `visibility` is not `public`, or archived | Nothing: it is skipped before its pull requests are requested. |
| Major update, or an update type it cannot read | One comment (marked, so never repeated) that it needs a human. Never merged, whatever the checks say. |
| Patch or minor, checks passing | Squash-merged, guarded by the head SHA it looked at. |
| Patch or minor, checks failing | Repaired on the Renovate branch by a Hermes subagent, on `tier-free`, then `tier-vibe`, then `tier-go-cheap` (at most `--max-attempts`, default 3; never `tier-strong-open`, never Claude). After each pushed fix it waits for the checks, merges when they pass, and tries the next tier when they fail. If no tier fixes it, one comment lists what was tried and the loop stops on that commit. |
| Checks running, or none | Left for the next run. |

The update type is the highest of Renovate's labels, an `Update type:` line, the `old -> new` version pairs in
the body and the title. On 0.x a minor step counts as major.

Every repair attempt is one JSON line (`renovate_repair_attempt`: repo, PR, tier, attempt, outcome, serving
model when known) and every decision another (`renovate_repair_decision`), written with the plugin's own
writer, so they sit in the same stdout stream as the `serving_model` lines.

The fixer clones the branch into `/work`, runs `hermes chat -q` there on the tier's provider with
`GITHUB_TOKEN_FILE` removed from its environment, and takes its change as a patch against the commit it
started from. A patch that touches `.github/` is refused. Otherwise the patch is applied to a second, clean
clone (so no hook or git configuration the subagent wrote is ever used), committed, and pushed from there
to an explicit URL through a git credential helper that reads the token file. This keeps the token out of
every argument and environment but git's. It does **not** hide the file from the subagent, which runs as
the same user in the same pod: the bounds are the token's scope and deploy-kit#874. A run repairs at most
`--max-repairs` pull requests (default 2), so it fits its hourly slot; the rest wait for the next run.
Draft pull requests are left alone, and tool output goes to the log, never into a comment.

**The token** is a fine-grained PAT (public repositories of the organisation: Contents and Pull requests
read and write, Checks and Commit statuses read; no Workflows). It is read from the file that
`GITHUB_TOKEN_FILE` names; the variable never holds the token.

```sh
export GITHUB_TOKEN_FILE=$(mktemp) && gh auth token > "$GITHUB_TOKEN_FILE"
python3 -m loops.renovate_repair --dry-run                      # first: prints a plan, writes nothing
python3 -m loops.renovate_repair --dry-run --repo hermes        # one repository
python3 -m loops.renovate_repair                                # for real
rm "$GITHUB_TOKEN_FILE"
```

A dry run makes no merge, comment or push and starts no subagent: it prints one `renovate_repair_plan`
line per pull request. Exit status: 0, 1 if any pull request or repository errored, 2 for no usable token.

On the cluster it is the `renovate-repair` scheduled process of `deploy/hermes.project.yml` (hourly, at
minute 17). It waits on deploy-kit#874: do not write `secret/data/hermes/github` (key
`renovate_repair_token`) in Vault before that egress narrowing is in the Project. Without the key the file is
not mounted and each run exits 2 having done nothing.

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

The egress check fails today, on purpose: deploy-kit can declare only `egress: internet` (every
public address), not GitHub alone, and its NetworkPolicy is render-only in v1. See the open
points in the pull request.

The design is tracked in JorisJonkers-dev/agent-kit#911. The inference plan is in
JorisJonkers-dev/litellm#57 (`docs/free-inference.md`).

This repository is source available for viewing only. See [LICENSE](LICENSE) and
[CONTRIBUTING.md](CONTRIBUTING.md).
