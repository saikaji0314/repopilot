"""解释最后一次心跳；不修改历史状态，也不把新鲜心跳等同完整进程健康。"""

from datetime import datetime, timezone

STALE_AFTER_SECONDS = 30


def heartbeat_health(record, *, now=None):
    result = {"pid": None, "heartbeat_at": None, "recorded_status": None,
              "status": "not_started", "healthy": False, "age_seconds": None,
              "stale_after_seconds": STALE_AFTER_SECONDS, "reason": "no_heartbeat"}
    if record is None:
        return result
    result.update(pid=record["pid"], heartbeat_at=record["heartbeat_at"], recorded_status=record["status"])
    result.update(status="unknown", reason="invalid_heartbeat")
    if not isinstance(record["heartbeat_at"], str):
        return result
    try:
        observed = datetime.fromisoformat(record["heartbeat_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return result
        age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
    except (TypeError, ValueError):
        return result
    if age < -5:
        result["reason"] = "clock_skew"
        return result
    result["age_seconds"] = round(max(0, age), 3)
    if record["status"] == "stopped":
        result.update(status="stopped", reason="last_shutdown_recorded")
    elif record["status"] == "running":
        stale = age > STALE_AFTER_SECONDS
        result.update(status="stale" if stale else "running", healthy=not stale,
                      reason="heartbeat_expired" if stale else "recent_heartbeat_only")
    return result
