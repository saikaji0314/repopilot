import pytest
from pydantic import ValidationError

from repopilot.runtime.agent import CheckEvidence, FileEvidence, InvestigationReport


def file_evidence(**overrides):
    return {"kind": "file", "path": "app.py", "start_line": 3, "observation": "当前比较条件", **overrides}


def check_evidence(**overrides):
    return {"kind": "check", "name": "unit", "exit_code": 0, "observation": "登记检查通过", **overrides}


def test_file_and_check_evidence_keep_separate_shapes():
    report = InvestigationReport.model_validate({
        "status": "implemented", "summary": "done",
        "evidence": [file_evidence(), check_evidence(exit_code=1)],
        "next_steps": [], "questions": [],
    })
    assert isinstance(report.evidence[0], FileEvidence)
    assert isinstance(report.evidence[1], CheckEvidence)
    assert report.evidence[1].exit_code == 1
    dumped = report.model_dump(mode="json")
    assert [item["kind"] for item in dumped["evidence"]] == ["file", "check"]


def test_file_evidence_requires_a_line_and_check_evidence_a_named_check():
    with pytest.raises(ValidationError):
        FileEvidence.model_validate({"kind": "file", "path": "app.py", "observation": "x"})
    with pytest.raises(ValidationError):
        CheckEvidence.model_validate({"kind": "check", "name": "unit", "observation": "x"})


def test_evidence_kind_must_match_the_declared_shape():
    with pytest.raises(ValidationError):
        FileEvidence.model_validate(check_evidence())
    with pytest.raises(ValidationError):
        CheckEvidence.model_validate(file_evidence())


@pytest.mark.parametrize("status,evidence,message", [
    ("planned", [], "requires evidence"),
    ("implemented", [], "requires evidence"),
    ("implemented", [file_evidence()], "requires a check result"),
])
def test_status_demands_matching_evidence(status, evidence, message):
    with pytest.raises(ValidationError, match=message):
        InvestigationReport.model_validate({"status": status, "summary": "s", "evidence": evidence,
                                            "next_steps": [], "questions": []})


def test_planned_report_may_use_only_file_evidence():
    report = InvestigationReport.model_validate({"status": "planned", "summary": "s",
                                                 "evidence": [file_evidence()],
                                                 "next_steps": ["改 app.py"], "questions": []})
    assert report.status == "planned"
