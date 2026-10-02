"""RepoPilot 的路径、模型和密钥配置。"""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs" / "baseline.yaml"
AGENT_CONFIG_PATH = PROJECT_ROOT / "configs" / "agent.yaml"
TASKS_DIR = PROJECT_ROOT / "evals" / "tasks"
RUNS_DIR = PROJECT_ROOT / "evals" / "runs"


@dataclass(frozen=True)
class BaselineSettings:
    workspace_id: str | None
    api_key: str | None
    provider: str
    region: str
    model: str
    system_prompt: str
    thinking_enabled: bool
    response_format: str
    repetitions_per_task: int
    timeout_seconds: int
    input_price_per_million: float
    output_price_per_million: float
    repo_dir: Path
    tasks_dir: Path = TASKS_DIR
    runs_dir: Path = RUNS_DIR

    @property
    def base_url(self) -> str:
        if not self.workspace_id:
            raise ValueError("缺少 DASHSCOPE_WORKSPACE_ID（兼容旧变量 WORKSPACE_ID）")
        return (
            f"https://{self.workspace_id}.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1"
        )

    def validate(self, *, require_credentials: bool) -> None:
        if require_credentials and not self.api_key:
            raise ValueError("缺少 DASHSCOPE_API_KEY（兼容旧变量 API_KEY）")
        if require_credentials and not self.workspace_id:
            raise ValueError("缺少 DASHSCOPE_WORKSPACE_ID（兼容旧变量 WORKSPACE_ID）")
        if not self.repo_dir.is_dir():
            raise FileNotFoundError(f"干净仓库不存在: {self.repo_dir}")
        if not self.tasks_dir.is_dir():
            raise FileNotFoundError(f"任务目录不存在: {self.tasks_dir}")
        if self.repetitions_per_task < 1:
            raise ValueError("repetitions_per_task 必须大于等于 1")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds 必须大于等于 1")


def load_settings(*, require_credentials: bool = True) -> BaselineSettings:
    """从固定项目路径加载非敏感 YAML，并从环境变量读取密钥。"""
    load_dotenv(PROJECT_ROOT / ".env")

    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(f"基线配置不存在: {CONFIG_PATH}")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"基线配置必须是 YAML 对象: {CONFIG_PATH}")

    pricing = config.get("pricing", {})
    settings = BaselineSettings(
        workspace_id=(
            os.getenv("DASHSCOPE_WORKSPACE_ID") or os.getenv("WORKSPACE_ID")
        ),
        api_key=os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY"),
        provider=str(config["provider"]),
        region=str(config["region"]),
        model=str(config["model"]),
        system_prompt=str(config["system_prompt"]).strip(),
        thinking_enabled=bool(config["thinking_enabled"]),
        response_format=str(config["response_format"]),
        repetitions_per_task=int(config["repetitions_per_task"]),
        timeout_seconds=int(config["timeout_seconds"]),
        input_price_per_million=float(pricing["input_per_million_tokens"]),
        output_price_per_million=float(pricing["output_per_million_tokens"]),
        repo_dir=PROJECT_ROOT / str(config["repository_path"]),
    )
    settings.validate(require_credentials=require_credentials)
    return settings


@dataclass(frozen=True, kw_only=True)
class AgentSettings(BaselineSettings):
    max_steps: int  # 整个agent运行最多几轮模型调用
    max_tool_calls: int  # 整个agent运行最多调用多少次工具
    max_total_tokens: int  # 累计token上限
    max_cost: float  # 累计估算成本上限
    tool_timeout_seconds: int  # 单次工具调用超时时间
    run_timeout_seconds: int  # 整个agent运行时间限制
    max_tool_output_chars: int  # 单次工具调用输出字符数上限

    def validate(self, *, require_credentials: bool) -> None:
        super().validate(require_credentials=require_credentials)
        
        limits = {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_total_tokens": self.max_total_tokens,
            "max_cost": self.max_cost,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "run_timeout_seconds": self.run_timeout_seconds,
            "max_tool_output_chars": self.max_tool_output_chars,
        }

        for name, value in limits.items():
            if value <= 0:
                raise ValueError(f"{name} 必须大于 0")


def load_agent_settings(*, require_credentials: bool = True) -> AgentSettings:
    """从固定项目路径加载非敏感 YAML，并从环境变量读取密钥。"""
    load_dotenv(PROJECT_ROOT / ".env")

    if not AGENT_CONFIG_PATH.is_file():
        raise FileNotFoundError(f"智能体配置不存在: {AGENT_CONFIG_PATH}")

    config = yaml.safe_load(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"智能体配置必须是 YAML 对象: {AGENT_CONFIG_PATH}")

    pricing = config.get("pricing", {})
    settings = AgentSettings(
        workspace_id=(
            os.getenv("DASHSCOPE_WORKSPACE_ID") or os.getenv("WORKSPACE_ID")
        ),
        api_key=os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY"),
        provider=str(config["provider"]),
        region=str(config["region"]),
        model=str(config["model"]),
        system_prompt=str(config["system_prompt"]).strip(),
        thinking_enabled=bool(config["thinking_enabled"]),
        response_format=str(config["response_format"]),
        repetitions_per_task=int(config["repetitions_per_task"]),
        timeout_seconds=int(config["timeout_seconds"]),
        input_price_per_million=float(pricing["input_per_million_tokens"]),
        output_price_per_million=float(pricing["output_per_million_tokens"]),
        repo_dir=PROJECT_ROOT / str(config["repository_path"]),
        max_steps=int(config["limit"]["max_steps"]),
        max_tool_calls=int(config["limit"]["max_tool_calls"]),
        max_total_tokens=int(config["limit"]["max_total_tokens"]),
        max_cost=float(config["limit"]["max_cost"]),
        tool_timeout_seconds=int(config["limit"]["tool_timeout_seconds"]),
        run_timeout_seconds=int(config["limit"]["run_timeout_seconds"]),
        max_tool_output_chars=int(config["limit"]["max_tool_output_chars"]),
    )
    settings.validate(require_credentials=require_credentials)
    return settings
