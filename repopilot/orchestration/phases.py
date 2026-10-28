"""各业务阶段只负责自己的动作与状态转换；检查点时机由执行器管理。"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from repopilot.agents import loop as engine
from repopilot.agents.trace import Trace
from repopilot.agents.prompts import SYSTEM_PROMPT, REVIEW_PROMPT, PLANNER_PROMPT
from repopilot.agents.reports import Decision, ImplementationReport, ReviewReport
from repopilot.memory import context as memory_context
from repopilot.core.config import RuntimeSettings, Task
from repopilot.orchestration.state import approval_digest, attach
from repopilot.orchestration import human
from repopilot.orchestration import knowledge
from repopilot.orchestration import parallel
from repopilot.orchestration.slots import SlotFault, SlotManager
from repopilot.planning import planner as planning
from repopilot.planning.contracts import PlanFault, PlanReport
from repopilot.storage.budget import RunLimit
from repopilot.storage.jobs import StateError, timestamp
from repopilot.execution.repository import Repository
from repopilot.execution.sandbox import repository_session
from repopilot.execution.workspace import (EditBudget, Workspace, WorkspaceError, head_commit, run_git,
                                           source_snapshot)


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
        handlers = {"prepare": self.prepare, "plan": self.plan, "anchor": self.anchor,
                    "verify": self.verify, "review": self.review}
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
        if self.workspace is not None and "knowledge_snapshot" not in self.state:
            knowledge.sync_answers(self.state, self.store_root, (self.settings.api_key.get_secret_value(),))
            knowledge.freeze(self.state, self.store_root, root)
        self.state["phase"] = ("plan" if self.settings.plan_enabled and
                               (not self.state.get("plan") or self.state.get("plan_refresh_required"))
                               else "implement")

    def _recovery_payload(self, payload):
        if self.state.get("recovery"):
            payload["recovery"] = self.state["recovery"]
        return payload

    def _plan_payload(self):
        payload = {"request": self.task.request, "task_brief": self.state.get("task_brief"),
                   "available_checks": list(self.task.checks),
                   "max_subtasks": self.settings.max_plan_subtasks,
                   "question_batches_used": len(self.state.get("question_batches") or []),
                   "question_limit": self.settings.max_question_batches}
        if self.state.get("human_answers"):
            payload["human_answers"] = self.state["human_answers"]
        refresh = planning.refresh_context(self.state)
        if refresh:
            payload["previous_plan"] = refresh
        self._recovery_payload(payload)
        self._attach_memory(payload, "plan")
        return payload

    def plan(self):
        """先整理任务说明与顺序子任务；不拆或不并行也由程序固定成可追溯计划。"""
        root = self.task.repository if self.workspace is None else self.workspace.repository()
        with repository_session(self.task, root, writable=False,
                                deadline=self.control.remaining, trace=self.trace) as session:
            repository = Repository(root) if session is None else session
            tools = engine.build_tools(self.task, self.control.remaining, workspace=None,
                                       repository=repository,
                                       **({} if session is None else {"checker": session.run_check}))
            tools += self._material_tools()
            report = engine.run_phase(prompt=PLANNER_PROMPT, schema=PlanReport,
                payload=self._plan_payload(), tools=tools, settings=self.settings,
                control=self.control, model=self.model, phase="plan")
        self.state["plan_report"] = report.model_dump(mode="json")
        self.trace.save("plan-report.json", self.state["plan_report"])
        knowledge.record_decisions(self.state, self.store_root, "plan", report.decisions,
                                   (self.settings.api_key.get_secret_value(),))
        if report.status == "needs_clarification":
            human.wait_for_clarification(self.state, self.store_root, summary=report.summary,
                questions=report.questions, limit=self.settings.max_question_batches,
                blocking={"phase": "plan"})
            return
        plan, projected = planning.build_plan(self.state, report,
            max_subtasks=self.settings.max_plan_subtasks)
        planning.record(self.state, plan, projected)
        self.trace.event("plan_recorded", version=plan.version, split=plan.split,
                         subtasks=len(plan.subtasks), order=plan.order,
                         brief_revision=projected["revision"])
        self.state["phase"] = "implement"

    def _implementation_payload(self, phase):
        payload = {"request": self.task.request, "available_checks": list(self.task.checks)}
        if self.state.get("human_answers"):
            payload["human_answers"] = self.state["human_answers"]
        if self.state.get("task_brief"):
            payload["task_brief"] = self.state["task_brief"]
        plan_view = planning.payload(self.state)
        if plan_view:
            payload["plan"] = plan_view
        refresh = planning.refresh_context(self.state)
        if refresh:
            payload["plan_refresh"] = refresh
        if phase == "rework":
            if self.state["rework_pending"]:
                self.state["reworks"] += 1
                self.state["rework_pending"] = False
            payload.update(review_feedback=self.state["history"][-1]["findings"],
                           reviewed_commit=self.state["reviewed_commit"])
            self.trace.event("rework_started", attempt=self.state["reworks"], findings=len(self.state["history"][-1]["findings"]))
        self._recovery_payload(payload)
        self._attach_memory(payload, phase)
        return payload

    def _subtask_payload(self, subtask):
        payload = self._implementation_payload("implement")
        payload["subtask"] = subtask
        payload["completed_subtasks"] = [
            {"id": item["id"], "summary": str(item.get("summary") or "")[:500],
             "commit": item.get("commit")}
            for item in (self.state.get("plan_progress") or {}).get("results") or []
            if item.get("status") == "completed"]
        payload["remaining_subtasks"] = [
            identifier for identifier in (self.state.get("plan_progress") or {}).get("order") or []
            if identifier != subtask["id"] and identifier not in {
                item["id"] for item in (self.state.get("plan_progress") or {}).get("results") or []}]
        return payload

    def _attach_memory(self, payload, phase):
        root = self.task.repository if self.workspace is None else self.workspace.repository()
        view = memory_context.context(self.state, self.settings, self.store_root, root, phase, self.trace)
        if view is not None:
            payload['repository_memory'] = view
        materials = knowledge.view(self.state, self.store_root, phase, (self.settings.api_key.get_secret_value(),))
        if materials is not None:
            payload['project_materials'] = materials

    def _material_tools(self):
        return knowledge.tools(self.state, self.store_root, (self.settings.api_key.get_secret_value(),))

    @contextmanager
    def _implementation_slot(self, label):
        """单Agent／顺序子任务／返工／整合修复与并行worker共用同一组全局槽位。"""
        manager = SlotManager(self.store_root, limit=self.settings.max_implementation_slots,
                              lease_seconds=max(120, int(self.settings.run_timeout_seconds) + 60))
        owner = f"{self.state['run_id']}:{label}:{uuid4().hex[:8]}"
        manager.acquire(owner, timeout=self.settings.implementation_slot_timeout_seconds)
        try:
            yield manager
        finally:
            manager.release(owner)

    def implement(self, phase):
        if phase == "implement" and self.state.get("plan"):
            if self.state["plan"].get("split") == "parallel":
                self._implement_parallel()
            else:
                self._implement_plan()
            return
        root = self.task.repository if self.workspace is None else self.workspace.repository()
        with self._implementation_slot(f"{phase}"):
            with repository_session(self.task, root, writable=self.workspace is not None,
                                    deadline=self.control.remaining, trace=self.trace) as session:
                tools = engine.build_tools(self.task, self.control.remaining, workspace=None if self.workspace is None else root,
                                           edits=None if self.workspace is None else self.edits,
                                           **({} if session is None else {"repository": session, "checker": session.run_check}))
                tools += self._material_tools()
                report = engine.run_phase(prompt=SYSTEM_PROMPT, schema=ImplementationReport,
                    payload=self._implementation_payload(phase), tools=tools, settings=self.settings,
                    control=self.control, model=self.model, phase=phase)
        report = engine.consistent_status(report, self.workspace.changes() if self.workspace else {})
        self.state["report"] = report.model_dump(mode="json")
        self.trace.save("report.json", self.state["report"])
        knowledge.record_decisions(self.state, self.store_root, phase, report.decisions,
                                   (self.settings.api_key.get_secret_value(),))
        if report.status == "needs_clarification" and self.workspace is not None:
            human.wait_for_clarification(self.state, self.store_root, summary=report.summary,
                questions=report.questions, limit=self.settings.max_question_batches,
                blocking={"phase": phase})
            return
        if report.status == "implemented" and self.workspace is not None and self.state["review_enabled"]:
            self.state["phase"] = "anchor"
            return
        self.trace.event("review_skipped", reason=f"{'implementer' if phase == 'implement' else phase}_status_{report.status}")
        if phase == "rework":
            self.state["status"], self.state["stop_reason"] = "phase_error", f"rework_status_{report.status}"
        else:
            self.state["status"], self.state["stop_reason"] = "completed", "report"

    def _implement_plan(self):
        """在一个工作区按依赖顺序执行子任务；每个子任务单独提交并落盘进度。"""
        if self.workspace is None:
            raise StateError("plan_requires_workspace")
        root = self.workspace.repository()
        while True:
            subtask = planning.current_subtask(self.state)
            if subtask is None:
                break
            planning.dependencies_done(self.state, subtask, root)
            planning.set_current(self.state, subtask["id"])
            self.state["subtask_report"] = None
            self.trace.event("subtask_started", subtask=subtask["id"], title=subtask["title"])
            self.checkpoint()
            started_at = timestamp()
            with self._implementation_slot(f"plan-{subtask['id']}"):
                with repository_session(self.task, root, writable=True,
                                        deadline=self.control.remaining, trace=self.trace) as session:
                    tools = engine.build_tools(self.task, self.control.remaining, workspace=root,
                                               edits=self.edits,
                                               **({} if session is None else {"repository": session,
                                                                              "checker": session.run_check}))
                    tools += self._material_tools()
                    report = engine.run_phase(prompt=SYSTEM_PROMPT, schema=ImplementationReport,
                        payload=self._subtask_payload(subtask), tools=tools, settings=self.settings,
                        control=self.control, model=self.model, phase="implement")
            # 前置子任务已经提交，因此这里看到的是当前子任务的增量；没有改动不能自称完成。
            dirty = run_git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip()
            report = engine.consistent_status(report, self.workspace.changes() if dirty else {})
            self.state["report"] = report.model_dump(mode="json")
            self.trace.save(f"report-{subtask['id']}.json", self.state["report"])
            knowledge.record_decisions(self.state, self.store_root, "implement", report.decisions,
                                       (self.settings.api_key.get_secret_value(),))
            if report.status == "needs_clarification":
                self.state["subtask_report"] = {"id": subtask["id"], "status": report.status,
                                                "summary": report.summary}
                human.wait_for_clarification(self.state, self.store_root, summary=report.summary,
                    questions=report.questions, limit=self.settings.max_question_batches,
                    blocking={"phase": "implement", "subtask": subtask["id"]})
                self.checkpoint()
                return
            evidence = [item.model_dump(mode="json") for item in report.evidence]
            decisions = [item.model_dump(mode="json") for item in report.decisions]
            if report.status != "implemented":
                planning.record_result(self.state, subtask, status=report.status,
                    summary=report.summary, evidence=evidence, decisions=decisions,
                    started_at=started_at)
                self.trace.event("subtask_blocked", subtask=subtask["id"], status=report.status)
                self.state["status"], self.state["stop_reason"] = "completed", f"subtask_{report.status}"
                self.checkpoint()
                return
            anchor = self.workspace.commit(f"chore: 运行 {self.state['run_id']} 的子任务 {subtask['id']}")
            self.state["expected_head"] = anchor["commit"]
            planning.record_result(self.state, subtask, status="completed", summary=report.summary,
                commit=anchor["commit"], evidence=evidence, decisions=decisions, started_at=started_at)
            self.state["workspace_snapshot"] = source_snapshot(root)
            self.trace.event("subtask_finished", subtask=subtask["id"], commit=anchor["commit"],
                             status="completed")
            self.checkpoint()
        if self.state["review_enabled"]:
            self.state["phase"] = "anchor"
        else:
            self.state["status"], self.state["stop_reason"] = "completed", "report"

    def _parallel_payload(self, subtask, base_commit):
        payload = self._subtask_payload(subtask)
        payload["parallel"] = {"mode": "parallel", "base_commit": base_commit,
                               "max_slots": min(2, self.settings.max_implementation_slots)}
        return payload

    def _parallel_ready(self, running_ids):
        plan = self.state.get("plan") or {}
        results = planning.result_map(self.state)
        nodes = {item["id"]: item for item in plan.get("subtasks") or []}
        for identifier in plan.get("order") or []:
            if identifier in running_ids or identifier in results:
                continue
            node = nodes[identifier]
            if all(results.get(dependency, {}).get("status") == "completed"
                   for dependency in node.get("depends_on") or []):
                return node
        return None

    def _record_parallel_run(self, info, status, **values):
        runs = self.state.setdefault("parallel_runs", [])
        entry = next((item for item in runs if item.get("attempt") == info["attempt"]), None)
        if entry is None:
            entry = {"attempt": info["attempt"], "subtask": info["subtask"]["id"],
                     "worktree": str(info["workspace"].path) if info.get("workspace") else None,
                     "base_commit": info.get("base_commit"), "started_at": info.get("started_at")}
            runs.append(entry)
        entry.update(status=status, updated_at=timestamp(), **values)
        if status in {"completed", "failed", "interrupted", "blocked", "paused"}:
            entry["finished_at"] = timestamp()
        return entry

    def _recover_parallel_runs(self):
        changed = False
        for item in self.state.get("parallel_runs") or []:
            if item.get("status") == "running":
                item["status"], item["finished_at"] = "interrupted", timestamp()
                changed = True
        if changed:
            self.trace.event("parallel_runs_interrupted", count=sum(
                1 for item in self.state.get("parallel_runs") or [] if item.get("status") == "interrupted"))
            self.checkpoint()

    def _append_integration(self, integration):
        record = {**integration, "at": timestamp()}
        self.state.setdefault("integrations", []).append(record)
        return record

    def _register_parallel_block(self, info, kind, summary, questions, *, phase="implement",
                                 report=None, source_commit=None):
        """局部阻塞只登记受影响子任务；独立任务继续完成并整合。"""
        blocks = self.state.setdefault("parallel_blocked", [])
        if any(item.get("subtask") == info["subtask"]["id"] and not item.get("handled")
               for item in blocks):
            return
        blocks.append({"subtask": info["subtask"]["id"], "kind": kind,
                       "summary": summary or "子任务需要处理", "questions": list(questions or []),
                       "phase": phase, "worktree": str(info["workspace"].path),
                       "source_commit": source_commit, "at": timestamp(), "handled": False,
                       "report_status": None if report is None else report.get("status")})
        self.trace.event("parallel_subtask_blocked", subtask=info["subtask"]["id"], kind=kind,
                         phase=phase)

    def _pause_parallel_blocks(self):
        """没有可继续的独立任务后，汇总局部阻塞并打开一次人工请求。"""
        blocks = [item for item in self.state.get("parallel_blocked") or []
                  if not item.get("handled")]
        if not blocks:
            return False
        needing_answers = [item for item in blocks
                           if item.get("kind") in {"clarification", "conflict"}]
        if needing_answers:
            primary = needing_answers[0]
            questions = [question for item in needing_answers for question in item.get("questions") or []]
            summary = "；".join(item.get("summary") or item["subtask"] for item in needing_answers)
            human.wait_for_clarification(self.state, self.store_root, summary=summary,
                questions=questions or [summary], limit=self.settings.max_question_batches,
                blocking={"phase": primary.get("phase", "implement"),
                          "subtask": primary["subtask"]})
            for item in blocks:
                item["handled"] = True
            return True
        self.state["status"], self.state["stop_reason"] = "completed", "subtask_blocked"
        for item in blocks:
            item["handled"] = True
        return False

    def _record_completed_parallel(self, info, result, integration, report):
        subtask = info["subtask"]
        self._append_integration({"subtask": subtask["id"], "status": "integrated",
            "source_commit": integration["source_commit"], "base_commit": integration["base_commit"],
            "integrated_commit": integration["integrated_commit"],
            "resolution": integration["resolution"], "conflicts": integration["conflicts"],
            "worktree": integration.get("worktree")})
        planning.record_result(self.state, subtask, status="completed",
            summary=(report or {}).get("summary") or "子任务完成",
            commit=integration["integrated_commit"],
            evidence=(report or {}).get("evidence") or [],
            decisions=(report or {}).get("decisions") or [],
            source_commit=integration["source_commit"],
            worktree=integration.get("worktree"), base_commit=integration["base_commit"],
            integration={"status": "integrated", "resolution": integration["resolution"],
                         "integrated_commit": integration["integrated_commit"]},
            started_at=result.get("started_at"))
        self._record_parallel_run(info, "completed", commit=integration["integrated_commit"],
                                  source_commit=integration["source_commit"],
                                  summary=(report or {}).get("summary"))
        self.state["expected_head"] = integration["integrated_commit"]
        self.state["workspace_snapshot"] = source_snapshot(self.workspace.repository())
        self.state["pending_integration"] = None
        self.trace.event("subtask_integrated", subtask=subtask["id"],
                         source_commit=integration["source_commit"],
                         integrated_commit=integration["integrated_commit"],
                         resolution=integration["resolution"])
        return "continue"

    def _commit_parallel_integration(self, info, source, base, result, conflicts, resolution,
                                     report, pending):
        parent = self.workspace
        pending["status"] = "committing"
        self.state["pending_integration"] = pending
        self.checkpoint()
        try:
            commit = parent.commit(f"chore: 整合 {info['subtask']['id']}（{source[:10]}）")
        except WorkspaceError:
            parent.abort_integration()
            self.state["pending_integration"] = None
            return {"status": "failed", "reason": "integration_commit_failed", "report": report,
                    "conflicts": conflicts}
        parent.finish_integration()
        if not commit.get("created"):
            self.state["pending_integration"] = None
            return {"status": "failed", "reason": "empty_integration", "report": report,
                    "conflicts": conflicts}
        if parent.integration_conflicts():
            self.state["pending_integration"] = None
            return {"status": "failed", "reason": "unresolved_index_conflict", "report": report,
                    "conflicts": parent.integration_conflicts()}
        self.state["pending_integration"] = None
        return {"status": "integrated", "source_commit": source, "base_commit": base,
                "integrated_commit": commit["commit"], "resolution": resolution,
                "conflicts": conflicts, "report": report, "worktree": result.get("worktree")}

    def _resolve_parallel_conflict(self, info, result, source, base, conflicts, pending):
        parent, subtask = self.workspace, info["subtask"]
        payload = {"request": self.task.request, "task_brief": self.state.get("task_brief"),
                   "plan": planning.payload(self.state), "subtask": subtask,
                   "conflict": {"files": conflicts, "source_commit": source,
                                "base_commit": base, "source_worktree": result.get("worktree")},
                   "other_integrations": (self.state.get("integrations") or [])[-3:],
                   "note": "冲突来自两个独立子任务；保留双方功能，无法判断接口／业务取舍时交人。"}
        pending.update(status="resolving", conflicts=conflicts)
        self.state["pending_integration"] = pending
        self.checkpoint()
        try:
            with self._implementation_slot(f"integrate-{subtask['id']}"):
                report = parallel.resolve_conflict(task=self.task, settings=self.settings,
                    control=self.control, trace=self.trace, workspace=parent, payload=payload,
                    model=self.model, edits=self.edits, material_tools=self._material_tools())
        except Exception as exc:
            # 保留冲突现场和整合意图；显式recover后可继续同一整合，不要求先把工作树改干净。
            pending.update(status="conflict", error_type=type(exc).__name__, error=str(exc)[:500])
            self.state["pending_integration"] = pending
            self.checkpoint()
            raise
        if report.get("status") != "resolved":
            parent.abort_integration()
            self.state["pending_integration"] = None
            return {"status": "needs_input" if report.get("status") == "needs_clarification" else "failed",
                    "report": report, "conflicts": conflicts}
        markers = parent.unresolved_markers(conflicts)
        if markers:
            parent.abort_integration()
            self.state["pending_integration"] = None
            return {"status": "failed", "reason": "unresolved_conflict",
                    "report": report, "conflicts": markers}
        return self._commit_parallel_integration(info, source, base, result, conflicts, "model",
                                                 report, pending)

    def _integrate_parallel_result(self, info, result):
        """把子任务提交串行合入父整合工作区；普通冲突交给受控实现Agent。"""
        parent, subtask = self.workspace, info["subtask"]
        source, base = result["commit"], result["base_commit"]
        before = head_commit(parent.repository())
        parent.assert_clean(before)
        pending = {"subtask": subtask["id"], "attempt": info["attempt"], "source_commit": source,
                   "base_commit": base, "worktree": result.get("worktree"),
                   "parent_head": before, "status": "applying", "conflicts": [],
                   "started_at": timestamp()}
        self.state["pending_integration"] = pending
        self.checkpoint()
        clean, conflicts, applied = parallel.apply_integration(parent, source)
        if not clean and not conflicts:
            parent.abort_integration()
            self.state["pending_integration"] = None
            return {"status": "failed", "reason": "apply_failed",
                    "returncode": applied.returncode, "conflicts": []}
        if clean:
            return self._commit_parallel_integration(info, source, base, result, [], "clean",
                                                     None, pending)
        pending.update(status="conflict", conflicts=conflicts)
        self.state["pending_integration"] = pending
        self.checkpoint()
        return self._resolve_parallel_conflict(info, result, source, base, conflicts, pending)

    def _record_parallel_integration_block(self, info, result, integration, status):
        report = integration.get("report") or {}
        planning.record_result(self.state, info["subtask"], status=status,
            summary=report.get("summary") or integration.get("reason") or "内部整合未完成",
            evidence=[], decisions=[], source_commit=result.get("commit"),
            worktree=info.get("workspace").path if info.get("workspace") else None,
            base_commit=result.get("base_commit"), started_at=result.get("started_at"),
            integration={key: integration.get(key) for key in
                         ("status", "reason", "conflicts", "resolution")})
        self._record_parallel_run(info, "paused" if status == "conflict" else "failed",
                                  summary=report.get("summary"))
        self._append_integration({"subtask": info["subtask"]["id"], "status": status,
            "source_commit": result.get("commit"), "base_commit": result.get("base_commit"),
            "conflicts": integration.get("conflicts") or [],
            "report": report.get("summary")})

    def _resume_pending_integration(self):
        """恢复未完成整合：冲突现场继续修复；已提交则补记结果，清洁现场允许重试。"""
        pending = self.state.get("pending_integration")
        if not pending:
            return True
        subtask = next((item for item in (self.state.get("plan") or {}).get("subtasks") or []
                        if item.get("id") == pending.get("subtask")), None)
        if subtask is None:
            self.state["pending_integration"] = None
            return True
        parent = self.workspace
        worktree = Path(pending["worktree"]) if pending.get("worktree") else parent.path
        info = {"subtask": subtask, "attempt": pending.get("attempt") or uuid4().hex[:12],
                "workspace": Workspace(self.task.repository, self.store_root / "worktrees",
                                       path=worktree, base_commit=pending.get("base_commit")),
                "base_commit": pending.get("base_commit"), "started_at": pending.get("started_at")}
        head = head_commit(parent.repository())
        if head != pending.get("parent_head"):
            if parent.integration_conflicts():
                parent.abort_integration()
                self.state["pending_integration"] = None
                return True
            integration = {"status": "integrated", "source_commit": pending["source_commit"],
                           "base_commit": pending["base_commit"], "integrated_commit": head,
                           "resolution": "recovered", "conflicts": pending.get("conflicts") or [],
                           "report": None, "worktree": pending.get("worktree")}
            return self._record_completed_parallel(info, {"started_at": pending.get("started_at")},
                                                   integration, None) == "continue"
        if parent.integration_conflicts():
            integration = self._resolve_parallel_conflict(info, {"worktree": pending.get("worktree")},
                pending["source_commit"], pending["base_commit"],
                pending.get("conflicts") or parent.integration_conflicts(), pending)
            if integration["status"] == "integrated":
                return self._record_completed_parallel(info, {"started_at": pending.get("started_at")},
                                                       integration, integration.get("report")) == "continue"
            if integration["status"] == "needs_input":
                self._record_parallel_integration_block(info,
                    {"commit": pending["source_commit"], "base_commit": pending["base_commit"],
                     "worktree": pending.get("worktree"), "started_at": pending.get("started_at")},
                    integration, "conflict")
                self._register_parallel_block(info, "conflict",
                    (integration.get("report") or {}).get("summary") or "内部整合冲突",
                    (integration.get("report") or {}).get("questions")
                    or ["内部整合冲突需要确认保留哪一方行为。"], phase="integrate",
                    report=integration.get("report"), source_commit=pending["source_commit"])
                return True
            self._record_parallel_integration_block(info,
                {"commit": pending["source_commit"], "base_commit": pending["base_commit"],
                 "worktree": pending.get("worktree"), "started_at": pending.get("started_at")},
                integration, "failed")
            self.state["status"] = "phase_error"
            self.state["stop_reason"] = f"integration_{integration.get('reason') or 'failed'}"
            return False
        # 清洁且父HEAD未变：没有可接续的冲突，丢弃整合意图并按正常调度重试该子任务。
        parent.abort_integration()
        self.state["pending_integration"] = None
        return True

    def _handle_parallel_result(self, info, result):
        subtask = info["subtask"]
        report = result.get("report") or {}
        self.state["report"] = report
        self.trace.save(f"parallel-result-{info['attempt']}.json", report)
        decision_models = [Decision.model_validate(item) for item in report.get("decisions") or []]
        decisions = [item.model_dump(mode="json") for item in decision_models]
        knowledge.record_decisions(self.state, self.store_root, "implement", decision_models,
                                   (self.settings.api_key.get_secret_value(),))
        evidence = report.get("evidence") or []
        if report.get("status") == "needs_clarification":
            planning.record_result(self.state, subtask, status="needs_clarification",
                summary=report.get("summary") or "子任务需要澄清", evidence=evidence,
                decisions=decisions, source_commit=result.get("commit"),
                worktree=result.get("worktree"), base_commit=result.get("base_commit"),
                started_at=result.get("started_at"))
            self._record_parallel_run(info, "paused", summary=report.get("summary"))
            self._register_parallel_block(info, "clarification",
                report.get("summary") or "子任务需要澄清",
                report.get("questions") or [report.get("summary") or "需要补充信息"],
                phase="implement", report=report)
            return "continue"
        if report.get("status") != "implemented" or not result.get("commit"):
            status = report.get("status") or "blocked"
            planning.record_result(self.state, subtask, status=status,
                summary=report.get("summary") or "子任务阻塞", evidence=evidence,
                decisions=decisions, source_commit=result.get("commit"),
                worktree=result.get("worktree"), base_commit=result.get("base_commit"),
                started_at=result.get("started_at"))
            self._record_parallel_run(info, "blocked", summary=report.get("summary"))
            self._register_parallel_block(info, status, report.get("summary") or "子任务阻塞",
                                          report.get("questions") or [], phase="implement",
                                          report=report)
            return "continue"
        integration = self._integrate_parallel_result(info, result)
        if integration["status"] != "integrated":
            status = "conflict" if integration["status"] == "needs_input" else "failed"
            planning.record_result(self.state, subtask, status=status,
                summary=(integration.get("report") or {}).get("summary") or integration.get("reason")
                        or "内部整合未完成", evidence=evidence, decisions=decisions,
                source_commit=result.get("commit"), worktree=result.get("worktree"),
                base_commit=result.get("base_commit"), started_at=result.get("started_at"),
                integration={key: integration.get(key) for key in
                             ("status", "reason", "conflicts", "resolution")})
            self._record_parallel_run(info, "paused" if status == "conflict" else "failed",
                                      summary=(integration.get("report") or {}).get("summary"))
            self._append_integration({"subtask": subtask["id"], "status": status,
                "source_commit": result.get("commit"), "base_commit": result.get("base_commit"),
                "conflicts": integration.get("conflicts") or [],
                "report": (integration.get("report") or {}).get("summary")})
            if status == "conflict":
                question = ((integration.get("report") or {}).get("questions")
                            or ["内部整合冲突需要确认保留哪一方行为；请补充接口／业务取舍。"])
                self._register_parallel_block(info, "conflict",
                    (integration.get("report") or {}).get("summary") or "内部整合冲突",
                    question, phase="integrate", report=integration.get("report"),
                    source_commit=result.get("commit"))
            else:
                self.state["status"] = "phase_error"
                self.state["stop_reason"] = f"integration_{integration.get('reason') or 'failed'}"
                return "fatal"
            return "continue"
        return self._record_completed_parallel(info, result, integration, report)

    def _implement_parallel(self):
        """按依赖启动最多2个独立worktree实现，再串行整合到父工作区。"""
        if self.workspace is None:
            raise StateError("plan_requires_workspace")
        parent = self.workspace
        limit = min(2, self.settings.max_implementation_slots)
        slots = SlotManager(self.store_root, limit=limit,
                            lease_seconds=max(120, int(self.settings.run_timeout_seconds) + 60))
        material_tools = self._material_tools()
        self._recover_parallel_runs()
        if not self._resume_pending_integration():
            return
        running, stop = {}, False
        stop_status = stop_reason = None
        with ThreadPoolExecutor(max_workers=limit) as pool:
            try:
                while True:
                    for info in running.values():
                        slots.touch(info["slot"])
                    done = set()
                    if running:
                        done, _ = wait(list(running), timeout=1, return_when=FIRST_COMPLETED)
                    for future in list(done):
                        info = running.pop(future)
                        slots.release(info["slot"])
                        try:
                            result = future.result()
                        except Exception as exc:
                            self.trace.event("parallel_worker_failed", subtask=info["subtask"]["id"],
                                             error_type=type(exc).__name__)
                            self._record_parallel_run(info, "failed", error_type=type(exc).__name__)
                            stop = True
                            if isinstance(exc, RunLimit):
                                stop_status, stop_reason = "limit_reached", str(exc)
                            else:
                                stop_status, stop_reason = "phase_error", f"parallel_{type(exc).__name__}"
                            break
                        if self._handle_parallel_result(info, result) == "fatal":
                            stop = True
                            break
                        self.checkpoint()
                    if stop:
                        break
                    while len(running) < limit:
                        subtask = self._parallel_ready({item["subtask"]["id"] for item in running.values()})
                        if subtask is None:
                            break
                        planning.dependencies_done(self.state, subtask, parent.repository())
                        parent.assert_clean(head_commit(parent.repository()))
                        base_commit = head_commit(parent.repository())
                        workspace = parallel.create_worktree(self.task, self.store_root,
                            self.state["run_id"], subtask["id"], base_commit)
                        owner = f"{self.state['run_id']}:{subtask['id']}:{uuid4().hex[:8]}"
                        try:
                            slots.acquire(owner, timeout=self.settings.implementation_slot_timeout_seconds)
                        except SlotFault as exc:
                            workspace.release()
                            stop, stop_status, stop_reason = True, "limit_reached", str(exc)
                            break
                        info = {"subtask": subtask, "workspace": workspace, "base_commit": base_commit,
                                "slot": owner, "attempt": uuid4().hex[:12], "started_at": timestamp()}
                        self._record_parallel_run(info, "running")
                        self.trace.event("parallel_subtask_dispatched", subtask=subtask["id"],
                                         base_commit=base_commit, worktree=str(workspace.path))
                        self.checkpoint()
                        control = self.control.fork("implement")
                        trace = self.trace.fork()
                        future = pool.submit(parallel.run_subtask, task=self.task,
                            settings=self.settings, control=control, trace=trace,
                            workspace=workspace, payload=self._parallel_payload(subtask, base_commit),
                            model=self.model, edits=self.edits, material_tools=material_tools)
                        running[future] = info
                    if stop:
                        break
                    if not running:
                        if planning.status(self.state)["state"] == "completed":
                            break
                        if any(not item.get("handled")
                               for item in self.state.get("parallel_blocked") or []):
                            self._pause_parallel_blocks()
                            stop = True
                            break
                        if any(item.get("status") != "completed"
                               for item in (self.state.get("plan_progress") or {}).get("results") or []):
                            break
                        raise PlanFault("parallel_schedule_stalled")
            finally:
                # 停止派发后等待已开始worker结束；不把未处理结果悄悄整合。
                for future, info in list(running.items()):
                    try:
                        future.result()
                    except Exception:
                        pass
                    slots.release(info["slot"])
                    self._record_parallel_run(info, "interrupted")
                running.clear()
                self.checkpoint()
        if stop_status and self.state["status"] == "running":
            self.state["status"], self.state["stop_reason"] = stop_status, stop_reason
        if stop:
            return
        status = planning.status(self.state)["state"]
        if status != "completed":
            self.state["status"], self.state["stop_reason"] = "phase_error", "parallel_schedule_incomplete"
            return
        if self.state["review_enabled"]:
            self.state["phase"] = "anchor"
        else:
            self.state["status"], self.state["stop_reason"] = "completed", "report"

    def anchor(self):
        if self.state.get("plan") and planning.status(self.state)["state"] != "completed":
            raise StateError("plan_incomplete")
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
        if self.state.get("human_answers"):
            payload["human_answers"] = self.state["human_answers"]
        if self.state.get("task_brief"):
            payload["task_brief"] = self.state["task_brief"]
        plan_view = planning.payload(self.state)
        if plan_view:
            payload["plan"] = plan_view
        if self.state.get("integrations"):
            payload["integrations"] = self.state["integrations"][-8:]
        if self.state["history"]:
            payload["previous_findings"] = self.state["history"][-1]["findings"]
        self._attach_memory(payload, 'review')
        return payload

    def review(self):
        self.workspace.assert_clean(self.state["reviewed_commit"])
        payload = self._review_payload()
        self.trace.event("review_started", commit=self.state["reviewed_commit"])
        with repository_session(self.task, self.workspace.repository(), writable=False,
                                deadline=self.control.remaining, trace=self.trace) as session:
            repository = Repository(self.workspace.path) if session is None else session
            tools = engine.build_tools(self.task, self.control.remaining, workspace=None,
                repository=repository,
                **({} if session is None else {"checker": session.run_check}))
            report = engine.run_phase(prompt=REVIEW_PROMPT, schema=ReviewReport,
                payload=payload, tools=tools + self._material_tools(),
                settings=self.settings, control=self.control, model=self.model, phase="review")
        self.workspace.assert_clean(self.state["reviewed_commit"])
        if report.verdict == "approved" and not engine.verification_passed(self.task, self.state["verified"]):
            report = report.model_copy(update={"verdict": "unable_to_review", "summary": "本地复核未全部通过，拒绝模型批准。"})
        self.state["review_report"] = report.model_dump(mode="json")
        self.trace.save("review.json", self.state["review_report"])
        knowledge.record_decisions(self.state, self.store_root, "review", report.decisions,
                                   (self.settings.api_key.get_secret_value(),))
        self.state["history"].append({"attempt": len(self.state["history"]), "commit": self.state["reviewed_commit"],
            "knowledge_snapshot_id": (self.state.get("knowledge_snapshot") or {}).get("snapshot_id"),
            "plan_version": (self.state.get("plan") or {}).get("version"),
            "verdict": report.verdict, "summary": report.summary, "findings": engine.findings_brief(report.findings)})
        self.trace.save("review-history.json", self.state["history"])
        self.trace.event("review_finished", verdict=report.verdict, commit=self.state["reviewed_commit"])
        if report.verdict == "unable_to_review" and report.questions:
            human.wait_for_clarification(self.state, self.store_root, summary=report.summary,
                questions=report.questions, limit=self.settings.max_question_batches,
                blocking={"phase": "review"})
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
