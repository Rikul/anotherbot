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
from .core.mcp_manager import initialize_mcp

from dotenv import load_dotenv
load_dotenv()

async def load_config() -> None:
    try:
        config.load()
    except FileNotFoundError:
        log.error("Configuration file not found. Please create config.toml")
        return
    except Exception as e:
        log.error(f"Failed to load configuration: {e}")
        return

def parse_args():
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("--trace", action="store_true", default=False,
                        help="Enable LLM call tracing")
    parser.add_argument("--tracedir", default=str(config.PROJECT_HOME / "trace"), metavar="DIR",
                        help="Trace output directory (default: ~/.crafterscode/trace)")
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("background", help="Run in background")

    cli_parser = subparsers.add_parser("cli", help="Run interactive CLI")

    cli_parser.add_argument("-p", "--prompt", metavar="PROMPT", dest="prompt", type=str, required=False, 
                   help="The initial prompt for the agent", default=None)
    cli_parser.add_argument("-y", "--auto-approve", dest="auto_approve", action="store_true", 
                   help="Allow the agent to call tools without asking for permission")
    cli_parser.add_argument("-i", "--max-iterations", metavar="N", dest="max_iterations", type=int, 
                   help="The maximum number of iterations the agent will run before stopping (default: 100)")
    cli_parser.add_argument("-q", "--quiet", dest="quiet", action="store_true",
                   help="Don't print log messages to the console (they still go to the log file)")


    args = parser.parse_args()

    if not hasattr(args, "max_iterations") or args.max_iterations is None:
        args.max_iterations = config.get("max_iterations", 100)
        
    return args

async def run_background_agent(args):
    await start_server()

async def main():
    
    ensure_home_dir()

    await load_config()
    args = parse_args()
    setup_logging(level=logging.INFO, console=not getattr(args, "quiet", False))

    runtime.set("model",  config.get("model", "deepseek/deepseek-v4.1-flash"))
    runtime.set("max_iterations", args.max_iterations)
    runtime.set("trace", args.trace)
    runtime.set("tracedir", Path(args.tracedir))

    from .core.mcp_manager import mcp_manager
    try:
        await initialize_mcp()
        if args.command == "cli":
            await run_cli(
                max_iterations=args.max_iterations,
                auto_approve=args.auto_approve,
                prompt=args.prompt,
            )
        elif args.command == "background":
            await run_background_agent(args)
        else:
            raise ValueError(f"Unknown command: {args.command}")
    finally:
        await mcp_manager.shutdown()
    
    
if __name__ == "__main__":
    
    # For better Ctrl+C handling, we use asyncio.Runner which is available in Python 3.11 and later
    try:
        with asyncio.Runner() as runner:
            runner.run(main())
    except KeyboardInterrupt:
        log.info("Exiting...")
        os._exit(0)
    except Exception as e:
        log.error(f"An error occurred: {e}")
        os._exit(1)

