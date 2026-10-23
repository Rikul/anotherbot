"""Scheduled runs and in-place restarts."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import app.config as config
from app.core import scheduled_tasks as st
from app.infra import restart


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    monkeypatch.setattr(st, "get_db_connection", lambda: config.get_db_connection(db))
    return st.ScheduledTasks()


@pytest.mark.asyncio
async def test_running_task_counts_as_a_turn_and_cancel_keeps_its_slot(tasks):
    next_run = (
        (datetime.now() - timedelta(minutes=1)).replace(microsecond=0).isoformat()
    )
    tasks.add_task("t", "p", next_run, repeat=1, interval_mins=60)
    started = asyncio.Event()

    class SlowAgent:
        def __init__(self, **_kw):
            pass

        async def agent_loop(self, _prompt):
            started.set()
            await asyncio.sleep(10)

    with patch.object(st, "HelperAgent", SlowAgent):
        run = asyncio.create_task(tasks.run_task(tasks.load_tasks()[0]))
        await started.wait()
        assert restart._state.active_turns == 1  # a restart waits for it
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    assert restart._state.active_turns == 0
    (out,) = tasks.get_output("t")
    assert out["status"] == "cancelled"
    task = tasks.load_tasks()[0]
    assert task["next_run"] == next_run and task["run_count"] == 0  # runs again


@pytest.mark.asyncio
async def test_no_new_runs_while_restart_is_pending(tasks):
    tasks.add_task("t", "p", "")
    restart.request_restart()
    with (
        patch.object(tasks, "run_task") as run_task,
        patch.object(st.asyncio, "sleep", side_effect=asyncio.CancelledError),
    ):
        with pytest.raises(asyncio.CancelledError):
            await tasks.run()
    run_task.assert_not_called()
