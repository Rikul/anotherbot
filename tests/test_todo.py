import pytest
import app.tools.todo as todo_module
from app.tools.todo import TodoAddTool, TodoListTool, TodoUpdateTool, TodoClearTool
todo_add = TodoAddTool.call
todo_list = TodoListTool.call
todo_update = TodoUpdateTool.call
todo_clear = TodoClearTool.call


@pytest.fixture(autouse=True)
def reset_tasks():
    """Reset the default (unscoped) todo list between tests."""
    todo_module._default.tasks.clear()
    todo_module._default.next_id = 1
    yield
    todo_module._default.tasks.clear()
    todo_module._default.next_id = 1


def test_todo_add_returns_task_id():
    result = todo_add("Write tests")
    assert "1" in result


def test_todo_add_increments_id():
    todo_add("First")
    result = todo_add("Second")
    assert "2" in result


def test_todo_add_stores_task():
    todo_add("My task")
    assert "1" in todo_module._default.tasks
    assert todo_module._default.tasks["1"]["title"] == "My task"
    assert todo_module._default.tasks["1"]["status"] == "todo"


def test_todo_add_with_description():
    todo_add("My task", description="Some details")
    assert todo_module._default.tasks["1"]["description"] == "Some details"


def test_todo_list_empty():
    assert todo_list() == "No tasks."


def test_todo_list_shows_tasks():
    todo_add("Task one")
    todo_add("Task two")
    result = todo_list()
    assert "Task one" in result
    assert "Task two" in result


def test_todo_list_shows_status():
    todo_add("A task")
    result = todo_list()
    assert "todo" in result


def test_todo_list_shows_description_when_present():
    todo_add("A task", description="Details here")
    result = todo_list()
    assert "Details here" in result


def test_todo_update_changes_status():
    todo_add("A task")
    result = todo_update("1", "in_progress")
    assert "in_progress" in result
    assert todo_module._default.tasks["1"]["status"] == "in_progress"


def test_todo_update_to_done():
    todo_add("A task")
    todo_update("1", "done")
    assert todo_module._default.tasks["1"]["status"] == "done"


def test_todo_update_unknown_id():
    result = todo_update("99", "done")
    assert "Error" in result
    assert "99" in result


def test_todo_update_invalid_status():
    todo_add("A task")
    result = todo_update("1", "invalid_status")
    assert "Error" in result
    assert "invalid_status" in result


def test_todo_clear_removes_all_tasks():
    todo_add("Task A")
    todo_add("Task B")
    todo_clear()
    assert todo_module._default.tasks == {}
    assert todo_module._default.next_id == 1


def test_todo_clear_empty_list():
    result = todo_clear()
    assert "0" in result


def test_todo_clear_resets_id_counter():
    todo_add("Task A")
    todo_clear()
    todo_add("New task")
    assert "1" in todo_module._default.tasks


def test_todo_multiple_tasks_independent():
    todo_add("Task A")
    todo_add("Task B")
    todo_update("1", "done")
    assert todo_module._default.tasks["1"]["status"] == "done"
    assert todo_module._default.tasks["2"]["status"] == "todo"


@pytest.mark.asyncio
async def test_todo_scopes_isolated_per_task():
    import asyncio

    async def channel(title):
        todo_module.init_task_todos()
        todo_add(title)
        await asyncio.sleep(0)  # let the other channel run in between
        return todo_list()

    a, b = await asyncio.gather(channel("from A"), channel("from B"))
    assert "from A" in a and "from B" not in a
    assert "from B" in b and "from A" not in b
    assert todo_module._default.tasks == {}


@pytest.mark.asyncio
async def test_todo_child_tasks_share_parent_scope():
    import asyncio

    async def add(title):
        return todo_add(title)

    todos = None

    async def channel():
        nonlocal todos
        todos = todo_module.init_task_todos()
        await asyncio.gather(add("one"), add("two"))

    await asyncio.create_task(channel())
    assert {t["title"] for t in todos.tasks.values()} == {"one", "two"}
