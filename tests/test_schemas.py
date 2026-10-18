import json

import pytest
from pydantic import ValidationError

from repopilot.legacy.schemas import BaselineAnswer


def _valid_answer() -> dict:
    return {
        "status": "diagnosed",
        "summary": "定位完成",
        "root_cause": "示例根因",
        "evidence": [{"source": "app.py:1", "observation": "示例证据"}],
        "proposed_fix": ["最小修复"],
        "verification": ["运行目标测试"],
        "risks": ["未确认边界"],
    }


def test_valid_answer_can_be_parsed():
    answer = BaselineAnswer.model_validate_json(
        json.dumps(_valid_answer(), ensure_ascii=False)
    )

    assert answer.status == "diagnosed"
    assert answer.evidence[0].source == "app.py:1"


def test_extra_fields_are_rejected():
    value = _valid_answer()
    value["unexpected"] = True

    with pytest.raises(ValidationError):
        BaselineAnswer.model_validate(value)
