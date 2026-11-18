#!/bin/sh
# Seed HERMES_HOME from the image's read-only copy, then run the command.
# HERMES_HOME is a writable emptyDir in the pod and may be empty or stale at every start.
set -eu

: "${HERMES_HOME:?HERMES_HOME must be set}"
mkdir -p "${HERMES_HOME}/plugins"
cp /opt/hermes-seed/config.yaml "${HERMES_HOME}/config.yaml"
rm -rf "${HERMES_HOME}/plugins/delegation-litellm-tiers"
cp -R /opt/hermes-seed/plugins/delegation-litellm-tiers "${HERMES_HOME}/plugins/delegation-litellm-tiers"

exec "$@"
