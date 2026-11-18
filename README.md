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

The design is tracked in JorisJonkers-dev/agent-kit#911. The inference plan is in
JorisJonkers-dev/litellm#57 (`docs/free-inference.md`).

This repository is source available for viewing only. See [LICENSE](LICENSE) and
[CONTRIBUTING.md](CONTRIBUTING.md).
