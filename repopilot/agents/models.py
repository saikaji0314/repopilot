"""供应商适配边界；工作流只依赖 LangChain 的消息和工具接口。"""

from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from repopilot.core.config import RuntimeSettings


class DeepSeekModel(ChatDeepSeek):
    """补齐当前适配器遗漏的思考字段回传；不记录或展示原始思考文本。"""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if (self.extra_body or {}).get("thinking", {}).get("type") == "enabled":
            # ToolStrategy 强制 required，但 DeepSeek 思考模式只接受 auto／none。
            if payload.get("tool_choice") == "required" or isinstance(payload.get("tool_choice"), dict):
                payload["tool_choice"] = "auto"
            messages = self._convert_input(input_).to_messages()
            for original, outbound in zip(messages, payload["messages"], strict=True):
                if outbound["role"] == "assistant":
                    outbound["reasoning_content"] = original.additional_kwargs.get("reasoning_content") or ""
        return payload


class CompatibleModel(ChatOpenAI):
    """只走 Chat Completions，使用兼容服务普遍接受的 max_tokens 字段。"""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload


def make_model(settings: RuntimeSettings):
    common = dict(model=settings.model, api_key=settings.api_key, base_url=settings.base_url,
                  max_tokens=settings.max_output_tokens, timeout=settings.request_timeout_seconds,
                  max_retries=0, streaming=False)
    if settings.provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ValueError('Anthropic requires: pip install -c constraints.txt -e ".[anthropic]"') from exc
        return ChatAnthropic(**common)
    if settings.provider in {"openai-compatible", "qwen"}:
        # Qwen 混合思考模型的默认行为不统一，此入口明确使用非思考工具调用。
        extra = {"enable_thinking": False} if settings.provider == "qwen" else {}
        return CompatibleModel(**common, extra_body=extra, use_responses_api=False, stream_usage=False)
    return DeepSeekModel(
        model=settings.model, api_key=settings.api_key, api_base=settings.base_url,
        max_tokens=settings.max_output_tokens, timeout=settings.request_timeout_seconds,
        max_retries=0, streaming=False,
        extra_body={"thinking": {"type": settings.thinking}, "reasoning_effort": settings.reasoning_effort},
    )
