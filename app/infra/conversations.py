from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import APP_DB, PROJECT_HOME
from .app_logging import log


def _clean_name(name: str, fallback: str = "New Conversation") -> str:
    n = name.strip()[:80]
    return n or fallback


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))[:40]


class ConversationStore:
    def __init__(self, db_path: Path = APP_DB):
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        now = datetime.now().isoformat()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        name       TEXT    NOT NULL DEFAULT 'New Conversation',
                        channel    TEXT    NOT NULL,
                        parent_id  INTEGER REFERENCES conversations(id),
                        created_at TEXT    NOT NULL,
                        updated_at TEXT    NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversations_channel ON conversations(channel)
                """)

                # Ensure messages table exists (MessageHistory may not have run yet)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel        TEXT    NOT NULL,
                        role           TEXT    NOT NULL,
                        content        TEXT    NOT NULL,
                        timestamp      TEXT    NOT NULL,
                        est_tokens     INTEGER,
                        conversation_id INTEGER REFERENCES conversations(id)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel)
                """)

                # Add conversation_id to messages only if that table exists
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if "messages" in tables:
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
                    if "conversation_id" not in cols:
                        conn.execute(
                            "ALTER TABLE messages ADD COLUMN "
                            "conversation_id INTEGER REFERENCES conversations(id)"
                        )

                    # Migration: re-entrant (WHERE conversation_id IS NULL is the guard)
                    rows = conn.execute(
                        "SELECT DISTINCT channel FROM messages WHERE conversation_id IS NULL"
                    ).fetchall()
                    for (channel,) in rows:
                        oldest = conn.execute(
                            "SELECT MIN(timestamp) FROM messages "
                            "WHERE channel=? AND conversation_id IS NULL",
                            (channel,),
                        ).fetchone()[0] or now
                        cid = conn.execute(
                            "INSERT INTO conversations (name, channel, created_at, updated_at) "
                            "VALUES (?,?,?,?)",
                            ("History", channel, oldest, oldest),
                        ).lastrowid
                        conn.execute(
                            "UPDATE messages SET conversation_id=? "
                            "WHERE channel=? AND conversation_id IS NULL",
                            (cid, channel),
                        )

                conn.commit()
        except sqlite3.Error as e:
            log.error(f"ConversationStore schema error: {e}")
            raise

    def create(self, channel: str, name: str = "New Conversation", parent_id: int = None) -> int:
        clean = _clean_name(name)
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cid = conn.execute(
                "INSERT INTO conversations (name, channel, parent_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (clean, channel, parent_id, now, now),
            ).lastrowid
            conn.commit()
        return cid

    def get(self, conversation_id: int) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, channel, parent_id, created_at, updated_at "
                "FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "name": row[1], "channel": row[2],
            "parent_id": row[3], "created_at": row[4], "updated_at": row[5],
        }

    def get_last(self, channel: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, channel, parent_id, created_at, updated_at "
                "FROM conversations WHERE channel=? ORDER BY updated_at DESC, id DESC LIMIT 1",
                (channel,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "name": row[1], "channel": row[2],
            "parent_id": row[3], "created_at": row[4], "updated_at": row[5],
        }

    def list(self, channel: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.parent_id, c.created_at, c.updated_at,
                          COUNT(m.id) as message_count
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.channel=?
                   GROUP BY c.id
                   ORDER BY c.updated_at DESC, c.id DESC""",
                (channel,),
            ).fetchall()
        return [
            {"id": r[0], "name": r[1], "parent_id": r[2],
             "created_at": r[3], "updated_at": r[4], "message_count": r[5]}
            for r in rows
        ]

    def rename(self, conversation_id: int, name: str, channel: str) -> None:
        conv = self.get(conversation_id)
        if conv is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        if conv["channel"] != channel:
            raise ValueError(
                f"Conversation {conversation_id} does not belong to channel {channel!r}"
            )
        clean = _clean_name(name)
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE conversations SET name=?, updated_at=? WHERE id=?",
                (clean, now, conversation_id),
            )
            conn.commit()

    def touch(self, conversation_id: int) -> None:
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (now, conversation_id),
            )
            conn.commit()

    def fork(self, conversation_id: int, channel: str) -> int:
        conv = self.get(conversation_id)
        if conv is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        if conv["channel"] != channel:
            raise ValueError(
                f"Conversation {conversation_id} does not belong to channel {channel!r}"
            )
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            new_id = conn.execute(
                "INSERT INTO conversations (name, channel, parent_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (conv["name"], channel, conversation_id, now, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO messages
                       (channel, role, content, timestamp, est_tokens, conversation_id)
                   SELECT ?, role, content, timestamp, est_tokens, ?
                   FROM messages WHERE conversation_id=?
                   ORDER BY timestamp ASC, id ASC""",
                (channel, new_id, conversation_id),
            )
            conn.commit()
        return new_id

    def load_messages(self, conversation_id: int, limit: int = 1000) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? "
                "ORDER BY timestamp ASC, id ASC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [{"role": row[0], "content": row[1]} for row in rows]

    def count_user_messages(self, conversation_id: int) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND role='user'",
                (conversation_id,),
            ).fetchone()[0]

    def export(self, conversation_id: int, channel: str) -> Path:
        conv = self.get(conversation_id)
        if conv is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        if conv["channel"] != channel:
            raise ValueError(
                f"Conversation {conversation_id} does not belong to channel {channel!r}"
            )
        messages = self.load_messages(conversation_id)
        payload = {**conv, "messages": messages}

        export_dir = PROJECT_HOME / "conversations"
        export_dir.mkdir(parents=True, exist_ok=True)
        slug = _slug(conv["name"]) or "conversation"
        filename = f"{conversation_id}-{slug}.json"
        path = export_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return path
