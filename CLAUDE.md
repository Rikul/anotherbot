# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Python-based AI agent ("crafterscode") that uses an OpenAI-compatible API (defaulting to OpenRouter/DeepSeek) via the `openai` Python SDK. It supports an interactive CLI REPL, silent/non-interactive mode, and a background server architecture with Telegram, Discord, and a FastHTML web UI channel.

## Running & Development

```bash
# Run the CLI agent
./run.sh cli -p "your prompt here"

# Run with auto-approve (no permission prompts) and exit after response
./run.sh cli -p "your prompt" -y -x

# Run in silent mode (suppresses output, implies --auto-approve --no-repl)
./run.sh cli -p "your prompt" -s

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_agent.py

# Run a single test
uv run pytest tests/test_agent.py::test_agent_loop_adds_user_message

# Run integration tests
uv run pytest tests/integration/
```

The project uses `uv` for dependency management. No compile step is needed.

## Configuration

Config lives at `~/.crafterscode/config.toml` (created automatically on first run with defaults). Key fields:
- `model` — LLM model string (default: `"deepseek/deepseek-v4-flash"`)
- `max_iterations` — max agentic loop iterations (default: `100`)
- `base_url` — API base URL (default: `"https://openrouter.ai/api/v1"`)
- `[telegram]` — `BOT_TOKEN`, `ALLOW_FROM` (list of integer user IDs)
- `[discord]` — `TOKEN`, `ALLOW_FROM` (list of integer user IDs; empty = allow all)
- `[websocket]` — `HOST` (default `"127.0.0.1"`), `PORT` (default `8765`)

Environment variables (all override config file values):
- `LLM_API_KEY` — required
- `LLM_BASE_URL` — optional API base URL override
- `MODEL` — optional model override
- `TELEGRAM_BOT_TOKEN` — Telegram bot token (alternative to config file)
- `TELEGRAM_ALLOW_FROM` — comma-separated Telegram user IDs (e.g. `"123,456"`)
- `DISCORD_BOT_TOKEN` — Discord bot token (alternative to config file)
- `DISCORD_ALLOW_FROM` — comma-separated Discord user IDs
- `WEBSOCKET_HOST` — bind host for the web UI (set to `0.0.0.0` in Docker)
- `WEBSOCKET_PORT` — port for web UI + WebSocket (default: `8765`)
- `ANOTHERBOT_HOME` — overrides the data directory (default: `~/.crafterscode`)

For Docker, no config file is needed — pass everything as env vars. See `Dockerfile` and the Docker section in README.

## Architecture

### Agent Loop

The shared loop lives in `Agent._loop()` (`app/core/agent.py`). Subclasses override hooks to specialise behaviour:

| Hook | CliAgent | BackgroundAgent | HelperAgent |
|---|---|---|---|
| `_on_thinking` | print if not silent | send via mq | — |
| `_check_permission` | ask stdin | — (always allow) | — |
| `_on_tool_start` | — | send status via mq | — |
| `_on_response` | print | send via mq | — |
| `_on_no_choices` | raise | exponential backoff | raise |
| `_should_stop` | — | `channel.has_stopped` | — |

Tool calls within a single LLM turn are dispatched in parallel via `asyncio.gather`. After each turn, the full message chain (assistant tool-call message + tool results + final response) is saved to `self.messages` with tool results truncated to `TOOL_RESULT_HISTORY_LIMIT` chars to keep context lean. `MessageHistory` (SQLite) stores only user + final assistant text for cross-session persistence.

### Tool System

Each tool is a class extending `Tool` (`app/core/tool.py`), an ABC requiring a static `spec()` (OpenAI function-call schema) and a static `call()` method. Tools are registered in `app/core/tool_calls.py` in `tool_registry` — a dict mapping tool name → `Tool` class. `run_tool()` dispatches by name and restores `os.getcwd()` after each call. Results are truncated to `MAX_TOOL_RESULT_LENGTH` (16 000 chars).

Current tools: `read_file`, `write_file`, `bash`, `web_fetch`, `get_skills_dir`, `todo_add/list/update/clear`, `calculator`, `hackernews`, `websearch_text/images/videos/news/books`, `list/add/update/remove_scheduled_task`, `get_scheduled_task_output`, `get_city_state`, `get_datetime`.

`_HELPER_AGENT_TOOLS` in `tool_calls.py` is an explicit allowlist of tools available to `HelperAgent` (used internally by scheduled tasks). Scheduled task mutation tools (`add/update/remove_scheduled_task`) are excluded to prevent recursion.

### System Context

On startup, `load_system_context()` (`app/infra/startup.py`) loads `app/core/sys_instructions.md` and prepends it as the system message to `self.messages`.

### Runtime Settings

`app/core/runtime.py` is an in-memory key-value singleton (`set()` / `get()`) for mutable settings like `model`, `base_url`, and `max_iterations`. Values are populated from config during startup in `main.py` and can be changed at runtime via the `/model` slash command.

### Channels & Command Registry

**Channel types** are defined in `ChannelType` enum (`app/channels/channel.py`): `CLI`, `TELEGRAM`, `DISCORD`, `WEB`. Each channel implements the `Channel` ABC and owns a `MessageQueue` instance. `bg_server.py` wires up enabled channels — each gets its own `MessageQueue`, `BackgroundAgent`, and set of coroutines (`run_polling`, `process_incoming`, `process_outgoing`) gathered into the event loop.

**WebChannel** (`app/channels/web_channel.py`) uses `python-fasthtml` + uvicorn to serve both a browser chat UI (`GET /`) and a JSON WebSocket endpoint (`WS /ws`) on the same port. Multiple concurrent browser tabs are supported — each connection gets a UUID tracked in `_connections`. A per-connection `asyncio.Lock` in `_send_locks` serializes writes to each WebSocket. The channel also exposes `GET /api/conversations`, `GET /api/messages`, and `GET /api/status` REST endpoints. Slash commands `/help`, `/status`, `/whoami`, `/stop` are handled inline without touching the `BackgroundAgent`; all others are forwarded via the message queue with `is_command=True` in metadata so `send_message()` emits `{"type":"system"}` responses, enabling the sidebar to refresh after conversation-mutating commands.

**Slash commands** are handled entirely by `BackgroundAgent`. Channel handlers (`command_handler` in Telegram, `on_message` in Discord) intercept only `/whoami` (resolved inline using the platform user object) and enqueue everything else as a plain `IncomingMessage`. `BackgroundAgent.process_incoming()` detects the leading `/` and dispatches via its own `CommandRegistry`. That registry owns the full command set: `/model`, `/status`, `/stop`, `/help`, `/list`, `/new`, `/load`, `/fork`, `/rename`, `/export`.

### Message Queue

`MessageQueue` (`app/channels/message_queue.py`) holds two `asyncio.Queue`s (incoming/outgoing). `BackgroundAgent.process_incoming()` consumes the incoming queue and drives `agent_loop()`; `process_outgoing()` dispatches outbound messages to registered delivery functions.

Each channel **must** have its own `MessageQueue` instance to avoid cross-channel message routing bugs.

### Scheduled Tasks

`ScheduledTasks` (`app/core/scheduled_tasks.py`) is a SQLite-backed task runner using the shared `APP_DB` (`~/.crafterscode/app.db`, or `$ANOTHERBOT_HOME/app.db`). It polls every 60 seconds, checks `next_run`, and executes due tasks via `HelperAgent`. Results are delivered to the configured channel via `MessageQueue`. Schema: `tasks` (id, name, prompt, enabled, repeat, interval_mins, next_run, last_run, delivery_channel, run_count, created_at) and `task_outputs` (id, name, prompt, output, status, duration_secs, timestamp). The `run()` coroutine is added to the `asyncio.gather` in `bg_server.py`.

## Testing Approach

Unit tests mock `app.core.agent.Client` and `load_system_context` to isolate the agent loop logic. `run_tool` is patched at `app.core.tool_calls.run_tool` (where the function lives) since `handle_tool_call` uses a lazy import. Integration tests in `tests/integration/` mock only the OpenAI HTTP client and run the full pipeline including `main()`, argparse, and agent construction. Tests use `pytest-asyncio` for async test functions.
