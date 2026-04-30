# Multi-stage build for Maxwell-Daemon.
#
# Stage 1 ("builder") installs all Python dependencies and the application
# itself into the system site-packages using `uv`. Stage 2 ("runtime") is a
# clean python:3.12-slim image that copies the installed environment from the
# builder, runs the daemon as a non-root `maxwell` user, and exposes the API
# on port 8080 with a built-in HEALTHCHECK against /api/health.
#
# Build:   docker build -t maxwell-daemon:local .
# Run:     docker run --rm -p 8080:8080 maxwell-daemon:local
# Health:  curl -fsS http://127.0.0.1:8080/api/health

# -----------------------------------------------------------------------------
# Stage 1 - builder
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Build deps for any wheels that need compilation. Kept minimal so the builder
# layer stays small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /build

# Install dependencies first so this layer is cached when only source changes.
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy the application source and install the package itself.
COPY . .
RUN uv pip install --system --no-cache --no-deps -e .

# -----------------------------------------------------------------------------
# Stage 2 - runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# `curl` is used by the HEALTHCHECK; everything else stays out of the runtime
# image to keep the attack surface small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 maxwell \
    && useradd --system --uid 1000 --gid maxwell --create-home \
       --home-dir /home/maxwell --shell /usr/sbin/nologin maxwell

# Pull the installed Python environment and application source from builder.
# The editable install in stage 1 leaves a .pth entry pointing at /build, so
# we copy /build to /app to keep that link valid.
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build /app

WORKDIR /app
RUN chown -R maxwell:maxwell /app /home/maxwell

USER maxwell

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["python", "-m", "maxwell_daemon.launcher"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--no-open-browser"]
