from contextvars import ContextVar

from ..infra.app_logging import log
from ..core.tool import Tool

_VALID_STATUSES = {"todo", "in_progress", "done"}


class TodoList:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.next_id = 1


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
    return _current.get(_default)

class TodoAddTool(Tool):
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
                            "description": "Short title for the task"
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional longer description of the task"
                        }
                    }
                }
            }
        }

    @staticmethod
    def call(title: str, description: str = "") -> str:
        log.info(f"todo_add, title: {title}")

        todos = current_todos()
        task_id = str(todos.next_id)
        todos.next_id += 1
        todos.tasks[task_id] = {"title": title, "description": description, "status": "todo"}
        return f"Task {task_id} added: {title}"


class TodoListTool(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "todo_list",
                "description": "List all tasks and their statuses",
                "parameters": {"type": "object", "properties": {}}
            }
        }

    @staticmethod
    def call() -> str:
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
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "todo_clear",
                "description": "Todos are done, clear all todos",
                "parameters": {"type": "object", "properties": {}}
            }
        }

    @staticmethod
    def call() -> str:
        log.info("todo_clear")
        todos = current_todos()
        count = len(todos.tasks)
        todos.tasks.clear()
        todos.next_id = 1
        return f"Cleared {count} task(s)."


class TodoUpdateTool(Tool):
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
                            "description": "The ID of the task to update"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["todo", "in_progress", "done"],
                            "description": "New status for the task"
                        }
                    }
                }
            }
        }

    @staticmethod
    def call(task_id: str, status: str) -> str:
        log.info(f"todo_update, task_id: {task_id}, status: {status}")

        tasks = current_todos().tasks
        if task_id not in tasks:
            log.error(f"TodoUpdateTool: task {task_id} not found")
            return f"Error: task {task_id} not found"

        if status not in _VALID_STATUSES:
            log.error(f"TodoUpdateTool: invalid status '{status}'")
            return f"Error: invalid status '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}"

        tasks[task_id]["status"] = status
        return f"Task {task_id} updated to '{status}'"
