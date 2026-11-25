# Hermes v0.23.2, pinned, with this repository's configuration and delegation plugin.
#
# Not the upstream image: that one starts as root under s6-overlay, which a non-root pod with a
# read-only root filesystem cannot run. This image installs Hermes from the release's commit,
# runs as an unprivileged user, and writes only below HERMES_HOME, /tmp and /work, which the pod
# mounts as emptyDir volumes (deploy/hermes.project.yml `writablePaths`).
#
# Hermes 0.23.2 is not on PyPI (the newest release there is 0.19.0), so it installs from the
# tag's commit. v0.23.2 = ae2a81ccc00e9b7c06301523e5bb09608397149c.

ARG PYTHON_IMAGE=python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS build
ARG HERMES_VERSION=0.23.2
ARG HERMES_COMMIT=ae2a81ccc00e9b7c06301523e5bb09608397149c
ARG UV_VERSION=0.12.3
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 UV_PROJECT_ENVIRONMENT=/opt/hermes UV_LINK_MODE=copy
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install "uv==${UV_VERSION}"
# The release's own uv.lock fixes every transitive dependency (a plain pip install cannot resolve
# it: Hermes pins Pillow exactly and an optional dependency pins another). Core dependencies only:
# no extras, so no messaging platform, browser or cloud SDK is in the image. The install is editable
# because Hermes refuses to build a wheel of itself ("distributed via the shell installer, Docker
# image, or Nix"); the source therefore stays at /src, root-owned and read-only to the runtime user.
RUN git init /src \
 && git -C /src fetch --depth 1 https://github.com/NousResearch/hermes-agent.git "${HERMES_COMMIT}" \
 && git -C /src checkout --detach FETCH_HEAD \
 && uv sync --directory /src --frozen --no-dev \
 && rm -rf /src/.git

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE}
ARG HERMES_VERSION=0.23.2
LABEL org.opencontainers.image.title="hermes" \
      org.opencontainers.image.source="https://github.com/JorisJonkers-dev/hermes" \
      org.opencontainers.image.description="Hermes ${HERMES_VERSION} routed through LiteLLM tier keys" \
      dev.jorisjonkers.hermes.version="${HERMES_VERSION}"

# git: the throwaway clone the loops work in. ca-certificates: GitHub over TLS. tini: PID 1, which reaps
# the orphans tools leave behind.
# The packages are the base image's Debian release at its pinned digest, so the apt versions follow it.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 hermes \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /home/hermes --shell /usr/sbin/nologin hermes \
 && mkdir -p /home/hermes /work /opt/hermes-seed \
 && chown 10001:10001 /home/hermes /work

COPY --from=build /opt/hermes /opt/hermes
COPY --from=build /src /src
COPY config/config.yaml /opt/hermes-seed/config.yaml
COPY plugins/ /opt/hermes-seed/plugins/
# The loops sit beside plugins/ as they do in the repository: a loop finds the plugin's modules by that layout.
COPY loops/ /opt/hermes-seed/loops/
COPY docker/render_config.py /opt/hermes-seed/render_config.py
COPY docker/entrypoint.sh /usr/local/bin/hermes-entrypoint

# HERMES_HOME is an emptyDir: the entrypoint seeds it from the image's read-only copy on every
# start, so the config and plugin in the image are always the ones that run.
ENV PATH=/opt/hermes/bin:$PATH \
    HERMES_HOME=/home/hermes/.hermes \
    HOME=/home/hermes \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/hermes-entrypoint"]
CMD ["hermes", "--version"]
