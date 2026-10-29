"""资料元数据、有界文本处理和章节切分；不依赖模型。"""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from pydantic import Field

from repopilot.core.config import Contract
from repopilot.storage.jobs import StateError


class KnowledgeFault(StateError):
    pass


class Material(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    kind: Literal["project", "task", "decision", "evidence"]
    title: str = Field(min_length=1, max_length=200)
    scope: str = Field(default="repository", min_length=1, max_length=120)
    required: bool = False
    sources: list[str] = Field(default_factory=list, max_length=20)
    relations: dict[str, list[str]] = Field(default_factory=dict)


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def checked_body(text, secrets=()):
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not text.strip() or "\0" in text or len(text.encode("utf-8")) > 256_000:
        raise KnowledgeFault("knowledge_body_invalid")
    if any(secret and secret in text for secret in secrets) or re.search(
        r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pour]_[A-Za-z0-9]{30,}|ghs_[A-Za-z0-9_.-]{20,}|github_pat_[A-Za-z0-9_]{20,})", text
    ):
        raise KnowledgeFault("knowledge_possible_secret")
    return text


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".writing-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def terms(text):
    """英文词及中文重叠双字，作为FTS派生词项，不复制一份可编辑正文。"""
    result = re.findall(r"[a-z_][a-z_0-9]{1,}", text.casefold())
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(segment[i:i + 2] for i in range(max(1, len(segment) - 1)))
    return result


def chunks(text):
    """按标题、空行、大小及200行读取上限划分，保留稳定块键。"""
    lines, result, seen = text.splitlines(), [], {}
    start, heading, buffer = 1, "", []

    def emit():
        if not buffer or not any(line.strip() for line in buffer):
            return
        body = "\n".join(buffer)
        key = digest([heading, body])
        ordinal = seen.get(key, 0)
        seen[key] = ordinal + 1
        result.append({"key": f"{key}:{ordinal}", "heading": heading,
                       "start_line": start, "end_line": start + len(buffer) - 1,
                       "terms": " ".join(terms(body))})

    for number, line in enumerate(lines, 1):
        if (line.startswith("#") or (not line.strip() and buffer) or len(buffer) >= 200 or
                sum(map(len, buffer)) > 1800):
            emit()
            buffer, start = [], number
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        if not buffer:
            start = number
        buffer.append(line)
    emit()
    return result
