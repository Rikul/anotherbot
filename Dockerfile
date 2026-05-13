FROM python:3.12-slim

RUN pip install uv --no-cache-dir

WORKDIR /app

# Dependency files first — this layer is cached unless deps change
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY app/ ./app/
COPY run.sh .
COPY restart.sh .

# Data dir for SQLite DB, workspace, and optional config.toml mount
ENV ANOTHERBOT_HOME=/data
VOLUME /data

# Non-secret runtime config
ENV LLM_BASE_URL=""
ENV MODEL=""
ENV TELEGRAM_ALLOW_FROM=""

# Secrets — pass at runtime only, never bake into the image:
#   docker run -e LLM_API_KEY=... -e TELEGRAM_BOT_TOKEN=...
#   docker run -e LLM_API_KEY=... -e DISCORD_BOT_TOKEN=...
#   or: docker run --env-file .env

CMD ["bash", "run.sh", "background"]
