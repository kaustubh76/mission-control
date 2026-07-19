FROM python:3.12-slim

# Install minimal build tooling for ccxt + pyarrow, then drop it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so editable rebuilds don't bust the cache on
# every source-only change. The [api] extra adds fastapi+uvicorn so the
# Mission Control dashboard backend can run from this same image (the
# scanner ignores them). scripts/ + config/ are needed by the API's
# sim-tick control (scripts/run_allocator.py) and strategy spec.
COPY pyproject.toml ./
COPY README.md ./
COPY src ./src
COPY scripts ./scripts
COPY config ./config
RUN pip install --no-cache-dir -e ".[api]"

# Runtime artefacts live under data/ — mount a volume here.
RUN mkdir -p data/journal data/runs data/logs data/cache

# Default to running the scanner. The dashboard API service overrides this
# via render.yaml's dockerCommand (uvicorn ictbot.api.app:app).
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "ictbot", "scan"]
