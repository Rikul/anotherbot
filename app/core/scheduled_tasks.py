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

            conn.commit()
        conn.close()


    def load_tasks(self) -> list[dict]:
        query = """SELECT name, prompt, enabled, repeat, interval_mins,
                          last_run, next_run, delivery_channel, run_count, created_at
                   FROM tasks"""
        with sqlite3.connect(APP_DB) as conn:
            rows = conn.execute(query).fetchall()
        conn.close()

        return [{"name": n, "prompt": p, "enabled": e, "repeat": rpt,
                 "interval_mins": i, "last_run": lr, "next_run": nr,
                 "delivery_channel": dc, "run_count": rc, "created_at": c}
                for n, p, e, rpt, i, lr, nr, dc, rc, c in rows]


    def add_task(self, name: str, prompt: str, next_run: str, interval_mins: int = 1,
                 repeat: int = 0, delivery_channel: str = "telegram", enabled: int = 1):
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(APP_DB) as conn:
                try:
                    conn.execute("""
                        INSERT INTO tasks (name, prompt, interval_mins, repeat, next_run, delivery_channel, enabled, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (name, prompt, interval_mins, repeat, next_run or now, delivery_channel, enabled, now))
                    conn.commit()
                except sqlite3.IntegrityError:
                    raise ValueError(f"Task '{name}' already exists")
        finally:
            conn.close()

    def remove_task(self, name: str):
        with sqlite3.connect(APP_DB) as conn:
            conn.execute("DELETE FROM tasks WHERE name = ?", (name,))
            conn.commit()
        conn.close()

    def update_task(self, name: str, **fields):
        if not fields:
            return
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values()) + [name]
        try:
            with sqlite3.connect(APP_DB) as conn:
                if not conn.execute("SELECT name FROM tasks WHERE name = ?", (name,)).fetchone():
                    raise ValueError(f"Task '{name}' not found")
                conn.execute(f"UPDATE tasks SET {set_clause} WHERE name = ?", values)
                conn.commit()
        finally:
            conn.close()

    def save_output(self, name: str, prompt: str, output: str,
                    status: str = "success", duration_secs: float = None):
        with sqlite3.connect(APP_DB) as conn:
            conn.execute("""
                INSERT INTO task_outputs (name, prompt, output, status, duration_secs, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, prompt, output, status, duration_secs, datetime.now().isoformat()))
            conn.commit()
        conn.close()

    def get_output(self, name: str, num_entries: int = 5) -> list[dict]:
        with sqlite3.connect(APP_DB) as conn:
            rows = conn.execute("""
                SELECT prompt, output, status, duration_secs, timestamp FROM task_outputs
                WHERE name = ?
                ORDER BY id DESC LIMIT ?
            """, (name, num_entries)).fetchall()
        conn.close()
        return [{"prompt": p, "output": o, "status": s, "duration_secs": d, "timestamp": t}
                for p, o, s, d, t in reversed(rows)]

    def _after_run(self, task: dict, now: datetime):
        """Update task state after execution.

        For repeating tasks, the next run is always advanced by exactly one
        interval from the original scheduled time (next_run). This prevents
        timing drift that would otherwise accumulate if task execution takes
        significant time.

        If multiple intervals have passed since the original next_run (e.g.
        after a server restart or prolonged downtime), skipped intervals are
        fast-forwarded rather than caught up one at a time. This avoids a
        catch-up storm where many runs would fire in rapid succession.

        Specifically:
        - Calculate how many full intervals have passed since the task's
          original next_run.
        - Schedule the next run at the next future slot:
          original_next_run + (intervals_passed + 1) * interval
        """
        name = task["name"]
        if task["repeat"]:
            original_next = datetime.fromisoformat(task["next_run"])
            interval = timedelta(minutes=task["interval_mins"])
            # Advance by one interval from original schedule
            next_due = original_next + interval
            # If we're already past that due time, skip missed intervals
            # by jumping to the next future slot
            if now >= next_due:
                elapsed_secs = (now - original_next).total_seconds()
                intervals_passed = int(elapsed_secs // interval.total_seconds())
                next_due = original_next + (intervals_passed + 1) * interval
            next_run = next_due.isoformat()
            with sqlite3.connect(APP_DB) as conn:
                conn.execute("""UPDATE tasks SET last_run = ?, next_run = ?, run_count = run_count + 1
                                WHERE name = ?""", (now.isoformat(), next_run, name))
                conn.commit()
            conn.close()
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
            await asyncio.sleep(30)
