"""``get_city_state`` tool: approximate location from the machine's IP."""

import geocoder

from ..core.tool import Tool
from ..infra.app_logging import log


class GetCityState(Tool):
    """Return the city and state this machine's public IP geolocates to."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "get_city_state",
                "description": "Get current city and state based on IP address",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def call() -> str:
        """Return ``"City, State"``, or ``"Unknown location"`` if the lookup fails."""
        log.info("get_city_state")
        g = geocoder.ip("me")

        return f"{g.city}, {g.state}" if g.ok else "Unknown location"
