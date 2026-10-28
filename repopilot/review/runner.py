"""只读审核执行：两视角、争议 A／B 与核对 C；不修改代码或验收。

每次调用都有独立的控制面／日志分叉，共享同一运行额度；只提供读取和登记检查工具。
并行执行的是同一固定提交，因此两个视角不会看到对方结论，也不会看到实现方辩解。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from repopilot.agents import loop as engine
from repopilot.agents.prompts import (ARBITRATION_PROMPT, DISPUTE_REVIEW_PROMPT,
                                      REVIEW_CONTEXT_PROMPT, REVIEW_CORRECTNESS_PROMPT)
from repopilot.agents.reports import ArbitrationReport, DisputeReviewReport, ReviewReport
from repopilot.execution.files import Repository
from repopilot.execution.sandbox import repository_session

from .contracts import PERSPECTIVES

REVIEW_PROMPTS = {
    "correctness": REVIEW_CORRECTNESS_PROMPT,
    "context": REVIEW_CONTEXT_PROMPT,
}


def _readonly_tools(task, control, root, session, materials):
    repository = Repository(root) if session is None else session
    tools = engine.build_tools(task, control.remaining, workspace=None, repository=repository,
                               **({} if session is None else {"checker": session.run_check}))
    return tools + list(materials or [])


def run_reviewer(*, task, settings, control, trace, root, payload, perspective, model, materials,
                 session_factory=None):
    """一个审核视角：只读源码与资料，可自行运行已登记检查。"""

    factory = session_factory or repository_session
    with factory(task, root, writable=False, deadline=control.remaining,
                 trace=trace) as session:
        tools = _readonly_tools(task, control, root, session, materials)
        report = engine.run_phase(prompt=REVIEW_PROMPTS[perspective], schema=ReviewReport,
            payload=payload, tools=tools, settings=settings, control=control,
            model=model, phase="review")
    trace.save(f"review-{perspective}.json", report.model_dump(mode="json"))
    return report


def run_pair(*, task, settings, control, trace, root, payloads, model, materials,
             perspectives=PERSPECTIVES, on_report=None, session_factory=None):
    """两个常规审核视角并行、首轮上下文独立；返回 {perspective: ReviewReport}。"""

    reports, errors = {}, {}
    targets = [name for name in perspectives if name in PERSPECTIVES]

    def work(perspective):
        worker = control.fork("review")
        worker_trace = trace.fork()
        return run_reviewer(task=task, settings=settings, control=worker, trace=worker_trace,
                            root=root, payload=payloads[perspective], perspective=perspective,
                            model=model, materials=materials, session_factory=session_factory)

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
        futures = {pool.submit(work, perspective): perspective for perspective in targets}
        for future in as_completed(futures):
            perspective = futures[future]
            try:
                report = future.result()
            except BaseException as exc:
                errors[perspective] = exc
                trace.event("review_perspective_error", perspective=perspective,
                            error_type=type(exc).__name__)
                continue
            reports[perspective] = report
            if on_report is not None:
                # 逐份落盘：进程在另一视角完成前退出时，恢复不必重跑已完成的审核。
                on_report(perspective, report)
    if errors:
        raise _primary(errors)
    return reports


def run_dispute_reviewer(*, side, task, settings, control, trace, root, payload, model, materials,
                         session_factory=None):
    """争议复核 A／B 之一：只读核对同一争议，不引用另一侧结论。"""

    factory = session_factory or repository_session
    with factory(task, root, writable=False, deadline=control.remaining,
                 trace=trace) as session:
        tools = _readonly_tools(task, control, root, session, materials)
        report = engine.run_phase(prompt=DISPUTE_REVIEW_PROMPT, schema=DisputeReviewReport,
            payload=payload, tools=tools, settings=settings, control=control,
            model=model, phase="dispute")
    trace.save(f"dispute-{side}.json", report.model_dump(mode="json"))
    return report


def run_dispute_pair(*, task, settings, control, trace, root, payloads, model, materials,
                     sides=("a", "b"), on_report=None, session_factory=None):
    reports, errors = {}, {}
    targets = [side for side in sides if side in {"a", "b"}]

    def work(side):
        worker = control.fork("dispute")
        worker_trace = trace.fork()
        return run_dispute_reviewer(side=side, task=task, settings=settings, control=worker,
                                    trace=worker_trace, root=root, payload=payloads[side],
                                    model=model, materials=materials,
                                    session_factory=session_factory)

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
        futures = {pool.submit(work, side): side for side in targets}
        for future in as_completed(futures):
            side = futures[future]
            try:
                report = future.result()
            except BaseException as exc:
                errors[side] = exc
                trace.event("dispute_reviewer_error", side=side, error_type=type(exc).__name__)
                continue
            reports[side] = report
            if on_report is not None:
                # 每侧结果立即落盘；恢复只运行缺失侧，不重跑已完成的复核。
                on_report(side, report)
    if errors:
        raise _primary(errors)
    return reports


def run_arbitration(*, task, settings, control, trace, root, payload, model, materials,
                    session_factory=None):
    """争议核对 C：比较双方证据，必要时用登记检查复现。"""

    factory = session_factory or repository_session
    with factory(task, root, writable=False, deadline=control.remaining,
                 trace=trace) as session:
        tools = _readonly_tools(task, control, root, session, materials)
        report = engine.run_phase(prompt=ARBITRATION_PROMPT, schema=ArbitrationReport,
            payload=payload, tools=tools, settings=settings, control=control,
            model=model, phase="arbitration")
    trace.save("dispute-arbitration.json", report.model_dump(mode="json"))
    return report


def _primary(errors):
    for exc in errors.values():
        if not isinstance(exc, KeyboardInterrupt):
            from repopilot.storage.budget import RunLimit
            if isinstance(exc, RunLimit):
                return exc
    for exc in errors.values():
        if not isinstance(exc, KeyboardInterrupt):
            return exc
    return next(iter(errors.values()))
