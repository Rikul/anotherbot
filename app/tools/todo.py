"""In-memory todo list tools, one list per channel (see ``init_task_todos``)."""

from contextvars import ContextVar
from dataclasses import dataclass, field

from ..core.tool import Tool
from ..infra.app_logging import log


_VALID_STATUSES = {"todo", "in_progress", "done"}


@dataclass
class TodoList:
    """One todo list: tasks by ID (as strings) and the next ID to hand out."""

    tasks: dict[str, dict] = field(default_factory=dict)
    next_id: int = 1


# Each channel's BackgroundAgent.process_incoming() runs in its own asyncio task
# and calls init_task_todos() so channels don't share todos. Tool calls run in
# child tasks (asyncio.gather) that copy the context, so they see the same list.
# Without a scope (CLI, tests) the module-level default list is used.
_default = TodoList()
_current: ContextVar[TodoList] = ContextVar("todo_list")


def init_task_todos() -> TodoList:
    """Bind a fresh todo list to the current asyncio task's context."""
    todos = TodoList()
    _current.set(todos)
    return todos


def current_todos() -> TodoList:
    """Return the todo list bound to the current task, or the shared default list."""
    return _current.get(_default)


class TodoAddTool(Tool):
    """Add a task to the current todo list."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "todo_add",
                "description": "Add a new task to the todo list",
                "parameters": {
                    "type": "object",
                    "required": ["title"],
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short title for the task",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional longer description of the task",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(title: str, description: str = "") -> str:
        """Add a task with status ``todo`` and return its ID."""
        log.info("todo_add, title: %s", title)

        todos = current_todos()
        task_id = str(todos.next_id)
        todos.next_id += 1
        todos.tasks[task_id] = {
            "title": title,
            "description": description,
            "status": "todo",
        }
        return f"Task {task_id} added: {title}"


class TodoListTool(Tool):
    """List the current todo list's tasks with their statuses."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "todo_list",
                "description": "List all tasks and their statuses",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def call() -> str:
        """Return one line per task (``[id] [status] title — description``)."""
        log.info("todo_list")

        tasks = current_todos().tasks
        if not tasks:
            return "No tasks."

        lines = []
        for task_id, task in tasks.items():
            line = f"[{task_id}] [{task['status']}] {task['title']}"
            if task["description"]:
                line += f" — {task['description']}"
            lines.append(line)
        return "\n".join(lines)


class TodoClearTool(Tool):
    """Remove every task from the current todo list and reset the IDs."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "todo_clear",
                "description": "Todos are done, clear all todos",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def call() -> str:
        """Clear the list and return how many tasks were removed."""
        log.info("todo_clear")
        todos = current_todos()
        count = len(todos.tasks)
        todos.tasks.clear()
        todos.next_id = 1
        return f"Cleared {count} task(s)."


class TodoUpdateTool(Tool):
    """Set a task's status to ``todo``, ``in_progress`` or ``done``."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "todo_update",
                "description": "Update the status of a task",
                "parameters": {
                    "type": "object",
                    "required": ["task_id", "status"],
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "The ID of the task to update",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["todo", "in_progress", "done"],
                            "description": "New status for the task",
                        },
                    },
                },
            },
        }

    @staticmethod
    def call(task_id: str, status: str) -> str:
        """Set the task's status and return a confirmation, or an error message."""
        log.info("todo_update, task_id: %s, status: %s", task_id, status)

        tasks = current_todos().tasks
        if task_id not in tasks:
            log.error("TodoUpdateTool: task %s not found", task_id)
            return f"Error: task {task_id} not found"

        if status not in _VALID_STATUSES:
            log.error("TodoUpdateTool: invalid status '%s'", status)
            valid = ", ".join(sorted(_VALID_STATUSES))
            return f"Error: invalid status '{status}'. Must be one of: {valid}"

        tasks[task_id]["status"] = status
        return f"Task {task_id} updated to '{status}'"
