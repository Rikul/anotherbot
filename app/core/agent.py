import asyncio
import base64
import json
import mimetypes
import os
import platform
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from .. import config
from .client import Client
from ..infra.app_logging import log
from . import runtime

MAX_CONTEXT_MESSAGES = 1000


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", name.strip().lower().replace(" ", "-"))[:40]


def get_default_sys_prompt(context: dict | None = None) -> str:
    ctx = context or {}
    channel = ctx.get("channel", "cli")

    now = datetime.now()
    sys_instructions_path = Path(__file__).parent / "sys_instructions.md"
    sys_prompt = ""

    try:
        with open(sys_instructions_path, "r", encoding="utf-8") as f:
            sys_prompt = f.read().strip()
    except Exception as e:
        log.error(f"Error loading system prompt: {e}")

    conv_id   = ctx.get("conversation_id", "")
    conv_name = ctx.get("conversation_name", "")
    conv_line = f"\n- Conversation: [{conv_id}] {conv_name}" if conv_id else ""

    sys_prompt += f"""

## Current System Context
- Conversation started on   {now.strftime("%Y-%m-%d %H:%M:%S")} {now.astimezone().tzname()}
- Day of Week: {now.strftime("%A")}
- OS:         {platform.system()} {platform.release()}
- Shell:      {os.environ.get("SHELL", "unknown")}
- CWD:        {Path.cwd()}
- Home:       {Path.home()}
- workspace:  {config.PROJECT_HOME / "workspace"}
- Python:     {platform.python_version()}
- Starting LLM Model:      {runtime.get("model", "unknown")}
- Current Channel: {channel}{conv_line}
"""

    log.info(f"Loaded system prompt: {len(sys_prompt)} characters")
    return sys_prompt


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


    @staticmethod
    def _as_list(value) -> list:
        if not value:
            return []
        if isinstance(value, (str, os.PathLike)):
            return [value]
        return list(value)

    @staticmethod
    def _attachment_part(attachment: str) -> dict:
        path = Path(attachment).expanduser()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data_url = f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

        if mime_type.startswith("image/"):
            return {
                "type": "image_url",
                "image_url": {"url": data_url},
            }

        return {
            "type": "file",
            "file": {
                "filename": path.name,
                "file_data": data_url,
            },
        }

    @classmethod
    def _build_user_message(cls, message: str, metadata: dict | None = None) -> dict:
        metadata = metadata or {}
        attachments = cls._as_list(metadata.get("images")) + cls._as_list(metadata.get("files"))
        if not attachments:
            return {"role": "user", "content": message}

        content = [{"type": "text", "text": message}]
        content.extend(cls._attachment_part(str(attachment)) for attachment in attachments)
        return {"role": "user", "content": content}

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

    async def _auto_name(self, store, conv_id: int, messages: list[dict], name_runtime_key: str) -> None:
        from .helper_agent import HelperAgent  # lazy — helper_agent imports Agent
        transcript = "\n".join(
            f"{m['role']}: {m['content'][:200]}" for m in messages[:4]
        )
        prompt = (
            "Summarize this conversation in 4-6 words as a title. "
            "Reply with just the title, nothing else.\n\n" + transcript
        )
        try:
            name = await HelperAgent().run(prompt)
            name = _slugify(name) or "new-conversation"
            conv = store.get(conv_id)
            if conv and conv["name"] == "New Conversation":
                store.rename(conv_id, name, conv["channel"])
                runtime.set(name_runtime_key, name)
                log.info(f"Auto-named conversation {conv_id}: {name!r}")
        except Exception as e:
            log.warning(f"Auto-naming conversation {conv_id} failed: {e}")

    # --- shared tool dispatch ---

    async def handle_tool_call(self, tool_call) -> str:
        from .tool_calls import run_tool_async  # lazy — tool_calls imports scheduled_tasks which imports Agent
        tool_name = tool_call.function.name
        try:
            tool_args = json.loads((tool_call.function.arguments or "").strip() or "{}")
            if not await self._check_permission(tool_name, tool_args):
                return "User denied permission to run this tool. Ask for permission to run the tool again if you want to try running it."
            await self._on_tool_start(tool_name, tool_args)
            return await run_tool_async(tool_name=tool_name, tool_args=tool_args)
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
                model = runtime.get("model", "deepseek/deepseek-v4-flash"),
                messages = messages,
                tools = tool_specs,
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
                messages.append(self._serialize_assistant_msg(assistant_message))
                await self._on_response(assistant_message.content)
                if finish_reason not in ("stop", "length") and finish_reason is not None:
                    log.warning(f"Unexpected finish_reason={finish_reason!r}, treating as terminal")
                break

            if self._should_stop():
                break

        return assistant_message.content.strip() if assistant_message and assistant_message.content else ""

    @abstractmethod
    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        pass
