"""Inspect effective framework requests; no live provider or repository writes."""

import json

import pytest
from pydantic import BaseModel

from repopilot.agents.loop import RunControl, run_phase
from repopilot.agents.trace import Trace
from repopilot.execution.repository import build_tools
from repopilot.storage.budget import Budget
from tests.runtime.test_agent import ScriptedModel, message, setup


class ProbeReport(BaseModel):
    summary: str


@pytest.mark.parametrize('phase,editable', [
    ('plan', False), ('review', False), ('dispute', False), ('arbitration', False),
    ('implement', False), ('implement', True), ('rework', True), ('integrate', True),
])
def test_progress_respects_actual_tools_and_current_report(tmp_path, phase, editable):
    from langchain_core.tools import tool

    task, settings, output = setup(tmp_path)
    output.mkdir()
    budget = Budget(output / 'budget.sqlite3', settings.budget_cny)
    control = RunControl(settings, budget, Trace(output, 'synthetic'), 'probe')
    tools = build_tools(task, control.remaining)
    if editable:
        @tool
        def write_file(path: str, content: str) -> dict:
            """A capability declaration; this test must never invoke it."""
            pytest.fail('unexpected file mutation')
        tools.append(write_file)
    requests = []

    class CapturingModel(ScriptedModel):
        def _generate(self, messages, *args, **kwargs):
            requests.append(messages[0].content)
            return super()._generate(messages, *args, **kwargs)

    result = run_phase(prompt='Only perform the current role.', schema=ProbeReport,
        payload={'request': task.request}, tools=tools, settings=settings, control=control,
        model=CapturingModel(responses=[message('read_file', {'path': 'README.md'}),
            message('ProbeReport', {'summary': 'done'})]), phase=phase)
    assert result.summary == 'done' and len(requests) == 2
    for prompt in requests:
        if not editable:
            assert '直接编辑' not in prompt
            assert '不能修改文件' in prompt
        start = prompt.index('{')
        progress, end = json.JSONDecoder().raw_decode(prompt[start:])
        assert progress['phase'] == phase
        assert progress['can_edit'] is editable
        assert progress['report_tool'] == 'ProbeReport'
        assert 'ProbeReport' in prompt[start + end:]
        if editable:
            assert '直接编辑' in prompt
    assert (task.repository / 'README.md').read_text(encoding='utf-8') == 'sample source'
    assert control.model_calls == 2 and control.tool_calls == 1
    assert budget.snapshot('probe')['calls'] == 2
