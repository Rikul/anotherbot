import asyncio
import os

from ..infra.app_logging import log
from ..infra.helpers import trunc_str_with_ellipsis
from ..tools.read_file import ReadFileTool
from ..tools.write_file import WriteFileTool
from ..tools.bash import BashTool
from ..tools.web_fetch import WebFetchTool
from ..tools.get_skills_dir import GetSkillsDirTool
from ..tools.todo import TodoAddTool, TodoListTool, TodoClearTool, TodoUpdateTool
from ..tools.calculator import CalculatorTool
from ..tools.hackernews import HackerNewsTool

from ..tools.web_search import WebSearchText
from ..tools.web_search import WebSearchImages
from ..tools.web_search import WebSearchVideos
from ..tools.web_search import WebSearchNews
from ..tools.web_search import WebSearchBooks

from ..tools.sched_tasks_tool import ListScheduledTasks, AddScheduledTask, UpdateScheduledTask, \
                        RemoveScheduledTask, GetScheduledTaskOutput

from ..tools.get_city_state import GetCityState
from ..tools.get_datetime import GetDateTime
from ..tools.helper_agent import HelperAgentTool

import json

tool_registry = {
    "read_file": ReadFileTool,
    "write_file": WriteFileTool,
    "bash": BashTool,
    "web_fetch": WebFetchTool,
    "get_skills_dir": GetSkillsDirTool,
    
    "todo_add": TodoAddTool,
    "todo_list": TodoListTool,
    "todo_update": TodoUpdateTool,
    "todo_clear": TodoClearTool,
    
    "calculator": CalculatorTool,
    "hackernews": HackerNewsTool,

    "websearch_text": WebSearchText,
    "websearch_images": WebSearchImages,
    "websearch_videos": WebSearchVideos,
    "websearch_news": WebSearchNews,
    "websearch_books": WebSearchBooks,

    "list_scheduled_tasks": ListScheduledTasks,
    "add_scheduled_task": AddScheduledTask,
    "update_scheduled_task": UpdateScheduledTask,
    "remove_scheduled_task": RemoveScheduledTask,
    "get_scheduled_task_output": GetScheduledTaskOutput,

    "get_city_state": GetCityState,
    "get_datetime": GetDateTime,
    "helper_agent": HelperAgentTool
}

all_tool_specs = [tool.spec() for tool in tool_registry.values()]
_HELPER_AGENT_TOOLS = {"read_file", "write_file", "bash", "web_fetch", "get_skills_dir", "calculator", "hackernews",
                        "websearch_text", "websearch_images", "websearch_videos", "websearch_news", "websearch_books",
                        "get_city_state", "get_datetime", "list_scheduled_tasks", "get_scheduled_task_output"}

helper_tool_specs = [tool.spec() for k, tool in tool_registry.items() if k in _HELPER_AGENT_TOOLS]

MAX_TOOL_RESULT_LENGTH = 16000

async def run_tool(tool_name: str, tool_args: dict) -> str:
    original_cwd = os.getcwd()
    
    try:
        func = tool_registry[tool_name].call
        result = func(**tool_args)
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as e:
        log.error(f"Error running tool {tool_name}: {str(e)}")
        result = f"Error running tool {tool_name}: {str(e)}"
    finally:
        os.chdir(original_cwd)
    
    if not isinstance(result, str):
        result = json.dumps(result)
    
    return trunc_str_with_ellipsis(MAX_TOOL_RESULT_LENGTH, result)
    