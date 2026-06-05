from __future__ import annotations

import asyncio
import json

from fastmcp import Client

from ..infra.app_logging import log
from ..infra.helpers import trunc_str_with_ellipsis
from .tool_calls import MAX_TOOL_RESULT_LENGTH


class MCPManager:
    """Owns persistent FastMCP client connections and their tool catalogs."""

    _SEP = "__"

    def __init__(self) -> None:
        self._clients: dict[str, Client] = {}
        self._specs: dict[str, dict] = {}

    async def initialize(self, mcp_servers: dict[str, dict]) -> None:
        await asyncio.gather(*(self._connect_server(n, c) for n, c in mcp_servers.items()))

    async def _connect_server(self, name: str, cfg: dict) -> None:
        try:
            client = self._build_client(cfg)
        except Exception as e:
            log.error(f"MCP server '{name}': invalid config — {e}")
            return
        try:
            await client.__aenter__()
        except Exception as e:
            log.error(f"MCP server '{name}': failed to connect — {e}")
            return
        try:
            tools = await client.list_tools()
            for tool in tools:
                namespaced = f"{name}{self._SEP}{tool.name}"
                self._specs[namespaced] = self._to_openai_spec(namespaced, tool)
            self._clients[name] = client
            log.info(f"MCP server '{name}': connected, {len(tools)} tool(s) discovered")
        except Exception as e:
            log.error(f"MCP server '{name}': failed to initialize — {e}")
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass

    def _build_client(self, cfg: dict) -> Client:
        if "url" in cfg:
            return Client(cfg["url"])
        spec: dict = {"command": cfg["command"]}
        if "args" in cfg:
            spec["args"] = cfg["args"]
        if "env" in cfg:
            spec["env"] = cfg["env"]
        return Client(spec)

    def _to_openai_spec(self, namespaced_name: str, tool) -> dict:
        return {
            "type": "function",
            "function": {
                "name": namespaced_name,
                "description": tool.description or "",
                "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            },
        }

    def get_tool_specs(self) -> list[dict]:
        return list(self._specs.values())

    def is_mcp_tool(self, tool_name: str) -> bool:
        return tool_name in self._specs

    async def call_tool(self, tool_name: str, tool_args: dict) -> str:
        server_name, sep, bare_name = tool_name.rpartition(self._SEP)
        if not sep:
            return f"Error: MCP tool '{tool_name}' is not namespaced with '{self._SEP}'."
        client = self._clients.get(server_name)
        if client is None:
            return f"Error: MCP server '{server_name}' is not connected."
        try:
            result = await client.call_tool(bare_name, tool_args)
            return self._result_to_str(result)
        except Exception as e:
            log.error(f"MCP tool call failed [{tool_name}]: {e}")
            return f"Error calling MCP tool {tool_name}: {e}"

    def _result_to_str(self, result) -> str:
        try:
            content = getattr(result, "content", None)
            if content:
                texts = [getattr(part, "text", None) for part in content]
                texts = [t for t in texts if t is not None]
                if texts:
                    return trunc_str_with_ellipsis(MAX_TOOL_RESULT_LENGTH, "\n".join(map(str, texts)))

            data = getattr(result, "data", None)
            if data is not None:
                return trunc_str_with_ellipsis(MAX_TOOL_RESULT_LENGTH, json.dumps(data))
        except Exception:
            pass
        return trunc_str_with_ellipsis(MAX_TOOL_RESULT_LENGTH, str(result))

    async def shutdown(self) -> None:
        for name, client in self._clients.items():
            try:
                await client.__aexit__(None, None, None)
                log.info(f"MCP server '{name}': disconnected")
            except Exception as e:
                log.warning(f"MCP server '{name}': error during shutdown — {e}")
        self._clients.clear()
        self._specs.clear()


mcp_manager = MCPManager()
