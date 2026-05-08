from pathlib import Path
import os
import platform
from datetime import datetime
from .app_logging import log
from .. import config

def load_system_context() -> str:
    """
    Load sys_instructions.md and return its contents as the system context string.
    """

    now = datetime.now()
    sys_instructions_path = Path(__file__).parent.parent / "core" / "sys_instructions.md"
    system_context = ""

    try:
        with open(sys_instructions_path, "r", encoding="utf-8") as f:
            system_context = f.read().strip()
    except Exception as e:
        log.error(f"Error loading system context: {e}")

    system_context += f"""

## System Context
- datetime:   {now.strftime("%Y-%m-%d %H:%M:%S")} {now.astimezone().tzname()}
- day of week: {now.strftime("%A")}
- os:         {platform.system()} {platform.release()}
- shell:      {os.environ.get("SHELL", "unknown")}
- cwd:        {Path.cwd()}
- home:       {Path.home()}
- workspace:  {config.PROJECT_HOME / "workspace"}

## Runtime
- python:     {platform.python_version()}
- model:      {config.get("model", "unknown")}
"""
    
    log.info(f"Loaded system context: {len(system_context)} characters")

    return system_context

