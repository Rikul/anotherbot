"""Long-term memory tools: save, search and delete facts about the user."""

from ..core.tool import Tool
from ..infra.app_logging import log
from ..infra.memory_store import MAX_MEMORY_LENGTH, MemoryStore, format_memory

_CATEGORIES = ["preference", "personal", "work", "project", "other"]


class SaveMemoryTool(Tool):
    """Store an important fact about the user, or replace an existing one."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": (
                    "Save an important, lasting fact about the user to long-term memory "
                    "(kept across conversations). One short, self-contained fact per call. "
                    "Pass memory_id to replace an outdated memory instead of adding a "
                    "duplicate. Never store secrets such as passwords or API keys."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": (
                                "The fact, written in third person, e.g. 'User prefers "
                                f"metric units'. Max {MAX_MEMORY_LENGTH} characters."
                            ),
                        },
                        "category": {
                            "type": "string",
                            "enum": _CATEGORIES,
                            "description": "Kind of memory.",
                        },
                        "memory_id": {
                            "type": "integer",
                            "description": "ID of an existing memory to replace.",
                        },
                    },
                    "required": ["content"],
                },
            },
        }

    @staticmethod
    def call(content: str, category: str = None, memory_id: int = None) -> str:
        """Save the memory and report its ID and whether it was new."""
        log.info("save_memory called (id=%s, category=%s)", memory_id, category)
        try:
            memory = MemoryStore().save(content, category, memory_id)
        except ValueError as e:
            return f"Memory not saved: {e}"
        status = memory["status"]
        if status == "duplicate":
            return f"Already remembered: {format_memory(memory)}"
        return f"Memory {status}: {format_memory(memory)}"


class SearchMemoryTool(Tool):
    """Find stored memories about the user."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": (
                    "Search long-term memory for facts about the user saved in earlier "
                    "conversations. Matches on words, best match first. An empty query "
                    "lists the most recent memories."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Keywords to look for, e.g. 'diet allergies'.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default 10, max 100).",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    @staticmethod
    def call(query: str, limit: int = 10) -> str:
        """Return matching memories, one per line."""
        log.info("search_memory called: %r", query)
        memories = MemoryStore().search(query, limit)
        if not memories:
            return "No matching memories."
        return "\n".join(format_memory(m) for m in memories)


class DeleteMemoryTool(Tool):
    """Remove a memory that is wrong, outdated, or the user asked to forget."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "delete_memory",
                "description": (
                    "Delete a long-term memory by ID, when it is wrong or outdated or the "
                    "user asks you to forget it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "integer",
                            "description": "ID of the memory to delete.",
                        },
                    },
                    "required": ["memory_id"],
                },
            },
        }

    @staticmethod
    def call(memory_id: int) -> str:
        """Delete the memory and echo what was removed."""
        log.info("delete_memory called: %s", memory_id)
        memory = MemoryStore().delete(memory_id)
        if memory is None:
            return f"No memory with id {memory_id}."
        return f"Deleted memory: {format_memory(memory)}"
