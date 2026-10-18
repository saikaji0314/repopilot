"""确认收敛提醒经过框架到达实现阶段，且不改变只读与审核权限。"""

import pytest

from repopilot.agents.loop import investigate
from tests.runtime.test_review_loop import setup, FakeModel, turn, implement_done, review_approved


@pytest.mark.parametrize("editable", [False, True])
def test_reminder_after_investigation_is_limited_to_editable_tasks(tmp_path, editable):
    task, settings, root = setup(tmp_path, max_model_calls=12)
    prompts = []

    class ObservedModel(FakeModel):
        def _generate(self, messages, *args, **kwargs):
            prompts.append(messages[0].content)
            return super()._generate(messages, *args, **kwargs)

    reads = [turn("read_file", {"path": "app.py", "start_line": n, "end_line": n})
             for n in [1, 2, 3, 1, 2]]
    done = turn("ImplementationReport", {"status": "blocked", "summary": "缺少关键事实",
                "evidence": [], "questions": ["需要进一步定位"], "next_steps": []})
    result = investigate(task, settings, root, model=ObservedModel(responses=[*reads, done]),
                         workspace=editable, review=False)
    assert result["report_status"] == "blocked"
    assert all("阶段收敛提醒" not in prompt for prompt in prompts[:4])
    assert ("阶段收敛提醒" in prompts[4]) is editable
    assert result["edits"]["edits"] == 0  # 提醒不强迫编辑，诚实阻塞报告仍可结束。


def test_reminder_stops_after_edit_and_does_not_reach_readonly_reviewer(tmp_path):
    task, settings, root = setup(tmp_path, max_model_calls=12)
    prompts = []

    class ObservedModel(FakeModel):
        def _generate(self, messages, *args, **kwargs):
            prompts.append(messages[0].content)
            return super()._generate(messages, *args, **kwargs)

    reads = [turn("read_file", {"path": "app.py", "start_line": n, "end_line": n})
             for n in [1, 2, 3, 1]]
    result = investigate(task, settings, root, require_approval=True, model=ObservedModel(responses=[*reads,
        turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
        turn("run_registered_check", {"name": "unit"}), implement_done(), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    assert "阶段收敛提醒" in prompts[4]
    assert all("阶段收敛提醒" not in prompt for prompt in prompts[5:])
