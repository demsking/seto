#
## Stage 1: Builder
FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-eo", "pipefail", "-c"]

# Install build dependencies
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /opt/seto

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --locked --no-install-project --compile-bytecode

# Sync project
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --locked && \
    uv build

#
## Stage 2: Final
FROM python:3.12-slim

ENV PATH="/opt/seto/.venv/bin:$PATH" \
    PYTHONPATH="/opt/seto/.venv/lib/python3.12/site-packages" \
    PYTHONUNBUFFERED="1" \
    PYTHONDONTWRITEBYTECODE="1" \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

SHELL ["/bin/bash", "-eo", "pipefail", "-c"]

# Install runtime dependencies
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends docker-cli openssh-client gettext ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Copy virtual environment and app
COPY --from=builder --chown=nobody:nogroup /opt/seto/.venv /opt/seto/.venv
COPY --from=builder --chown=nobody:nogroup /opt/seto/src /opt/seto/src

ENTRYPOINT ["seto"]
