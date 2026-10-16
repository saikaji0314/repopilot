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
