"""Interactive terminal REPL and single-prompt mode."""

import asyncio
import os
from collections.abc import AsyncIterator

from ..channels.commands import build_command_registry
from ..core import runtime
from ..infra.app_logging import log
from .cli_agent import CliAgent


async def input_loop() -> AsyncIterator[str]:
    """Yield non-empty lines typed by the user until EOF or Ctrl+C."""
    loop = asyncio.get_event_loop()

    while True:
        try:
            user_input = await loop.run_in_executor(None, lambda: input("> "))
            print("", end="\r")  # clean up line

            if user_input.strip():
                yield user_input

        except (EOFError, KeyboardInterrupt):
            # Stop the loop on EOF or Ctrl+C
            break
        except asyncio.CancelledError:
            raise KeyboardInterrupt from None


async def run_cli(max_iterations: int, auto_approve: bool, prompt: str | None):
    """Run the CLI agent: one prompt and exit, or an interactive REPL with slash commands.

    Args:
        max_iterations: must be positive, otherwise nothing runs.
        auto_approve: run tools without asking.
        prompt: run just this prompt and exit; ``None`` starts the REPL.
    """

    if max_iterations <= 0:
        log.warning("max_iterations must be positive (got %s), exiting", max_iterations)
        return

    log.info("Starting agent...")
    agent = CliAgent(
        auto_approve=auto_approve, max_iterations=runtime.get("max_iterations", 250)
    )

    # If prompt is provided, run the agent loop with the initial prompt
    # and skip the interactive REPL if a prompt is provided
    if prompt:
        await agent.agent_loop(prompt)
        return

    print(
        "Starting interactive session. Type your prompts below. Press Ctrl+C to exit."
    )

    cli_registry = build_command_registry(agent)

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
    except Exception as e:  # pylint: disable=broad-exception-caught  # end the REPL cleanly
        log.error("An error occurred: %s", e)
