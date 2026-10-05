"""First-run setup of the data directory (``PROJECT_HOME``)."""

from __future__ import annotations

import os

from ..config import PROJECT_HOME
from .app_logging import log


def ensure_home_dir() -> None:
    """Create ``PROJECT_HOME`` (mode 0700) and its ``workspace`` subdirectory if missing."""
    # Create app home directory in user's home if it doesn't exist
    home_dir = PROJECT_HOME

    if not home_dir.exists():
        home_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(home_dir, 0o700)
        log.info("Created home directory: %s", home_dir)

    # Create workspace, skills, logs directories
    for subdir in ["workspace"]:
        subdir_path = home_dir / subdir
        if not subdir_path.exists():
            subdir_path.mkdir(parents=True, exist_ok=True)
            log.info("Created %s directory: %s", subdir, subdir_path)
