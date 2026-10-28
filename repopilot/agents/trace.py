"""本地 Agent 证据记录：脱敏、原子文件写入与事件追加。"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from threading import Lock


class Trace:
    def __init__(self, directory: Path, secret: str):
        self.directory, self.secret = directory, secret
        self.lock = Lock()
        self.on_event = None

    def fork(self):
        """并行worker共用事件文件锁，但不触发父会话的检查点回调。"""
        clone = Trace(self.directory, self.secret)
        clone.lock = self.lock
        return clone

    def encode(self, value) -> str:
        text = json.dumps(value, ensure_ascii=False, indent=2)
        return text.replace(json.dumps(self.secret, ensure_ascii=False)[1:-1], "[REDACTED]")

    def save(self, name: str, value) -> None:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                         prefix=".writing-", delete=False) as stream:
            path = Path(stream.name)
            stream.write(self.encode(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, self.directory / name)

    def event(self, event: str, **data) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        with self.lock, (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(json.loads(self.encode(record)), ensure_ascii=False) + "\n")
        if self.on_event is not None:
            self.on_event()
