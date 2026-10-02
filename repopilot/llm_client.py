"""阿里云百炼 OpenAI 兼容接口封装。"""

from openai import OpenAI

from repopilot.config import BaselineSettings


def create_client(settings: BaselineSettings) -> OpenAI:
    if not settings.api_key:
        raise ValueError("API Key 未配置")
    return OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
    )


def call_llm(
    client: OpenAI,
    settings: BaselineSettings,
    user_prompt: str,
):
    request: dict = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": settings.system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "extra_body": {"enable_thinking": settings.thinking_enabled},
        "timeout": settings.timeout_seconds,
    }
    if settings.response_format == "json_object":
        request["response_format"] = {"type": "json_object"}

    return client.chat.completions.create(**request)
