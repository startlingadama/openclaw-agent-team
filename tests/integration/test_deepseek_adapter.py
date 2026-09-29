import asyncio
import json

import httpx
import pytest

from openclaw.domain.agents.decision import CallTool, Finish
from openclaw.domain.shared.errors import AuthenticationError, LLMError, ValidationError
from openclaw.infrastructure.llm.deepseek import DeepSeekAdapter, DeepSeekConfig
from tests.unit.test_deepseek_prompt import make_context


def completion(message, finish_reason="stop"):
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def finish_message(answer="ok"):
    call = {"function": {"name": "finish", "arguments": json.dumps({"answer": answer})}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


class Server:
    """Scripted DeepSeek: one response (or exception) per request, requests recorded."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_adapter(server, **config):
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    config = DeepSeekConfig(api_key="sk-secret", **config)
    return DeepSeekAdapter(config, client, sleep), sleeps


def decide(adapter):
    return asyncio.run(adapter.decide(make_context()))


def ok(message=None):
    return httpx.Response(200, json=completion(message or finish_message()))


def test_request_shape_and_authentication_header():
    server = Server(ok())
    adapter, _ = make_adapter(server, model="deepseek-chat")
    assert decide(adapter) == Finish("ok")

    (request,) = server.requests
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-secret"
    body = json.loads(request.content)
    assert body["model"] == "deepseek-chat" and body["tool_choice"] == "required"
    assert body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "You are the GitHub agent." in body["messages"][0]["content"]
    assert "find TODOs" in body["messages"][1]["content"]
    assert [t["function"]["name"] for t in body["tools"]] == [
        "finish",
        "use_skill",
        "memory_update",
    ]


def test_tool_call_is_mapped_back_to_the_domain_tool_name():
    call = {"function": {"name": "memory_update", "arguments": '{"section": "S", "content": "c"}'}}
    adapter, _ = make_adapter(Server(ok({"tool_calls": [call]})))
    decision = decide(adapter)
    assert isinstance(decision, CallTool) and decision.call.name == "memory.update"
    assert decision.call.arguments == {"section": "S", "content": "c"}


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_errors_are_not_retried_and_do_not_leak_the_key(status):
    server = Server(httpx.Response(status, json={"error": {"message": "Authentication Fails"}}))
    adapter, sleeps = make_adapter(server)
    with pytest.raises(AuthenticationError) as info:
        decide(adapter)
    assert "sk-secret" not in str(info.value) and "Authentication Fails" in str(info.value)
    assert len(server.requests) == 1 and sleeps == []


def test_rate_limit_is_retried_with_exponential_backoff_then_succeeds():
    server = Server(httpx.Response(429), httpx.Response(503), ok())
    adapter, sleeps = make_adapter(server, backoff=0.5, max_retries=2)
    assert decide(adapter) == Finish("ok")
    assert len(server.requests) == 3 and sleeps == [0.5, 1.0]


def test_retries_are_bounded():
    server = Server(httpx.Response(500), httpx.Response(500), httpx.Response(500))
    adapter, sleeps = make_adapter(server, max_retries=2)
    with pytest.raises(LLMError) as info:
        decide(adapter)
    assert info.value.retryable and "HTTP 500" in str(info.value)
    assert len(server.requests) == 3 and len(sleeps) == 2


def test_max_retries_zero_disables_retries():
    server = Server(httpx.Response(500))
    adapter, sleeps = make_adapter(server, max_retries=0)
    with pytest.raises(LLMError):
        decide(adapter)
    assert len(server.requests) == 1 and sleeps == []


def test_timeouts_and_network_errors_are_retryable():
    server = Server(httpx.ReadTimeout("slow"), httpx.ConnectError("down"), ok())
    adapter, sleeps = make_adapter(server)
    assert decide(adapter) == Finish("ok")
    assert len(sleeps) == 2


def test_client_errors_are_not_retried():
    server = Server(httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}))
    adapter, _ = make_adapter(server)
    with pytest.raises(LLMError) as info:
        decide(adapter)
    assert not info.value.retryable and "Insufficient Balance" in str(info.value)
    assert len(server.requests) == 1


def test_malformed_model_output_is_asked_again():
    bad = {"tool_calls": [{"function": {"name": "finish", "arguments": "{oops"}}]}
    server = Server(ok(bad), ok())
    adapter, sleeps = make_adapter(server)
    assert decide(adapter) == Finish("ok")
    assert len(server.requests) == 2 and len(sleeps) == 1


def test_unreadable_http_body_is_a_retryable_error():
    server = Server(httpx.Response(200, text="<html>"), httpx.Response(200, json={"nope": 1}))
    adapter, _ = make_adapter(server, max_retries=1)
    with pytest.raises(LLMError) as info:
        decide(adapter)
    assert info.value.retryable and len(server.requests) == 2


def test_truncated_answer_is_reported_and_not_retried():
    server = Server(httpx.Response(200, json=completion(finish_message(), "length")))
    adapter, _ = make_adapter(server, max_tokens=50)
    with pytest.raises(LLMError, match="max_tokens=50") as info:
        decide(adapter)
    assert not info.value.retryable and len(server.requests) == 1


def test_config_from_env_and_validation():
    config = DeepSeekConfig.from_env(
        {"DEEPSEEK_API_KEY": "k", "DEEPSEEK_MODEL": "", "DEEPSEEK_BASE_URL": "http://localhost:1/"}
    )
    assert config.model == "deepseek-chat" and config.base_url == "http://localhost:1/"
    with pytest.raises(AuthenticationError):
        DeepSeekConfig.from_env({})
    with pytest.raises(ValidationError, match="tool calling"):
        DeepSeekConfig(api_key="k", model="deepseek-reasoner")


def test_base_url_trailing_slash_is_handled():
    server = Server(ok())
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    adapter = DeepSeekAdapter(DeepSeekConfig("k", base_url="http://x.test/v1/"), client)
    asyncio.run(adapter.decide(make_context()))
    assert str(server.requests[0].url) == "http://x.test/v1/chat/completions"


def test_adapter_drives_a_full_react_loop_through_the_runtime():
    """Real AgentRuntime + DeepSeekAdapter (scripted HTTP): tool call, observation, final answer."""
    from openclaw.domain.messages.model import AgentMessage
    from openclaw.domain.tasks.execution import ExecutionStatus
    from tests.unit.fakes import Harness, make_agent

    search = {"function": {"name": "search", "arguments": '{"q": "TODO"}'}}
    server = Server(ok({"tool_calls": [search]}), ok(finish_message("found 3 TODOs")))
    adapter, _ = make_adapter(server)
    harness = Harness([])
    harness.tools.add("search")
    harness.runtime._llm = adapter  # the scripted LLM is replaced by the real adapter

    agent = make_agent(allowed=["search"])
    result = asyncio.run(
        harness.runtime.run(agent, AgentMessage("user", "test", "t1", "find TODOs"))
    )

    assert result.status is ExecutionStatus.COMPLETED and result.answer == "found 3 TODOs"
    assert result.steps == 2 and len(server.requests) == 2
    second_prompt = json.loads(server.requests[1].content)["messages"][1]["content"]
    assert "tool_result (search)" in second_prompt  # the observation reached the next step



def test_final_step_sends_only_the_finish_function_and_accepts_the_answer():
    from dataclasses import replace

    server = Server(ok(finish_message("what I found so far")))
    adapter, _ = make_adapter(server)
    result = asyncio.run(adapter.decide(replace(make_context(), final_step=True)))
    assert result == Finish("what I found so far")

    (request,) = server.requests
    body = json.loads(request.content)
    assert [t["function"]["name"] for t in body["tools"]] == ["finish"]
    assert body["tool_choice"] == "required"
    assert "Step limit reached" in body["messages"][0]["content"]
