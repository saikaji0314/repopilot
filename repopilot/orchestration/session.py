"""单次执行会话：恢复计数、保存检查点、调度阶段并统一收敛错误。"""

from importlib.metadata import PackageNotFoundError, version
import json
from threading import RLock
import time

from langchain.agents.structured_output import StructuredOutputValidationError
from pydantic import ValidationError

from repopilot.agents import loop as engine
from repopilot.agents.trace import Trace
from repopilot.storage.budget import Budget, RunLimit
from repopilot.core.config import Task
from repopilot.orchestration.phases import PhaseRunner
from repopilot.orchestration import knowledge
from repopilot.knowledge.documents import KnowledgeFault
from repopilot.orchestration.state import attach
from repopilot.execution.workspace import WorkspaceError, source_snapshot


CONTROL_FIELDS = ("model_calls", "tool_calls", "input_tokens", "output_tokens",
                  "accounted_cny", "checks", "progress")


def installed_versions(provider):
    packages = ("langchain", "langgraph", {"deepseek": "langchain-deepseek",
                "anthropic": "langchain-anthropic"}.get(provider, "langchain-openai"))
    result = {}
    for package in packages:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


class RunSession:
    def __init__(self, store, state, settings, model):
        self.store, self.state = store, state
        directory = store.root / "runs" / state["run_id"]
        directory.mkdir(parents=True, exist_ok=True)
        self.trace = Trace(directory, settings.api_key.get_secret_value())
        budget = Budget(store.root / "budget.sqlite3", settings.budget_cny)
        self.control = engine.RunControl(settings, budget, self.trace, state["run_id"])
        self._restore_control(budget)
        self.edits = engine.EditBudget(settings.max_edits)
        self.edits.count = state["edits"].get("edits", 0)
        self.edits.touched = state["edits"].get("edited_files", [])
        self.previous_active, self.started = state["active_seconds"], time.monotonic()
        self.control.deadline = self.started + max(0, settings.run_timeout_seconds - self.previous_active)
        self.saving = RLock()
        self.trace.on_event = self.checkpoint
        self.trace.save("input.json", {"task": state["task"], "settings": state["settings"],
            "issue": state["issue"], "versions": installed_versions(settings.provider)})
        usable_model = engine.make_model(settings) if model is None else model
        self.runner = PhaseRunner(state, Task.model_validate(state["task"]), settings,
            self.control, self.trace, self.edits, usable_model, attach(state, store.root),
            self.checkpoint, store.root)

    def _restore_control(self, budget):
        for name in CONTROL_FIELDS:
            if name in self.state["control"]:
                setattr(self.control, name, self.state["control"][name])
        accounted = budget.snapshot(self.state["run_id"])
        self.control.model_calls = max(self.control.model_calls, accounted["calls"])
        self.control.accounted_cny = max(self.control.accounted_cny, accounted["accounted_cny"])

    def checkpoint(self):
        with self.saving:
            self.state["control"] = {name: getattr(self.control, name) for name in CONTROL_FIELDS}
            self.state["edits"] = self.edits.snapshot()
            self.state["active_seconds"] = self.previous_active + time.monotonic() - self.started
            # SQLite 与 JSON 共用脱敏规则，并保留阶段执行器持有的字典引用。
            cleaned = json.loads(self.trace.encode(self.state))
            self.state.clear()
            self.state.update(cleaned)
            self.store.save(self.state)

    def step(self):
        self.control.remaining()
        if self.state["phase"] != "prepare":
            knowledge.sync_answers(self.state, self.store.root, (self.runner.settings.api_key.get_secret_value(),))
        knowledge.validate(self.state, self.store.root)
        phase = self.state["phase"]
        self.state["in_flight"] = phase
        self.checkpoint()
        self.runner.execute(phase)
        self.state["in_flight"] = None
        if self.runner.workspace:
            self.state["workspace_snapshot"] = source_snapshot(self.runner.workspace.repository())
        knowledge.validate(self.state, self.store.root)
        self.checkpoint()
        return phase

    def fail(self, exc):
        state, phase = self.state, self.state["phase"]
        if isinstance(exc, KnowledgeFault):
            knowledge.pause(state, self.store.root, str(exc))
            self.trace.event("knowledge_changed", reason=str(exc), phase=phase)
        elif isinstance(exc, (ValidationError, StructuredOutputValidationError)):
            state["status"] = "review_inconclusive" if phase == "review" else "phase_error"
            state["stop_reason"], state["in_flight"] = f"invalid_{phase}_report", None
            if phase == "review":
                state["review_report"] = None
            self.trace.event("run_error", error_type=type(exc).__name__, phase=phase,
                             validation=engine.validation_details(exc))
        elif isinstance(exc, RunLimit):
            state["status"], state["stop_reason"] = "limit_reached", str(exc)
        elif isinstance(exc, WorkspaceError):
            state["status"], state["stop_reason"] = "workspace_error", str(exc)
        elif isinstance(exc, KeyboardInterrupt):
            state["status"], state["stop_reason"] = "interrupted", "interrupted"
        else:
            state["status"], state["stop_reason"] = "failed", type(exc).__name__
            self.trace.event("run_error", error_type=type(exc).__name__, phase=phase)


def execute_run(store, state, settings, *, model=None, stop_after=None):
    session = RunSession(store, state, settings, model)
    state["status"], state["stop_reason"] = "running", "running"
    try:
        session.runner.restore_workspace()
        while state["status"] == "running":
            phase = session.step()
            if stop_after == phase and state["status"] == "running":
                state["status"], state["stop_reason"] = "paused", "checkpoint"
                session.checkpoint()
    except (Exception, KeyboardInterrupt) as exc:
        session.fail(exc)
    session.checkpoint()
    session.trace.on_event = None
