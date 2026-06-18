## Overview

A Python-based AI agent that can execute prompts, interact with the filesystem, run shell commands, fetch web content, search the web, and manage scheduled tasks. Built on the OpenAI SDK against OpenRouter (defaulting to DeepSeek).

- **Interactive CLI**: Multi-turn REPL sessions with tool use
- **Background Agent**: Runs as a persistent bot, receiving and sending messages via channels
- **Web UI**: Browser-based chat interface served by FastHTML + uvicorn — dark/light theme, collapsible conversation sidebar, full conversation history
- **Telegram Integration**: Built-in Telegram bot — receive messages, respond, run tools, deliver results
- **Discord Integration**: Discord bot — same agent loop, per-channel message isolation, owner DM fallback for scheduled tasks
- **Slack Integration**: Slack bot via Socket Mode — no public webhook required
- **Scheduled Tasks**: SQLite-backed task scheduler — run prompts on a recurring or one-shot schedule and deliver results to a channel
- **MCP Servers**: Connect any [Model Context Protocol](https://modelcontextprotocol.io) server via `mcp_servers.json` — tools are auto-discovered and available alongside built-ins
- **Tool Calling**: File I/O, shell commands, web fetch, web search (text/images/video/news/books), calculator, Hacker News, todo list
- **Skills System**: Extendable skills in `app/skills/` (e.g., `puppeteer` for headless browsing)
- **Persistent History**: Per-channel SQLite message history at `~/.crafterscode/app.db` (shared with scheduled tasks)


## Prerequisites

- Python 3.12 or higher
- `uv` package manager
- OpenRouter API key (or any OpenAI-compatible API)

## Installation

```bash
uv sync
```

## Configuration

### API Key (Required)

Set `LLM_API_KEY` in a `.env` file or as an environment variable. Alternatively, set `api_key` directly in `config.toml` (env var takes precedence):

```env
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://openrouter.ai/api/v1  # optional override
```

### Agent Config

Config lives at `~/.crafterscode/config.toml` and is created automatically on first run with defaults:

```toml
model = "deepseek/deepseek-v3.2"
max_iterations = 100
base_url = "https://openrouter.ai/api/v1"
api_key = ""  # fallback if LLM_API_KEY env var is not set

[telegram]
BOT_TOKEN = ""
ALLOW_FROM = []  # List of allowed Telegram user IDs (integers).

[discord]
TOKEN = ""
ALLOW_FROM = []  # List of allowed Discord user IDs (integers). Empty = deny all.

[websocket]
HOST = "127.0.0.1"   # use 0.0.0.0 to expose on all interfaces (required for Docker)
PORT = 8765

[slack]
BOT_TOKEN = ""   # xoxb-... bot token
APP_TOKEN = ""   # xapp-... app-level token (required for Socket Mode)
ALLOW_FROM = []  # List of allowed Slack user IDs (strings). Empty = deny all.
```

Message history is stored in `~/.crafterscode/history.db` (SQLite). Each channel maintains its own history with estimated token counts per message.

## Usage

```bash
./run.sh cli -p "your prompt here"

# Flags
-p, --prompt          Initial prompt (required)
-y, --auto-approve    Skip tool permission prompts
-x, --no-repl         Exit after initial prompt (no REPL)
-s, --silent          Suppress output, implies -y -x
-i, --max-iterations  Max agentic loop iterations (default: 100)
```

### Examples

```bash
# Interactive REPL session
./run.sh cli -p "List all Python files in the current directory"

# Single-shot, auto-approved
./run.sh cli -p "Create a hello world script" -y -x

# Silent mode
./run.sh cli -p "Summarize this repo" -s
```

### Web UI

The background agent can serve a browser-based chat UI on the same port as the WebSocket endpoint. Enable it by adding a `[websocket]` section to `config.toml` or by setting the `WEBSOCKET_*` env vars:

```bash
# Start the background server with the web channel enabled
WEBSOCKET_HOST=127.0.0.1 WEBSOCKET_PORT=8765 LLM_API_KEY=... ./run.sh background
```

Then open `http://localhost:8765/` in a browser.

**Features:**
- Dark/light theme toggle (persisted in `localStorage`)
- Collapsible sidebar listing all conversations — click to load history
- `+ New` button and `/new` command to start a fresh conversation
- `/help`, `/status`, `/whoami`, `/stop` answered instantly without an LLM call
- All other slash commands (`/model`, `/load`, `/fork`, `/rename`, `/export`) forwarded to the agent

### Background Agent (Telegram / Discord / Slack)

Configure one or more channels in `~/.crafterscode/config.toml`:

```toml
[telegram]
BOT_TOKEN = "123456:ABC-your-bot-token"
ALLOW_FROM = [123456789]  # restrict by user ID

[discord]
TOKEN = "your-discord-bot-token"
ALLOW_FROM = []  # restrict by user ID

[slack]
BOT_TOKEN = "xoxb-..."   # bot token from Slack app settings
APP_TOKEN = "xapp-..."   # app-level token with connections:write scope
ALLOW_FROM = []          # restrict by Slack user ID
```

```bash
./run.sh background
```

Each channel gets its own message queue and agent. Scheduled task results are delivered to the channel the task was created from; if no context is available, the Discord bot owner is DM'd.

**Bot commands:** `/help` — list all commands; `/model [name]` — get or set the model; `/status` — show uptime and current conversation; `/stop` — pause the bot; `/whoami` — show your user ID (Telegram only); `/list`, `/new`, `/load <id>`, `/fork [id]`, `/rename <id> <name>`, `/export [id]` — manage conversation history.

### Scheduled Tasks

The background agent supports scheduled prompts that run automatically and deliver results to a channel. Manage them by messaging the bot:

```
add a task to fetch HN top stories every 60 minutes starting now
run "summarize the latest news" once at 2025-06-01T09:00:00
list my scheduled tasks
remove the HN task
```

Tasks persist in `~/.crafterscode/app.db` (shared with message history) and survive restarts.

## MCP Servers

External [Model Context Protocol (MCP)](https://modelcontextprotocol.io) servers extend the agent with additional tools. Configured servers are initialized at startup; their tools appear alongside the built-in ones in the agent's tool list.

### Setup

Create `~/.crafterscode/mcp_servers.json` (same directory as `config.toml`). The format matches Claude Desktop's `mcpServers` config, so existing Claude Desktop configs can be copied directly:

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

Mount `mcp_servers.json` into the container's data directory:

```bash
docker run ... \
  -v /path/to/mcp_servers.json:/data/mcp_servers.json \
  -v anotherbot-data:/data \
  anotherbot
```

## Docker

### Build

```bash
docker build -t anotherbot .
```

### Run — Web UI

**All flags inline:**
```bash
docker run -d \
  -e LLM_API_KEY=sk-... \
  -e WEBSOCKET_HOST=0.0.0.0 \
  -e WEBSOCKET_PORT=8765 \
  -p 8765:8765 \
  -v anotherbot-data:/data \
  anotherbot
```

**Using `export` first (keeps the run command clean):**
```bash
export LLM_API_KEY=sk-...
export WEBSOCKET_HOST=0.0.0.0
export WEBSOCKET_PORT=8765

docker run -d \
  -e LLM_API_KEY \
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

# Slack
docker run -d \
  -e LLM_API_KEY=sk-... \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_APP_TOKEN=xapp-... \
  -e SLACK_ALLOW_FROM=U123456789 \
  -v anotherbot-data:/data \
  anotherbot
```

### Run — all channels at once

```bash
export LLM_API_KEY=sk-...
export TELEGRAM_BOT_TOKEN=123:abc...
export TELEGRAM_ALLOW_FROM=123456789
export DISCORD_BOT_TOKEN=your-discord-token
export WEBSOCKET_HOST=0.0.0.0
export WEBSOCKET_PORT=8765

docker run -d \
  -e LLM_API_KEY \
  -e TELEGRAM_BOT_TOKEN \
  -e TELEGRAM_ALLOW_FROM \
  -e DISCORD_BOT_TOKEN \
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
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOW_FROM` | — | Comma-separated Telegram user IDs (empty = deny all) |
| `DISCORD_BOT_TOKEN` | — | Discord bot token from developer portal |
| `DISCORD_ALLOW_FROM` | — | Comma-separated Discord user IDs (empty = deny all) |
| `SLACK_BOT_TOKEN` | — | Slack bot token (`xoxb-...`) from app settings |
| `SLACK_APP_TOKEN` | — | Slack app-level token (`xapp-...`) with `connections:write` scope |
| `SLACK_ALLOW_FROM` | — | Comma-separated Slack user IDs (empty = deny all) |
| `LLM_BASE_URL` | no | API base URL (default: `https://openrouter.ai/api/v1`) |
| `MODEL` | no | Model string (default: `deepseek/deepseek-v3.2`) |
| `ANOTHERBOT_HOME` | no | Data directory for DB and workspace (default: `/data` in container) |

At least one channel (`WEBSOCKET_HOST`, `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, or `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN`) must be set or the server will exit.

The `/data` volume persists the SQLite database and workspace across restarts. To supply a `config.toml` instead of env vars, mount it at `/data/config.toml` — env vars always take precedence over the file.

## Roadmap

- **Email Support**: IMAP/SMTP integration for reading and sending emails, attachment handling, and mailbox management
<<<<<<< HEAD
- **MCP Support**: Integration with Model Context Protocol for external data sources, tools, and state management
=======
- **Slack Integration**: Slack app with interactive messages, modals, and workspace management
>>>>>>> origin/main
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
