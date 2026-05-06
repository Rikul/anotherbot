import asyncio
import json
from abc import ABC, abstractmethod

from .. import config
from .client import Client
from ..infra.app_logging import log

MAX_CONTEXT_MESSAGES = 100


class Agent(ABC):

    def __init__(self, max_iterations: int = 250) -> None:
        self.client = Client().get_client()
        self.messages: list[dict] = []
        self.max_iterations = max_iterations

    def _trim_messages(self) -> None:
        if len(self.messages) > MAX_CONTEXT_MESSAGES:
            self.messages = self.messages[-MAX_CONTEXT_MESSAGES:]

    @staticmethod
    def _serialize_assistant_msg(msg) -> dict:
        d = {"role": msg.role, "content": msg.content}
        if msg.tool_calls:
            d["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            raw = msg.model_dump()
            reasoning = raw.get("reasoning_content") or raw.get("reasoning")
            if reasoning:
                d["reasoning_content"] = reasoning
        return d

    # --- hooks ---

    async def _on_thinking(self, content: str | None) -> None:
        """Called when the assistant emits text alongside tool calls."""

    async def _check_permission(self, tool_name: str, tool_args: dict) -> bool:
        """Return False to deny; refusal string is sent back as the tool result."""
        return True

    async def _on_tool_start(self, tool_name: str, tool_args: dict) -> None:
        """Called just before each tool executes."""

    async def _on_response(self, content: str | None) -> None:
        """Called when the assistant emits a final text response (no tool calls)."""

    async def _on_no_choices(self) -> None:
        """Called when the API returns no choices. Raise to abort, return to retry."""
        raise RuntimeError("no choices in response")

    def _should_stop(self) -> bool:
        """Return True to break out of the loop early."""
        return False

    # --- shared tool dispatch ---

    async def handle_tool_call(self, tool_call) -> str:
        from .tool_calls import run_tool  # lazy — tool_calls imports scheduled_tasks which imports Agent
        tool_name = tool_call.function.name
        try:
            tool_args = json.loads(tool_call.function.arguments)
            if not await self._check_permission(tool_name, tool_args):
                return "User denied permission to run this tool. Ask for permission to run the tool again if you want to try running it."
            await self._on_tool_start(tool_name, tool_args)
            return run_tool(tool_name=tool_name, tool_args=tool_args)
        except Exception as e:
            error_msg = f"Error running tool {tool_name}: {str(e)}"
            log.error(error_msg)
            return error_msg

    # --- shared loop ---

    async def _loop(self, messages: list, tool_specs: list) -> str:
        iteration = 0
        assistant_message = None

        while iteration < self.max_iterations:
            iteration += 1
            log.info("chat.completions.create...")
            chat = await self.client.chat.completions.create(
                model=config.get("model", "deepseek/deepseek-v3.2"),
                messages=messages,
                tools=tool_specs,
            )

            if not chat.choices:
                await self._on_no_choices()
                continue

            choice = chat.choices[0]
            assistant_message = choice.message
            finish_reason = getattr(choice, "finish_reason", None)
            if not isinstance(finish_reason, str):
                finish_reason = None

            if assistant_message.tool_calls is not None:
                messages.append(self._serialize_assistant_msg(assistant_message))
                await self._on_thinking(assistant_message.content)
                results = await asyncio.gather(*[
                    self.handle_tool_call(tc) for tc in assistant_message.tool_calls
                ])
                for tc, result in zip(assistant_message.tool_calls, results):
                    messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name, "content": result})
                    log.info(f"{result[:250]}...")
            else:
                await self._on_response(assistant_message.content)
                if finish_reason == "stop":
                    messages.append(self._serialize_assistant_msg(assistant_message))
                    break

            if self._should_stop():
                break

        return assistant_message.content.strip() if assistant_message and assistant_message.content else ""

    @abstractmethod
    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        pass
