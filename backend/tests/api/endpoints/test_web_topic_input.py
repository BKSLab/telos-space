"""HTTP and model/MCP topic schemas share the provider query contract."""

# ruff: noqa: F811 -- pytest injects the re-exported fixture by name.

from dataclasses import replace

import pytest
from pydantic import ValidationError

from src.schemas.agent_context import WebTopicInput
from tests.api.endpoints.test_web_research_api import web_api as web_api


@pytest.mark.parametrize(
    "topic", ["   ", "public\x00topic", "public\ntopic", "word " * 76, "x" * 601]
)
async def test_http_and_model_reject_the_same_invalid_topics_before_service(web_api, topic):
    client, service, state = web_api
    state.principal = replace(state.principal, via_api_token=False)
    response = await client.post("/api/v1/projects/3/web-research/topics", json={"query": topic})
    assert response.status_code == 422
    service.approve_topic.assert_not_awaited()
    with pytest.raises(ValidationError):
        WebTopicInput(public_topic=topic)


@pytest.mark.parametrize("topic", [" ".join(f"word{i:02}" for i in range(75)), "x" * 600])
def test_model_topic_accepts_shared_boundaries_without_truncating_human_payload(topic):
    value = WebTopicInput(public_topic=topic)
    assert value.model_dump() == {"public_topic": topic}
