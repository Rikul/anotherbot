"""Slash commands (``/model``, ``/list``, ``/load``, …) shared by all channels and the CLI.

Each ``*_cmd`` factory returns an async handler that takes the text after
the command name and returns the reply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Awaitable, Protocol, TYPE_CHECKING

from .. import config
from ..core import mcp_manager as mcp
from ..core import runtime

if TYPE_CHECKING:
    from ..infra.conversations import ConversationStore


class ConversationAgent(Protocol):  # pylint: disable=too-few-public-methods
    """Structural type for agents that support conversation management."""

    store: Any  # ConversationStore
    channel_str: str
    conversation_id: int

    def switch_conversation(self, conv: dict) -> None:
        """Make ``conv`` the active conversation and load its messages."""


log = logging.getLogger(__name__)

CommandHandler = Callable[[str], Awaitable[str]]

_STARTUP_TIME: datetime = datetime.now()


@dataclass
class BotCommand:
    """A slash command: its name (without ``/``), help text and handler."""

    name: str
    description: str
    handler: CommandHandler


class CommandRegistry:
    """Slash commands by name, with error handling around each handler."""

    def __init__(self) -> None:
        self._commands: dict[str, BotCommand] = {}

    def register(self, cmd: BotCommand) -> None:
        """Add a command, replacing any existing command with the same name."""
        self._commands[cmd.name] = cmd

    async def execute(self, name: str, args: str = "") -> str | None:
        """Run the named command with ``args``.

        Returns:
            The handler's reply, an error message if the handler raised, or
            ``None`` if no such command exists.
        """
        cmd = self._commands.get(name)
        if cmd is None:
            return None
        try:
            return await cmd.handler(args)
        except Exception:  # pylint: disable=broad-exception-caught  # reply instead of crashing the agent
            log.exception("Command /%s raised an exception", name)
            return "An error occurred running that command."

    def list(self) -> list[BotCommand]:
        """Return the registered commands in registration order."""
        return list(self._commands.values())


def help_cmd(registry: CommandRegistry) -> CommandHandler:
    """Build ``/help``, which lists every command in ``registry``."""

    async def _help(_args: str = "") -> str:
        lines = ["Available commands:"]
        for cmd in registry.list():
            lines.append(f"/{cmd.name} — {cmd.description}")
        return "\n".join(lines)

    return _help


async def model_cmd(args: str = "") -> str:
    """``/model [name]``: show the current model, or switch to ``name``."""
    if not args.strip():
        return f"Current model: {runtime.get('model', 'unknown')}"
    runtime.set("model", args.strip())
    return f"Model set to: {args.strip()}"


async def trace_cmd(args: str = "") -> str:
    """``/trace [on|off]``: turn LLM call tracing on or off, or show its state."""
    arg = args.strip().lower()
    tracedir = runtime.get("tracedir")
    if arg == "on":
        runtime.set("trace", True)
        return f"Tracing on. Writing to {tracedir}"
    if arg == "off":
        runtime.set("trace", False)
        return "Tracing off."
    state = runtime.get("trace", False)
    return f"Tracing is {'on' if state else 'off'}. Dir: {tracedir}"


def make_status_cmd(channel_str: str = "") -> CommandHandler:
    """Build ``/status``: model, uptime, active conversation and tracing state.

    Args:
        channel_str: channel whose active conversation to show; empty uses the
            CLI's un-namespaced runtime keys.
    """

    async def _status(_args: str = "") -> str:
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
        trace_line = (
            f"on ({last_trace})"
            if (tracing and last_trace)
            else ("on" if tracing else "off")
        )
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
    """Build ``/list [all]``: the channel's 10 most recent conversations, or all of them."""

    async def _list(args: str = "") -> str:
        convs = store.list(channel)
        if not convs:
            return "No conversations yet."

        lines = []

        if args.strip().lower() != "all":
            convs = convs[:10]

        for c in convs:
            lines.append(f"#{c['id']} {c['name']} — {c['message_count']} msgs")

        return "\n".join(lines)

    return _list


def new_conversation_cmd(agent: ConversationAgent) -> CommandHandler:
    """Build ``/new``: start a new conversation and switch to it."""

    async def _new(_args: str = "") -> str:
        cid = agent.store.create(agent.channel_str)
        conv = agent.store.get(cid)
        agent.switch_conversation(conv)
        return f"Started new conversation [{conv['id']}] {conv['name']}"

    return _new


def load_conversation_cmd(agent: ConversationAgent) -> CommandHandler:
    """Build ``/load <id>``: switch to any conversation, from any channel."""

    async def _load(args: str = "") -> str:
        if not args.strip():
            return "Usage: /load <id>"
        try:
            conv_id = int(args.strip())
        except ValueError:
            return "Invalid id: must be an integer."
        conv = agent.store.get(conv_id)
        if conv is None:
            return "Conversation not found"
        agent.switch_conversation(conv)
        convs = agent.store.list()
        msg_count = next((c["message_count"] for c in convs if c["id"] == conv_id), 0)
        return (
            f"Loaded conversation [{conv['id']}] {conv['name']} ({msg_count} messages)"
        )

    return _load


def fork_conversation_cmd(agent: ConversationAgent) -> CommandHandler:
    """Build ``/fork [id]``: copy a conversation (default: the active one), switch to it."""

    async def _fork(args: str = "") -> str:
        source_id = agent.conversation_id
        if args.strip():
            try:
                source_id = int(args.strip())
            except ValueError:
                return "Invalid id: must be an integer."
        try:
            new_id = agent.store.fork(source_id, agent.channel_str)
        except ValueError as e:
            return str(e)
        conv = agent.store.get(new_id)
        agent.switch_conversation(conv)
        return f"Forked into new conversation [{conv['id']}] {conv['name']}"

    return _fork


def rename_conversation_cmd(store: ConversationStore, channel: str) -> CommandHandler:
    """Build ``/rename <id> <name>``.

    If the renamed conversation is the active one, its cached runtime name is
    updated too.
    """

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

        return f'Conversation [{conv_id}] renamed to "{persisted_name}"'

    return _rename


def export_conversation_cmd(store: ConversationStore, channel: str) -> CommandHandler:
    """Build ``/export [id]``: write a conversation (default: the active one) to a JSON file."""

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


def build_command_registry(
    agent: ConversationAgent,
    *,
    status_channel: str = "",
    extra: tuple[BotCommand, ...] = (),
) -> CommandRegistry:
    """Build the standard slash-command registry for a conversation agent.

    Args:
        agent: the agent whose conversations /list, /new, /load, /fork,
            /rename and /export manage.
        status_channel: channel name whose conversation /status reports;
            empty means the CLI's un-namespaced runtime keys.
        extra: channel-specific commands, registered after /status.

    Returns:
        The registry; /help is registered last and lists everything.
    """
    store, channel = agent.store, agent.channel_str
    registry = CommandRegistry()
    for cmd in (
        BotCommand("model", "Get or set model. Usage: /model [name]", model_cmd),
        BotCommand("trace", "Toggle LLM tracing. Usage: /trace [on|off]", trace_cmd),
        BotCommand("status", "Show bot status.", make_status_cmd(status_channel)),
        *extra,
        BotCommand(
            "list",
            "List conversations. Usage: /list [all]",
            list_conversations_cmd(store, channel),
        ),
        BotCommand("new", "Start a new conversation.", new_conversation_cmd(agent)),
        BotCommand(
            "load",
            "Load a conversation. Usage: /load <id>",
            load_conversation_cmd(agent),
        ),
        BotCommand(
            "fork",
            "Fork a conversation. Usage: /fork [id]",
            fork_conversation_cmd(agent),
        ),
        BotCommand(
            "rename",
            "Rename a conversation. Usage: /rename <id> <name>",
            rename_conversation_cmd(store, channel),
        ),
        BotCommand(
            "export",
            "Export a conversation to JSON. Usage: /export [id]",
            export_conversation_cmd(store, channel),
        ),
        BotCommand(
            "mcp", "Show MCP server status. Usage: /mcp [tools [<server>]]", mcp_cmd()
        ),
    ):
        registry.register(cmd)
    registry.register(
        BotCommand("help", "Show available commands.", help_cmd(registry))
    )
    return registry


def _format_tool_line(name: str, description: str) -> str:
    return f"  {name}" + (f" — {description}" if description else "")


def _mcp_server_tools(server: str) -> str:
    """``/mcp tools <server>``: list one server's tools by bare name."""
    manager = mcp.mcp_manager
    specs = manager.get_tools_for_server(server)
    if not specs:
        configured = [s["name"] for s in manager.get_server_status()]
        if server not in configured:
            return f"Server '{server}' not found. Configured: {', '.join(configured) or 'none'}"
        return f"Server '{server}' has no tools."
    lines = [f"Tools for '{server}' ({len(specs)}):"]
    for spec in specs:
        fn = spec["function"]
        lines.append(
            _format_tool_line(fn["name"].partition("__")[2], fn.get("description", ""))
        )
    return "\n".join(lines)


def _mcp_all_tools() -> str:
    """``/mcp tools``: list every MCP tool by its namespaced name."""
    specs = mcp.mcp_manager.get_tool_specs()
    if not specs:
        return "No MCP tools available."
    lines = [f"MCP tools ({len(specs)}):"]
    for spec in specs:
        fn = spec["function"]
        lines.append(_format_tool_line(fn["name"], fn.get("description", "")))
    return "\n".join(lines)


def _mcp_server_status() -> str:
    """``/mcp``: one line per configured server with its connection state."""
    statuses = mcp.mcp_manager.get_server_status()
    if not statuses:
        servers_file = config.PROJECT_HOME / "mcp_servers.json"
        return f"No MCP servers configured.\nCreate {servers_file} to add servers."
    lines = [f"MCP servers ({len(statuses)}):"]
    for s in statuses:
        if s["disabled"]:
            status = "disabled"
        elif s["connected"]:
            status = "connected"
        else:
            status = "disconnected"
        lines.append(
            f"  {s['name']} — {status}, {s['transport']} ({s['target']}), "
            f"{s['tool_count']} tool(s)"
        )
    return "\n".join(lines)


def mcp_cmd() -> CommandHandler:
    """Build the ``/mcp [tools [<server>]]`` handler."""

    async def _mcp(args: str = "") -> str:
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0].lower() if parts and parts[0] else ""
        subargs = parts[1].strip() if len(parts) > 1 else ""
        if subcmd != "tools":
            return _mcp_server_status()
        if subargs:
            return _mcp_server_tools(subargs)
        return _mcp_all_tools()

    return _mcp
