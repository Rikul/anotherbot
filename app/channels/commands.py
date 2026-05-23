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
        return (
            f"Bot status:\n"
            f"  Model:        {model}\n"
            f"  Uptime:       {hours}h {minutes}m {seconds}s\n"
            f"  Conversation: [{conv_id}] {conv_name}"
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
            return "Usage: /load-conversation <id>"
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
            return "Usage: /rename-conversation <id> <new name>"
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
        return f"Conversation [{conv_id}] renamed to \"{new_name[:80]}\""
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
