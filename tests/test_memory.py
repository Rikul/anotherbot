import sqlite3
from unittest.mock import patch

import pytest

from app.core import agent as agent_module
from app.core.agent import get_default_sys_prompt
from app.core.tool_calls import helper_tool_specs, run_tool, tool_registry
from app.infra.memory_store import MAX_MEMORY_LENGTH, MemoryStore
from app.tools.memory import DeleteMemoryTool, SaveMemoryTool, SearchMemoryTool


@pytest.fixture
def store():
    return MemoryStore()


# --- store ---


def test_save_creates_memory(store):
    m = store.save("User prefers metric units", "Preference")
    assert m["status"] == "created"
    assert m["category"] == "preference"
    assert store.get(m["id"])["content"] == "User prefers metric units"


def test_save_strips_and_rejects_empty(store):
    with pytest.raises(ValueError):
        store.save("   ")


def test_save_rejects_too_long(store):
    with pytest.raises(ValueError):
        store.save("x" * (MAX_MEMORY_LENGTH + 1))


def test_save_duplicate_is_not_stored_twice(store):
    first = store.save("User is vegetarian")
    second = store.save("user is vegetarian")
    assert second["status"] == "duplicate"
    assert second["id"] == first["id"]
    assert store.count() == 1


def test_save_with_id_updates(store):
    m = store.save("User lives in Boston")
    updated = store.save("User lives in Denver", "personal", memory_id=m["id"])
    assert updated["status"] == "updated"
    assert store.count() == 1
    assert store.search("Denver")[0]["id"] == m["id"]
    assert store.search("Boston") == []


def test_save_with_unknown_id_raises(store):
    with pytest.raises(ValueError):
        store.save("anything", memory_id=999)
    assert store.count() == 0


def test_search_ranks_better_matches_first(store):
    store.save("User likes Python")
    best = store.save("User writes Python on a Linux laptop")
    store.save("User has a dog")
    results = store.search("python linux")
    assert results[0]["id"] == best["id"]
    assert len(results) == 2


def test_search_matches_category(store):
    m = store.save("Uses tabs", "preference")
    assert store.search("preference")[0]["id"] == m["id"]


@pytest.mark.parametrize("query", ['"', "c++ OR*", "-dog", "NOT AND (", "col:umn"])
def test_search_handles_fts_syntax(store, query):
    store.save("User has a dog")
    store.search(query)  # must not raise


def test_search_empty_query_returns_recent(store):
    store.save("one")
    store.save("two")
    assert [m["content"] for m in store.search("", limit=5)] == ["two", "one"]


def test_delete(store):
    m = store.save("User has a cat")
    assert store.delete(m["id"])["content"] == "User has a cat"
    assert store.delete(m["id"]) is None
    assert store.search("cat") == []


# --- tools ---


def test_tools_registered():
    for name in ("save_memory", "search_memory", "delete_memory"):
        assert name in tool_registry
    helper_names = {s["function"]["name"] for s in helper_tool_specs}
    assert "search_memory" in helper_names
    assert "save_memory" not in helper_names
    assert "delete_memory" not in helper_names


def test_tool_round_trip():
    out = SaveMemoryTool.call("User's birthday is 1990-04-12", "personal")
    assert out.startswith("Memory created: [1] (personal)")
    assert "Already remembered" in SaveMemoryTool.call("User's birthday is 1990-04-12")
    assert "birthday" in SearchMemoryTool.call("birthday")
    assert SearchMemoryTool.call("spaceship") == "No matching memories."
    assert "Deleted memory" in DeleteMemoryTool.call(1)
    assert "No memory with id 1" in DeleteMemoryTool.call(1)


def test_tool_save_error_is_returned():
    assert SaveMemoryTool.call("").startswith("Memory not saved:")


@pytest.mark.asyncio
async def test_run_tool_dispatches_memory_tools():
    await run_tool("save_memory", {"content": "User speaks French"})
    assert "French" in await run_tool("search_memory", {"query": "french"})


# --- system prompt ---


def test_sys_prompt_without_memories_has_no_section():
    assert "## Saved Memories" not in get_default_sys_prompt()


def test_sys_prompt_includes_memories():
    MemoryStore().save("User prefers short answers", "preference")
    prompt = get_default_sys_prompt()
    assert "## Saved Memories" in prompt
    assert "[1] (preference) User prefers short answers" in prompt


def test_sys_prompt_caps_memories(monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_PROMPT_MEMORIES", 2)
    store = MemoryStore()
    for i in range(5):
        store.save(f"fact {i}")
    prompt = get_default_sys_prompt()
    assert "fact 4" in prompt and "fact 3" in prompt
    assert "fact 0" not in prompt
    assert "3 older memories not shown" in prompt


def test_sys_prompt_survives_db_error():
    with patch.object(
        MemoryStore, "recent", side_effect=sqlite3.OperationalError("boom")
    ):
        prompt = get_default_sys_prompt()
    assert "## Saved Memories" not in prompt
    assert "## Current System Context" in prompt
