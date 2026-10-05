from ..infra.app_logging import log
from ..core.tool import Tool

from ddgs import DDGS


class WebSearchText(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "websearch_text",
                "description": "Web text search for text search queries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "text search query"}
                    },
                },
            },
        }

    @staticmethod
    def call(query: str, max_results: int = 10) -> list[dict[str, str]]:
        log.info(f"WebSearchText: {query} {max_results}")

        try:
            ddgs = DDGS()
            results = ddgs.text(
                query, max_results=max_results, safesearch="off", timelimit="y"
            )
            return results

        except Exception as e:
            log.error(f"Error performing web search: {e}")
            return [{"error": f"Error performing web search: {e}"}]


class WebSearchImages(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "websearch_images",
                "description": "Web image search for image search queries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "image search query"}
                    },
                },
            },
        }

    @staticmethod
    def call(query: str, max_results: int = 10) -> list[dict[str, str]]:
        log.info(f"WebSearchImages: {query} {max_results}")

        try:
            ddgs = DDGS()
            results = ddgs.images(query, max_results=max_results, safesearch="off")
            return results

        except Exception as e:
            log.error(f"Error performing web image search: {e}")
            return [{"error": f"Error performing web image search: {e}"}]


class WebSearchVideos(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "websearch_videos",
                "description": "Web search for video search queries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "video search query"}
                    },
                },
            },
        }

    @staticmethod
    def call(query: str, max_results: int = 10) -> list[dict[str, str]]:
        log.info(f"WebSearchVideos: {query} {max_results}")

        try:
            ddgs = DDGS()
            results = ddgs.videos(
                query, max_results=max_results, safesearch="off", timelimit="y"
            )
            return results

        except Exception as e:
            log.error(f"Error performing web video search: {e}")
            return [{"error": f"Error performing web video search: {e}"}]


class WebSearchNews(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "websearch_news",
                "description": "Web search for news search queries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "news search query"}
                    },
                },
            },
        }

    @staticmethod
    def call(query: str, max_results: int = 10) -> list[dict[str, str]]:
        log.info(f"WebSearchNews: {query} {max_results}")

        try:
            ddgs = DDGS()
            results = ddgs.news(
                query, max_results=max_results, safesearch="off", timelimit="y"
            )
            return results

        except Exception as e:
            log.error(f"Error performing web news search: {e}")
            return [{"error": f"Error performing web news search: {e}"}]


class WebSearchBooks(Tool):
    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "websearch_books",
                "description": "Web search for book search queries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "book search query"}
                    },
                },
            },
        }

    @staticmethod
    def call(query: str, max_results: int = 10) -> list[dict[str, str]]:
        log.info(f"WebSearchBooks: {query} {max_results}")

        try:
            ddgs = DDGS()
            results = ddgs.books(query, max_results=max_results)
            return results

        except Exception as e:
            log.error(f"Error performing web book search: {e}")
            return [{"error": f"Error performing web book search: {e}"}]
