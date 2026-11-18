"""``self_update`` tool: check for or pull the latest code for this checkout."""

import asyncio
from functools import partial

from ..core.tool import Tool
from ..infra import self_update
from ..infra.app_logging import log


class SelfUpdateTool(Tool):
    """Check or pull the bot's own git checkout, optionally restarting to load it."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "self_update",
                "description": (
                    "Update this bot's own git checkout from its remote. "
                    "action='check' only reports what is available and changes nothing; "
                    "action='update' fetches, pulls, and (unless restart=false) restarts "
                    "the bot so the new code is loaded. Refuses a dirty working tree "
                    "unless stash or allow_dirty is set, and refuses a diverged branch "
                    "unless strategy is rebase or merge. Creates a backup branch before "
                    "moving HEAD."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["check", "update"],
                            "description": (
                                "check: report available commits only. "
                                "update: pull them. Default: check."
                            ),
                        },
                        "strategy": {
                            "type": "string",
                            "enum": list(self_update.STRATEGIES),
                            "description": (
                                "How to move onto the upstream. ff-only (default) refuses "
                                "a diverged branch; rebase replays local commits on top; "
                                "merge creates a merge commit."
                            ),
                        },
                        "stash": {
                            "type": "boolean",
                            "description": (
                                "Stash local changes (including untracked) before pulling. "
                                "Default: false."
                            ),
                        },
                        "allow_dirty": {
                            "type": "boolean",
                            "description": (
                                "Pull with uncommitted changes present instead of refusing. "
                                "Default: false."
                            ),
                        },
                        "backup": {
                            "type": "boolean",
                            "description": (
                                "Create a backup-pre-update-<timestamp> branch at the old "
                                "HEAD. Default: true."
                            ),
                        },
                        "restart": {
                            "type": "boolean",
                            "description": (
                                "Restart the bot after a successful pull so the new code "
                                "loads. Default: true."
                            ),
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    @staticmethod
    async def call(  # pylint: disable=too-many-arguments  # one per spec property
        action: str = "check",
        strategy: str = "ff-only",
        stash: bool = False,
        allow_dirty: bool = False,
        backup: bool = True,
        restart: bool = True,
    ) -> str:
        """Run the requested action and return a formatted report.

        git runs in a worker thread: fetching and merging are I/O that can take
        seconds, and blocking the event loop would stall every channel.
        """
        log.info("self_update, action: %s strategy: %s", action, strategy)

        if action == "check":
            report = await asyncio.to_thread(self_update.check)
        elif action == "update":
            report = await asyncio.to_thread(
                partial(
                    self_update.update,
                    strategy=strategy,
                    stash=stash,
                    allow_dirty=allow_dirty,
                    backup=backup,
                    restart_after=restart,
                )
            )
        else:
            return f"Unknown action {action!r}; expected 'check' or 'update'."

        return self_update.format_report(report)
