"""各业务阶段只负责自己的动作与状态转换；检查点时机由执行器管理。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from repopilot.agents import loop as engine
from repopilot.agents.trace import Trace
from repopilot.agents.prompts import SYSTEM_PROMPT, REVIEW_PROMPT
from repopilot.agents.reports import ImplementationReport, ReviewReport
from repopilot.memory import context as memory_context
from repopilot.core.config import RuntimeSettings, Task
from repopilot.orchestration.state import approval_digest, attach
from repopilot.orchestration import human
from repopilot.storage.jobs import StateError
from repopilot.execution.sandbox import repository_session
from repopilot.execution.workspace import EditBudget, Workspace, WorkspaceError, head_commit, source_snapshot


@dataclass
class PhaseRunner:
    state: dict
    task: Task
    settings: RuntimeSettings
    control: engine.RunControl
    trace: Trace
    edits: EditBudget
    model: Any
    workspace: Workspace | None
    checkpoint: Callable[[], None]
    store_root: Path

    def restore_workspace(self):
        if self.workspace is None or self.state["phase"] == "prepare":
            return
        self.workspace.prepare()
        if self.state.get("workspace_snapshot"):
            if source_snapshot(self.workspace.repository()) != self.state["workspace_snapshot"]:
                raise WorkspaceError("checkpoint_tree_changed")
        elif self.state["expected_head"]:
            self.workspace.assert_clean(self.state["expected_head"])

    def execute(self, phase):
        if phase in {"implement", "rework"}:
            self.implement(phase)
            return
        handlers = {"prepare": self.prepare, "anchor": self.anchor, "verify": self.verify, "review": self.review}
        if phase not in handlers:
            raise StateError("unknown_phase")
        handlers[phase]()

    def prepare(self):
        if self.state["workspace_enabled"]:
            if not self.state["base_commit"]:
                self.state["base_commit"] = head_commit(self.task.repository)
            if not self.state["before"]:
                self.state["before"] = source_snapshot(self.task.repository)
            # 固定基线必须先落盘，才能创建可恢复的工作区副作用。
            self.checkpoint()
            self.workspace = attach(self.state, self.store_root).prepare()
            self.state["expected_head"] = self.state["base_commit"]
        self.trace.event("workspace_ready", worktree=str(self.task.repository if self.workspace is None else self.workspace.path),
                         base_commit=self.state["base_commit"])
        root = self.task.repository if self.workspace is None else self.workspace.repository()
        memory_context.freeze(self.state, self.settings, self.store_root, root, self.trace)
        self.state["phase"] = "implement"

    def _implementation_payload(self, phase):
        payload = {"request": self.task.request, "available_checks": list(self.task.checks)}
        if self.state.get("human_answers"):
            payload["human_answers"] = self.state["human_answers"]
        if phase == "rework":
            if self.state["rework_pending"]:
                self.state["reworks"] += 1
                self.state["rework_pending"] = False
            payload.update(review_feedback=self.state["history"][-1]["findings"],
                           reviewed_commit=self.state["reviewed_commit"])
            self.trace.event("rework_started", attempt=self.state["reworks"], findings=len(self.state["history"][-1]["findings"]))
        if self.state.get("recovery"):
            payload["recovery"] = self.state["recovery"]
        self._attach_memory(payload, phase)
        return payload

    def _attach_memory(self, payload, phase):
        root = self.task.repository if self.workspace is None else self.workspace.repository()
        view = memory_context.context(self.state, self.settings, self.store_root, root, phase, self.trace)
        if view is not None:
            payload['repository_memory'] = view

    def implement(self, phase):
        root = self.task.repository if self.workspace is None else self.workspace.repository()
        with repository_session(self.task, root, writable=self.workspace is not None,
                                deadline=self.control.remaining, trace=self.trace) as session:
            tools = engine.build_tools(self.task, self.control.remaining, workspace=None if self.workspace is None else root,
                                       edits=None if self.workspace is None else self.edits,
                                       **({} if session is None else {"repository": session, "checker": session.run_check}))
            report = engine.run_phase(prompt=SYSTEM_PROMPT, schema=ImplementationReport,
                payload=self._implementation_payload(phase), tools=tools, settings=self.settings,
                control=self.control, model=self.model, phase=phase)
        report = engine.consistent_status(report, self.workspace.changes() if self.workspace else {})
        self.state["report"] = report.model_dump(mode="json")
        self.trace.save("report.json", self.state["report"])
        if report.status == "needs_clarification" and self.workspace is not None:
            human.wait_for_clarification(self.state, self.store_root,
                                         summary=report.summary, questions=report.questions)
            return
        if report.status == "implemented" and self.workspace is not None and self.state["review_enabled"]:
            self.state["phase"] = "anchor"
            return
        self.trace.event("review_skipped", reason=f"{'implementer' if phase == 'implement' else phase}_status_{report.status}")
        if phase == "rework":
            self.state["status"], self.state["stop_reason"] = "phase_error", f"rework_status_{report.status}"
        else:
            self.state["status"], self.state["stop_reason"] = "completed", "report"

    def anchor(self):
        anchor = self.workspace.commit(f"chore: 运行 {self.state['run_id']} 的实现改动")
        self.state["reviewed_commit"] = self.state["expected_head"] = anchor["commit"]
        self.state["phase"] = "verify"

    def verify(self):
        self.workspace.assert_clean(self.state["reviewed_commit"])
        with repository_session(self.task, self.workspace.repository(), writable=False,
                                deadline=self.control.remaining, trace=self.trace) as session:
            self.state["verified"] = engine.verify_checks(self.task, self.workspace.repository(), self.control,
                self.trace, self.control.remaining, **({} if session is None else {"checker": session.run_check}))
        self.workspace.assert_clean(self.state["reviewed_commit"])
        self.trace.save("verification.json", self.state["verified"])
        self.state["phase"] = "review"

    def _review_payload(self):
        diff = self.workspace.diff()
        payload = {"request": self.task.request, "available_checks": list(self.task.checks),
            "review_target": {"commit": self.state["reviewed_commit"], "base_commit": self.state["base_commit"]},
            "changed_files": [change["path"] for change in self.workspace.changes()["files"]],
            "verified_checks": self.state["verified"], "diff": diff["diff"], "diff_truncated": diff["truncated"]}
        if self.state["history"]:
            payload["previous_findings"] = self.state["history"][-1]["findings"]
        if self.state.get("human_answers"):
            payload["human_answers"] = self.state["human_answers"]
        self._attach_memory(payload, 'review')
        return payload

    def review(self):
        self.workspace.assert_clean(self.state["reviewed_commit"])
        payload = self._review_payload()
        self.trace.event("review_started", commit=self.state["reviewed_commit"])
        with repository_session(self.task, self.workspace.repository(), writable=False,
                                deadline=self.control.remaining, trace=self.trace) as session:
            report = engine.run_phase(prompt=REVIEW_PROMPT, schema=ReviewReport,
                payload=payload, tools=engine.build_tools(self.task, self.control.remaining, workspace=self.workspace.path,
                    **({} if session is None else {"repository": session, "checker": session.run_check})),
                settings=self.settings, control=self.control, model=self.model, phase="review")
        self.workspace.assert_clean(self.state["reviewed_commit"])
        if report.verdict == "approved" and not engine.verification_passed(self.task, self.state["verified"]):
            report = report.model_copy(update={"verdict": "unable_to_review", "summary": "本地复核未全部通过，拒绝模型批准。"})
        self.state["review_report"] = report.model_dump(mode="json")
        self.trace.save("review.json", self.state["review_report"])
        self.state["history"].append({"attempt": len(self.state["history"]), "commit": self.state["reviewed_commit"],
            "verdict": report.verdict, "summary": report.summary, "findings": engine.findings_brief(report.findings)})
        self.trace.save("review-history.json", self.state["history"])
        self.trace.event("review_finished", verdict=report.verdict, commit=self.state["reviewed_commit"])
        if report.verdict == "unable_to_review" and report.questions:
            human.wait_for_clarification(self.state, self.store_root,
                                         summary=report.summary, questions=report.questions)
        else:
            self._review_outcome(report.verdict)
        if report.verdict == 'approved':
            memory_context.collect_candidates(self.state, self.settings, self.store_root, self.workspace.repository(), self.trace)

    def _review_outcome(self, verdict):
        if verdict == "unable_to_review":
            self.state["status"], self.state["stop_reason"] = "review_inconclusive", "review_unable_to_review"
        elif verdict == "changes_requested":
            if self.state["reworks"] >= self.settings.max_rework:
                self.state["status"], self.state["stop_reason"] = "rework_limit_reached", "max_rework"
            else:
                self.state["phase"], self.state["rework_pending"] = "rework", True
        else:
            self.state["status"] = "awaiting_approval" if self.state["require_approval"] else "completed"
            self.state["stop_reason"] = self.state["status"] if self.state["require_approval"] else "report"
            self.state["phase"] = "approval"
            self.state["approval_digest"] = approval_digest(self.state)
            if self.state["require_approval"]:
                human.approval_request(self.state, self.store_root)
