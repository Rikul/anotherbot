# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Python-based AI agent ("anotherbot") that uses an OpenAI-compatible API (defaulting to OpenRouter/DeepSeek) via the `openai` Python SDK. It supports an interactive CLI REPL, single-prompt (`-p`) mode with optional `--quiet` console logging, and a background server architecture with Telegram, Discord, and a FastHTML web UI channel.

## Running & Development

```bash
# Run the CLI agent (interactive REPL)
./run.sh cli

# Run a single prompt and exit (-p always exits after the response)
./run.sh cli -p "your prompt here"

# Run with auto-approve (no permission prompts)
./run.sh cli -p "your prompt" -y

# Quiet mode: no log messages on the console (still written to the log file)
./run.sh cli -p "your prompt" -y -q

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

All configuration comes from environment variables — there is no config file. `app/config.py` calls `load_dotenv()` at import time, before computing `PROJECT_HOME`: first the nearest `.env` (searched from `app/` upwards, normally the repo root), then `$PROJECT_HOME/.env`. Neither overrides variables already set in the real environment. `config.load()` (called at the start of `main()`) builds `_config` from defaults + env vars and raises `ValueError` on invalid integers so bad config fails fast. Template: `app/.env.example`.

- `LLM_API_KEY` — required (`config.get("api_key")`)
- `LLM_BASE_URL` — API base URL (default: `https://openrouter.ai/api/v1`)
- `LLM_MODEL` — model string (default: `deepseek/deepseek-v4.1-flash`)
- `MAX_ITERATIONS` — max agentic loop iterations (default: `250`; CLI `-i` overrides)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOW_FROM` — bot token and comma-separated user IDs (→ `config.telegram`)
- `DISCORD_BOT_TOKEN`, `DISCORD_ALLOW_FROM` — bot token and comma-separated user IDs; empty = allow all (→ `config.discord`)
- `WEBSOCKET_HOST` — bind host for the web UI; setting it enables the web channel (`0.0.0.0` in Docker)
- `WEBSOCKET_PORT` — port for web UI + WebSocket (default: `8765`)
- `WEB_PASSWORD` — web UI login password (→ top-level `config.get("web_password")`, *not* the `websocket` section, so setting it alone doesn't enable the channel)
- `ANOTHERBOT_HOME` — data directory, `PROJECT_HOME` (default: `~/.anotherbot`). Holds `app.db`, `logs/app.log`, uploads, traces, `mcp_servers.json` and an optional `.env`. Read at import time, so it can be set in the repo-root `.env` but not in `$PROJECT_HOME/.env`.

MCP servers are configured separately in `$ANOTHERBOT_HOME/mcp_servers.json` (same format as Claude Desktop's `mcpServers` key).

For Docker, `compose.yaml` is the easy path (`docker compose up -d --build` reads the repo-root `.env`, forces `WEBSOCKET_HOST=0.0.0.0` and `ANOTHERBOT_HOME=/data`, and publishes `${WEBSOCKET_PORT:-8765}` on both sides). With plain `docker run`, pass env vars with `-e`/`--env-file`. `.dockerignore` excludes `.env` files so secrets are never baked into the image. See `Dockerfile` and the Docker section in README.

## Architecture

### Agent Loop

The shared loop lives in `Agent._loop()` (`app/core/agent.py`). Subclasses override hooks to specialise behaviour:

| Hook | CliAgent | BackgroundAgent | HelperAgent |
|---|---|---|---|
| `_on_thinking` | print | send via mq | — |
| `_check_permission` | ask stdin | — (always allow) | — |
| `_on_tool_start` | — | send status via mq | — |
| `_on_response` | print | send via mq | — |
| `_on_no_choices` | raise | exponential backoff | raise |
| `_should_stop` | — | `channel.has_stopped` | — |

Tool calls within a single LLM turn are dispatched in parallel via `asyncio.gather`. After each turn, the full message chain (assistant tool-call message + tool results + final response) is saved to `self.messages` at full length. `MessageHistory` (SQLite) stores only user + final assistant text for cross-session persistence.

### Tool System

Each tool is a class extending `Tool` (`app/core/tool.py`), an ABC requiring a static `spec()` (OpenAI function-call schema) and a static `call()` taking the parameters declared in `spec()` (sync or async). Because `call` signatures differ per tool, `call` is declared as an attribute rather than an abstract method and checked in `Tool.__init_subclass__`. Tools are registered in `app/core/tool_calls.py` in `tool_registry` — a dict mapping tool name → `Tool` class. `run_tool()` is asynchronous and dispatches by name, awaiting the tool's `call` if it is a coroutine function, and restores `os.getcwd()` after execution. Results are truncated to `MAX_TOOL_RESULT_LENGTH` (16 000 chars, defined in `app/core/tool.py` so `mcp_manager` can use it without importing `tool_calls`).

Current built-in tools: `read_file`, `write_file`, `bash`, `web_fetch`, `get_skills_dir`, `todo_add/list/update/clear`, `calculator`, `hackernews`, `websearch_text/images/videos/news/books`, `list/add/update/remove_scheduled_task`, `get_scheduled_task_output`, `get_city_state`, `get_datetime`, `helper_agent`.

Todo tools (`app/tools/todo.py`) keep an in-memory `TodoList` per asyncio task via a `ContextVar`: `BackgroundAgent.process_incoming()` calls `init_task_todos()` so each channel has its own list, and tool calls (child tasks from `asyncio.gather`) inherit it. Without a scope (CLI, tests) a module-level default list is used.

`_HELPER_AGENT_TOOLS` in `tool_calls.py` is an explicit allowlist of tools available to `HelperAgent` (used internally by scheduled tasks). Scheduled task mutation tools (`add/update/remove_scheduled_task`) are excluded to prevent recursion.

`get_all_tool_specs()` merges built-in specs with any MCP tool specs at call time (not module load). `run_tool_async()` is the async dispatcher used by `handle_tool_call` — it routes to `MCPManager.call_tool()` for MCP tools or falls through to the synchronous `run_tool()` for built-ins.

### MCP Servers

`MCPManager` (`app/core/mcp_manager.py`) owns persistent FastMCP client connections and their tool catalogs. It is a module-level singleton (`mcp_manager`). `initialize_mcp()` (same module, called from `main()`) reads `mcp_servers.json`, then calls `mcp_manager.initialize()` which connects all servers concurrently via `asyncio.gather`. Each server's tools are discovered via `list_tools()` and registered under the namespace `servername__toolname` (`_SEP = "__"`). `rpartition` is used when routing calls so tool names may contain underscores freely; only the server name must not contain `__`.

The singleton is shut down via `mcp_manager.shutdown()` in a `try/finally` block in `main()`. `HelperAgent` does not receive MCP tools — it uses the static `helper_tool_specs` allowlist to prevent recursion.

### System Context

On startup, `load_system_context()` (`app/infra/startup.py`) loads `app/core/sys_instructions.md` and prepends it as the system message to `self.messages`.

### Runtime Settings

`app/core/runtime.py` is an in-memory key-value singleton (`set()` / `get()`) for mutable settings like `model`, `base_url`, and `max_iterations`. Values are populated from `config` (env vars) during startup in `main.py` and can be changed at runtime via the `/model` slash command.

### Channels & Command Registry

**Channel types** are defined in `ChannelType` enum (`app/channels/channel.py`): `CLI`, `TELEGRAM`, `DISCORD`, `WEB`. Each channel implements the `Channel` ABC and owns a `MessageQueue` instance. `bg_server.py` wires up enabled channels — each gets its own `MessageQueue`, `BackgroundAgent`, and set of coroutines (`run_polling`, `process_incoming`, `process_outgoing`) gathered into the event loop.

**WebChannel** (`app/channels/web_channel.py`) uses `python-fasthtml` + uvicorn to serve both a browser chat UI (`GET /`) and a JSON WebSocket endpoint (`WS /ws`) on the same port. Multiple concurrent browser tabs are supported — each connection gets a UUID tracked in `_connections`. A per-connection `asyncio.Lock` in `_send_locks` serializes writes to each WebSocket. The channel also exposes `GET /api/conversations`, `GET /api/messages`, `GET /api/status`, and `POST /api/upload` REST endpoints. The web UI is an admin interface. Auth lives in `app/channels/web_auth.py`: when `WEB_PASSWORD` is set, `WebChannel.start()` wraps the FastHTML app in `WebAuthMiddleware` (pure ASGI, so it also guards the `/ws` handshake) and uvicorn serves `_asgi_app` instead of `_fasthtml_app`. The middleware serves `/login` + `/logout` itself, issues a signed `HttpOnly`/`SameSite=Lax` cookie (itsdangerous `TimestampSigner`, key derived from the password, 30-day max age), redirects `GET /` to `/login`, returns 401 for other HTTP paths (including `/static`), rejects unauthenticated WebSocket handshakes, and rejects cross-origin WebSocket handshakes and non-GET requests (`Origin` host must equal `Host`). Without a password, `start()` raises `RuntimeError` unless the host is loopback; `bg_server` catches it, logs, and skips only the web channel. Tests use `TestClient(ch._asgi_app)`. Because it is an admin view, `/api/conversations` calls `ConversationStore.list()` with no channel and returns conversations from **all** channels (each row includes `channel`, rendered as a badge in the sidebar), and `/api/messages` serves any existing conversation (404 only if the ID does not exist). `POST /api/upload` accepts multipart `files` (one or more), writes each under `$ANOTHERBOT_HOME/uploads` with a UUID prefix, and returns the stored basenames; the browser (paperclip button next to the input) sends those basenames back in the WebSocket `message` frame's `files` field. `_resolve_upload_paths()` joins each basename to the upload dir (basename-only, so client paths can't traverse out) and the resolved absolute paths reach the agent via `metadata["files"]`, which `Agent._build_user_message()` encodes as image/file parts. Only `/whoami` is handled inline (it needs the per-connection client ID); all other slash commands — including `/help`, `/status`, `/stop` — are forwarded to `BackgroundAgent`'s `CommandRegistry` via the message queue with `is_command=True` in metadata so `send_message()` emits `{"type":"system"}` responses, enabling the sidebar to refresh after conversation-mutating commands.

**Slash commands** are handled entirely by `BackgroundAgent`. Channel handlers (`command_handler` in Telegram, `on_message` in Discord) intercept only `/whoami` (resolved inline using the platform user object) and enqueue everything else as a plain `IncomingMessage`. `BackgroundAgent.process_incoming()` detects the leading `/` and dispatches via its own `CommandRegistry`. That registry owns the full command set: `/model`, `/status`, `/stop`, `/help`, `/list`, `/new`, `/load`, `/fork`, `/rename`, `/export`, `/mcp`. Both `BackgroundAgent` and the CLI build it with `build_command_registry()` (`app/channels/commands.py`); the bot adds `/stop` via `extra=`. Conversation commands use the agent through the `ConversationAgent` protocol (`store`, `channel_str`, `conversation_id`, `switch_conversation()`). `/list` is scoped to the caller's channel, but `/load`, `/fork`, `/rename` and `/export` accept any conversation ID regardless of which channel created it — `ConversationStore.rename/fork/export` no longer enforce channel ownership (`fork` creates the copy under the caller's channel).

### Message Queue

`MessageQueue` (`app/channels/message_queue.py`) holds two `asyncio.Queue`s (incoming/outgoing). `BackgroundAgent.process_incoming()` consumes the incoming queue and drives `agent_loop()`; `process_outgoing()` dispatches outbound messages to registered delivery functions.

Each channel **must** have its own `MessageQueue` instance to avoid cross-channel message routing bugs.

### Scheduled Tasks

`ScheduledTasks` (`app/core/scheduled_tasks.py`) is a SQLite-backed task runner using the shared `APP_DB` (`$ANOTHERBOT_HOME/app.db`, default `~/.anotherbot/app.db`). It polls every 30 seconds, checks `next_run`, and executes due tasks via `HelperAgent`. Results are delivered to the configured channel via `MessageQueue`. Schema: `tasks` (id, name, prompt, enabled, repeat, interval_mins, next_run, last_run, delivery_channel, run_count, created_at) and `task_outputs` (id, name, prompt, output, status, duration_secs, timestamp). The `run()` coroutine is added to the `asyncio.gather` in `bg_server.py`.

## Linting

`uv run ruff check app tests`, `uv run ruff format app`, and `uv run pylint app` (pylint is a dev dependency; run it via `uv run` so it sees the project venv). Deliberate exceptions are marked inline with `# pylint: disable=<rule>  # reason` — e.g. broad `except Exception` at boundaries where errors are returned to the model or a loop must keep running, and lazy imports that break import cycles.

## Testing Approach

Unit tests mock `app.core.agent.Client` and `load_system_context` to isolate the agent loop logic. `run_tool` is patched at `app.core.tool_calls.run_tool` (where the function lives) since `handle_tool_call` uses a lazy import. Integration tests in `tests/integration/` mock only the OpenAI HTTP client and run the full pipeline including `main()`, argparse, and agent construction. Tests use `pytest-asyncio` for async test functions.

Config is env-only, so tests set it with `monkeypatch.setenv(...)` and call `config.load()`. The autouse fixture in `tests/conftest.py` clears every config env var (`CONFIG_ENV_VARS`) and restores `config._config` around each test, so the developer's shell or `.env` never leaks in — add new config env vars to that list.
