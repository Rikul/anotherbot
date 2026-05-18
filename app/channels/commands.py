from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Awaitable

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

    async def execute(self, name: str, args : str = "") -> str | None:
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
    async def _help(args : str = "") -> str:
        lines = ["Available commands:"]
        for cmd in registry.list():
            lines.append(f"/{cmd.name} — {cmd.description}")
        return "\n".join(lines)
    return _help

async def model_cmd(args: str = "") -> str:
    from .. import config
    if not args.strip():
        return f"Current model: {config.get('model', 'unknown')}"
    
    config.set("model", args.strip())
    return f"Model set to: {args.strip()}"
    
async def status_cmd(args: str = "") -> str:
    from .. import config
    uptime = datetime.now() - _STARTUP_TIME
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    model = config.get("model", "unknown")
    return f"Bot status:\n  Model:  {model}\n  Uptime: {hours}h {minutes}m {seconds}s"
