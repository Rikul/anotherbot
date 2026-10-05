"""Optional LLM call tracing: appends the full agent trajectory to JSONL files.

Each agent owns a :class:`Tracer`. While the ``trace`` runtime setting is on,
the agent records one JSON object per line as the turn runs: the turn's input
(system prompt + user message), every LLM response (with tool calls, reasoning,
usage, latency and finish reason), every tool result, and the turn's end or
error. Events are written as they happen, so a turn that crashes or is stopped
still leaves a trace.

A trace file is opened once and kept open (line-buffered) for an agent's
conversation; it is rotated when the conversation or trace directory changes,
or after tracing is turned off and on again. The first event in a new file is
the conversation history the model already had, so each file is a complete,
self-contained trajectory.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import IO

from ..core import runtime

log = logging.getLogger(__name__)

_DATA_URL = re.compile(r"^data:([^;,]*);base64,")


def _redact(obj):
    """Replace base64 ``data:`` URLs (attachments) with a short placeholder."""
    if isinstance(obj, str):
        m = _DATA_URL.match(obj)
        if m:
            return f"data:{m.group(1)};base64,<{len(obj) - m.end()} chars omitted>"
        return obj
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    return obj


class Tracer:
    """Writes trace events for one agent to a JSONL file in ``tracedir``.

    Args:
        label: short name used in trace file names (e.g. the channel).

    All methods are no-ops while tracing is off. Failures are logged once per
    file and never raised: tracing must never break a turn.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.path: Path | None = None
        self._file: IO[str] | None = None
        self._dir: Path | None = None
        self._conversation_id = None
        self._failed = False

    @staticmethod
    def enabled() -> bool:
        """Whether the ``trace`` runtime setting is on."""
        return bool(runtime.get("trace"))

    def start_turn(self, messages: list, conversation_id=None) -> None:
        """Record the start of a turn.

        Args:
            messages: the full message list sent to the model for this turn;
                an optional leading system message, the prior history, and the
                new user message last.
            conversation_id: the active conversation, if any. A change rotates
                the trace file.
        """
        if not self.enabled():
            self.close()
            return
        if self._file is not None and (
            conversation_id != self._conversation_id
            or runtime.get("tracedir") != self._dir
        ):
            self.close()
        self._conversation_id = conversation_id

        system = (
            messages[0] if messages and messages[0].get("role") == "system" else None
        )
        start = 1 if system else 0
        history, user = messages[start:-1], messages[-1] if messages else None

        if self._file is None:
            self._failed = False  # retry a failed open once per turn
            if not self._open():
                return
            self._write("history", messages=history)
        self._write(
            "turn_start",
            model=runtime.get("model", "unknown"),
            system=system["content"] if system else None,
            message=user,
        )

    def record(self, event: str, **data) -> None:
        """Append one ``event`` with ``data`` to the trace (opening it if needed)."""
        if not self.enabled():
            return
        if self._file is None and not self._open():
            return
        self._write(event, **data)

    def close(self) -> None:
        """Close the current trace file; the next event starts a new one."""
        if self._file is not None:
            try:
                self._file.close()
            except OSError as e:
                log.warning("Failed to close trace %s: %s", self.path, e)
        self._file = None
        self.path = None
        self._failed = False

    def _open(self) -> bool:
        if self._failed:
            return False
        tracedir: Path = runtime.get("tracedir")
        try:
            tracedir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            conv = (
                f"_c{self._conversation_id}"
                if self._conversation_id is not None
                else ""
            )
            path = tracedir / f"trace_{self.label}{conv}_{ts}.jsonl"
            # Line-buffered: each event is flushed as it is written, without
            # reopening the file per write.
            self._file = open(path, "a", encoding="utf-8", buffering=1)  # pylint: disable=consider-using-with  # kept open across turns
        except Exception as e:  # pylint: disable=broad-exception-caught  # tracing must never break a turn
            log.warning("Failed to open trace file in %s: %s", tracedir, e)
            self._failed = True
            return False
        self.path = path
        self._dir = tracedir
        log.info("Tracing to %s", path)
        self._write(
            "session",
            label=self.label,
            conversation_id=self._conversation_id,
            model=runtime.get("model", "unknown"),
        )
        return True

    def _write(self, event: str, **data) -> None:
        if self._file is None:
            return
        record = {"ts": datetime.now().isoformat(), "event": event, **_redact(data)}
        try:
            self._file.write(json.dumps(record, default=str) + "\n")
        except Exception as e:  # pylint: disable=broad-exception-caught  # tracing must never break a turn
            log.warning("Failed to write trace %s: %s", self.path, e)
            self.close()
            self._failed = True
