"""``get_skills_dir`` tool: where the agent's skill files live."""

import pathlib

from ..core.tool import Tool
from ..infra.app_logging import log


class GetSkillsDirTool(Tool):
    """Return the path of the ``app/skills`` directory so the agent can read skill files."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "get_skills_dir",
                "description": "Get the path to the skills directory",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def call() -> str:
        """Return the absolute path of ``app/skills``."""
        log.info("get_skills_dir")

        skills_dir = pathlib.Path(__file__).parent.parent / "skills"
        return str(skills_dir.resolve())
