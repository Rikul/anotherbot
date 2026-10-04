## Overview

A Python-based AI agent that can execute prompts, interact with the filesystem, run shell commands, fetch web content, search the web, and manage scheduled tasks. Built on the OpenAI SDK against OpenRouter (defaulting to DeepSeek).

- **Interactive CLI**: Multi-turn REPL sessions with tool use
- **Background Agent**: Runs as a persistent bot, receiving and sending messages via channels
- **Web UI**: Browser-based chat interface served by FastHTML + uvicorn — dark/light theme, collapsible conversation sidebar, full conversation history
- **Telegram Integration**: Built-in Telegram bot — receive messages, respond, run tools, deliver results
- **Discord Integration**: Discord bot — same agent loop, per-channel message isolation, owner DM fallback for scheduled tasks
- **Scheduled Tasks**: SQLite-backed task scheduler — run prompts on a recurring or one-shot schedule and deliver results to a channel
- **MCP Servers**: Connect any [Model Context Protocol](https://modelcontextprotocol.io) server via `mcp_servers.json` — tools are auto-discovered and available alongside built-ins
- **Tool Calling**: File I/O, shell commands, web fetch, web search (text/images/video/news/books), calculator, Hacker News, todo list
- **Skills System**: Extendable skills in `app/skills/` (e.g., `puppeteer` for headless browsing)
- **Persistent History**: Per-channel SQLite message history at `~/.anotherbot/app.db` (shared with scheduled tasks)


## Prerequisites

- OpenRouter API key (or any OpenAI-compatible API)
- Docker with Compose, **or** Python 3.12+ and the `uv` package manager to run without Docker

## Quickstart

1. Copy the example env file:

   ```bash
   cp app/.env.example .env
   ```

2. Open `.env` and set your API key:

   ```env
   LLM_API_KEY=sk-or-your-key-here
   WEB_PASSWORD=pick-a-long-password    # login for the web UI
   ```

   Optionally set `TELEGRAM_BOT_TOKEN` / `DISCORD_BOT_TOKEN` (and their `*_ALLOW_FROM`) to enable those channels. See [Configuration](#configuration) for all variables.

3. Build and start it:

   ```bash
   docker compose up --build
   ```

4. Open `http://localhost:8765/` and log in with `WEB_PASSWORD`. Stop with `Ctrl+C` (or run with `-d` to keep it in the background).

**Without Docker** (needs Python 3.12+ and `uv`): after steps 1–2, run

```bash
uv sync
./run.sh cli                 # interactive REPL
./run.sh background          # Telegram / Discord / web UI server
```

`background` needs at least one channel: set `WEBSOCKET_HOST=127.0.0.1` in `.env` for the web UI at `http://localhost:8765/`, and/or a Telegram or Discord token.

## Configuration

All configuration comes from environment variables. Put them in a `.env` file, or set them in the shell or container; real environment variables always win over `.env` values. Two `.env` files are read at startup:

1. The nearest `.env` found from `app/` upwards — normally `.env` in the repo root.
2. `$ANOTHERBOT_HOME/.env` (default `~/.anotherbot/.env`).

Start from the template:

```bash
cp app/.env.example .env   # then set LLM_API_KEY
```

| Env var | Default | Description |
|---|---|---|
| `LLM_API_KEY` | — (**required**) | OpenRouter / OpenAI-compatible API key |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | API base URL |
| `LLM_MODEL` | `deepseek/deepseek-v4.1-flash` | Model string |
| `MAX_ITERATIONS` | `250` | Max agentic loop iterations (CLI `-i` overrides) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOW_FROM` | — | Comma-separated Telegram user IDs |
| `DISCORD_BOT_TOKEN` | — | Discord bot token |
| `DISCORD_ALLOW_FROM` | — | Comma-separated Discord user IDs (empty = allow all) |
| `WEBSOCKET_HOST` | — | Bind host for the web UI; setting it enables the web channel (`0.0.0.0` in Docker) |
| `WEBSOCKET_PORT` | `8765` | Port for the web UI + WebSocket |
| `WEB_PASSWORD` | — | Web UI login password. Required when `WEBSOCKET_HOST` isn't localhost; without it the web channel is disabled |
| `ANOTHERBOT_HOME` | `~/.anotherbot` | Data directory (SQLite DB, logs, uploads, `mcp_servers.json`, optional `.env`) |

Invalid values (e.g. a non-integer `WEBSOCKET_PORT` or `MAX_ITERATIONS`) stop startup with an error.

Message history and conversations are stored in `$ANOTHERBOT_HOME/app.db` (SQLite). Logs go to `$ANOTHERBOT_HOME/logs/app.log`.

## Usage

```bash
./run.sh cli -p "your prompt here"

# Flags
-p, --prompt          Run this prompt once and exit (omit to start the REPL)
-y, --auto-approve    Skip tool permission prompts
-q, --quiet           Don't print log messages to the console (still written to the log file)
-i, --max-iterations  Max agentic loop iterations (default: MAX_ITERATIONS or 250)
```

### Examples

```bash
# Interactive REPL session
./run.sh cli

# Single-shot, auto-approved
./run.sh cli -p "Create a hello world script" -y

# Quiet, auto-approved (only the agent's output on the console)
./run.sh cli -p "Summarize this repo" -y -q
```

### Web UI

The background agent can serve a browser-based chat UI on the same port as the WebSocket endpoint. Enable it by setting the `WEBSOCKET_*` env vars (in `.env` or the shell):

```bash
# Start the background server with the web channel enabled
WEBSOCKET_HOST=127.0.0.1 WEBSOCKET_PORT=8765 LLM_API_KEY=... ./run.sh background
```

Then open `http://localhost:8765/` in a browser.

**Login:** set `WEB_PASSWORD` to require a password. The UI, REST API, uploads and the WebSocket are all behind the login (a signed `HttpOnly` session cookie, valid 30 days; changing the password logs everyone out). Without `WEB_PASSWORD` the web channel only starts when bound to localhost; on any other host it is disabled with an error. The web UI is an admin interface — the agent can run shell commands — so use a long password and put HTTPS in front (e.g. Caddy, Tailscale) if it is reachable over a network.

**Features:**
- Dark/light theme toggle (persisted in `localStorage`)
- Collapsible sidebar listing conversations from **all** channels (web, Telegram, Discord, CLI), each tagged with a channel badge — click to load history
- `+ New` button and `/new` command to start a fresh conversation
- `/help`, `/status`, `/whoami`, `/stop` answered instantly without an LLM call
- All other slash commands (`/model`, `/load`, `/fork`, `/rename`, `/export`) forwarded to the agent

### Background Agent (Telegram / Discord)

Configure one or both channels in `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-your-bot-token
TELEGRAM_ALLOW_FROM=123456789        # comma-separated user IDs

DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_ALLOW_FROM=                  # comma-separated user IDs; empty = allow all
```

```bash
./run.sh background
```

Each channel gets its own message queue and agent. Scheduled task results are delivered to the channel the task was created from; if no context is available, the Discord bot owner is DM'd.

**Bot commands:** `/help` — list all commands; `/model [name]` — get or set the model; `/status` — show uptime and current conversation; `/stop` — pause the bot; `/whoami` — show your user ID (Telegram only); `/list`, `/new`, `/load <id>`, `/fork [id]`, `/rename <id> <name>`, `/export [id]` — manage conversation history. `/list` shows only the current channel's conversations; `/load`, `/fork`, `/rename` and `/export` accept any conversation ID, including ones from other channels.

### Scheduled Tasks

The background agent supports scheduled prompts that run automatically and deliver results to a channel. Manage them by messaging the bot:

```
add a task to fetch HN top stories every 60 minutes starting now
run "summarize the latest news" once at 2025-06-01T09:00:00
list my scheduled tasks
remove the HN task
```

Tasks persist in `$ANOTHERBOT_HOME/app.db` (shared with message history) and survive restarts.

## MCP Servers

External [Model Context Protocol (MCP)](https://modelcontextprotocol.io) servers extend the agent with additional tools. Configured servers are initialized at startup; their tools appear alongside the built-in ones in the agent's tool list.

### Setup

Create `$ANOTHERBOT_HOME/mcp_servers.json` (default `~/.anotherbot/mcp_servers.json`). The format matches Claude Desktop's `mcpServers` config, so existing Claude Desktop configs can be copied directly:

```json
{
  "mcpServers": {
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"]
    },
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time"]
    },
    "weather": {
      "url": "https://weather-mcp.example.com/sse"
    },
    "custom": {
      "command": "python",
      "args": ["./my_server.py"],
      "env": {"MY_API_KEY": "secret"}
    }
  }
}
```

Each server entry supports one of two transport types:

| Field | Required | Description |
|---|---|---|
| `command` | one of | Executable to spawn (stdio transport) |
| `args` | no | Argument list for the command |
| `env` | no | Extra environment variables for the subprocess |
| `url` | one of | SSE/HTTP endpoint URL (remote transport) |
| `disabled` | no | Set to `true` to skip this server at startup |

To enable only a subset of servers, add `"disabled": true` to those you want to skip:

```json
{
  "mcpServers": {
    "memory": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"] },
    "time":   { "command": "uvx", "args": ["mcp-server-time"], "disabled": true }
  }
}
```

Disabled servers appear in `/mcp` output with status `disabled` so you can see what is configured without connecting it.

### Tool namespacing

MCP tool names are prefixed with their server name using `__` as a separator: `servername__toolname`. This prevents collisions between built-in tools and between different servers.

> **Note:** Server names must not contain `__` — it is reserved as the tool namespace separator.

### Startup behaviour

- All servers connect concurrently at startup.
- Failed connections are logged and skipped — the agent starts normally with whichever servers connected successfully.
- All connections are gracefully shut down when the process exits.

### Docker

The image does not include Node.js, so `npx`-based servers need it installed first (`uvx`-based servers work out of the box):

```bash
docker compose exec anotherbot sh -c "apt-get update && apt-get install -y nodejs npm"
```

This lasts until the container is recreated.

Mount `mcp_servers.json` into the container's data directory:

```bash
docker run ... \
  -v /path/to/mcp_servers.json:/data/mcp_servers.json \
  -v anotherbot-data:/data \
  anotherbot
```

## Docker

### Docker Compose (easiest)

```bash
cp app/.env.example .env      # set LLM_API_KEY and any channel tokens
docker compose up -d --build
docker compose logs -f        # follow output
```

Then open `http://localhost:8765/` and log in with your `WEB_PASSWORD` (required: the container binds to `0.0.0.0`). `compose.yaml` passes your `.env` into the container and keeps data in the `anotherbot-data` volume. `WEBSOCKET_HOST` and `ANOTHERBOT_HOME` are always forced to `0.0.0.0` and `/data`, so local-dev values in `.env` don't break the container. Setting `WEBSOCKET_PORT` in `.env` changes both the port the app listens on and the published port.

To add MCP servers, copy the file into the volume: `docker compose cp mcp_servers.json anotherbot:/data/`, then run `docker compose restart`.

### Build

```bash
docker build -t anotherbot .
```

### Run — Web UI

**All flags inline:**
```bash
docker run -d \
  -e LLM_API_KEY=sk-... \
  -e WEB_PASSWORD=pick-a-long-password \
  -e WEBSOCKET_HOST=0.0.0.0 \
  -e WEBSOCKET_PORT=8765 \
  -p 8765:8765 \
  -v anotherbot-data:/data \
  anotherbot
```

**Using `export` first (keeps the run command clean):**
```bash
export LLM_API_KEY=sk-...
export WEB_PASSWORD=pick-a-long-password
export WEBSOCKET_HOST=0.0.0.0
export WEBSOCKET_PORT=8765

docker run -d \
  -e LLM_API_KEY \
  -e WEB_PASSWORD \
  -e WEBSOCKET_HOST \
  -e WEBSOCKET_PORT \
  -p 8765:8765 \
  -v anotherbot-data:/data \
  anotherbot
```

Then open `http://localhost:8765/` in a browser. `WEBSOCKET_HOST=0.0.0.0` is required — the default `127.0.0.1` is the container's own loopback and is not reachable via Docker port mapping.

### Run — Telegram

```bash
docker run -d \
  -e LLM_API_KEY=sk-... \
  -e TELEGRAM_BOT_TOKEN=123:abc... \
  -e TELEGRAM_ALLOW_FROM=123456789 \
  -v anotherbot-data:/data \
  anotherbot
```

### Run — Discord

```bash
docker run -d \
  -e LLM_API_KEY=sk-... \
  -e DISCORD_BOT_TOKEN=your-discord-token \
  -e DISCORD_ALLOW_FROM=123456789 \
  -v anotherbot-data:/data \
  anotherbot
```

### Run — all channels at once

```bash
export LLM_API_KEY=sk-...
export TELEGRAM_BOT_TOKEN=123:abc...
export TELEGRAM_ALLOW_FROM=123456789
export DISCORD_BOT_TOKEN=your-discord-token
export WEB_PASSWORD=pick-a-long-password
export WEBSOCKET_HOST=0.0.0.0
export WEBSOCKET_PORT=8765

docker run -d \
  -e LLM_API_KEY \
  -e TELEGRAM_BOT_TOKEN \
  -e TELEGRAM_ALLOW_FROM \
  -e DISCORD_BOT_TOKEN \
  -e WEB_PASSWORD \
  -e WEBSOCKET_HOST \
  -e WEBSOCKET_PORT \
  -p 8765:8765 \
  -v anotherbot-data:/data \
  anotherbot
```

### Environment variables

| Env var | Required | Description |
|---|---|---|
| `LLM_API_KEY` | **yes** | OpenRouter / OpenAI-compatible API key |
| `WEBSOCKET_HOST` | — | Bind host for web UI (use `0.0.0.0` in Docker; default: `127.0.0.1`) |
| `WEBSOCKET_PORT` | — | Port for web UI and WebSocket (default: `8765`) |
| `WEB_PASSWORD` | for web UI | Web UI login password. The image binds to `0.0.0.0`, so without it the web channel is disabled |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOW_FROM` | — | Comma-separated Telegram user IDs (empty = allow all) |
| `DISCORD_BOT_TOKEN` | — | Discord bot token from developer portal |
| `DISCORD_ALLOW_FROM` | — | Comma-separated Discord user IDs (empty = allow all) |
| `LLM_BASE_URL` | no | API base URL (default: `https://openrouter.ai/api/v1`) |
| `LLM_MODEL` | no | Model string (default: `deepseek/deepseek-v4.1-flash`) |
| `MAX_ITERATIONS` | no | Max agentic loop iterations (default: `250`) |
| `ANOTHERBOT_HOME` | no | Data directory for DB and workspace (default: `/data` in container) |

At least one channel must be usable or the server will exit: the web UI (`WEBSOCKET_HOST` plus `WEB_PASSWORD` in Docker), `TELEGRAM_BOT_TOKEN`, or `DISCORD_BOT_TOKEN`. The image always sets `WEBSOCKET_HOST=0.0.0.0`; a Telegram/Discord-only container without `WEB_PASSWORD` just logs that the web channel is disabled.

The `/data` volume persists the SQLite database, logs and workspace across restarts. Instead of `-e` flags you can put a `.env` file in the volume (`/data/.env`) or use `docker run --env-file .env`; variables passed with `-e` take precedence. `.env` files are excluded from the image by `.dockerignore`.

## Roadmap

- **Email Support**: IMAP/SMTP integration for reading and sending emails, attachment handling, and mailbox management
- **Slack Integration**: Slack app with interactive messages, modals, and workspace management
- **WhatsApp Support**: WhatsApp Business API integration via providers like Twilio or MessageBird
- **Anthropic OAuth**: Direct integration with Claude API using OAuth 2.0
- **Codex OAuth**: OpenAI Codex API authentication
- **GitHub OAuth**: Access to repositories, issues, and GitHub Actions
- **Gemini OAuth**: Google Gemini API authentication with Google Cloud credentials
- **Useful Skills**: Advanced skills for web scraping (headless browsers), data analysis (Pandas, NumPy), document processing (PDF, DOCX), and media manipulation
- **Web Dashboard**: Admin interface for monitoring agents, configuring channels, and viewing analytics

## Testing

```bash
# All tests
uv run pytest

# Single file
uv run pytest tests/test_agent.py

# Integration tests
uv run pytest tests/integration/

# With coverage
uv run pytest --cov=app --cov-report=term-missing
```

Unit tests mock `app.cli_agent.Client` and `app.cli_agent.load_system_context` (see `tests/test_startup.py`). Integration tests mock only the OpenAI HTTP client and run the full pipeline including `main()` and argparse.

## Adding New Tools

Tools use a class-based system with the `Tool` abstract base class (`app/tools/tool.py`).

1. Create `app/tools/my_tool.py` subclassing `Tool`
2. Register in `app/tool_calls.py` `tool_registry`

```python
from .tool import Tool

class MyTool(Tool):
    @staticmethod
    def spec() -> dict:
        return {
            "type": "function",
            "function": {
                "name": "my_tool",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": {"param": {"type": "string", "description": "..."}},
                    "required": ["param"]
                }
            }
        }

    @staticmethod
    def call(param: str) -> str:
        return "result"
```

## Author

Rikul Patel <rikulpatel@gmail.com>
