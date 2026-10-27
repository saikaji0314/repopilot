from uuid import uuid4

import pytest

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from repopilot.agents.loop import RunControl, consistent_status, investigate
from repopilot.agents.loop import ImplementationReport
from repopilot.storage.budget import Budget
from repopilot.core.config import RuntimeSettings, Task


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
    options = {"api_key": "synthetic-private-key", "plan_enabled": False}
    options.update(overrides)
    return (Task(id="sample", request="inspect README", repository=repo),
            RuntimeSettings(**options), tmp_path / "runtime")


def test_framework_tool_loop_returns_report_and_redacts_saved_artifacts(tmp_path):
    task, settings, output = setup(tmp_path)
    plan = {"status": "needs_clarification", "summary": "synthetic-private-key",
            "evidence": [{"path": "README.md", "start_line": 1, "observation": "sample"}],
            "next_steps": ["add a test"], "questions": ["which language?"]}
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}),
                                     message("ImplementationReport", plan)])
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["status"] == "completed", summary
    assert summary["model_calls"] == 2
    assert summary["input_tokens"] == 200
    assert summary["cost"]["uncertain_calls"] == 0
    run_dir = next((output / "runs").iterdir())
    for path in run_dir.iterdir():
        assert "synthetic-private-key" not in path.read_text(encoding="utf-8")
    assert "sample source" in (run_dir / "events.jsonl").read_text(encoding="utf-8")


def test_framework_clears_old_read_results_before_context_limit(tmp_path):
    import json
    task, settings, output = setup(tmp_path)
    (task.repository / "README.md").write_text("synthetic source " * 490, encoding="utf-8")
    model = ScriptedModel(responses=[*[message("read_file", {"path": "README.md"}) for _ in range(9)],
        message("ImplementationReport", {"status": "needs_clarification", "summary": "已读取",
            "evidence": [], "next_steps": [], "questions": ["请确认范围"]})])
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["status"] == "completed", summary
    events = [json.loads(line) for line in (next((output / "runs").iterdir()) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = [e for e in events if e["event"] == "model_started"]
    assert any(e["cleared_tool_results"] > 0 for e in calls)
    assert all(e["input_bytes"] <= settings.max_context_bytes for e in calls)
    assert any("synthetic source" in e.get("result", "") for e in events)


def test_model_calls_stop_without_infinite_retry(tmp_path):
    task, settings, output = setup(tmp_path, max_model_calls=2)
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}),
                                     message("read_file", {"path": "README.md"})])
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["status"] == "limit_reached", summary
    assert summary["stop_reason"] == "max_model_calls"
    assert summary["model_calls"] == 2


def test_missing_usage_retains_reservation_and_stops(tmp_path):
    task, settings, output = setup(tmp_path)
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}, usage=False)])
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["stop_reason"] == "usage_unavailable", summary
    assert summary["cost"]["uncertain_calls"] == 1
    assert summary["cost"]["accounted_cny"] > 0


def test_budget_rejection_occurs_before_model_invocation(tmp_path):
    task, settings, output = setup(tmp_path, budget_cny=0.001)
    budget = Budget(output / "budget.sqlite3", settings.budget_cny)
    model = ScriptedModel(responses=[AIMessage(content="must not run")])
    summary = investigate(task, settings, output, model=model, workspace=False)
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
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["stop_reason"] == reason
    assert summary["model_calls"] == 0


def test_tool_limit_stops_further_execution(tmp_path):
    task, settings, output = setup(tmp_path, max_tool_calls=1)
    model = ScriptedModel(responses=[message("read_file", {"path": "README.md"}),
                                     message("read_file", {"path": "README.md"})])
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["stop_reason"] == "max_tool_calls", summary
    assert summary["tool_calls"] == 1
    assert summary["model_calls"] == 2


@pytest.mark.parametrize("metadata", [{"finish_reason": "length"}, {"stop_reason": "max_tokens"}])
def test_truncated_completion_is_a_limit_with_usage_not_a_format_error(tmp_path, metadata):
    task, settings, output = setup(tmp_path)
    truncated = message("ImplementationReport", {"status": "implemented"})
    truncated.response_metadata.update(metadata)
    model = ScriptedModel(responses=[truncated])
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["stop_reason"] == "max_output_tokens", summary
    assert summary["cost"]["uncertain_calls"] == 0
    assert summary["input_tokens"] == 100


def test_provider_failure_retains_unknown_charge_without_logging_exception_body(tmp_path):
    class FailingModel(ScriptedModel):
        def _generate(self, *args, **kwargs):
            raise RuntimeError("synthetic-private-key provider response")

    task, settings, output = setup(tmp_path)
    summary = investigate(task, settings, output, model=FailingModel(responses=[AIMessage(content="unused")]),
                          workspace=False)
    assert summary["status"] == "failed"
    assert summary["stop_reason"] == "RuntimeError"
    assert summary["cost"]["uncertain_calls"] == 1
    events = next((output / "runs").glob("*/events.jsonl")).read_text(encoding="utf-8")
    assert "provider response" not in events
    assert "synthetic-private-key" not in events


def test_response_after_deadline_is_accounted_but_not_accepted(tmp_path):
    task, settings, output = setup(tmp_path, run_timeout_seconds=0.05)
    model = ScriptedModel(sleep=0.1, responses=[message("read_file", {"path": "README.md"})])
    summary = investigate(task, settings, output, model=model, workspace=False)
    assert summary["stop_reason"] == "run_timeout", summary
    assert summary["tool_calls"] == 0
    # Initialization can consume the deadline before the first model call.
    assert summary["cost"]["uncertain_calls"] == 0


def test_check_summary_keeps_verdict_not_the_raw_output():
    control = RunControl(RuntimeSettings(api_key="synthetic"), None, None, "run")
    control.record_check("unit", {"ok": True, "name": "unit", "exit_code": 0, "timed_out": False,
                                  "truncated": True, "output": "x" * 50_000, "duration_seconds": 1.2})
    control.record_check("blog", {"ok": False, "error": "check_already_ran"})
    assert control.checks == [{"name": "unit", "exit_code": 0, "timed_out": False, "truncated": True},
                              {"name": "blog", "error": "check_already_ran"}]


def report(status, evidence):
    return ImplementationReport.model_validate({"status": status, "summary": "s", "evidence": evidence,
                                               "next_steps": [], "questions": []})


def test_read_only_run_cannot_claim_implemented():
    blocked = report("blocked", [{"kind": "file", "path": "a.py", "start_line": 1, "observation": "x"}])
    assert consistent_status(blocked, {"changed_files": 0}) is blocked
    claimed = report("implemented", [{"kind": "file", "path": "a.py", "start_line": 1, "observation": "x"},
                                     {"kind": "check", "name": "unit", "exit_code": 0, "observation": "pass"}])
    downgraded = consistent_status(claimed, {"changed_files": 0})
    assert downgraded.status == "blocked"
    assert downgraded.questions, "缺少文件变更时应当留下说明，而不是静默改写"


def test_changed_files_do_not_override_a_declared_blocker():
    blocked = report("blocked", [{"kind": "check", "name": "unit", "exit_code": 0, "observation": "pass"}])
    assert consistent_status(blocked, {"changed_files": 1}) is blocked, "返工中间态不能被当成完成"
    needs_clarification = report("needs_clarification",
                                 [{"kind": "check", "name": "unit", "exit_code": 0, "observation": "pass"}])
    assert consistent_status(needs_clarification, {"changed_files": 3}).status == "needs_clarification"
