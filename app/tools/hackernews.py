"""``hackernews`` tool: top Hacker News stories."""

import json

import httpx

from ..core.tool import Tool
from ..infra.app_logging import log


class HackerNewsTool(Tool):
    """Fetch the top N Hacker News stories from the HN API and return them as JSON items."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "hackernews",
                "description": (
                    "Hacker News stores from https://news.ycombinator.com/. Fetches the top "
                    "stories from Hacker News"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "number_of_stories": {
                            "type": "integer",
                            "description": "The number of top stories to fetch (default is 10)",
                        }
                    },
                },
            },
        }

    @staticmethod
    def call(number_of_stories: int = 10) -> str:
        """Return the top ``number_of_stories`` stories as a JSON array (errors as text)."""
        log.info("hackernews: number_of_stories: %s", number_of_stories)

        try:
            response = httpx.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json"
            )
            story_ids = response.json()

            # Fetch story details
            stories = []
            for story_id in story_ids[:number_of_stories]:
                story_response = httpx.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                )
                story = story_response.json()
                if story is None:
                    continue
                story["username"] = story.get("by", "unknown")
                stories.append(story)
            return json.dumps(stories)

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error getting hackernews stories: %s", e)
            return f"Error getting hackernews stories: {e}"
