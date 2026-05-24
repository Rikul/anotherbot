from __future__ import annotations

import argparse
import asyncio
import logging
import os

from . import config
from .infra.app_logging import setup_logging, log
from .infra.setup import ensure_home_dir
from .cli.cli import input_loop
from .cli.cli_agent import CliAgent
from .bg_server import start_server
from .core import runtime

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
    subparsers = parser.add_subparsers(dest="command", required=True)

    cli_parser = subparsers.add_parser("cli", help="Run interactive CLI")

    cli_parser.add_argument("-p", "--prompt", metavar="PROMPT", dest="prompt", type=str, required=False, 
                   help="The initial prompt for the agent", default=None)
    cli_parser.add_argument("-y", "--auto-approve", dest="auto_approve", action="store_true", 
                   help="Allow the agent to call tools without asking for permission")
    cli_parser.add_argument("-x", "--no-repl", dest="no_repl", action="store_true", 
                   help="Run the agent with the initial prompt and then exit without starting the REPL")
    cli_parser.add_argument("-i", "--max-iterations", metavar="N", dest="max_iterations", type=int, 
                   help="The maximum number of iterations the agent will run before stopping (default: 100)")
    cli_parser.add_argument("-s", "--silent", dest="silent", action="store_true",
                   help="Suppress all output except the final response (implies --auto-approve --no-repl)")

    subparsers.add_parser("background", help="Run in background")

    args = parser.parse_args()

    if not hasattr(args, "max_iterations") or args.max_iterations is None:
        args.max_iterations = config.get("max_iterations", 100)
        
    return args
    
async def run_cli(args):

    if args.max_iterations <= 0:
        log.warning(f"max_iterations must be positive (got {args.max_iterations}), exiting")
        return

    if args.silent:
        log.setLevel(logging.WARNING)

    log.info("Starting agent...")
    agent = CliAgent(auto_approve=args.auto_approve or args.silent,
                        max_iterations=runtime.get("max_iterations", 250), silent=args.silent)

    if args.prompt:
        await agent.agent_loop(args.prompt)
    elif not (args.no_repl or args.silent):
        print("Starting interactive session. Type your prompts below. Press Ctrl+C to exit.")

    if args.no_repl or args.silent:
        return

    from .channels.commands import (
        CommandRegistry, BotCommand, make_status_cmd, help_cmd, model_cmd,
        list_conversations_cmd, new_conversation_cmd, load_conversation_cmd,
        fork_conversation_cmd, rename_conversation_cmd, export_conversation_cmd,
    )
    cli_registry = CommandRegistry()
    cli_registry.register(BotCommand("status",               "Show bot status.",                        make_status_cmd()))
    cli_registry.register(BotCommand("model",                "Get or set model. Usage: /model [name]",  model_cmd))
    cli_registry.register(BotCommand("list",   "List conversations.",                      list_conversations_cmd(agent._store, agent._channel_str)))
    cli_registry.register(BotCommand("new",    "Start a new conversation.",                new_conversation_cmd(agent)))
    cli_registry.register(BotCommand("load",   "Load a conversation. Usage: /load <id>",   load_conversation_cmd(agent)))
    cli_registry.register(BotCommand("fork",   "Fork a conversation. Usage: /fork [id]",   fork_conversation_cmd(agent)))
    cli_registry.register(BotCommand("rename", "Rename a conversation. Usage: /rename <id> <name>", rename_conversation_cmd(agent._store, agent._channel_str)))
    cli_registry.register(BotCommand("export", "Export a conversation to JSON. Usage: /export [id]", export_conversation_cmd(agent._store, agent._channel_str)))
    cli_registry.register(BotCommand("help",                 "Show available commands.",                 help_cmd(cli_registry)))

    try:
        async for user_input in input_loop():
            if user_input.startswith("/"):
                parts = user_input[1:].split(maxsplit=1)
                cmd_name = parts[0].lower()
                cmd_args = parts[1] if len(parts) > 1 else ""
                result = await cli_registry.execute(cmd_name, cmd_args)
                print(result if result is not None else f"Unknown command: /{cmd_name}")
            else:
                await agent.agent_loop(user_input)
    except KeyboardInterrupt:
        log.info("Exiting...")
        os._exit(0)
    except Exception as e:
        log.error(f"An error occurred: {e}")


async def run_background_agent(args):
    await start_server()


async def main():
    
    ensure_home_dir()
    
    await load_config()
    setup_logging(level=logging.INFO)

    args = parse_args()

    runtime.set("model",  config.get("model", "deepseek/deepseek-v4-flash"))
    runtime.set("max_iterations", args.max_iterations)

    if args.command == "cli":
        await run_cli(args)
    elif args.command == "background":
        await run_background_agent(args)
    else:
        raise ValueError(f"Unknown command: {args.command}")
    
    
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

