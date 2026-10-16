import json

from repopilot.runtime import cli
from repopilot.runtime.config import RuntimeSettings, Task


def test_summary_remains_console_safe_with_unicode_tool_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_task", lambda _: Task(id="test", request="inspect", repository=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: RuntimeSettings(api_key="synthetic"))
    monkeypatch.setattr(cli.workflow, "start", lambda *args, **kwargs: {"status": "completed", "output": "✔ 中文"})
    assert cli.main(["task.yaml"]) == 0
    output = capsys.readouterr().out
    output.encode("ascii")
    assert json.loads(output)["output"] == "✔ 中文"


def test_execution_profile_is_validated_and_command_line_wins(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'load_task', lambda _: Task(id='test', request='inspect', repository=tmp_path))
    monkeypatch.setattr(cli, 'load_settings', lambda: RuntimeSettings(api_key='synthetic'))
    received = []
    def start(task, settings, *args, **kwargs):
        received.append(settings)
        return {'status': 'completed'}
    monkeypatch.setattr(cli.workflow, 'start', start)
    profile = tmp_path / 'profile.yaml'
    profile.write_text('thinking: enabled\nmax_output_tokens: 8192\nmax_tool_calls: 80\n', encoding='utf-8')
    assert cli.main(['run', 'task.yaml', '--profile', str(profile), '--max-tool-calls', '70']) == 0
    assert received[0].thinking == 'enabled' and received[0].max_output_tokens == 8192
    assert received[0].max_tool_calls == 70
    profile.write_text('budget_cny: 999\n', encoding='utf-8')
    assert cli.main(['run', 'task.yaml', '--profile', str(profile)]) == 2
