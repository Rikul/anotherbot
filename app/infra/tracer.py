from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from ..core import runtime

log = logging.getLogger(__name__)


def write_trace(messages: list) -> Path | None:

    tracedir: Path = runtime.get("tracedir")
    model: str = runtime.get("model", "unknown")

    try:
        tracedir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        ts = now.strftime("%m%d%Y_%H%M%S_%f")
        path = tracedir / f"trace_{ts}.json"
        suffix = 1
        while path.exists():
            path = tracedir / f"trace_{ts}_{suffix}.json"
            suffix += 1
        data = {
            "timestamp": now.isoformat(),
            "model": model,
            "messages": messages,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        log.info(f"Trace written to {path}")
        return path
    except Exception as e:
        log.warning(f"Failed to write trace: {e}")
        return None
