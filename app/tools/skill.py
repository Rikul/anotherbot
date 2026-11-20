"""``use_skill`` tool: load a skill's instructions (see ``app/infra/skills.py``)."""

from ..core.tool import Tool
from ..infra import skills
from ..infra.app_logging import log


class UseSkillTool(Tool):
    """Return a skill's full instructions plus the files that come with it."""

    read_only = True

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "use_skill",
                "description": (
                    "Load a skill: step-by-step instructions for a kind of task "
                    "(the Skills section of the system prompt lists them). Call it "
                    "before starting a task a skill covers, then follow it. Returns "
                    "the instructions, the skill's directory and its extra files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The skill's name, e.g. 'browser'.",
                        }
                    },
                    "required": ["name"],
                },
            },
        }

    @staticmethod
    def call(name: str) -> str:
        """Return the skill's text, or an error naming the available skills."""
        log.info("use_skill: %s", name)
        try:
            return skills.load_skill(str(name))
        except KeyError:
            available = ", ".join(skills.discover_skills()) or "none"
            return f"Error: no skill named {name!r}. Available skills: {available}"
        except (OSError, UnicodeDecodeError, ValueError) as e:
            return f"Error: could not read skill {name!r}: {e}"
