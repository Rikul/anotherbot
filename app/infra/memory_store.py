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
        core INTEGER NOT NULL DEFAULT 0,
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
        content, category, content='memories', content_rowid='id',
        tokenize='porter unicode61'
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

_COLUMNS = "id, content, category, core, created_at, updated_at"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "content": row[1],
        "category": row[2],
        "core": bool(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
    }


# Words too common to say anything about relevance. "user" is here because
# memories are written as "User likes …", so it would match every one.
_STOPWORDS = frozenset(
    """
    a about above after again all am an and any are as at be because been before
    being below between both but by can could did do does doing down during each
    few for from further had has have having he her here hers him his how i if in
    into is it its just me more most my no nor not now of off on once only or other
    our out over own same she should so some such than that the their them then
    there these they this those through to too under until up very was we were what
    when where which while who whom why will with would you your yours
    hi hello hey please thanks thank ok okay yes yeah also get got like know want
    need tell give make let us one user
    """.split()
)


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 query: each word quoted, OR'd together.

    Quoting stops user text (``"``, ``*``, ``-``, ``OR``…) from being parsed as
    FTS syntax; OR plus bm25 ranking means rows matching more words come first.
    Stopwords and one-letter words are dropped (unless nothing else is left),
    so "what do I like to eat" searches for "eat", not "what" or "do".
    """
    words = list(dict.fromkeys(re.findall(r"\w+", query.lower())))
    keywords = [w for w in words if len(w) > 1 and w not in _STOPWORDS]
    return " OR ".join(f'"{w}"' for w in (keywords or words)[:50])


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
                # Rebuild the index if it is missing (e.g. a memories table from
                # elsewhere) or was made before stemming was added.
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'memories_fts'"
                ).fetchone()
                rebuild = not row or "porter" not in row[0]
                if row and rebuild:
                    conn.execute("DROP TABLE memories_fts")
                for stmt in _SCHEMA:
                    conn.execute(stmt)
                # Tables made before core memories existed.
                cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
                if "core" not in cols:
                    conn.execute(
                        "ALTER TABLE memories ADD COLUMN core INTEGER NOT NULL DEFAULT 0"
                    )
                if rebuild:
                    conn.execute(
                        "INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')"
                    )
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
        self,
        content: str,
        category: str | None = None,
        memory_id: int | None = None,
        core: bool | None = None,
    ) -> dict:
        """Add a memory, or replace memory ``memory_id``.

        ``core`` marks a memory that always goes into the system prompt. When
        updating, ``core=None`` and ``category=None`` keep the current values.

        Returns the stored memory plus ``status``: ``created``, ``updated``,
        ``duplicate`` (identical content already stored; nothing was written)
        or ``promoted`` (a duplicate saved with ``core=True`` made the existing
        one core). Raises ``ValueError`` for empty/too-long content or an
        unknown id.
        """
        # One line per memory: newlines would let a memory forge extra lines
        # in the system prompt.
        content = " ".join((content or "").split())
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
                            "UPDATE memories SET content = ?, category = COALESCE(?, category), "
                            "core = COALESCE(?, core), updated_at = ? WHERE id = ?",
                            (content, category, _core_value(core), now, memory_id),
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
                        "INSERT INTO memories "
                        "(content, category, core, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        (content, category, int(bool(core)), now, now),
                    )
                    if cur.rowcount:
                        status, new_id = "created", cur.lastrowid
                    else:
                        status, new_id = "duplicate", self._find_content(conn, content)
                        if (
                            core
                            and conn.execute(
                                "UPDATE memories SET core = 1, updated_at = ? "
                                "WHERE id = ? AND core = 0",
                                (now, new_id),
                            ).rowcount
                        ):
                            status = "promoted"
        finally:
            conn.close()

        memory = self.get(new_id)
        if memory is None:  # deleted by another connection in the meantime
            raise ValueError("The memory was deleted while saving; try again.")
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

    def relevant(self, text: str, limit: int = 10) -> list[dict]:
        """Memories related to ``text`` (e.g. the user's message), best first.

        Unlike ``search()``, returns nothing when ``text`` has no keywords
        instead of falling back to the most recent memories.
        """
        words = re.findall(r"\w+", (text or "").lower())
        if not any(len(w) > 1 and w not in _STOPWORDS for w in words):
            return []
        return self.search(text, limit)

    def core(self, limit: int = 20) -> list[dict]:
        """Core memories (always in the prompt), most recently updated first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE core = 1 "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
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


def _core_value(core: bool | None) -> int | None:
    return None if core is None else int(bool(core))


def format_memory(memory: dict) -> str:
    """One line per memory, as shown to the model: ``[id] ★ (category) content``.

    ★ marks a core memory.
    """
    star = " ★" if memory.get("core") else ""
    cat = f" ({memory['category']})" if memory.get("category") else ""
    return (
        f"[{memory['id']}]{star}{cat} {memory['content']} — {memory['updated_at'][:10]}"
    )
