"""Base class for built-in tools the agent can call."""

from abc import ABC, abstractmethod
from typing import Any, Callable

# Tool results (built-in and MCP) are truncated to this many characters.
MAX_TOOL_RESULT_LENGTH = 16000


class Tool(ABC):  # pylint: disable=too-few-public-methods  # interface: spec() + call
    """A tool the LLM can call: an OpenAI function schema plus its implementation.

    Subclasses are registered by name in ``tool_calls.tool_registry`` and must
    define:

    * ``spec()`` — the OpenAI function-calling schema.
    * ``call(**params)`` — a staticmethod (sync or async) taking the parameters
      declared in ``spec()`` and returning the result; non-string results are
      JSON-encoded by ``run_tool``.

    ``call`` signatures differ per tool, so it is declared as an attribute
    rather than an abstract method, and checked when a subclass is defined.
    """

    call: Callable[..., Any]

    # Synchronous ``call``s run in a worker thread so they can't block the
    # event loop. Tools that only touch shared in-memory state (the todo list)
    # set this to False: they are instant, and running them on the loop keeps
    # parallel calls from racing on that state.
    runs_in_thread: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not callable(getattr(cls, "call", None)):
            raise TypeError(f"Tool subclass {cls.__name__} must define call()")

    @staticmethod
    @abstractmethod
    def spec() -> dict:
        """Return the OpenAI function-calling schema for this tool.

        Shape: ``{"type": "function", "function": {"name": ..., "parameters": ...}}``.
        """
