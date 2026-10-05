"""Tools for managing scheduled tasks (the ``ScheduledTasks`` store)."""

from ..core.scheduled_tasks import ScheduledTasks
from ..core.tool import Tool
from ..infra.app_logging import log


class ListScheduledTasks(Tool):
    """List every scheduled task and its settings."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "list_scheduled_tasks",
                "description": "List all background scheduled tasks",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def call() -> str:
        """Return every task (as dicts), or a message if there are none."""
        log.info("list_scheduled_tasks called")

        tasks = ScheduledTasks().load_tasks()
        if not tasks:
            return "No scheduled tasks found."

        return tasks


class AddScheduledTask(Tool):
    """Create a scheduled task (one-shot or repeating)."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "add_scheduled_task",
                "description": (
                    "Add a new background scheduled task that will be executed at intervals by "
                    "the background agent with your prompt"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "A name for the scheduled task",
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "The prompt that will be executed at intervals by the background "
                                "agent"
                            ),
                        },
                        "interval_minutes": {
                            "type": "integer",
                            "description": (
                                "The interval in minutes between executions (only used when "
                                "repeat=true)"
                            ),
                        },
                        "repeat": {
                            "type": "boolean",
                            "description": (
                                "Whether to repeat the task at the given interval. False means "
                                "run once. Defaults to false."
                            ),
                        },
                        "next_run": {
                            "type": "string",
                            "description": (
                                "ISO 8601 datetime for the first run (e.g. '2026-05-03T09:00:00') "
                                "in local time."
                            ),
                        },
                        "delivery_channel": {
                            "type": "string",
                            "description": (
                                "Channel to deliver the task output to. Defaults to 'telegram'."
                            ),
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "Whether the task is enabled. Defaults to true.",
                        },
                    },
                    "required": ["name", "prompt", "next_run", "interval_minutes"],
                },
            },
        }

    @staticmethod
    def call(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # one per spec field
        name: str,
        prompt: str,
        interval_minutes: int,
        next_run: str,
        repeat: bool = False,
        delivery_channel: str = "telegram",
        enabled: bool = True,
    ) -> str:
        """Add a task and return a confirmation.

        Raises:
            ValueError: if a task with this name already exists.
        """
        log.info("add_scheduled_task called")

        ScheduledTasks().add_task(
            name=name,
            prompt=prompt,
            interval_mins=interval_minutes,
            repeat=int(repeat),
            next_run=next_run,
            delivery_channel=delivery_channel,
            enabled=int(enabled),
        )

        suffix = f" every {interval_minutes} minutes" if repeat else " (runs once)"
        return f"Added scheduled task '{name}'{suffix}"


class RemoveScheduledTask(Tool):
    """Delete a scheduled task by name."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "remove_scheduled_task",
                "description": (
                    "Remove a background scheduled task so that it will no longer be executed by "
                    "the background agent"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name of the scheduled task to remove",
                        }
                    },
                    "required": ["name"],
                },
            },
        }

    @staticmethod
    def call(name: str) -> str:
        """Delete the task and return a confirmation."""
        log.info("remove_scheduled_task called")

        ScheduledTasks().remove_task(name)

        return f"Removed scheduled task '{name}'"


class UpdateScheduledTask(Tool):
    """Change some fields of a scheduled task; omitted fields stay as they are."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "update_scheduled_task",
                "description": (
                    "Update an existing background scheduled task. Only the fields provided in "
                    "the parameters will be updated."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name of the scheduled task to update",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "The new prompt for the scheduled task",
                        },
                        "interval_minutes": {
                            "type": "integer",
                            "description": (
                                "The new interval in minutes between executions (only used when "
                                "repeat=true)"
                            ),
                        },
                        "repeat": {
                            "type": "boolean",
                            "description": (
                                "Whether to repeat the task at the given interval. False means "
                                "run once."
                            ),
                        },
                        "next_run": {
                            "type": "string",
                            "description": (
                                "ISO 8601 datetime for the next run (e.g. '2026-05-03T09:00:00') "
                                "in local time."
                            ),
                        },
                        "delivery_channel": {
                            "type": "string",
                            "description": "Channel to deliver the task output to.",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "Enable or disable the task.",
                        },
                    },
                    "required": ["name"],
                },
            },
        }

    @staticmethod
    def call(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # one per spec field
        name: str,
        prompt: str = None,
        interval_minutes: int = None,
        next_run: str = None,
        repeat: bool = None,
        delivery_channel: str = None,
        enabled: bool = None,
    ) -> str:
        """Update the fields that were given and return which ones changed."""
        log.info("update_scheduled_task called")

        tasks = ScheduledTasks().load_tasks()
        task = next((t for t in tasks if t["name"] == name), None)
        if not task:
            return f"Scheduled task '{name}' not found"

        updated_fields = {}
        if prompt is not None:
            updated_fields["prompt"] = prompt
        if interval_minutes is not None:
            updated_fields["interval_mins"] = interval_minutes
        if next_run is not None:
            updated_fields["next_run"] = next_run
        if repeat is not None:
            updated_fields["repeat"] = int(repeat)
        if delivery_channel is not None:
            updated_fields["delivery_channel"] = delivery_channel
        if enabled is not None:
            updated_fields["enabled"] = int(enabled)

        if not updated_fields:
            return f"No fields to update for task '{name}'"

        ScheduledTasks().update_task(name, **updated_fields)
        changed = ", ".join(updated_fields.keys())
        return f"Updated scheduled task '{name}': {changed}"


class GetScheduledTaskOutput(Tool):
    """Return a scheduled task's most recent outputs."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "get_scheduled_task_output",
                "description": (
                    "Get the output from a scheduled task that is executed by the "
                    "background agent. This can be used to check the results of the "
                    "scheduled task prompts"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name of the scheduled task to get output for",
                        },
                        "num_entries": {
                            "type": "integer",
                            "description": (
                                "The number of recent output entries to return for the specified "
                                "scheduled task (default: 5)"
                            ),
                        },
                    },
                    "required": ["name"],
                },
            },
        }

    @staticmethod
    def call(name: str, num_entries: int = 5) -> str:
        """Return the task's last ``num_entries`` outputs, or a message if there are none."""
        log.info("get_scheduled_task_output called")

        tasks = ScheduledTasks()
        output = tasks.get_output(name=name, num_entries=num_entries)

        return output if output else f"No output found for scheduled task '{name}'"
