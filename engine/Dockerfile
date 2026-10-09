# QueryMind engine (design §3.4, §14.1). Build context: engine/.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl: health checks (`docker compose exec engine curl …`) and the compose healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code changes don't reinstall them.
COPY pyproject.toml ./
COPY src ./src
COPY eval/qm_eval ./eval/qm_eval
RUN pip install .

# Few-shot pool, read at startup relative to the working directory (QM_FEW_SHOT_POOL).
COPY fewshot ./fewshot

RUN useradd --create-home --uid 10001 engine \
    && mkdir -p /app/.engine_cache \
    && chown engine:engine /app/.engine_cache
USER engine

EXPOSE 8000
CMD ["qm-engine"]
