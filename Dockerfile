# Build stage
FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Export runtime requirements (no dev group, pinned from the lockfile)
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (git and gh CLI for SSH-based GitHub operations)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    curl \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements from builder
COPY --from=builder /app/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY dbt_training_wheels/ ./dbt_training_wheels/

# Create temp directory
RUN mkdir -p /app/temp

# Note: dbt_training_wheels_config.yaml should be mounted at runtime via:
#   docker run -v /path/to/dbt_training_wheels_config.yaml:/app/dbt_training_wheels_config.yaml ...

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=dbt_training_wheels.app:app
ENV FLASK_ENV=production
ENV DBT_TRAINING_WHEELS_CONFIG_PATH=/app/dbt_training_wheels_config.yaml

# Expose port
EXPOSE 8000

# Health check (uses the /api/health endpoint)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Create non-root user for security
RUN groupadd -r dbt_training_wheels && useradd -r -g dbt_training_wheels dbt_training_wheels
RUN chown -R dbt_training_wheels:dbt_training_wheels /app
# so pre-commit can access the cache
RUN mkdir -p /home/dbt_training_wheels/.cache && chown -R dbt_training_wheels:dbt_training_wheels /home/dbt_training_wheels
USER dbt_training_wheels

# gh reads its config and extensions from HOME, as does ssh
ENV HOME=/home/dbt_training_wheels

# Stacked pull requests use the gh-stack extension. `gh extension install` resolves the
# release through the GitHub API, which has no credentials during a build - so fetch the
# published binary directly instead. The download needs no auth, so the extension is
# baked into the image and never depends on runtime network or a token.
#
# Pinned rather than tracking latest: gh-stack is in public preview, so a rebuild
# shouldn't silently pick up a behaviour change. Bump with --build-arg.
#
# The final `gh stack --help` is a check, not decoration: it fails the build rather
# than shipping an image that silently falls back to compare links at deploy time.
ARG GH_STACK_VERSION=v0.1.0
ARG TARGETARCH
RUN set -eu; \
    arch="${TARGETARCH:-$(case "$(uname -m)" in x86_64) echo amd64 ;; aarch64|arm64) echo arm64 ;; esac)}"; \
    dir="$HOME/.local/share/gh/extensions/gh-stack"; \
    mkdir -p "$dir"; \
    curl -fsSL -o "$dir/gh-stack" \
        "https://github.com/github/gh-stack/releases/download/${GH_STACK_VERSION}/linux-${arch}"; \
    chmod +x "$dir/gh-stack"; \
    gh stack --help > /dev/null

# Fix macOS SSH config compatibility (UseKeychain option not supported on Linux)
COPY --chown=dbt_training_wheels:dbt_training_wheels entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Run with gunicorn for production
CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "dbt_training_wheels.app:app"]
