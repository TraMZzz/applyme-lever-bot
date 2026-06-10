# Headless Chrome runtime for the Lever auto-apply bot.
# Uses Chromium (works on both amd64 and arm64; Google Chrome ships amd64-only).
FROM python:3.12-slim

# Chromium + headless runtime deps + fonts (so pages render/measure correctly) + tini (zombie reaping).
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# uv (installed from PyPI — avoids a build-time registry pull of the uv image,
# which times out in network-restricted environments; PyPI egress is reliable).
RUN pip install --no-cache-dir uv

# Run as a non-root user (security best practice; CWE-269/250).
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY scripts ./scripts
COPY tests ./tests
RUN uv sync --frozen && chown -R appuser:appuser /app

# Headless + no-sandbox is required inside a container; point the engine at Chromium.
ENV JOOBLE_HEADFUL=false \
    JOOBLE_CHROME_NO_SANDBOX=true \
    JOOBLE_CHROME_PATH=/usr/bin/chromium \
    HOME=/home/appuser \
    PYTHONUNBUFFERED=1

USER appuser

# tini reaps zombie Chromium processes cleanly.
ENTRYPOINT ["tini", "--"]
# Default: prove Chrome works end-to-end (needs no data/ or API keys).
CMD ["uv", "run", "python", "scripts/check_chrome.py"]
