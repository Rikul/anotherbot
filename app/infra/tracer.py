"""Optional LLM call tracing: dumps the messages sent to the model as JSON."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from ..core import runtime

log = logging.getLogger(__name__)


def write_trace(messages: list) -> Path | None:
    """Write ``messages`` to a timestamped JSON file in the trace directory.

    The directory and model name come from the ``tracedir`` and ``model``
    runtime settings. Failures are logged, never raised.

    Returns:
        The path of the trace file, or ``None`` if writing failed.
    """

    tracedir: Path = runtime.get("tracedir")
    model: str = runtime.get("model", "unknown")

    try:
        tracedir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%m%d%Y_%H%M%S")
        path = tracedir / f"trace_{ts}.json"
        data = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "messages": messages,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        log.info("Trace written to %s", path)
        return path
    except Exception as e:  # pylint: disable=broad-exception-caught  # tracing must never break a turn
        log.warning("Failed to write trace: %s", e)
        return None
