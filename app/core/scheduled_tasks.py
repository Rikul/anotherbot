from __future__ import annotations

from datetime import datetime, timedelta
from ..infra.app_logging import log
from ..config import APP_DB
import sqlite3
import asyncio
from .helper_agent import HelperAgent

TASKS_SYSTEM_PROMPT = """
You are a background agent running periodic tasks. The user is not present.
Read your instructions and execute them. Be concise.
"""

class ScheduledTasks:
    def __init__(self, mqs: dict = None, channels: dict = None):
        self._mqs = mqs or {}
        self._channels = channels or {}
        self._init_tasks_db()

    def _migrate(self, conn, table: str, columns: list[tuple[str, str]]):
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col_name, col_def in columns:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

    def _init_tasks_db(self):
        with sqlite3.connect(APP_DB) as conn:

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT    NOT NULL UNIQUE,
                    prompt          TEXT    NOT NULL,
                    enabled         INTEGER NOT NULL DEFAULT 1,
                    repeat          INTEGER NOT NULL DEFAULT 0,
                    interval_mins   INTEGER NOT NULL DEFAULT 1,
                    last_run        TEXT,
                    next_run        TEXT    NOT NULL,
                    delivery_channel   TEXT    NOT NULL DEFAULT 'telegram',
                    run_count       INTEGER NOT NULL DEFAULT 0,
                    created_at      TEXT    NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_outputs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT    NOT NULL,
                    prompt       TEXT    NOT NULL,
                    output       TEXT    NOT NULL,
                    status      TEXT    NOT NULL DEFAULT 'success',
                    duration_secs REAL,
                    timestamp    TEXT    NOT NULL
                )
            """)

            self._migrate(conn, "tasks", [
                ("repeat",           "INTEGER NOT NULL DEFAULT 0"),
                ("next_run",         "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00'"),
                ("delivery_channel", "TEXT NOT NULL DEFAULT 'telegram'"),
                ("run_count",        "INTEGER NOT NULL DEFAULT 0"),
            ])
            self._migrate(conn, "task_outputs", [
                ("status",        "TEXT NOT NULL DEFAULT 'success'"),
                ("duration_secs", "REAL"),
            ])
            conn.commit()


    def load_tasks(self) -> list[dict]:
        query = """SELECT name, prompt, enabled, repeat, interval_mins,
                          last_run, next_run, delivery_channel, run_count, created_at
                   FROM tasks"""
        with sqlite3.connect(APP_DB) as conn:
            rows = conn.execute(query).fetchall()

        return [{"name": n, "prompt": p, "enabled": e, "repeat": rpt,
                 "interval_mins": i, "last_run": lr, "next_run": nr,
                 "delivery_channel": dc, "run_count": rc, "created_at": c}
                for n, p, e, rpt, i, lr, nr, dc, rc, c in rows]


    def add_task(self, name: str, prompt: str, next_run: str, interval_mins: int = 1,
                 repeat: int = 0, delivery_channel: str = "telegram", enabled: int = 1):
        now = datetime.now().isoformat()
        with sqlite3.connect(APP_DB) as conn:
            try:
                conn.execute("""
                    INSERT INTO tasks (name, prompt, interval_mins, repeat, next_run, delivery_channel, enabled, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, prompt, interval_mins, repeat, next_run or now, delivery_channel, enabled, now))
                conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(f"Task '{name}' already exists")

    def remove_task(self, name: str):
        with sqlite3.connect(APP_DB) as conn:
            conn.execute("DELETE FROM tasks WHERE name = ?", (name,))
            conn.commit()

    def update_task(self, name: str, **fields):
        if not fields:
            return
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values()) + [name]
        with sqlite3.connect(APP_DB) as conn:
            if not conn.execute("SELECT name FROM tasks WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"Task '{name}' not found")
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE name = ?", values)
            conn.commit()

    def save_output(self, name: str, prompt: str, output: str,
                    status: str = "success", duration_secs: float = None):
        with sqlite3.connect(APP_DB) as conn:
            conn.execute("""
                INSERT INTO task_outputs (name, prompt, output, status, duration_secs, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, prompt, output, status, duration_secs, datetime.now().isoformat()))
            conn.commit()

    def get_output(self, name: str, num_entries: int = 5) -> list[dict]:
        with sqlite3.connect(APP_DB) as conn:
            rows = conn.execute("""
                SELECT prompt, output, status, duration_secs, timestamp FROM task_outputs
                WHERE name = ?
                ORDER BY id DESC LIMIT ?
            """, (name, num_entries)).fetchall()
        return [{"prompt": p, "output": o, "status": s, "duration_secs": d, "timestamp": t}
                for p, o, s, d, t in reversed(rows)]

    def _after_run(self, task: dict, now: datetime):
        name = task["name"]
        if task["repeat"]:
            next_run = (now + timedelta(minutes=task["interval_mins"])).isoformat()
            with sqlite3.connect(APP_DB) as conn:
                conn.execute("""UPDATE tasks SET last_run = ?, next_run = ?, run_count = run_count + 1
                                WHERE name = ?""", (now.isoformat(), next_run, name))
                conn.commit()
        else:
            self.remove_task(name)

    def _is_due(self, task: dict, now: datetime) -> bool:
        return now >= datetime.fromisoformat(task["next_run"])

    async def run_task(self, task: dict) -> str:
        name, prompt = task["name"], task["prompt"]
        log.info(f"Running scheduled task '{name}'")
        start = datetime.now()
        status = "success"
        output = ""
        try:
            agent = HelperAgent(system_prompt=TASKS_SYSTEM_PROMPT)
            output = await agent.agent_loop(prompt)
        except Exception as e:
            status = "error"
            output = str(e)
            log.error(f"Scheduled task '{name}' failed: {e}")
        finally:
            duration = (datetime.now() - start).total_seconds()
            self.save_output(name=name, prompt=prompt, output=output,
                             status=status, duration_secs=duration)
            self._after_run(task=task, now=datetime.now())

        channel_name = task["delivery_channel"]
        mq = self._mqs.get(channel_name)
        channel = self._channels.get(channel_name)
        if mq and channel:
            try:
                from ..channels.message import OutgoingMessage
                await mq.outgoing_msg(OutgoingMessage(content=output, channel=channel, metadata=channel.default_metadata))
            except Exception as e:
                log.error(f"Scheduled task '{name}': failed to deliver to '{channel_name}': {e}")
        else:
            log.warning(f"Delivery channel '{channel_name}' not found for task '{name}'")

        return output

    async def run(self):
        while True:
            try:
                now = datetime.now()
                tasks = self.load_tasks()
                due = [t for t in tasks if t["enabled"] and self._is_due(t, now)]
                if due:
                    await asyncio.gather(*[self.run_task(t) for t in due], return_exceptions=True)
            except Exception as e:
                log.error(f"ScheduledTasks.run error: {e}", exc_info=True)
            await asyncio.sleep(60)
