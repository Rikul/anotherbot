from ..infra.app_logging import log
import pathlib
from ..core.tool import Tool


class GetSkillsDirTool(Tool):
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
        log.info("get_skills_dir")

        skills_dir = pathlib.Path(__file__).parent.parent / "skills"
        return str(skills_dir.resolve())
