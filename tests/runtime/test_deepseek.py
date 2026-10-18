from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from repopilot.agents.loop import make_model
from repopilot.core.config import RuntimeSettings


def test_thinking_preserves_provider_field_and_uses_supported_tool_choice():
    model = make_model(RuntimeSettings(api_key='synthetic', thinking='enabled'))
    messages = [HumanMessage(content='inspect'), AIMessage(content='', additional_kwargs={'reasoning_content': 'synthetic-provider-field'},
        tool_calls=[{'id': 'call1', 'name': 'read_file', 'args': {'path': 'app.py'}}]),
        ToolMessage(content='source', tool_call_id='call1')]
    payload = model._get_request_payload(messages, tool_choice='required')
    assert payload['tool_choice'] == 'auto'
    assert payload['messages'][1]['reasoning_content'] == 'synthetic-provider-field'
    assert payload['extra_body']['thinking']['type'] == 'enabled'


def test_non_thinking_retains_required_tool_choice():
    model = make_model(RuntimeSettings(api_key='synthetic'))
    assert model._get_request_payload([HumanMessage(content='inspect')], tool_choice='required')['tool_choice'] == 'required'
