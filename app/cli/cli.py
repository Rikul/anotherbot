from ..infra.term_display import ANSI
import asyncio
from ..core import runtime
from ..infra.app_logging import log
from .cli_agent import CliAgent
import os

def ask_permission(tool_name: str, args: dict) -> bool:
    msg = f"{ANSI.YELLOW}⚡ Tool Call{ANSI.RESET}: {ANSI.CYAN}{tool_name}{ANSI.RESET}   Args: {args}"
    print(msg)

    msg = f"Proceed? {ANSI.DIM}[Y/n]{ANSI.RESET} "
    print(msg, end="", flush=True)

    answer = input().strip().lower()
    return answer in ("", "y", "yes")

async def input_loop() -> str :
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
            raise KeyboardInterrupt

    
async def run_cli(max_iterations: int, auto_approve: bool, prompt: str | None):

    if max_iterations <= 0:
        log.warning(f"max_iterations must be positive (got {max_iterations}), exiting")
        return

    log.info("Starting agent...")
    agent = CliAgent(auto_approve=auto_approve,
                        max_iterations=runtime.get("max_iterations", 250))

    # If prompt is provided, run the agent loop with the initial prompt 
    # and skip the interactive REPL if a prompt is provided
    if prompt:
        await agent.agent_loop(prompt)
        return

    print("Starting interactive session. Type your prompts below. Press Ctrl+C to exit.")

    from ..channels.commands import (
        CommandRegistry, BotCommand, make_status_cmd, help_cmd, model_cmd, trace_cmd,
        list_conversations_cmd, new_conversation_cmd, load_conversation_cmd,
        fork_conversation_cmd, rename_conversation_cmd, export_conversation_cmd,
        mcp_cmd,
    )
    cli_registry = CommandRegistry()
    cli_registry.register(BotCommand("status","Show bot status.",make_status_cmd()))
    cli_registry.register(BotCommand("model","Get or set model. Usage: /model [name]",model_cmd))
    cli_registry.register(BotCommand("trace","Toggle LLM tracing. Usage: /trace [on|off]", trace_cmd))
    cli_registry.register(BotCommand("list","List conversations. Usage: /list [all]",list_conversations_cmd(agent._store, agent._channel_str)))
    cli_registry.register(BotCommand("new","Start a new conversation.",new_conversation_cmd(agent)))
    cli_registry.register(BotCommand("load","Load a conversation. Usage: /load <id>",load_conversation_cmd(agent)))
    cli_registry.register(BotCommand("fork","Fork a conversation. Usage: /fork [id]",fork_conversation_cmd(agent)))
    cli_registry.register(BotCommand("rename","Rename a conversation. Usage: /rename <id> <name>", rename_conversation_cmd(agent._store, agent._channel_str)))
    cli_registry.register(BotCommand("export","Export a conversation to JSON. Usage: /export [id]", export_conversation_cmd(agent._store, agent._channel_str)))
    cli_registry.register(BotCommand("mcp","Show MCP server status. Usage: /mcp [tools [<server>]]", mcp_cmd()))
    cli_registry.register(BotCommand("help","Show available commands.",help_cmd(cli_registry)))

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