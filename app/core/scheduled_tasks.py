"""SQLite-backed scheduler that runs prompts on a schedule and delivers the results."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta

from ..channels.message import OutgoingMessage
from ..config import get_db_connection
from ..infra.app_logging import log
from .helper_agent import HelperAgent

TASKS_SYSTEM_PROMPT = """
You are a background agent running periodic tasks. The user is not present.
Read your instructions and execute them. Be concise.
"""


class ScheduledTasks:
    """Stores scheduled tasks and their outputs, and runs them when due.

    Args:
        mqs: message queue per channel name, for delivering results.
        channels: channel object per channel name.
    """

    def __init__(self, mqs: dict = None, channels: dict = None):
        self._mqs = mqs or {}
        self._channels = channels or {}
        self._init_tasks_db()

    def _init_tasks_db(self):
        conn = get_db_connection()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
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
        finally:
            conn.close()

    def load_tasks(self) -> list[dict]:
        """Return every task as a dict (enabled or not)."""
        query = """SELECT name, prompt, enabled, repeat, interval_mins,
                          last_run, next_run, delivery_channel, run_count, created_at
                   FROM tasks"""
        conn = get_db_connection()
        try:
            rows = conn.execute(query).fetchall()
        finally:
            conn.close()

        return [
            {
                "name": n,
                "prompt": p,
                "enabled": e,
                "repeat": rpt,
                "interval_mins": i,
                "last_run": lr,
                "next_run": nr,
                "delivery_channel": dc,
                "run_count": rc,
                "created_at": c,
            }
            for n, p, e, rpt, i, lr, nr, dc, rc, c in rows
        ]

    def add_task(  # pylint: disable=too-many-arguments  # one argument per task column
        self,
        name: str,
        prompt: str,
        next_run: str,
        *,
        interval_mins: int = 1,
        repeat: int = 0,
        delivery_channel: str = "telegram",
        enabled: int = 1,
    ):
        """Add a task.

        Args:
            next_run: ISO datetime of the first run; empty means now.
            interval_mins: minutes between runs of a repeating task.
            repeat: 1 to repeat every ``interval_mins``, 0 to run once.
            delivery_channel: channel name to send the output to.
            enabled: 0 to add it paused.

        Raises:
            ValueError: if a task with this name already exists.
        """
        now = datetime.now().isoformat()
        conn = get_db_connection()
        try:
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO tasks (name, prompt, interval_mins, repeat,
                                           next_run, delivery_channel, enabled, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            name,
                            prompt,
                            interval_mins,
                            repeat,
                            next_run or now,
                            delivery_channel,
                            enabled,
                            now,
                        ),
                    )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Task '{name}' already exists") from e
        finally:
            conn.close()

    def remove_task(self, name: str):
        """Delete a task (no error if it doesn't exist)."""
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("DELETE FROM tasks WHERE name = ?", (name,))
        finally:
            conn.close()

    def update_task(self, name: str, **fields):
        """Update the given columns of a task, e.g. ``update_task("x", enabled=0)``.

        Raises:
            ValueError: if the task doesn't exist.
        """
        if not fields:
            return
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values()) + [name]
        conn = get_db_connection()
        try:
            with conn:
                if not conn.execute(
                    "SELECT name FROM tasks WHERE name = ?", (name,)
                ).fetchone():
                    raise ValueError(f"Task '{name}' not found")
                conn.execute(f"UPDATE tasks SET {set_clause} WHERE name = ?", values)
        finally:
            conn.close()

    def save_output(
        self,
        name: str,
        prompt: str,
        output: str,
        status: str = "success",
        duration_secs: float = None,
    ):
        """Record one run's output, status and duration."""
        conn = get_db_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO task_outputs
                        (name, prompt, output, status, duration_secs, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        name,
                        prompt,
                        output,
                        status,
                        duration_secs,
                        datetime.now().isoformat(),
                    ),
                )
        finally:
            conn.close()

    def get_output(self, name: str, num_entries: int = 5) -> list[dict]:
        """Return a task's last ``num_entries`` outputs, oldest first."""
        conn = get_db_connection()
        try:
            rows = conn.execute(
                """
                SELECT prompt, output, status, duration_secs, timestamp FROM task_outputs
                WHERE name = ?
                ORDER BY id DESC LIMIT ?
            """,
                (name, num_entries),
            ).fetchall()
        finally:
            conn.close()
        return [
            {"prompt": p, "output": o, "status": s, "duration_secs": d, "timestamp": t}
            for p, o, s, d, t in reversed(rows)
        ]

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
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute(
                        """UPDATE tasks SET last_run = ?, next_run = ?, run_count = run_count + 1
                                    WHERE name = ?""",
                        (now.isoformat(), next_run, name),
                    )
            finally:
                conn.close()
        else:
            self.remove_task(name)

    def _is_due(self, task: dict, now: datetime) -> bool:
        return now >= datetime.fromisoformat(task["next_run"])

    async def run_task(self, task: dict) -> str:
        """Run one task with a ``HelperAgent``, record the output, deliver it, and reschedule.

        Returns:
            The output (or the error message if the run failed).
        """
        name, prompt = task["name"], task["prompt"]
        log.info("Running scheduled task '%s'", name)
        start = datetime.now()
        status = "success"
        output = ""
        try:
            agent = HelperAgent(system_prompt=TASKS_SYSTEM_PROMPT)
            output = await agent.agent_loop(prompt)
        except Exception as e:  # pylint: disable=broad-exception-caught  # recorded as the task's output
            status = "error"
            output = str(e)
            log.error("Scheduled task '%s' failed: %s", name, e)
        finally:
            duration = (datetime.now() - start).total_seconds()
            self.save_output(
                name=name,
                prompt=prompt,
                output=output,
                status=status,
                duration_secs=duration,
            )
            self._after_run(task=task, now=datetime.now())

        channel_name = task["delivery_channel"]
        mq = self._mqs.get(channel_name)
        channel = self._channels.get(channel_name)
        if mq and channel:
            try:
                await mq.outgoing_msg(
                    OutgoingMessage(
                        content=output,
                        channel=channel,
                        metadata=channel.default_metadata,
                    )
                )
            except Exception as e:  # pylint: disable=broad-exception-caught  # delivery is best-effort
                log.error(
                    "Scheduled task '%s': failed to deliver to '%s': %s",
                    name,
                    channel_name,
                    e,
                )
        else:
            log.warning(
                "Delivery channel '%s' not found for task '%s'", channel_name, name
            )

        return output

    async def run(self):
        """Check for due tasks every 30 seconds and run them concurrently, forever."""
        while True:
            try:
                now = datetime.now()
                tasks = self.load_tasks()
                due = [t for t in tasks if t["enabled"] and self._is_due(t, now)]
                if due:
                    await asyncio.gather(
                        *[self.run_task(t) for t in due], return_exceptions=True
                    )
            except Exception as e:  # pylint: disable=broad-exception-caught  # keep the scheduler alive
                log.error("ScheduledTasks.run error: %s", e, exc_info=True)
            await asyncio.sleep(30)
