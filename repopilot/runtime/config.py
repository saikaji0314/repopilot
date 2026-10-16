"""Validated task contracts and credentials, without mutating process environment."""

import os
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
    max_model_calls: int = Field(default=6, gt=0, le=20)
    max_tool_calls: int = Field(default=12, gt=0, le=50)
    max_output_tokens: int = Field(default=2048, gt=0, le=8192)
    max_context_bytes: int = Field(default=60_000, gt=0, le=500_000)
    run_timeout_seconds: float = Field(default=120, gt=0, le=1800)
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_run_cost_cny: float = Field(default=1, gt=0)
    budget_cny: float = Field(default=200, gt=0)
    # Conservative estimates, not a claim about the provider's actual bill.
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
        budget_cny=get("REPOPILOT_BUDGET_CNY", 200),
    )
