import pytest
from unittest.mock import patch, AsyncMock
from app.tools.helper_agent import HelperAgentTool


def test_helper_agent_tool_spec():
    spec = HelperAgentTool.spec()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "helper_agent"
    assert "prompt" in spec["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_helper_agent_tool_call():
    mock_helper = AsyncMock()
    mock_helper.run = AsyncMock(return_value="delegated result")

    with patch("app.core.helper_agent.HelperAgent", return_value=mock_helper) as MockHelperAgent:
        result = await HelperAgentTool.call(prompt="do something", system_prompt="be helpful")
        
        MockHelperAgent.assert_called_once_with(system_prompt="be helpful")
        mock_helper.run.assert_called_once_with("do something")
        assert result == "delegated result"
