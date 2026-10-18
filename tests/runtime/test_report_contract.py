import pytest
from pydantic import ValidationError

from repopilot.agents.loop import CheckEvidence, FileEvidence, Finding, ImplementationReport, ReviewReport


def file_evidence(**overrides):
    return {"kind": "file", "path": "app.py", "start_line": 3, "observation": "当前比较条件", **overrides}


def check_evidence(**overrides):
    return {"kind": "check", "name": "unit", "exit_code": 0, "observation": "登记检查通过", **overrides}


def finding(**overrides):
    return {"path": "app.py", "start_line": 12, "severity": "major",
            "observation": "缺少边界用例", "suggestion": "补一条空标题用例", **overrides}


def test_file_and_check_evidence_keep_separate_shapes():
    report = ImplementationReport.model_validate({
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


@pytest.mark.parametrize("payload,message", [
    ({"status": "implemented", "summary": "s", "evidence": []}, "requires evidence"),
    ({"status": "implemented", "summary": "s", "evidence": [file_evidence()]}, "requires a check result"),
])
def test_implementation_status_demands_matching_evidence(payload, message):
    with pytest.raises(ValidationError, match=message):
        ImplementationReport.model_validate({**payload, "next_steps": [], "questions": []})


def test_blocked_report_needs_no_evidence():
    report = ImplementationReport.model_validate({"status": "blocked", "summary": "s",
                                                  "evidence": [], "next_steps": [], "questions": ["缺环境"]})
    assert report.status == "blocked"


def test_review_finding_must_point_at_a_file_and_offer_a_fix():
    with pytest.raises(ValidationError):
        Finding.model_validate({"path": "app.py", "start_line": 1, "severity": "major",
                                "observation": "x"})
    with pytest.raises(ValidationError):
        Finding.model_validate({"path": "app.py", "start_line": 1, "severity": "fatal",
                                "observation": "x", "suggestion": "y"})


def test_review_verdict_matches_its_findings():
    approved = ReviewReport.model_validate({"verdict": "approved", "summary": "没发现问题",
                                            "findings": [finding(severity="minor")], "questions": []})
    assert approved.verdict == "approved"
    with pytest.raises(ValidationError, match="at least one finding"):
        ReviewReport.model_validate({"verdict": "changes_requested", "summary": "s",
                                     "findings": [], "questions": []})
    with pytest.raises(ValidationError, match="blocker"):
        ReviewReport.model_validate({"verdict": "approved", "summary": "s",
                                     "findings": [finding(severity="blocker")], "questions": []})
    unable = ReviewReport.model_validate({"verdict": "unable_to_review", "summary": "缺依赖",
                                          "findings": [], "questions": ["如何准备环境？"]})
    assert unable.questions == ["如何准备环境？"]
