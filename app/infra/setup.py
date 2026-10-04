from __future__ import annotations

from ..config import PROJECT_HOME
from .app_logging import log

def ensure_home_dir() -> None:
    import os

    # Create app home directory in user's home if it doesn't exist
    home_dir = PROJECT_HOME

    if not home_dir.exists():
        home_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(home_dir, 0o700)
        log.info(f"Created home directory: {home_dir}")

    # Create workspace, skills, logs directories
    for subdir in ["workspace"]:
        subdir_path = home_dir / subdir
        if not subdir_path.exists():
            subdir_path.mkdir(parents=True, exist_ok=True)
            log.info(f"Created {subdir} directory: {subdir_path}")
