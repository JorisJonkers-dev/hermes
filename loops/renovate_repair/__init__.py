"""The Renovate repair loop (JorisJonkers-dev/agent-kit#912).

Over the open Renovate pull requests of the public repositories of one organisation: merge a
patch or minor update whose checks pass, comment on a major, and ask a Hermes subagent to repair a
failing one on the Renovate branch, trying tier-free, then tier-vibe, then tier-go-cheap.

Run it with ``python -m loops.renovate_repair --dry-run`` (see README.md).
"""
