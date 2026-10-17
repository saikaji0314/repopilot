"""经过校验的任务契约与凭证读取，不修改进程环境变量。"""

import os
from typing import Literal
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)


class Check(Contract):
    argv: list[str] = Field(min_length=1, max_length=40)
    cwd: str = "."
    timeout_seconds: float = Field(default=30, gt=0, le=300)

    @field_validator("argv")
    @classmethod
    def valid_argv(cls, value):
        if any(not item or "\0" in item for item in value):
            raise ValueError("command arguments must be nonempty and contain no NUL")
        if Path(value[0]).suffix.lower() in {".bat", ".cmd", ".ps1"}:
            raise ValueError("register an executable, not a shell script")
        return value


class Task(Contract):
    schema_version: str = "2.0"
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    request: str = Field(min_length=1, max_length=8000)
    repository: Path
    checks: dict[str, Check] = Field(default_factory=dict, max_length=10)

    @field_validator("schema_version")
    @classmethod
    def version_supported(cls, value):
        if value != "2.0":
            raise ValueError("use the version 2.0 task contract")
        return value


def load_task(path: Path) -> Task:
    path = path.resolve(strict=True)
    task = Task.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    repository = (path.parent / task.repository).resolve(strict=True)
    if not repository.is_dir():
        raise ValueError("repository must be a directory")
    return task.model_copy(update={"repository": repository})


class RuntimeSettings(Contract):
    api_key: SecretStr = Field(repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    thinking: Literal["enabled", "disabled"] = "disabled"
    reasoning_effort: Literal["low", "high", "max"] = "low"
    memory_enabled: bool = False
    max_model_calls: int = Field(default=36, gt=0, le=80)
    max_tool_calls: int = Field(default=40, gt=0, le=200)
    max_edits: int = Field(default=12, gt=0, le=50)
    max_rework: int = Field(default=3, ge=0, le=5)
    max_output_tokens: int = Field(default=2048, gt=0, le=8192)
    max_context_bytes: int = Field(default=60_000, gt=0, le=500_000)
    run_timeout_seconds: float = Field(default=300, gt=0, le=3600)
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_run_cost_cny: float = Field(default=2, gt=0)
    budget_cny: float = Field(default=200, gt=0)
    # 保守估算参数，不代表供应商的实际账单单价。
    input_cny_per_million: float = Field(default=3, gt=0)
    output_cny_per_million: float = Field(default=12, gt=0)

    @field_validator("api_key")
    @classmethod
    def key_present(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("DS_API_KEY is required")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value):
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or
                url.password or url.query or url.fragment):
            raise ValueError("DS_BASE_URL must be an HTTPS URL without credentials")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def model_present(cls, value):
        if not value.strip():
            raise ValueError("DS_MODEL must not be empty")
        return value.strip()


def load_settings(env_file: Path = PROJECT_ROOT / ".env") -> RuntimeSettings:
    file_values = dotenv_values(env_file, interpolate=False) if env_file.exists() else {}

    def get(name, default):
        return os.environ.get(name, file_values.get(name)) or default

    return RuntimeSettings(
        api_key=get("DS_API_KEY", ""),
        base_url=get("DS_BASE_URL", "https://api.deepseek.com"),
        model=get("DS_MODEL", "deepseek-flash"),
        thinking=get("DS_THINKING", "enabled"),
        reasoning_effort=get("DS_REASONING_EFFORT", "low"),
        budget_cny=get("REPOPILOT_BUDGET_CNY", 200),
        memory_enabled=get("REPOPILOT_MEMORY_ENABLED", "false"),
    )


EXECUTION_OPTIONS = {"max_model_calls", "max_tool_calls", "max_edits", "max_rework", "max_output_tokens",
                     "max_context_bytes", "run_timeout_seconds", "request_timeout_seconds", "max_run_cost_cny",
                     "thinking", "reasoning_effort", "memory_enabled"}


def execution_options(value):
    """公开配置只接收执行参数，凭证、模型端点和累计预算不能藏在 profile 中。"""
    if not isinstance(value, dict) or set(value) - EXECUTION_OPTIONS:
        raise ValueError("profile only accepts execution limits and thinking options")
    RuntimeSettings.model_validate({"api_key": "validation-only", **value})
    return value


def load_profile(path):
    return execution_options(yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")))
