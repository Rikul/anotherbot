from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from .app_logging import log
from ..config import APP_DB

def _est_tokens(content: str) -> int:
    return max(1, len(content) // 4)

class MessageHistory:
    def __init__(self, channel_type: str, db_path: Path = APP_DB):
        self.db_path = db_path
        self.channel = channel_type
        self._ensure_db()

    def _ensure_db(self):
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel text NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        est_tokens INTEGER
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel)
                """)
                # Add conversation_id if missing (ConversationStore adds it too,
                # but MessageHistory must be self-consistent for standalone use)
                cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
                if "conversation_id" not in cols:
                    conn.execute(
                        "ALTER TABLE messages ADD COLUMN conversation_id INTEGER"
                    )
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
                    ON messages(conversation_id)
                """)
                conn.commit()

        except sqlite3.Error as e:
            log.error(f"Error creating message history database: {str(e)}")
            raise

    def add_message(self, role: str, content: str, conversation_id: int = None):
        timestamp = datetime.now().isoformat()
        est = _est_tokens(content)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (channel, role, content, timestamp, est_tokens, conversation_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self.channel, role, content, timestamp, est, conversation_id),
            )
            conn.commit()
        log.info(f"Added message to history: role={role}, est_tokens={est}, content={content[:30]}...")

    def get_history(self, limit: int = 100) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""SELECT role, content FROM messages
                                    WHERE channel = ?
                                    ORDER BY id DESC LIMIT ?""", (self.channel, limit)).fetchall()
            return [{"role": row[0], "content": row[1]} for row in reversed(rows)]


