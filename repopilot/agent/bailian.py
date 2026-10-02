from openai import OpenAI
from repopilot.config import AgentSettings
from repopilot.agent.schemas import ModelTurn, ToolCall, Usage


class BailianChatModel:
    def __init__(self, settings: AgentSettings):
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
        )
        self.model = settings.model
        self.thinking_enabled = settings.thinking_enabled
        self.response_format = settings.response_format

    def complete(self, messages: list[dict], tools: list[dict], *, tool_choice: str | None = None) -> ModelTurn:
        request = dict(
            model=self.model,
            messages=messages,
            tools=tools,
            extra_body={"enable_thinking": self.thinking_enabled},
            response_format={"type": "json_object" if self.response_format == "json_object" else "text"},
        )
        if tool_choice is not None:
            request["tool_choice"] = tool_choice
        response = self.client.chat.completions.create(**request)
        if not response.choices or not response.usage:
            raise ValueError("OpenAI API response is empty")
        return ModelTurn(
            content=response.choices[0].message.content,
            tool_calls=[] if not response.choices[0].message.tool_calls else [ToolCall(
                id=tool_call.id,
                name=tool_call.function.name,
                arguments_json=tool_call.function.arguments,
            ) for tool_call in response.choices[0].message.tool_calls],
            finish_reason=response.choices[0].finish_reason,
            usage=Usage.model_validate(response.usage, from_attributes=True),
        )
