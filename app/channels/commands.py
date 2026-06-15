from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Awaitable, Protocol, TYPE_CHECKING
from ..core import runtime

if TYPE_CHECKING:
    from ..infra.conversations import ConversationStore


class ConversationAgent(Protocol):
    """Structural type for agents that support conversation management."""
    _store: Any          # ConversationStore
    _channel_str: str
    conversation_id: int

    def _switch_conversation(self, conv: dict) -> None: ...

log = logging.getLogger(__name__)

CommandHandler = Callable[[str], Awaitable[str]]

_STARTUP_TIME: datetime = datetime.now()


@dataclass
class BotCommand:
    name: str
    description: str
    handler: CommandHandler


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, BotCommand] = {}

    def register(self, cmd: BotCommand) -> None:
        self._commands[cmd.name] = cmd

    async def execute(self, name: str, args: str = "") -> str | None:
        cmd = self._commands.get(name)
        if cmd is None:
            return None
        try:
            return await cmd.handler(args)
        except Exception:
            log.exception(f"Command /{name} raised an exception")
            return "An error occurred running that command."

    def list(self) -> list[BotCommand]:
        return list(self._commands.values())


def help_cmd(registry: CommandRegistry) -> CommandHandler:
    async def _help(args: str = "") -> str:
        lines = ["Available commands:"]
        for cmd in registry.list():
            lines.append(f"/{cmd.name} — {cmd.description}")
        return "\n".join(lines)
    return _help


async def model_cmd(args: str = "") -> str:
    if not args.strip():
        return f"Current model: {runtime.get('model', 'unknown')}"
    runtime.set("model", args.strip())
    return f"Model set to: {args.strip()}"


async def trace_cmd(args: str = "") -> str:
    arg = args.strip().lower()
    tracedir = runtime.get("tracedir")
    if arg == "on":
        runtime.set("trace", True)
        return f"Tracing on. Writing to {tracedir}"
    elif arg == "off":
        runtime.set("trace", False)
        return "Tracing off."
    else:
        state = runtime.get("trace", False)
        return f"Tracing is {'on' if state else 'off'}. Dir: {tracedir}"


def make_status_cmd(channel_str: str = "") -> CommandHandler:
    async def _status(args: str = "") -> str:
        uptime = datetime.now() - _STARTUP_TIME
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        model = runtime.get("model", "unknown")
        if channel_str:
            conv_id = runtime.get(f"conversation_id:{channel_str}", "—")
            conv_name = runtime.get(f"conversation_name:{channel_str}", "—")
        else:
            conv_id = runtime.get("conversation_id", "—")
            conv_name = runtime.get("conversation_name", "—")
        tracing = runtime.get("trace", False)
        last_trace = runtime.get("last_trace")
        trace_line = f"on ({last_trace})" if (tracing and last_trace) else ("on" if tracing else "off")
        return (
            f"Bot status:\n"
            f"  Model:        {model}\n"
            f"  Uptime:       {hours}h {minutes}m {seconds}s\n"
            f"  Conversation: [{conv_id}] {conv_name}\n"
            f"  Tracing:      {trace_line}"
        )
    return _status


# Backward-compatible alias used by existing tests and CLI
status_cmd = make_status_cmd()


# --- Conversation management commands ---

def list_conversations_cmd(store: ConversationStore, channel: str) -> CommandHandler:
    async def _list(args: str = "") -> str:
        convs = store.list(channel)
        if not convs:
            return "No conversations yet."
        lines = [f"Conversations ({len(convs)}):"]
        for c in convs:
            lines.append(f"  [{c['id']}] {c['name']} — {c['message_count']} messages, updated {c['updated_at'][:16]}")
        return "\n".join(lines)
    return _list


def new_conversation_cmd(agent: ConversationAgent) -> CommandHandler:
    async def _new(args: str = "") -> str:
        cid = agent._store.create(agent._channel_str)
        conv = agent._store.get(cid)
        agent._switch_conversation(conv)
        return f"Started new conversation [{conv['id']}] {conv['name']}"
    return _new


def load_conversation_cmd(agent: ConversationAgent) -> CommandHandler:
    async def _load(args: str = "") -> str:
        if not args.strip():
            return "Usage: /load <id>"
        try:
            conv_id = int(args.strip())
        except ValueError:
            return "Invalid id: must be an integer."
        conv = agent._store.get(conv_id)
        if conv is None or conv["channel"] != agent._channel_str:
            return "Conversation not found or access denied."
        agent._switch_conversation(conv)
        convs = agent._store.list(agent._channel_str)
        msg_count = next((c["message_count"] for c in convs if c["id"] == conv_id), 0)
        return f"Loaded conversation [{conv['id']}] {conv['name']} ({msg_count} messages)"
    return _load


def fork_conversation_cmd(agent: ConversationAgent) -> CommandHandler:
    async def _fork(args: str = "") -> str:
        source_id = agent.conversation_id
        if args.strip():
            try:
                source_id = int(args.strip())
            except ValueError:
                return "Invalid id: must be an integer."
        try:
            new_id = agent._store.fork(source_id, agent._channel_str)
        except ValueError as e:
            return str(e)
        conv = agent._store.get(new_id)
        agent._switch_conversation(conv)
        return f"Forked into new conversation [{conv['id']}] {conv['name']}"
    return _fork


def rename_conversation_cmd(store: ConversationStore, channel: str) -> CommandHandler:
    async def _rename(args: str = "") -> str:
        parts = args.strip().split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: /rename <id> <new name>"
        try:
            conv_id = int(parts[0])
        except ValueError:
            return "Invalid id: must be an integer."
        new_name = parts[1].strip()
        if not new_name:
            return "Name cannot be empty."
        try:
            store.rename(conv_id, new_name, channel)
        except ValueError as e:
            return str(e)

        persisted_name = store.get(conv_id)["name"]
        if runtime.get("conversation_id") == conv_id:
            runtime.set("conversation_name", persisted_name)
        if channel and runtime.get(f"conversation_id:{channel}") == conv_id:
            runtime.set(f"conversation_name:{channel}", persisted_name)

        return f"Conversation [{conv_id}] renamed to \"{persisted_name}\""
    return _rename


def export_conversation_cmd(store: ConversationStore, channel: str) -> CommandHandler:
    async def _export(args: str = "") -> str:
        if args.strip():
            try:
                conv_id = int(args.strip())
            except ValueError:
                return "Invalid id: must be an integer."
        else:
            if channel:
                conv_id = runtime.get(f"conversation_id:{channel}")
            else:
                conv_id = runtime.get("conversation_id")
            if conv_id is None:
                return "No active conversation."
        try:
            path = store.export(conv_id, channel)
        except ValueError as e:
            return str(e)
        return f"Exported to {path}"
    return _export


def mcp_cmd() -> CommandHandler:
    async def _mcp(args: str = "") -> str:
        from ..core.mcp_manager import mcp_manager

        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0].lower() if parts and parts[0] else ""
        subargs = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "tools":
            if subargs:
                specs = mcp_manager.get_tools_for_server(subargs)
                if not specs:
                    configured = [s["name"] for s in mcp_manager.get_server_status()]
                    if subargs not in configured:
                        return f"Server '{subargs}' not found. Configured: {', '.join(configured) or 'none'}"
                    return f"Server '{subargs}' has no tools."
                lines = [f"Tools for '{subargs}' ({len(specs)}):"]
                for spec in specs:
                    fn = spec["function"]
                    bare = fn["name"].partition("__")[2]
                    desc = fn.get("description", "")
                    lines.append(f"  {bare}" + (f" — {desc}" if desc else ""))
                return "\n".join(lines)
            else:
                specs = mcp_manager.get_tool_specs()
                if not specs:
                    return "No MCP tools available."
                lines = [f"MCP tools ({len(specs)}):"]
                for spec in specs:
                    fn = spec["function"]
                    desc = fn.get("description", "")
                    lines.append(f"  {fn['name']}" + (f" — {desc}" if desc else ""))
                return "\n".join(lines)
        else:
            statuses = mcp_manager.get_server_status()
            if not statuses:
                return "No MCP servers configured.\nCreate ~/.crafterscode/mcp_servers.json to add servers."
            lines = [f"MCP servers ({len(statuses)}):"]
            for s in statuses:
                if s["disabled"]:
                    status = "disabled"
                elif s["connected"]:
                    status = "connected"
                else:
                    status = "disconnected"
                lines.append(
                    f"  {s['name']} — {status}, {s['transport']} ({s['target']}), {s['tool_count']} tool(s)"
                )
            return "\n".join(lines)

    return _mcp
