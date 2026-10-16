"""Tests for ConversationStore."""
from __future__ import annotations

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


def test_get_last_returns_most_recently_updated(store, db):
    store.create("cli", "First")
    cid2 = store.create("cli", "Second")
    # Pin cid2 to a future timestamp so ordering is deterministic
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE conversations SET updated_at='2099-01-01T00:00:00' WHERE id=?", (cid2,)
        )
        conn.commit()
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


def test_list_without_channel_returns_all_with_channel_field(store):
    store.create("cli", "A")
    store.create("telegram", "T")
    convs = store.list()
    assert {(c["name"], c["channel"]) for c in convs} == {("A", "cli"), ("T", "telegram")}


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


def test_fork_rejects_missing_conversation(store):
    with pytest.raises(ValueError, match="not found"):
        store.fork(9999, "cli")


def test_fork_gets_a_distinct_numbered_name(store):
    cid = store.create("telegram", "Rome trip")
    first = store.fork(cid, "web")
    second = store.fork(cid, "web")
    assert store.get(cid)["name"] == "Rome trip"
    assert store.get(first)["name"] == "Rome trip (fork)"
    assert store.get(second)["name"] == "Rome trip (fork 2)"


def test_forking_a_fork_numbers_instead_of_stacking(store):
    cid = store.create("web", "Rome trip")
    fork1 = store.fork(cid, "web")
    fork2 = store.fork(fork1, "web")
    fork3 = store.fork(fork2, "web")
    assert store.get(fork2)["name"] == "Rome trip (fork 2)"
    assert store.get(fork3)["name"] == "Rome trip (fork 3)"


def test_fork_numbering_uses_highest_taken_and_exact_base(store):
    store.create("web", "Rome trip (fork 5)")
    store.create("web", "Rome trip extended (fork 9)")  # different base
    cid = store.create("web", "Rome trip")
    assert store.get(store.fork(cid, "web"))["name"] == "Rome trip (fork 6)"


def test_fork_of_default_name_stays_default_for_auto_naming(store):
    cid = store.create("web")
    assert store.get(store.fork(cid, "web"))["name"] == "New Conversation"


def test_fork_name_fits_80_chars(store):
    cid = store.create("web", "x" * 80)
    name = store.get(store.fork(cid, "web"))["name"]
    assert len(name) == 80 and name.endswith(" (fork)")


# --- load_messages ordering ---

def test_load_messages_ordered_by_timestamp_then_id(store, db):
    cid = store.create("cli")
    with sqlite3.connect(db) as conn:
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


def test_load_messages_returns_most_recent_when_truncated(store, db):
    """With limit=2, load_messages must return the 2 newest messages, not the 2 oldest."""
    cid = store.create("cli")
    with sqlite3.connect(db) as conn:
        for i in range(5):
            conn.execute(
                "INSERT INTO messages (channel, role, content, timestamp, conversation_id) VALUES (?,?,?,?,?)",
                ("cli", "user", f"msg{i}", f"2024-01-01T00:00:0{i}", cid),
            )
        conn.commit()
    msgs = store.load_messages(cid, limit=2)
    assert len(msgs) == 2
    # Should be the two most recent, in chronological order
    assert msgs[0]["content"] == "msg3"
    assert msgs[1]["content"] == "msg4"


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


def test_export_payload_builds_filename_and_messages_without_writing(store, db, tmp_path):
    from app.infra.message_history import MessageHistory
    cid = store.create("web", "Trip Plan: Rome!")
    MessageHistory("web", db_path=db).add_message("user", "hi", cid)
    from app.infra import conversations
    with patch.object(conversations, "PROJECT_HOME", tmp_path):
        filename, payload = store.export_payload(cid)
    assert filename == f"{cid}-trip-plan-rome.json"
    assert payload["id"] == cid and payload["name"] == "Trip Plan: Rome!"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert not (tmp_path / "conversations").exists()


def test_export_payload_rejects_missing_conversation(store):
    with pytest.raises(ValueError, match="not found"):
        store.export_payload(999)


# --- delete ---

def test_delete_removes_conversation_and_its_messages_only(store, db):
    from app.infra.message_history import MessageHistory
    keep = store.create("web", "Keep")
    gone = store.create("telegram", "Gone")
    history = MessageHistory("web", db_path=db)
    history.add_message("user", "keep me", keep)
    history.add_message("user", "delete me", gone)
    deleted = store.delete(gone)
    assert deleted["id"] == gone and deleted["name"] == "Gone"
    assert store.get(gone) is None
    assert store.load_messages(gone) == []
    assert store.load_messages(keep) == [{"role": "user", "content": "keep me"}]


def test_delete_source_of_a_fork_keeps_the_fork(store, db):
    from app.infra.message_history import MessageHistory
    src = store.create("web", "Source")
    MessageHistory("web", db_path=db).add_message("user", "hi", src)
    fork = store.fork(src, "web")
    store.delete(src)  # must not trip the parent_id foreign key
    assert store.get(fork)["parent_id"] is None
    assert store.load_messages(fork) == [{"role": "user", "content": "hi"}]


def test_delete_rejects_missing_conversation(store):
    with pytest.raises(ValueError, match="not found"):
        store.delete(999)
