"""集中提问的编号、去重、批次计数与最终缺口；不调用模型或平台。"""

from repopilot.storage.jobs import timestamp
from repopilot.planning.contracts import normalize

DEFAULT_LIMIT = 3
MAX_QUESTIONS = 10


def clean(questions, fallback):
    """同一批问题去重并编号；完全没有问题时才用摘要作为一条具体请求。"""
    result, seen = [], set()
    for item in questions:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text[:1000])
        if len(result) >= MAX_QUESTIONS:
            break
    if result:
        return result
    text = " ".join(str(fallback or "").split())
    return [text[:1000]] if text else []


def blocking_items(questions, phase, subtask=None):
    return [{"question": item, "phase": phase, "subtask": subtask} for item in questions]


def used(state):
    return len(state.get("question_batches") or [])


def limit(state):
    return int(state.get("question_limit") or DEFAULT_LIMIT)


def record(state, request):
    """批次在请求落盘的同一次检查点里登记，不依赖后续网络成功。"""
    if request.final:
        state.setdefault("question_handovers", []).append({
            "request_id": request.id, "version": request.version, "questions": request.questions,
            "blocking": request.blocking, "opened_at": request.created_at, "answered_at": None})
        return
    state.setdefault("question_batches", []).append({
        "batch": request.batch, "request_id": request.id, "version": request.version,
        "questions": request.questions, "blocking": request.blocking,
        "opened_at": request.created_at, "answered_at": None, "answer_revision": None})


def mark_answered(state, request):
    """答复后只追加时间和版本，不删除原批次记录。"""
    for collection in ("question_batches", "question_handovers"):
        for item in state.get(collection) or []:
            if item.get("request_id") == request.id:
                item["answered_at"] = timestamp()
                item["answer_revision"] = state.get("requirements_revision")


def batches_for(state):
    return list(state.get("question_batches") or [])


def summary(state):
    return {"limit": limit(state), "used": used(state),
            "batches": [{"batch": item.get("batch"), "request_id": item.get("request_id"),
                         "questions": item.get("questions"), "opened_at": item.get("opened_at"),
                         "answered_at": item.get("answered_at")}
                        for item in batches_for(state)],
            "handovers": list(state.get("question_handovers") or [])}
