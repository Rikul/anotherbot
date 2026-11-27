"""Named groups of tools ("toolsets") that agents can be given.

A toolset is a list of members, each one of:

* a built-in tool name (``"read_file"``),
* another toolset, prefixed with ``@`` (``"@read_only"``),
* every tool of one MCP server, prefixed with ``mcp:`` (``"mcp:nmap"``),
  resolved when the specs are built, since MCP servers connect at startup,
* ``"*"`` (every built-in tool) or ``"mcp:*"`` (every MCP server's tools).

Agents are given toolsets instead of hand-written tool lists, so a new agent
role is a new entry here composed from existing groups, e.g. a security
reviewer could be ``["@read_only", "@web", "mcp:scanner"]``.

This module holds only names (no tool imports); ``tool_calls`` resolves them
against ``tool_registry`` and checks every name when it is imported.
"""

TOOLSETS: dict[str, tuple[str, ...]] = {
    # Every built-in and MCP tool: the default for the CLI and the bot.
    "all": ("*", "mcp:*"),
    # Read local files, directories and git, run nothing: no permission prompt
    # in the CLI. Must match the tools marked ``read_only`` (checked in tests).
    "read_only": (
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "git_status",
        "git_diff",
        "git_log",
        "use_skill",
    ),
    "files": ("@read_only", "write_file"),
    "shell": ("bash",),
    "web": (
        "web_fetch",
        "websearch_text",
        "websearch_images",
        "websearch_videos",
        "websearch_news",
        "websearch_books",
        "hackernews",
    ),
    "utility": ("calculator", "get_datetime", "get_city_state"),
    "todo": ("todo_add", "todo_list", "todo_update", "todo_clear"),
    "memory_read": ("search_memory",),
    "memory": ("@memory_read", "save_memory", "delete_memory"),
    "schedule_read": ("list_scheduled_tasks", "get_scheduled_task_output"),
    "schedule": (
        "@schedule_read",
        "add_scheduled_task",
        "update_scheduled_task",
        "remove_scheduled_task",
    ),
    "delegate": ("helper_agent",),
    # HelperAgent (scheduled tasks, helper_agent sub-tasks): no scheduled-task
    # changes, no delegation (recursion), memories read-only, no MCP tools.
    "helper": (
        "@files",
        "@shell",
        "@web",
        "@utility",
        "@schedule_read",
        "@memory_read",
    ),
}

# Tool modes the CLI and bot agents switch between with /default, /yolo and
# /readonly (any toolsets can be chosen with /toolset).
AGENT_MODES: dict[str, tuple[str, ...]] = {
    # Every tool; the CLI asks before running one (unless started with -y).
    "default": ("all",),
    # Every tool and no permission prompts, like -y (bots never ask anyway).
    "yolo": ("all",),
    # Look, don't touch: read files and git, plan with todos, read memories
    # and scheduled tasks. No writes, shell, web or MCP tools.
    "readonly": ("read_only", "utility", "todo", "memory_read", "schedule_read"),
}
# Modes that turn the CLI's permission prompts off; every other mode turns
# them back on.
AUTO_APPROVE_MODES = frozenset({"yolo"})
DEFAULT_MODE = "default"


def mode_name(toolsets: tuple[str, ...], auto_approve: bool = False) -> str:
    """The mode these toolsets (and prompt setting) are, or the toolsets
    joined by '+'."""
    toolsets = tuple(toolsets)
    for name, sets in AGENT_MODES.items():
        if toolsets == sets and auto_approve == (name in AUTO_APPROVE_MODES):
            return name
    for name, sets in AGENT_MODES.items():
        if toolsets == sets and name not in AUTO_APPROVE_MODES:
            return name
    return "+".join(toolsets)
