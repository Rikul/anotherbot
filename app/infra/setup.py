from __future__ import annotations

from pathlib import Path
from ..config import get_default_config, APP_NAME, APP_DB
from .app_logging import log

def ensure_home_dir() -> None:
    import os

    # Create app home directory in user's home if it doesn't exist
    home_dir = Path.home() / f".{APP_NAME}"
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
    
    # Copy config.toml from current directory to home directory if it doesn't exist
    config_path = home_dir / "config.toml"
    if not config_path.exists():
        default_config = get_default_config()
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(default_config)
        log.info(f"Created default config.toml in home directory: {config_path}")
