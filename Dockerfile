#
## Stage 1: Builder
FROM python:3.12-alpine AS builder

ENV UV_LINK_MODE=copy

# Set the shell to /bin/ash and enable pipefail
SHELL ["/bin/ash", "-eo", "pipefail", "-c"]

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Change the working directory
WORKDIR /opt/seto

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --locked --no-install-project --compile-bytecode

# Sync the project
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --no-dev --locked \
    && uv build

#
## Stage 2: Final
FROM python:3.12-alpine

ENV PATH="/opt/seto/.venv/bin:$PATH"
ENV PYTHONPATH="/opt/seto/.venv/lib/python3.12/site-packages"
ENV PYTHONUNBUFFERED="1"
ENV PYTHONDONTWRITEBYTECODE="1"

WORKDIR /app

# Set the shell to /bin/ash and enable pipefail
SHELL ["/bin/ash", "-eo", "pipefail", "-c"]

RUN apk add --no-cache docker-cli gettext ca-certificates \
  && update-ca-certificates

# Copy the environment, but not the source code
COPY --from=builder --chown=setouser:setouser /opt/seto/.venv /opt/seto/.venv
COPY --from=builder --chown=setouser:setouser /opt/seto/src /opt/seto/src

ENTRYPOINT ["seto"]
