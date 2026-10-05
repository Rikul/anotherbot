from ..infra.app_logging import log
from ..core.tool import Tool

import httpx
import json


class HackerNewsTool(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "hackernews",
                "description": "Hacker News stores from https://news.ycombinator.com/. Fetches the top stories from Hacker News",
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
        log.info(f"hackernews: number_of_stories: {number_of_stories}")

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

        except Exception as e:
            log.error(f"Error getting hackernews stories: {e}")
            return f"Error getting hackernews stories: {e}"
