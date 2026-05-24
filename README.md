## Overview

A Python-based AI agent that can execute prompts, interact with the filesystem, run shell commands, fetch web content, search the web, and manage scheduled tasks. Built on the OpenAI SDK against OpenRouter (defaulting to DeepSeek).

- **Interactive CLI**: Multi-turn REPL sessions with tool use
- **Background Agent**: Runs as a persistent bot, receiving and sending messages via channels
- **Telegram Integration**: Built-in Telegram bot — receive messages, respond, run tools, deliver results
- **Discord Integration**: Discord bot — same agent loop, per-channel message isolation, owner DM fallback for scheduled tasks
- **Scheduled Tasks**: SQLite-backed task scheduler — run prompts on a recurring or one-shot schedule and deliver results to a channel
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
ALLOW_FROM = []  # List of allowed Discord user IDs (integers). Empty means allow all.
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

### Background Agent (Telegram / Discord)

Configure one or both channels in `~/.crafterscode/config.toml`:

```toml
[telegram]
BOT_TOKEN = "123456:ABC-your-bot-token"
ALLOW_FROM = [123456789]  # restrict by user ID; empty = allow all

[discord]
TOKEN = "your-discord-bot-token"
ALLOW_FROM = []  # restrict by user ID; empty = allow all
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

## Docker

```bash
docker build -t anotherbot .

# Telegram
docker run -d \
  -e LLM_API_KEY=sk-... \
  -e TELEGRAM_BOT_TOKEN=123:abc... \
  -e TELEGRAM_ALLOW_FROM=123456789 \
  -v anotherbot-data:/data \
  anotherbot

# Discord
docker run -d \
  -e LLM_API_KEY=sk-... \
  -e DISCORD_BOT_TOKEN=your-discord-token \
  -e DISCORD_ALLOW_FROM=123456789 \
  -v anotherbot-data:/data \
  anotherbot
```

| Env var | Required | Description |
|---|---|---|
| `LLM_API_KEY` | yes | OpenRouter / OpenAI-compatible API key |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOW_FROM` | — | Comma-separated Telegram user IDs (empty = allow all) |
| `DISCORD_BOT_TOKEN` | — | Discord bot token from developer portal |
| `DISCORD_ALLOW_FROM` | — | Comma-separated Discord user IDs (empty = allow all) |
| `LLM_BASE_URL` | no | API base URL (default: `https://openrouter.ai/api/v1`) |
| `MODEL` | no | Model string (default: `deepseek/deepseek-v3.2`) |
| `ANOTHERBOT_HOME` | no | Data directory for DB and workspace (default: `/data` in container) |

At least one channel (`TELEGRAM_BOT_TOKEN` or `DISCORD_BOT_TOKEN`) must be set.

The `/data` volume persists the SQLite database and workspace across restarts. To supply a `config.toml` instead of env vars, mount it at `/data/config.toml` — env vars always take precedence over the file.

## Roadmap

- **Email Support**: IMAP/SMTP integration for reading and sending emails, attachment handling, and mailbox management
- **MCP Support**: Integration with Model Context Protocol for external data sources, tools, and state management
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
