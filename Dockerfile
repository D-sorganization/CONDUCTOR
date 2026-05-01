# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — builder
# Compile wheels and install all dependencies into a user prefix so the
# runtime stage only needs to COPY the result, keeping the final image lean.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast pip replacement) once in the builder.
RUN pip install --no-cache-dir uv

WORKDIR /build

# Copy only the manifest + lockfile first so dependency layers are cached.
COPY pyproject.toml uv.lock ./

# Install production dependencies into /install prefix (no editable install).
RUN uv pip install --prefix /install --no-cache -r pyproject.toml

# Copy the rest of the source and install the package itself.
COPY . .
RUN uv pip install --prefix /install --no-cache --no-deps .


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime
# Minimal image: non-root user, no build tools, HEALTHCHECK, read-only root FS.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Add /install/bin to PATH so the maxwell-daemon entry-point is found.
    PATH="/install/bin:$PATH" \
    PYTHONPATH="/install/lib/python3.12/site-packages"

# Install only runtime system dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    # Create a non-root user and group.
    && groupadd --gid 1000 maxwell \
    && useradd --uid 1000 --gid maxwell --no-create-home --shell /sbin/nologin maxwell \
    # Prepare writable directories for data and logs.
    && mkdir -p /data/maxwell /var/log/maxwell \
    && chown -R maxwell:maxwell /data/maxwell /var/log/maxwell

# Copy the compiled package from the builder stage.
COPY --from=builder /install /install

# Copy the application source (needed for the static UI assets).
COPY --chown=maxwell:maxwell maxwell_daemon /app/maxwell_daemon
COPY --chown=maxwell:maxwell pyproject.toml /app/pyproject.toml

WORKDIR /app

# Drop to non-root before the process starts.
USER maxwell

EXPOSE 8080

# Liveness probe — lightweight, no auth required.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsSL http://localhost:8080/health || exit 1

# Default entrypoint; override CMD to pass additional CLI flags.
ENTRYPOINT ["python", "-m", "maxwell_daemon.launcher"]
CMD ["--no-open-browser"]
