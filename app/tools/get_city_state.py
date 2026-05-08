from ..infra.app_logging import log
from ..core.tool import Tool
import geocoder

class GetCityState(Tool):

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "get_city_state",
                "description": "Get current city and state based on IP address",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        }

    @staticmethod
    def call() -> str:
        log.info("get_city_state")
        g = geocoder.ip('me')

        return f"{g.city}, {g.state}" if g.ok else "Unknown location"