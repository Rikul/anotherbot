from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastmcp import Client
from fastmcp.client import StdioTransport

from ..infra.app_logging import log
from ..infra.helpers import trunc_str_with_ellipsis
from .tool_calls import MAX_TOOL_RESULT_LENGTH


class MCPManager:
    """Owns persistent FastMCP client connections and their tool catalogs."""

    _SEP = "__"

    def __init__(self) -> None:
        self._clients: dict[str, Client] = {}
        self._specs: dict[str, dict] = {}
        self._server_configs: dict[str, dict] = {}
        self._config_path: Path | None = None

    async def initialize(self, mcp_servers: dict[str, dict], config_path: Path | None = None) -> None:
        self._server_configs = dict(mcp_servers)
        self._config_path = config_path
        await asyncio.gather(*(
            self._connect_server(n, c)
            for n, c in mcp_servers.items()
            if not c.get("disabled")
        ))

    async def _connect_server(self, name: str, cfg: dict) -> None:
        if self._SEP in name:
            log.error(f"MCP server '{name}': invalid name — must not contain '{self._SEP}'.")
            return
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
        command = cfg["command"]
        args = cfg.get("args", [])
        env = cfg.get("env")
        cwd = cfg.get("cwd")
        return Client(StdioTransport(command=command, args=args, env=env, cwd=cwd))

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

    def get_server_status(self) -> list[dict]:
        return [self._status_for(name) for name in self._server_configs]

    def _status_for(self, name: str) -> dict:
        cfg = self._server_configs[name]
        prefix = f"{name}{self._SEP}"
        return {
            "name": name,
            "connected": name in self._clients,
            "disabled": bool(cfg.get("disabled")),
            "transport": "url" if "url" in cfg else "stdio",
            "target": cfg.get("url") or cfg.get("command", "?"),
            "tool_count": sum(1 for k in self._specs if k.startswith(prefix)),
        }

    async def enable_server(self, name: str) -> dict:
        """Connect a configured server at runtime and clear its disabled flag."""
        cfg = self._server_configs.get(name)
        if cfg is None:
            raise ValueError(f"Unknown MCP server '{name}'")
        cfg.pop("disabled", None)
        if name not in self._clients:
            await self._connect_server(name, cfg)
        self._persist_disabled(name, False)
        return self._status_for(name)

    async def disable_server(self, name: str) -> dict:
        """Disconnect a server at runtime, drop its tools, and set its disabled flag."""
        cfg = self._server_configs.get(name)
        if cfg is None:
            raise ValueError(f"Unknown MCP server '{name}'")
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception as e:
                log.warning(f"MCP server '{name}': error during disconnect — {e}")
        prefix = f"{name}{self._SEP}"
        for key in [k for k in self._specs if k.startswith(prefix)]:
            del self._specs[key]
        cfg["disabled"] = True
        self._persist_disabled(name, True)
        return self._status_for(name)

    def _persist_disabled(self, name: str, disabled: bool) -> None:
        if self._config_path is None:
            return
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = json.load(f)
            servers = data.get("mcpServers")
            if not isinstance(servers, dict) or name not in servers:
                return
            if disabled:
                servers[name]["disabled"] = True
            else:
                servers[name].pop("disabled", None)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
        except Exception as e:
            log.warning(f"Failed to persist MCP config for '{name}': {e}")

    def get_tools_for_server(self, server_name: str) -> list[dict]:
        prefix = f"{server_name}{self._SEP}"
        return [spec for k, spec in self._specs.items() if k.startswith(prefix)]

    def is_mcp_tool(self, tool_name: str) -> bool:
        return tool_name in self._specs

    async def call_tool(self, tool_name: str, tool_args: dict) -> str:
        server_name, sep, bare_name = tool_name.partition(self._SEP)
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
        self._server_configs.clear()


mcp_manager = MCPManager()
