"""运行内压缩保留工具协议与本地证据，不能通过丢弃当前任务绕过上限。"""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from repopilot.agents.context import compact_messages, message_bytes, remember_observation
from tests.runtime.test_agent import ScriptedModel, message, setup
from repopilot.agents.loop import investigate
from repopilot.agents.loop import RunControl
from repopilot.storage.budget import Budget
from repopilot.agents.trace import Trace


def history(count=10):
    messages = [HumanMessage(content='Keep the actual request and registered checks.')]
    for i in range(count):
        messages.extend([AIMessage(content='', additional_kwargs={'reasoning_content':str(i)*10000},
            tool_calls=[{'id':str(i),'name':'read_file','args':{'path':'app.py'}}]),
            ToolMessage(content='source',tool_call_id=str(i),name='read_file')])
    return messages


def test_whole_turn_compaction_preserves_task_pairing_and_recent_reasoning():
    messages = history()
    before = [m.model_dump(mode='json') for m in messages]
    view, event = compact_messages(messages, {'phase':'implement'}, [], 30000)
    assert event and event['after_message_bytes'] < 24000
    assert view[0] is messages[0] and view[-1] is messages[-1]
    assert view[-2].additional_kwargs == messages[-2].additional_kwargs
    assert isinstance(view[1],HumanMessage) and 'host_working_set' in view[1].content
    assert [m.model_dump(mode='json') for m in messages] == before
    calls = {c['id'] for m in view if isinstance(m,AIMessage) for c in m.tool_calls}
    assert calls == {m.tool_call_id for m in view if isinstance(m,ToolMessage)}
    assert 'reasoning_content' not in view[1].content


@pytest.mark.parametrize('change', ['orphan', 'missing', 'duplicate', 'user_boundary'])
def test_unsafe_or_incomplete_turns_are_not_silently_rewritten(change):
    messages = history()
    if change == 'orphan': messages[-1] = ToolMessage(content='bad',tool_call_id='unknown')
    elif change == 'missing': messages.pop()
    elif change == 'duplicate': messages.append(messages[-1])
    else: messages.append(HumanMessage(content='new instruction'))
    view, event = compact_messages(messages, {'phase':'review'}, [], 30000)
    assert view is messages and event is None


def test_multiple_parallel_tool_results_remain_an_atomic_turn():
    messages = history()
    messages[-2].tool_calls.append({'id':'extra','name':'read_file','args':{'path':'other.py'}})
    messages.append(ToolMessage(content='other source',tool_call_id='extra'))
    view, event = compact_messages(messages, {'phase':'implement'}, [], 30000)
    assert event and view[-3].tool_calls[-1]['id'] == 'extra'
    assert {m.tool_call_id for m in view[-2:]} == {'9','extra'}


def test_source_observations_are_bounded_and_invalidated_after_edit():
    progress = {'phase':'implement'}
    remember_observation(progress,'read_file',{'path':'app.py'}, {'ok':True,'content':'old source'})
    remember_observation(progress,'search_text',{'query':'old'}, {'ok':True,'complete':False,'matches':[]})
    remember_observation(progress,'replace_in_file',{'path':'app.py'}, {'ok':True,'path':'app.py','replaced':1})
    assert [x['tool'] for x in progress['observations']] == ['replace_in_file']
    for i in range(30):
        remember_observation(progress,'read_file',{'path':f'{i}.py'}, {'ok':True,'content':'x'*8000})
    assert len(progress['observations']) <= 10
    assert len(json.dumps(progress['observations'],ensure_ascii=False).encode()) <= 8000
    assert all(x['excerpt_truncated'] for x in progress['observations'])


def test_framework_request_view_compacts_without_losing_limits_or_raw_tool_evidence(tmp_path):
    task, settings, output = setup(tmp_path,max_context_bytes=24000)
    responses = []
    for i in range(8):
        response = message('read_file',{'path':'README.md'})
        response.additional_kwargs['reasoning_content'] = 'private-reasoning-'*600
        responses.append(response)
    responses.append(message('ImplementationReport',{'status':'needs_clarification','summary':'read source',
        'questions':['scope?'],'evidence':[]}))
    result = investigate(task,settings,output,model=ScriptedModel(responses=responses),workspace=False)
    assert result['status'] == 'completed' and result['model_calls'] == 9
    raw = next((output/'runs').iterdir()).joinpath('events.jsonl').read_text(encoding='utf-8')
    events = [json.loads(line) for line in raw.splitlines()]
    assert any(e['event']=='context_compacted' for e in events)
    assert all(e['input_bytes']<=24000 for e in events if e['event']=='model_started')
    assert raw.count('sample source') == 8
    assert 'private-reasoning-' not in raw


def test_uncompressible_task_remains_over_limit():
    messages = [HumanMessage(content='x'*100000)]
    view, event = compact_messages(messages,{'phase':'implement'},[],20000)
    assert view is messages and event is None and message_bytes(view)>20000


def test_safe_headroom_is_not_compressed_just_to_reduce_input():
    messages = history(5)
    assert 50000 < message_bytes(messages) < 65000
    view, event = compact_messages(messages, {'phase': 'implement'}, [], 80000)
    assert view is messages and event is None


def test_context_checkpoint_keeps_new_responses_and_allows_later_compaction(tmp_path):
    _, settings, output = setup(tmp_path)
    output.mkdir()
    control = RunControl(settings, Budget(output/'budget.sqlite3', 200), Trace(output, 'synthetic'), 'case')
    messages = history()
    view, event = compact_messages(messages, {'phase': 'implement'}, [], 30000)
    response = AIMessage(content='', tool_calls=[{'id':'new','name':'read_file','args':{'path':'app.py'}}])
    receipt = ToolMessage(content='new source',tool_call_id='new')
    control.pending_context = (view, len(messages))
    update = control.after_model({'messages': [*messages, response, receipt]}, None)
    assert update['messages'][1:] == [*view, response, receipt]
    assert control.pending_context is None
    again, event = compact_messages(update['messages'][1:], {'phase':'implement'}, [], 20000)
    assert event and again[0] is messages[0] and again[-1] is receipt
