"""Entry point: ``python -m app.main {cli,background}``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from . import config
from .infra.app_logging import setup_logging, log
from .infra.setup import ensure_home_dir
from .cli.cli import run_cli
from .bg_server import start_server
from .core import runtime
from .core import mcp_manager as mcp
from .core.mcp_manager import initialize_mcp
from .infra import tracer
from .infra import restart


def parse_args():
    """Parse the command line; ``max_iterations`` defaults to the configured value."""
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument(
        "--trace", action="store_true", default=False, help="Enable LLM call tracing"
    )
    parser.add_argument(
        "--tracedir",
        default=str(config.PROJECT_HOME / "trace"),
        metavar="DIR",
        help="Trace output directory (default: %(default)s)",
    )

    parser.add_argument(
        "-p",
        "--prompt",
        metavar="PROMPT",
        dest="prompt",
        type=str,
        required=False,
        help="Run this prompt once, print the reply and exit (omit for the interactive UI)",
        default=None,
    )
    parser.add_argument(
        "-y",
        "--auto-approve",
        dest="auto_approve",
        action="store_true",
        help="Allow the agent to call tools without asking for permission",
    )
    parser.add_argument(
        "-i",
        "--max-iterations",
        metavar="N",
        dest="max_iterations",
        type=int,
        help="The maximum number of iterations the agent will run before stopping (default: 250)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        help="Don't print log messages to the console (they still go to the log file)",
    )

    # No command runs the CLI agent (TUI, or one prompt with -p); the only
    # command is "background".
    subparsers = parser.add_subparsers(dest="command", metavar="{background}")
    subparsers.add_parser(
        "background", help="Run the Telegram / Discord / web UI server"
    )

    args = parser.parse_args()

    if args.command is None:
        args.command = "cli"
    elif (
        args.prompt is not None or args.auto_approve or args.max_iterations is not None
    ):
        parser.error("-p, -y and -i only apply to the CLI agent, not 'background'")

    if args.max_iterations is None:
        args.max_iterations = config.get("max_iterations")

    return args


async def run_background_agent():
    """Run the background server (Telegram / Discord / web channels)."""
    await start_server()


async def main():
    """Set up config, logging, runtime settings and MCP, then run the chosen command.

    MCP connections are always shut down on the way out.
    """

    ensure_home_dir()

    config.load()  # env vars only; raises on invalid values so bad config fails fast
    args = parse_args()
    setup_logging(level=logging.INFO, console=not args.quiet)

    runtime.set("model", config.get("model", "deepseek/deepseek-v4.1-flash"))
    runtime.set("max_iterations", args.max_iterations)
    runtime.set("trace", args.trace)
    runtime.set("tracedir", Path(args.tracedir))

    try:
        await initialize_mcp()
        if args.command == "cli":
            await run_cli(
                max_iterations=args.max_iterations,
                auto_approve=args.auto_approve,
                prompt=args.prompt,
            )
        elif args.command == "background":
            await run_background_agent()
        else:
            raise ValueError(f"Unknown command: {args.command}")
    finally:
        tracer.close_all()
        await mcp.mcp_manager.shutdown()


if __name__ == "__main__":
    # asyncio.Runner (Python 3.11+) gives better Ctrl+C handling than asyncio.run
    try:
        with asyncio.Runner() as runner:
            runner.run(main())
        if restart.requested():
            restart.exec_restart()
    except KeyboardInterrupt:
        log.info("Exiting...")
        os._exit(0)
    except Exception:  # pylint: disable=broad-exception-caught  # top-level: log and exit non-zero
        log.exception("Application failed")
        os._exit(1)
