from uuid import uuid4

import pytest

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from repopilot.runtime.agent import investigate
from repopilot.runtime.budget import Budget
from repopilot.runtime.config import RuntimeSettings, Task


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def message(name, args, *, usage=True):
    return AIMessage(content="", tool_calls=[{"id": uuid4().hex, "name": name, "args": args}],
                     usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}
                     if usage else None)


def setup(tmp_path, **overrides):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("sample source", encoding="utf-8")
    return (Task(id="sample", request="inspect README", repository=repo),
            RuntimeSettings(api_key="synthetic-private-key", **overrides), tmp_path / "runtime")


def test_framework_tool_loop_returns_report_and_redacts_saved_artifacts(tmp_path):
    task, settings, output = setup(tmp_path)
    plan = {"status": "planned", "summary": "synthetic-private-key",
            "evidence": [{"path": "README.md", "start_line": 1, "observation": "sample"}],
            "next_steps": ["add a test"], "questions": []}
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}),
                                     message("InvestigationReport", plan)])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "completed", summary
    assert summary["model_calls"] == 2
    assert summary["input_tokens"] == 200
    assert summary["cost"]["uncertain_calls"] == 0
    run_dir = next((output / "runs").iterdir())
    for path in run_dir.iterdir():
        assert "synthetic-private-key" not in path.read_text(encoding="utf-8")
    assert "sample source" in (run_dir / "events.jsonl").read_text(encoding="utf-8")


def test_model_calls_stop_without_infinite_retry(tmp_path):
    task, settings, output = setup(tmp_path, max_model_calls=2)
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}),
                                     message("read_file", {"path": "README.md"})])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "limit_reached", summary
    assert summary["stop_reason"] == "max_model_calls"
    assert summary["model_calls"] == 2


def test_missing_usage_retains_reservation_and_stops(tmp_path):
    task, settings, output = setup(tmp_path)
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}, usage=False)])
    summary = investigate(task, settings, output, model=model)
    assert summary["stop_reason"] == "usage_unavailable", summary
    assert summary["cost"]["uncertain_calls"] == 1
    assert summary["cost"]["accounted_cny"] > 0


def test_budget_rejection_occurs_before_model_invocation(tmp_path):
    task, settings, output = setup(tmp_path, budget_cny=0.001)
    budget = Budget(output / "budget.sqlite3", settings.budget_cny)
    model = ScriptedModel(responses=[AIMessage(content="must not run")])
    summary = investigate(task, settings, output, model=model)
    assert summary["stop_reason"] == "cumulative_cost_budget", summary
    assert summary["model_calls"] == 0
    assert budget.snapshot()["calls"] == 0


@pytest.mark.parametrize("overrides,reason", [
    ({"max_context_bytes": 1}, "context_bytes"),
    ({"max_run_cost_cny": 0.001}, "run_cost_budget"),
])
def test_request_size_and_run_cost_limits_stop_before_request(tmp_path, overrides, reason):
    task, settings, output = setup(tmp_path, **overrides)
    model = ScriptedModel(responses=[AIMessage(content="must not run")])
    summary = investigate(task, settings, output, model=model)
    assert summary["stop_reason"] == reason
    assert summary["model_calls"] == 0


def test_tool_limit_stops_further_execution(tmp_path):
    task, settings, output = setup(tmp_path, max_tool_calls=1)
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}),
                                     message("read_file", {"path": "README.md"})])
    summary = investigate(task, settings, output, model=model)
    assert summary["stop_reason"] == "max_tool_calls", summary
    assert summary["tool_calls"] == 1
    assert summary["model_calls"] == 2


def test_truncated_completion_is_a_limit_with_usage_not_a_format_error(tmp_path):
    task, settings, output = setup(tmp_path)
    truncated = message("InvestigationReport", {"status": "planned"})
    truncated.response_metadata["finish_reason"] = "length"
    model = ScriptedModel(responses=[truncated])
    summary = investigate(task, settings, output, model=model)
    assert summary["stop_reason"] == "max_output_tokens", summary
    assert summary["cost"]["uncertain_calls"] == 0
    assert summary["input_tokens"] == 100


def test_provider_failure_retains_unknown_charge_without_logging_exception_body(tmp_path):
    class FailingModel(ScriptedModel):
        def _generate(self, *args, **kwargs):
            raise RuntimeError("synthetic-private-key provider response")

    task, settings, output = setup(tmp_path)
    summary = investigate(task, settings, output, model=FailingModel(responses=[AIMessage(content="unused")]))
    assert summary["status"] == "failed"
    assert summary["stop_reason"] == "RuntimeError"
    assert summary["cost"]["uncertain_calls"] == 1
    events = next((output / "runs").glob("*/events.jsonl")).read_text(encoding="utf-8")
    assert "provider response" not in events
    assert "synthetic-private-key" not in events


def test_response_after_deadline_is_accounted_but_not_accepted(tmp_path):
    task, settings, output = setup(tmp_path, run_timeout_seconds=0.05)
    model = ScriptedModel(sleep=0.1, responses=[message("read_file", {"path": "README.md"})])
    summary = investigate(task, settings, output, model=model)
    assert summary["stop_reason"] == "run_timeout", summary
    assert summary["tool_calls"] == 0
    # Initialization can consume the deadline before the first model call.
    assert summary["cost"]["uncertain_calls"] == 0
