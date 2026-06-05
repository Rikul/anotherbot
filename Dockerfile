FROM debian:bookworm

# apt dependencies for Python, SQLite, and common tools
RUN DEBIAN_FRONTEND=noninteractive apt update && apt install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    sqlite3 \
    curl ca-certificates \
    git \
    nodejs npm \
    chromium \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY app/ ./app/
COPY run.sh restart.sh ./

# Data dir for SQLite DB, workspace, and optional config.toml mount
ENV ANOTHERBOT_HOME=/data
VOLUME /data

# Non-secret runtime config
ENV LLM_BASE_URL=""
ENV MODEL=""
ENV TELEGRAM_ALLOW_FROM=""
ENV DISCORD_ALLOW_FROM=""
ENV SLACK_ALLOW_FROM=""
ENV TZ=UTC

# Web channel — bind to all interfaces inside the container so Docker
# port mapping (-p 8765:8765) works. Without this the server listens on
# 127.0.0.1 only and is unreachable from outside the container.
ENV WEBSOCKET_HOST=0.0.0.0
ENV WEBSOCKET_PORT=8765

# Secrets — pass at runtime only, never bake into the image:
# docker run -d \
#  -e LLM_API_KEY=sk-... \
#  -e DISCORD_BOT_TOKEN=your-discord-token \
#  -e DISCORD_ALLOW_FROM=123456789 \
#  -e SLACK_BOT_TOKEN=xoxb-... \
#  -e SLACK_APP_TOKEN=xapp-... \
#  -e SLACK_ALLOW_FROM=U123456789 \
#  -p 8765:8765 \
#  -v ./anotherbot-data:/data \
#   or: docker run --env-file .env

EXPOSE 8765

CMD ["bash", "run.sh", "background"]
