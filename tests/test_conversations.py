"""Tests for ConversationStore."""
from __future__ import annotations

import asyncio
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from app.infra.conversations import ConversationStore


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def store(db) -> ConversationStore:
    return ConversationStore(db_path=db)


# --- Schema idempotency ---

def test_ensure_schema_idempotent(db):
    ConversationStore(db_path=db)
    # Second call must not raise
    ConversationStore(db_path=db)


def test_conversations_table_created(store, db):
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "conversations" in tables


# --- create / get ---

def test_create_returns_integer_id(store):
    cid = store.create("cli")
    assert isinstance(cid, int)
    assert cid > 0


def test_get_returns_correct_fields(store):
    cid = store.create("cli", "My Conv")
    conv = store.get(cid)
    assert conv["id"] == cid
    assert conv["name"] == "My Conv"
    assert conv["channel"] == "cli"
    assert conv["parent_id"] is None


def test_get_returns_none_for_missing(store):
    assert store.get(9999) is None


def test_create_trims_and_caps_name(store):
    long_name = "x" * 100
    cid = store.create("cli", "  " + long_name + "  ")
    conv = store.get(cid)
    assert len(conv["name"]) == 80
    assert not conv["name"].startswith(" ")


def test_create_falls_back_for_empty_name(store):
    cid = store.create("cli", "   ")
    conv = store.get(cid)
    assert conv["name"] == "New Conversation"


# --- get_last ---

def test_get_last_returns_none_when_empty(store):
    assert store.get_last("cli") is None


def test_get_last_returns_most_recently_updated(store):
    cid1 = store.create("cli", "First")
    import time; time.sleep(0.01)
    cid2 = store.create("cli", "Second")
    store.touch(cid2)
    last = store.get_last("cli")
    assert last["id"] == cid2


def test_get_last_respects_channel(store):
    cid = store.create("cli", "CLI conv")
    store.create("telegram", "TG conv")
    last = store.get_last("cli")
    assert last["id"] == cid


# --- list ---

def test_list_returns_all_for_channel(store):
    store.create("cli", "A")
    store.create("cli", "B")
    store.create("telegram", "T")
    convs = store.list("cli")
    assert len(convs) == 2
    names = {c["name"] for c in convs}
    assert names == {"A", "B"}


def test_list_includes_message_count(store, db):
    cid = store.create("cli", "With messages")
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT, role TEXT, content TEXT,
                timestamp TEXT, est_tokens INTEGER, conversation_id INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO messages (channel, role, content, timestamp, conversation_id) VALUES (?,?,?,?,?)",
            ("cli", "user", "hello", "2024-01-01T00:00:00", cid),
        )
        conn.commit()
    convs = store.list("cli")
    assert convs[0]["message_count"] == 1


# --- rename ---

def test_rename_updates_name(store):
    cid = store.create("cli", "Old Name")
    store.rename(cid, "New Name", "cli")
    assert store.get(cid)["name"] == "New Name"


def test_rename_rejects_cross_channel(store):
    cid = store.create("cli", "My Conv")
    with pytest.raises(ValueError, match="does not belong"):
        store.rename(cid, "Other", "telegram")


def test_rename_rejects_missing_conversation(store):
    with pytest.raises(ValueError, match="not found"):
        store.rename(9999, "Name", "cli")


# --- fork ---

def _setup_messages(db: Path, channel: str, conv_id: int, count: int = 2):
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT, role TEXT, content TEXT,
                timestamp TEXT, est_tokens INTEGER, conversation_id INTEGER
            )
        """)
        for i in range(count):
            conn.execute(
                "INSERT INTO messages (channel, role, content, timestamp, est_tokens, conversation_id) "
                "VALUES (?,?,?,?,?,?)",
                (channel, "user", f"msg{i}", f"2024-01-01T00:00:0{i}", 1, conv_id),
            )
        conn.commit()


def test_fork_creates_new_conversation(store, db):
    cid = store.create("cli", "Source")
    _setup_messages(db, "cli", cid, 2)
    new_id = store.fork(cid, "cli")
    assert new_id != cid
    assert store.get(new_id)["parent_id"] == cid


def test_fork_copies_messages_with_new_channel(store, db):
    cid = store.create("cli", "Source")
    _setup_messages(db, "cli", cid, 2)
    new_id = store.fork(cid, "cli")
    msgs = store.load_messages(new_id)
    assert len(msgs) == 2
    # Verify messages are stored with the new channel
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT channel FROM messages WHERE conversation_id=?", (new_id,)
        ).fetchall()
    assert all(r[0] == "cli" for r in rows)


def test_fork_rejects_cross_channel(store):
    cid = store.create("cli")
    with pytest.raises(ValueError, match="does not belong"):
        store.fork(cid, "telegram")


def test_fork_rejects_missing_conversation(store):
    with pytest.raises(ValueError, match="not found"):
        store.fork(9999, "cli")


# --- load_messages ordering ---

def test_load_messages_ordered_by_timestamp_then_id(store, db):
    cid = store.create("cli")
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT, role TEXT, content TEXT,
                timestamp TEXT, est_tokens INTEGER, conversation_id INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO messages (channel, role, content, timestamp, conversation_id) VALUES (?,?,?,?,?)",
            ("cli", "user", "second", "2024-01-01T00:00:02", cid),
        )
        conn.execute(
            "INSERT INTO messages (channel, role, content, timestamp, conversation_id) VALUES (?,?,?,?,?)",
            ("cli", "user", "first", "2024-01-01T00:00:01", cid),
        )
        conn.commit()
    msgs = store.load_messages(cid)
    assert msgs[0]["content"] == "first"
    assert msgs[1]["content"] == "second"


# --- count_user_messages ---

def test_count_user_messages(store, db):
    cid = store.create("cli")
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT, role TEXT, content TEXT,
                timestamp TEXT, est_tokens INTEGER, conversation_id INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO messages (channel, role, content, timestamp, conversation_id) VALUES (?,?,?,?,?)",
            ("cli", "user", "hello", "2024-01-01T00:00:00", cid),
        )
        conn.execute(
            "INSERT INTO messages (channel, role, content, timestamp, conversation_id) VALUES (?,?,?,?,?)",
            ("cli", "assistant", "hi", "2024-01-01T00:00:01", cid),
        )
        conn.commit()
    assert store.count_user_messages(cid) == 1


# --- Migration ---

def test_migration_assigns_existing_messages(db):
    # Seed the db with messages table and no conversation_id column
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT, role TEXT, content TEXT,
                timestamp TEXT, est_tokens INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO messages (channel, role, content, timestamp) VALUES (?,?,?,?)",
            ("cli", "user", "old message", "2024-01-01T00:00:00"),
        )
        conn.commit()

    store = ConversationStore(db_path=db)

    # Migration should have created a "History" conversation and linked messages
    convs = store.list("cli")
    assert len(convs) == 1
    assert convs[0]["name"] == "History"
    msgs = store.load_messages(convs[0]["id"])
    assert len(msgs) == 1
    assert msgs[0]["content"] == "old message"


def test_migration_is_reentrant(db):
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT, role TEXT, content TEXT,
                timestamp TEXT, est_tokens INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO messages (channel, role, content, timestamp) VALUES (?,?,?,?)",
            ("cli", "user", "hello", "2024-01-01T00:00:00"),
        )
        conn.commit()

    ConversationStore(db_path=db)
    # Second init: migration WHERE conversation_id IS NULL finds nothing → no duplicate
    ConversationStore(db_path=db)

    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    assert count == 1


# --- Auto-name guard ---

@pytest.mark.asyncio
async def test_auto_name_does_not_overwrite_manual_rename(store):
    cid = store.create("cli", "New Conversation")

    # Simulate manual rename before auto-name fires
    store.rename(cid, "My Manual Name", "cli")

    mock_helper_instance = AsyncMock()
    mock_helper_instance.run = AsyncMock(return_value="Auto Title")

    with patch("app.core.helper_agent.HelperAgent", return_value=mock_helper_instance):
        from app.core.agent import Agent
        agent_stub = MagicMock(spec=[])  # _auto_name doesn't use self
        from app.core import runtime
        await Agent._auto_name(agent_stub, store, cid, [], "conversation_name")

    # Name should still be the manually set name
    assert store.get(cid)["name"] == "My Manual Name"


# --- Export ---

def test_export_writes_json(store, db, tmp_path):
    cid = store.create("cli", "Test Export")
    from app import config
    with patch.object(config, "PROJECT_HOME", tmp_path):
        from app.infra import conversations
        with patch.object(conversations, "PROJECT_HOME", tmp_path):
            path = store.export(cid, "cli")
    assert path.exists()
    import json
    data = json.loads(path.read_text())
    assert data["name"] == "Test Export"
    assert data["channel"] == "cli"


def test_export_rejects_cross_channel(store):
    cid = store.create("cli")
    with pytest.raises(ValueError, match="does not belong"):
        store.export(cid, "telegram")
