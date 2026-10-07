"""The shared agentic loop (LLM call → tool calls → repeat) and its hooks."""

import asyncio
import base64
import json
import mimetypes
import os
import platform
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from .. import config
from .client import Client
from ..infra.app_logging import log
from ..infra.tracer import Tracer
from . import runtime

MAX_CONTEXT_MESSAGES = 1000


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", name.strip().lower().replace(" ", "-"))[:40]


def get_default_sys_prompt(context: dict | None = None) -> str:
    """Build the system prompt: ``sys_instructions.md`` plus a current-context section.

    Args:
        context: optional ``channel``, ``conversation_id`` and
            ``conversation_name`` to include.
    """
    ctx = context or {}
    channel = ctx.get("channel", "cli")

    now = datetime.now()
    sys_instructions_path = Path(__file__).parent / "sys_instructions.md"
    sys_prompt = ""

    try:
        with open(sys_instructions_path, "r", encoding="utf-8") as f:
            sys_prompt = f.read().strip()
    except (OSError, UnicodeDecodeError) as e:
        log.error("Error loading system prompt: %s", e)

    conv_id = ctx.get("conversation_id", "")
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

    log.info("Loaded system prompt: %s characters", len(sys_prompt))
    return sys_prompt


class Agent(ABC):
    """Base agent: runs the LLM/tool loop and leaves I/O to subclass hooks.

    Subclasses implement ``agent_loop`` and override the ``_on_*``,
    ``_check_permission`` and ``_should_stop`` hooks (see CLAUDE.md for which
    agent overrides what).
    """

    trace_label = "agent"

    def __init__(self, max_iterations: int = 250) -> None:
        self.client = Client().get_client()
        self.messages: list[dict] = []
        self.max_iterations = max_iterations
        self.tracer = Tracer(self.trace_label)

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

    _TOOL_ARG_PREVIEW = 80

    @classmethod
    def _format_tool_call(cls, tool_call: dict) -> str:
        """Render one serialized tool call as ``name(key=value, ...)`` with values truncated."""
        fn = tool_call.get("function") or {}
        name = str(fn.get("name") or "?")
        raw = fn.get("arguments") or ""
        try:
            args = json.loads(raw) if raw.strip() else {}
        except (TypeError, ValueError, AttributeError):
            args = None
        if isinstance(args, dict):
            parts = []
            for key, value in args.items():
                text = json.dumps(value, ensure_ascii=False)
                if len(text) > cls._TOOL_ARG_PREVIEW:
                    text = text[: cls._TOOL_ARG_PREVIEW] + "…"
                parts.append(f"{key}={text}")
            arg_text = ", ".join(parts)
        else:
            arg_text = str(raw)[: cls._TOOL_ARG_PREVIEW]
        return f"{name}({arg_text})"

    @classmethod
    def _compact_turn(cls, turn: list[dict], final: str) -> list[dict]:
        """Compact one turn's messages into assistant text messages for history.

        ``turn`` is what ``_loop`` appended after the user message. Intermediate
        assistant text is kept; each tool call becomes a one-line summary
        (name + truncated args) on the text it followed; tool results and
        reasoning are dropped (tracing keeps them). The final reply is last.
        """
        assistant = [m for m in turn if m.get("role") == "assistant"]
        entries: list[tuple[str, list[str]]] = []
        for msg in assistant:
            if not msg.get("tool_calls"):
                continue
            text = (msg.get("content") or "").strip()
            if text or not entries:
                entries.append((text, []))
            entries[-1][1].extend(cls._format_tool_call(tc) for tc in msg["tool_calls"])

        compacted = []
        for text, calls in entries:
            summary = f"[tool calls: {'; '.join(calls)}]"
            content = f"{text}\n\n{summary}" if text else summary
            compacted.append({"role": "assistant", "content": content})
        # A turn cut short (stop / max iterations) ends on a tool-call message,
        # whose text is already in the last entry.
        if not assistant or not assistant[-1].get("tool_calls"):
            compacted.append({"role": "assistant", "content": final})
        return compacted

    @staticmethod
    def _as_list(value) -> list:
        if value is None:
            return []
        if isinstance(value, (str, os.PathLike)):
            return [value]
        if isinstance(value, (list, tuple)):
            for item in value:
                if not isinstance(item, (str, os.PathLike)):
                    raise TypeError(
                        f"metadata['files'] entries must be paths, got {type(item).__name__}"
                    )
            return list(value)
        raise TypeError(
            f"metadata['files'] must be a path or list of paths, got {type(value).__name__}"
        )

    _MAX_COMBINED_ATTACHMENT_BYTES = (
        5 * 1024 * 1024
    )  # 5 MB combined across all attachments

    @classmethod
    def _attachment_part(cls, attachment: str) -> dict:
        path = Path(attachment).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Attachment not found: {path}")

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            raw = path.read_bytes()
        except PermissionError as e:
            raise PermissionError(f"Cannot read attachment: {path}") from e

        log.info("Encoding attachment %s (%s, %s bytes)", path, mime_type, len(raw))

        data_url = f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"

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
        attachments = cls._as_list(metadata.get("files"))
        if not attachments:
            return {"role": "user", "content": message}

        # Validate every path and accumulate size before encoding anything,
        # so a bad path always raises the same clear error regardless of order.
        total_size = 0
        for a in attachments:
            p = Path(a).expanduser()
            if not p.is_file():
                raise FileNotFoundError(f"Attachment not found: {p}")
            try:
                total_size += p.stat().st_size
            except PermissionError as e:
                raise PermissionError(f"Cannot stat attachment: {p}") from e
        if total_size > cls._MAX_COMBINED_ATTACHMENT_BYTES:
            raise ValueError(
                f"Total attachment size {total_size / 1024 / 1024:.1f} MB exceeds "
                f"{cls._MAX_COMBINED_ATTACHMENT_BYTES / 1024 / 1024:.0f} MB combined limit"
            )

        content = [{"type": "text", "text": message}]
        content.extend(cls._attachment_part(str(a)) for a in attachments)
        return {"role": "user", "content": content}

    @staticmethod
    def _build_placeholder_content(message: str, attachments: list) -> str:
        if not attachments:
            return message
        placeholders = " ".join(f"[Attachment: {Path(a).name}]" for a in attachments)
        return f"{message} {placeholders}"

    # --- hooks ---

    async def _on_thinking(self, content: str | None) -> None:
        """Called when the assistant emits text alongside tool calls."""

    async def _check_permission(  # pylint: disable=unused-argument  # hook; subclasses use the args
        self, tool_name: str, tool_args: dict
    ) -> bool:
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

    async def _auto_name(
        self, store, conv_id: int, messages: list[dict], name_runtime_key: str
    ) -> None:
        # Lazy: helper_agent imports Agent (circular import).
        from .helper_agent import HelperAgent  # pylint: disable=import-outside-toplevel

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
                log.info("Auto-named conversation %s: %r", conv_id, name)
        except Exception as e:  # pylint: disable=broad-exception-caught  # naming is best-effort
            log.warning("Auto-naming conversation %s failed: %s", conv_id, e)

    # --- shared tool dispatch ---

    async def handle_tool_call(self, tool_call) -> str:
        """Run one tool call from the model and return its result as text.

        Permission is checked first; a refusal or any error is returned as text
        so the model can react to it.
        """
        # Lazy: tool_calls imports scheduled_tasks, which imports Agent (circular import).
        from .tool_calls import run_tool_async  # pylint: disable=import-outside-toplevel

        tool_name = tool_call.function.name
        try:
            tool_args = json.loads((tool_call.function.arguments or "").strip() or "{}")
            if not await self._check_permission(tool_name, tool_args):
                return (
                    "User denied permission to run this tool. Ask for permission "
                    "to run the tool again if you want to try running it."
                )
            await self._on_tool_start(tool_name, tool_args)
            return await run_tool_async(tool_name=tool_name, tool_args=tool_args)
        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            error_msg = f"Error running tool {tool_name}: {str(e)}"
            log.error(error_msg)
            return error_msg

    # --- shared loop ---

    @staticmethod
    def _usage(chat) -> dict | None:
        usage = getattr(chat, "usage", None)
        return usage.model_dump() if hasattr(usage, "model_dump") else usage

    async def _traced_tool_call(self, tool_call, iteration: int) -> str:
        start = time.perf_counter()
        result = await self.handle_tool_call(tool_call)
        # Record as soon as this call finishes, not after the whole batch, so a
        # hung sibling tool doesn't hold back results that already completed.
        if self.tracer.enabled():
            self.tracer.record(
                "tool_result",
                iteration=iteration,
                tool_call_id=tool_call.id,
                name=tool_call.function.name,
                duration_s=round(time.perf_counter() - start, 3),
                content=result,
            )
        return result

    async def _run_tool_calls(
        self, tool_calls: list, messages: list, iteration: int
    ) -> None:
        """Run ``tool_calls`` in parallel and append (and trace) their results."""
        results = await asyncio.gather(
            *[self._traced_tool_call(tc, iteration) for tc in tool_calls]
        )
        for tc, result in zip(tool_calls, results):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": result,
                }
            )
            log.info("%s...", result[:250])

    async def _loop(self, messages: list, tool_specs: list) -> str:
        try:
            return await self._run_loop(messages, tool_specs)
        except BaseException as e:
            if self.tracer.enabled():
                self.tracer.record("error", error=repr(e))
            raise

    async def _run_loop(self, messages: list, tool_specs: list) -> str:
        iteration = 0
        assistant_message = None

        while iteration < self.max_iterations:
            iteration += 1
            log.info("chat.completions.create...")
            model = runtime.get("model", "deepseek/deepseek-v4.1-flash")
            start = time.perf_counter()
            chat = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_specs,
            )
            duration = time.perf_counter() - start

            tracing = self.tracer.enabled()

            if not chat.choices:
                if tracing:
                    self.tracer.record(
                        "no_choices",
                        iteration=iteration,
                        model=model,
                        duration_s=round(duration, 3),
                    )
                await self._on_no_choices()
                continue

            choice = chat.choices[0]
            assistant_message = choice.message
            finish_reason = getattr(choice, "finish_reason", None)
            if not isinstance(finish_reason, str):
                finish_reason = None

            serialized = self._serialize_assistant_msg(assistant_message)
            messages.append(serialized)
            if tracing:
                self.tracer.record(
                    "llm_response",
                    iteration=iteration,
                    model=model,
                    duration_s=round(duration, 3),
                    finish_reason=finish_reason,
                    usage=self._usage(chat),
                    message=serialized,
                )

            if assistant_message.tool_calls is not None:
                await self._on_thinking(assistant_message.content)
                await self._run_tool_calls(
                    assistant_message.tool_calls, messages, iteration
                )
            else:
                await self._on_response(assistant_message.content)
                if (
                    finish_reason not in ("stop", "length")
                    and finish_reason is not None
                ):
                    log.warning(
                        "Unexpected finish_reason=%r, treating as terminal",
                        finish_reason,
                    )
                break

            if self._should_stop():
                if tracing:
                    self.tracer.record("stopped", iteration=iteration)
                break

        final = (
            assistant_message.content.strip()
            if assistant_message and assistant_message.content
            else ""
        )
        if self.tracer.enabled():
            self.tracer.record("turn_end", iterations=iteration, final=final)
        return final

    @abstractmethod
    async def agent_loop(self, message: str, metadata: dict = None) -> str:
        """Handle one user message end to end and return the final reply."""
