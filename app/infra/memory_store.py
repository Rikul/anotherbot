"""SQLite-backed long-term memories about the user, with full-text search.

Memories are short facts the agent chooses to keep across conversations
(preferences, personal details, ongoing projects). They live in the
``memories`` table of the app database; ``memories_fts`` is an FTS5 index over
them, kept in sync by triggers.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import APP_DB, get_db_connection
from .app_logging import log

# Default database. Read at call time (not bound as a default argument) so
# tests can point it at a temporary file.
DB_PATH: Path = APP_DB

MAX_MEMORY_LENGTH = 1000

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        category TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # Makes the duplicate check atomic across connections.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_content
    ON memories(lower(content))
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content, category, content='memories', content_rowid='id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content, category)
        VALUES (new.id, new.content, new.category);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, category)
        VALUES ('delete', old.id, old.content, old.category);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, category)
        VALUES ('delete', old.id, old.content, old.category);
        INSERT INTO memories_fts(rowid, content, category)
        VALUES (new.id, new.content, new.category);
    END
    """,
)

_COLUMNS = "id, content, category, created_at, updated_at"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "content": row[1],
        "category": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 query: each word quoted, OR'd together.

    Quoting stops user text (``"``, ``*``, ``-``, ``OR``…) from being parsed as
    FTS syntax; OR plus bm25 ranking means rows matching more words come first.
    """
    words = re.findall(r"\w+", query.lower())
    return " OR ".join(f'"{w}"' for w in words)


class MemoryStore:
    """Save, search and delete long-term memories."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return get_db_connection(self.db_path)

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            with conn:
                for stmt in _SCHEMA:
                    conn.execute(stmt)
        finally:
            conn.close()

    def get(self, memory_id: int) -> dict | None:
        """Return one memory, or None if it doesn't exist."""
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(row) if row else None

    @staticmethod
    def _find_content(conn, content: str) -> int | None:
        row = conn.execute(
            "SELECT id FROM memories WHERE lower(content) = lower(?)", (content,)
        ).fetchone()
        return row[0] if row else None

    def save(
        self, content: str, category: str | None = None, memory_id: int | None = None
    ) -> dict:
        """Add a memory, or replace memory ``memory_id``.

        Returns the stored memory plus ``status``: ``created``, ``updated`` or
        ``duplicate`` (identical content already stored; nothing was written).
        Raises ``ValueError`` for empty/too-long content or an unknown id.
        """
        content = (content or "").strip()
        category = (category or "").strip().lower() or None
        if not content:
            raise ValueError("Memory content is empty.")
        if len(content) > MAX_MEMORY_LENGTH:
            raise ValueError(
                f"Memory is too long ({len(content)} chars, max {MAX_MEMORY_LENGTH}). "
                "Store one short fact per memory."
            )

        now = _now()
        conn = self._connect()
        try:
            with conn:
                if memory_id is not None:
                    try:
                        cur = conn.execute(
                            "UPDATE memories SET content = ?, category = ?, updated_at = ? "
                            "WHERE id = ?",
                            (content, category, now, memory_id),
                        )
                    except sqlite3.IntegrityError as e:
                        dup = self._find_content(conn, content)
                        raise ValueError(
                            f"Memory {dup} already says that; delete one of them instead."
                        ) from e
                    if cur.rowcount == 0:
                        raise ValueError(f"No memory with id {memory_id}.")
                    status, new_id = "updated", memory_id
                else:
                    # The unique index turns a concurrent duplicate into a no-op.
                    cur = conn.execute(
                        "INSERT INTO memories (content, category, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        (content, category, now, now),
                    )
                    if cur.rowcount:
                        status, new_id = "created", cur.lastrowid
                    else:
                        status, new_id = "duplicate", self._find_content(conn, content)
        finally:
            conn.close()

        memory = self.get(new_id)
        memory["status"] = status
        return memory

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Memories matching ``query``, best match first.

        An empty query (or one with no words) returns the most recent ones.
        """
        limit = max(1, min(int(limit), 100))
        fts = _fts_query(query or "")
        if not fts:
            return self.recent(limit)

        conn = self._connect()
        try:
            try:
                rows = conn.execute(
                    f"SELECT {', '.join('m.' + c for c in _COLUMNS.split(', '))} "
                    "FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                    "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
                    (fts, limit),
                ).fetchall()
            except sqlite3.Error as e:
                log.warning("Memory FTS search failed (%s), falling back to LIKE", e)
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM memories WHERE content LIKE ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (f"%{query.strip()}%", limit),
                ).fetchall()
        finally:
            conn.close()
        return [_row_to_dict(r) for r in rows]

    def recent(self, limit: int = 30) -> list[dict]:
        """The most recently created or updated memories."""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_dict(r) for r in rows]

    def count(self) -> int:
        """Total number of stored memories."""
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            conn.close()

    def delete(self, memory_id: int) -> dict | None:
        """Delete a memory; returns the deleted memory, or None if it didn't exist."""
        conn = self._connect()
        try:
            with conn:
                # RETURNING reads and deletes the same row in one statement.
                row = conn.execute(
                    f"DELETE FROM memories WHERE id = ? RETURNING {_COLUMNS}",
                    (memory_id,),
                ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(row) if row else None


def format_memory(memory: dict) -> str:
    """One line per memory, as shown to the model: ``[id] (category) content``."""
    cat = f" ({memory['category']})" if memory.get("category") else ""
    return f"[{memory['id']}]{cat} {memory['content']} — {memory['updated_at'][:10]}"
