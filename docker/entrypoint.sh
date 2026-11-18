#!/bin/sh
# Seed HERMES_HOME from the image's read-only copy, then run the command.
#
# HERMES_HOME is a writable emptyDir in the pod and may be empty or stale at every start. The
# configuration is owned by the image, not by the running pod: it is rendered from the image's
# seed on EVERY start, so a runtime edit is gone after a restart, and render_config.py exits
# non-zero (so nothing below runs) unless the result routes through the tier providers.
# Session state in HERMES_HOME is throwaway by design (the pod holds no durable state).
#
# The Project passes `sleep infinity` as ARGS, not as `command`, so this entrypoint always runs.
set -eu

: "${HERMES_HOME:?HERMES_HOME must be set}"
mkdir -p "${HERMES_HOME}/plugins"
python /opt/hermes-seed/render_config.py /opt/hermes-seed/config.yaml "${HERMES_HOME}/config.yaml"
rm -rf "${HERMES_HOME}/plugins/delegation-litellm-tiers"
cp -R /opt/hermes-seed/plugins/delegation-litellm-tiers "${HERMES_HOME}/plugins/delegation-litellm-tiers"

exec "$@"
