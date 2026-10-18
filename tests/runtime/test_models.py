"""不访问模型服务：验证真实适配器的消息、工具协议与工作流边界。"""

import builtins
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from openai import OpenAI
from pydantic import ValidationError

from repopilot.agents.models import make_model
from repopilot.core.config import RuntimeSettings, load_settings
from repopilot.orchestration.workflow import frozen_settings
from repopilot.storage.jobs import StateError


def settings(provider, **overrides):
    return RuntimeSettings(**dict(dict(provider=provider, api_key="synthetic-only",
        model="synthetic-model", base_url="https://provider.example.test/v1",
        input_cny_per_million=5, output_cny_per_million=20), **overrides))


def test_new_provider_never_falls_back_to_deepseek_key_or_prices(tmp_path, monkeypatch):
    monkeypatch.setenv("DS_API_KEY", "synthetic-deepseek-only")
    monkeypatch.setenv("DS_REASONING_EFFORT", "synthetic-invalid-for-other-provider")
    path = tmp_path / ".env"
    path.write_text("REPOPILOT_PROVIDER=qwen\nREPOPILOT_MODEL=synthetic-model\n"
                    "REPOPILOT_BASE_URL=https://provider.example.test/v1\n")
    with pytest.raises(ValidationError):
        load_settings(path)
    with path.open("a") as stream:
        stream.write("REPOPILOT_API_KEY=synthetic-qwen-only\n")
    with pytest.raises(ValidationError, match="CNY token rates"):
        load_settings(path)
    with path.open("a") as stream:
        stream.write("REPOPILOT_INPUT_CNY_PER_MILLION=5\nREPOPILOT_OUTPUT_CNY_PER_MILLION=20\n")
    loaded = load_settings(path)
    assert loaded.api_key.get_secret_value() == "synthetic-qwen-only"
    assert loaded.thinking == "disabled"
    assert loaded.input_cny_per_million == 5
    assert "synthetic-qwen-only" not in loaded.model_dump_json()


@pytest.mark.parametrize("provider", ["qwen", "openai-compatible", "anthropic"])
def test_non_deepseek_rejects_unsupported_thinking(provider):
    with pytest.raises(ValidationError, match="thinking"):
        settings(provider, thinking="enabled")


@pytest.mark.parametrize("provider", ["qwen", "openai-compatible"])
def test_compatible_sdk_request_tool_roundtrip_and_usage(provider):
    cfg = settings(provider)
    model = make_model(cfg)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion",
            "created": 0, "model": cfg.model, "choices": [{"index": 0, "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "read-1", "type": "function", "function": {"name": "read_file",
                    "arguments": '{"path":"README.md"}'}}]}}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        with OpenAI(api_key="synthetic-only", base_url=cfg.base_url, http_client=transport) as client:
            model.root_client, model.client = client, client.chat.completions
            bound = model.bind_tools([{"name": "read_file", "description": "Read source",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}])
            reply = bound.invoke([HumanMessage(content="inspect")])
            bound.invoke([HumanMessage(content="inspect"), reply,
                          ToolMessage(content='{"ok":true}', tool_call_id="read-1")])
    assert reply.tool_calls[0]["args"] == {"path": "README.md"}
    assert reply.usage_metadata["total_tokens"] == 26
    payload = requests[0]
    assert payload["max_tokens"] == cfg.max_output_tokens
    assert "max_completion_tokens" not in payload
    assert "thinking" not in payload and "reasoning_effort" not in payload
    assert payload.get("enable_thinking") is (False if provider == "qwen" else None)
    assert requests[1]["messages"][-1]["tool_call_id"] == "read-1"
    assert model.max_retries == 0


def test_anthropic_native_tool_blocks_and_usage(monkeypatch):
    pytest.importorskip("langchain_anthropic")
    from anthropic.types import Message

    model = make_model(settings("anthropic"))
    captured = []

    def respond(payload):
        captured.append(payload)
        raw = Message.model_validate({"id": "synthetic", "type": "message", "role": "assistant",
            "model": "synthetic-model", "content": [
                {"type": "text", "text": "inspect"},
                {"type": "tool_use", "id": "read-1", "name": "read_file", "input": {"path": "README.md"}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 17, "output_tokens": 9}})
        return SimpleNamespace(parse=lambda: raw)

    monkeypatch.setattr(type(model), "_create", lambda self, payload: respond(payload))
    bound = model.bind_tools([{"name": "read_file", "description": "Read source",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}])
    reply = bound.invoke([HumanMessage(content="inspect")])
    bound.invoke([HumanMessage(content="inspect"), reply,
                  ToolMessage(content='{"ok":true}', tool_call_id="read-1")])
    assert reply.tool_calls[0]["args"] == {"path": "README.md"}
    assert reply.usage_metadata["total_tokens"] == 26
    assert "thinking" not in captured[0] and "reasoning_effort" not in captured[0]
    assert captured[1]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert model.max_retries == 0


def test_optional_anthropic_dependency_error_is_actionable(monkeypatch):
    original = builtins.__import__

    def without_anthropic(name, *args, **kwargs):
        if name == "langchain_anthropic":
            raise ImportError("synthetic missing dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_anthropic)
    with pytest.raises(ValueError, match=r"\[anthropic\]"):
        make_model(settings("anthropic"))


def test_resume_legacy_job_and_key_rotation_keep_frozen_configuration():
    old = RuntimeSettings(api_key="synthetic-old", model="old-model").model_dump(exclude={"api_key", "provider"})
    current = RuntimeSettings(api_key="synthetic-rotated", model="new-model")
    frozen = frozen_settings(old, current)
    assert frozen.provider == "deepseek" and frozen.model == "old-model"
    assert frozen.api_key == current.api_key
    with pytest.raises(StateError, match="endpoint_mismatch"):
        frozen_settings(old, settings("qwen"))
    with pytest.raises(StateError, match="endpoint_mismatch"):
        frozen_settings(old, current.model_copy(update={"base_url": "https://other.example.test"}))
    with pytest.raises(ValueError, match="profile only accepts"):
        frozen_settings(old, current, {"provider": "qwen"})


@pytest.mark.parametrize("provider", ["openai-compatible", "qwen", "anthropic"])
def test_real_adapter_through_framework_to_human_approval(tmp_path, monkeypatch, provider):
    from contextlib import ExitStack
    from repopilot.orchestration import workflow
    from repopilot.storage.jobs import JobStore
    from tests.runtime.test_review_loop import setup, turn, implement_done, review_approved

    task, _, root = setup(tmp_path)
    cfg = settings(provider)
    model = make_model(cfg) if provider != "anthropic" else None
    if provider == "anthropic":
        pytest.importorskip("langchain_anthropic")
        model = make_model(cfg)
    script = iter([turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                   turn("run_registered_check", {"name": "unit"}), implement_done(), review_approved()])

    with ExitStack() as stack:
        if provider == "anthropic":
            from anthropic.types import Message

            def respond(self, payload):
                calls = next(script).tool_calls
                response = Message.model_validate({"id": "synthetic", "type": "message", "role": "assistant",
                    "model": cfg.model, "content": [{"type": "tool_use", "id": c["id"],
                        "name": c["name"], "input": c["args"]} for c in calls],
                    "stop_reason": "tool_use", "stop_sequence": None,
                    "usage": {"input_tokens": 80, "output_tokens": 30}})
                return SimpleNamespace(parse=lambda: response)

            monkeypatch.setattr(type(model), "_create", respond)
        else:
            def respond(request):
                calls = next(script).tool_calls
                return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion",
                    "created": 0, "model": cfg.model, "choices": [{"index": 0, "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "content": None, "tool_calls": [{
                            "id": c["id"], "type": "function", "function": {"name": c["name"],
                            "arguments": json.dumps(c["args"])}} for c in calls]}}],
                    "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110}})

            transport = stack.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
            client = stack.enter_context(OpenAI(api_key="synthetic-only", base_url=cfg.base_url, http_client=transport))
            model.root_client, model.client = client, client.chat.completions
        result = workflow.start(task, cfg, root, model=model)
    assert result["status"] == "awaiting_approval", result
    assert result["model_calls"] == 4
    assert result["input_tokens"] == 320
    state = JobStore(root).get(result["run_id"])
    assert state["settings"]["provider"] == provider
    assert "api_key" not in state["settings"]
    assert state["reviewed_commit"] and not state["approval"]
    for path in (root / "runs" / result["run_id"]).glob("*.json*"):
        assert "synthetic-only" not in path.read_text(encoding="utf-8")
