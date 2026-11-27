"""Named groups of tools ("toolsets") that agents can be given.

A toolset is a list of members, each one of:

* a built-in tool name (``"read_file"``),
* another toolset, prefixed with ``@`` (``"@read_only"``),
* every tool of one MCP server, prefixed with ``mcp:`` (``"mcp:nmap"``),
  resolved when the specs are built, since MCP servers connect at startup.

Agents are given toolsets instead of hand-written tool lists, so a new agent
role is a new entry here composed from existing groups, e.g. a security
reviewer could be ``["@read_only", "@web", "mcp:scanner"]``.

This module holds only names (no tool imports); ``tool_calls`` resolves them
against ``tool_registry`` and checks every name when it is imported.
"""

TOOLSETS: dict[str, tuple[str, ...]] = {
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
