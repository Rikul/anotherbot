"""DuckDuckGo search tools (via ``ddgs``): text, images, videos, news and books."""

from ddgs import DDGS

from ..core.tool import Tool
from ..infra.app_logging import log


class WebSearchText(Tool):
    """Web search: return up to ``max_results`` text results."""

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
        """Return text results (past year, safe search off); errors as ``[{"error": ...}]``."""
        log.info("WebSearchText: %s %s", query, max_results)

        try:
            ddgs = DDGS()
            results = ddgs.text(
                query, max_results=max_results, safesearch="off", timelimit="y"
            )
            return results

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error performing web search: %s", e)
            return [{"error": f"Error performing web search: {e}"}]


class WebSearchImages(Tool):
    """Image search: return up to ``max_results`` image results."""

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
        """Return image results; errors as ``[{"error": ...}]``."""
        log.info("WebSearchImages: %s %s", query, max_results)

        try:
            ddgs = DDGS()
            results = ddgs.images(query, max_results=max_results, safesearch="off")
            return results

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error performing web image search: %s", e)
            return [{"error": f"Error performing web image search: {e}"}]


class WebSearchVideos(Tool):
    """Video search: return up to ``max_results`` video results."""

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
        """Return video results; errors as ``[{"error": ...}]``."""
        log.info("WebSearchVideos: %s %s", query, max_results)

        try:
            ddgs = DDGS()
            results = ddgs.videos(
                query, max_results=max_results, safesearch="off", timelimit="y"
            )
            return results

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error performing web video search: %s", e)
            return [{"error": f"Error performing web video search: {e}"}]


class WebSearchNews(Tool):
    """News search: return up to ``max_results`` news results."""

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
        """Return news results; errors as ``[{"error": ...}]``."""
        log.info("WebSearchNews: %s %s", query, max_results)

        try:
            ddgs = DDGS()
            results = ddgs.news(
                query, max_results=max_results, safesearch="off", timelimit="y"
            )
            return results

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error performing web news search: %s", e)
            return [{"error": f"Error performing web news search: {e}"}]


class WebSearchBooks(Tool):
    """Book search: return up to ``max_results`` book results."""

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
        """Return book results; errors as ``[{"error": ...}]``."""
        log.info("WebSearchBooks: %s %s", query, max_results)

        try:
            ddgs = DDGS()
            results = ddgs.books(query, max_results=max_results)
            return results

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error performing web book search: %s", e)
            return [{"error": f"Error performing web book search: {e}"}]
