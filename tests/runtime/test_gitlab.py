"""GitLab REST 适配测试；HTTP 全部为模拟，不代表已在真实 GitLab 实例上验证。

覆盖点：认证头与路径编码、Issue 字段归一、仓库来源核对、MR 草稿前缀、
发布幂等（响应丢失后靠标记复用同一 MR）、以及限流提示的保留。
"""

import json

import httpx
import pytest

from repopilot.delivery.forge import ForgeError, publish
from repopilot.delivery.gitlab import GitLab, GitLabError
from repopilot.storage.jobs import JobStore, StateError
from repopilot.execution.workspace import run_git
import repopilot.orchestration.workflow as workflow
from tests.runtime.test_review_loop import setup, FakeModel, review_approved
from tests.runtime.test_workflow import implementation

URL = "https://gitlab.com/group/project/-/issues/3"
ISSUE = {"repository": "group/project", "number": 3, "url": URL, "title": "change", "body": "需求"}
PROJECT = "group%2Fproject"


def raw_path(request):
    """httpx 会把 %2F 解码成 /，比较编码结果必须看 raw_path。"""
    return request.url.raw_path.split(b"?")[0].decode()


def handler_for(issue=None, *, state="opened"):
    def handler(request):
        assert request.headers["PRIVATE-TOKEN"] == "synthetic-token"
        if raw_path(request).endswith(f"/projects/{PROJECT}/issues/3"):
            return httpx.Response(200, json=issue or {
                "iid": 3, "web_url": URL, "state": state, "title": "需求", "description": "只读任务文本"})
        return httpx.Response(404, json={"message": "not found"})
    return handler


def test_request_sends_private_token_and_encodes_subgroups():
    seen = []
    subgroup = "https://gitlab.com/group/sub/project/-/issues/3"

    def handler(request):
        seen.append((request.headers["PRIVATE-TOKEN"], raw_path(request)))
        return httpx.Response(200, json={"iid": 3, "web_url": subgroup, "state": "opened",
                                         "title": "t", "description": "d"})

    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler))
    api.issue(subgroup)
    assert seen == [("synthetic-token", "/api/v4/projects/group%2Fsub%2Fproject/issues/3")]
    api.close()


def test_draft_prefix_and_payload_for_merge_request_creation():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"iid": 2, "web_url": "https://gitlab.com/group/project/-/merge_requests/2",
                                         "sha": "a" * 40})

    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler))
    pull = api.create_pull_request("group/project", title="实现需求", branch="feature-x",
                                   base="main", body="closes #3")
    assert captured["title"] == "Draft: 实现需求", "GitLab 用标题前缀表达草稿"
    assert captured["source_branch"] == "feature-x" and captured["target_branch"] == "main"
    assert pull == {"url": "https://gitlab.com/group/project/-/merge_requests/2",
                    "number": 2, "head_sha": "a" * 40}
    api.close()


def test_http_error_keeps_code_and_never_echoes_token():
    api = GitLab("synthetic-token", transport=httpx.MockTransport(
        lambda request: httpx.Response(500, text="synthetic-token")))
    with pytest.raises(GitLabError, match="gitlab_http_500") as error:
        api.issue(URL)
    assert "synthetic-token" not in str(error.value)
    with pytest.raises(ForgeError, match="invalid_api_path"):
        api.request("GET", "https://evil.test/api/v4/projects")
    api.close()


def test_rate_limit_hint_is_preserved():
    api = GitLab("synthetic-token", transport=httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": "90"})))
    with pytest.raises(GitLabError) as error:
        api.issue(URL)
    assert error.value.retry_after == 90
    api.close()


def test_closed_or_mismatched_issue_is_rejected():
    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler_for(state="closed")))
    with pytest.raises(GitLabError, match="open_issue_required"):
        api.issue(URL)
    api.close()
    other = handler_for(issue={"iid": 4, "web_url": URL, "state": "opened", "title": "t", "description": "d"})
    api = GitLab("synthetic-token", transport=httpx.MockTransport(other))
    with pytest.raises(GitLabError, match="issue_identity_mismatch"):
        api.issue(URL)
    api.close()


def test_issue_intake_keeps_trusted_checks_and_rejects_other_repository(tmp_path):
    task, settings, root = setup(tmp_path)
    run_git(task.repository, "remote", "add", "origin", "https://gitlab.com/group/project.git")
    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler_for()))
    derived, issue = api.intake(URL, task)
    assert derived.checks == task.checks and derived.repository == task.repository
    assert derived.id == "issue-3" and "只读任务文本" in derived.request
    assert issue["repository"] == "group/project"
    api.close()

    run_git(task.repository, "remote", "set-url", "origin", "https://gitlab.com/group/other.git")
    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler_for()))
    with pytest.raises(GitLabError, match="issue_repository_mismatch"):
        api.intake(URL, task)
    api.close()


def test_open_issues_normalises_fields_to_the_shared_shape():
    def handler(request):
        assert request.url.params["state"] == "opened"
        return httpx.Response(200, json=[
            {"iid": 3, "web_url": URL, "state": "opened", "title": "开放", "created_at": "2026-10-18T00:00:00Z",
             "labels": ["repopilot"]},
            {"iid": 4, "web_url": "https://gitlab.com/group/project/-/issues/4", "state": "closed",
             "title": "关闭", "created_at": "2026-10-18T00:00:00Z", "labels": []},
        ])

    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler))
    issues = api.open_issues("group/project", label="repopilot")
    assert issues == [{"number": 3, "html_url": URL, "title": "开放", "state": "open",
                       "created_at": "2026-10-18T00:00:00Z", "labels": [{"name": "repopilot"}]}]
    api.close()


def gitlab_candidate(tmp_path):
    task, settings, root = setup(tmp_path)
    run_git(task.repository, "remote", "add", "origin", "https://gitlab.com/group/project.git")
    result = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[*implementation(), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    state = JobStore(root).get(result["run_id"])
    return state, root, FakeGitLab(state)


class FakeGitLab:
    """在 provider 钩子层模拟 GitLab；发布流程由 forge.publish 覆盖。"""

    def __init__(self, state):
        self.state, self.merge_requests, self.posts, self.pushes = state, [], 0, 0
        self.lose_response = False
        self.current_issue = ISSUE

    def issue(self, url):
        return self.current_issue

    def push(self, path, repo, branch, commit):
        self.pushes += 1

    def fail(self, code, *, retry_after=0):
        return GitLabError(code, retry_after=retry_after)

    def repository_metadata(self, repository):
        return {"full_name": "group/project", "default_branch": "main"}

    def commit_sha(self, repository, ref):
        return self.state["base_commit"]

    def find_pull_request(self, repository, branch, base, marker):
        if not self.merge_requests:
            return None
        assert len(self.merge_requests) == 1 and marker in self.merge_requests[0]["description"]
        return self._mr(self.merge_requests[0])

    def create_pull_request(self, repository, *, title, branch, base, body):
        self.posts += 1
        mr = {"iid": 2, "web_url": "https://gitlab.com/group/project/-/merge_requests/2",
              "description": body, "sha": self.state["reviewed_commit"]}
        self.merge_requests.append(mr)
        if self.lose_response:
            raise GitLabError("gitlab_transport_error")
        return self._mr(mr)

    @staticmethod
    def _mr(mr):
        return {"url": mr["web_url"], "number": mr["iid"], "head_sha": mr["sha"]}

    def pull_matches(self, pull, repository, commit):
        return (pull["head_sha"] == commit and
                pull["url"].startswith("https://gitlab.com/group/project/-/merge_requests/"))


def approve(state, root):
    workflow.decide(state["run_id"], root, commit=state["reviewed_commit"], approved=True,
                    reason="synthetic approval")


def test_publish_creates_one_draft_merge_request_and_is_idempotent(tmp_path):
    state, root, api = gitlab_candidate(tmp_path)
    with pytest.raises(StateError, match="matching_human_approval_required"):
        publish(state["run_id"], root, api)
    approve(state, root)
    result = publish(state["run_id"], root, api)
    assert result["status"] == "published"
    assert result["pull_request"]["url"].endswith("/merge_requests/2")
    assert result["pull_request"]["commit"] == state["reviewed_commit"]
    assert api.posts == 1 and api.pushes == 1
    assert publish(state["run_id"], root, api) == result
    assert api.posts == 1, "重复发布会复用同一 MR"


def test_lost_merge_request_response_is_reconciled_without_duplicate_post(tmp_path):
    state, root, api = gitlab_candidate(tmp_path)
    approve(state, root)
    api.lose_response = True
    with pytest.raises(GitLabError):
        publish(state["run_id"], root, api)
    assert JobStore(root).get(state["run_id"])["status"] == "publication_failed"
    api.lose_response = False
    result = publish(state["run_id"], root, api)
    assert result["status"] == "published"
    assert api.posts == 1, "重试必须通过标记找回已创建的 MR，而不是再建一个"


def test_issue_edit_blocks_publication(tmp_path):
    state, root, api = gitlab_candidate(tmp_path)
    approve(state, root)
    api.current_issue = {**ISSUE, "body": "改过的需求"}
    with pytest.raises(GitLabError, match="issue_changed_reapproval_required"):
        publish(state["run_id"], root, api)
    assert api.posts == 0


def test_pull_request_url_prefix_is_checked(tmp_path):
    state, root, api = gitlab_candidate(tmp_path)
    approve(state, root)
    original = api.pull_matches
    api.pull_matches = lambda pull, repository, commit: False
    with pytest.raises(GitLabError, match="published_head_mismatch"):
        publish(state["run_id"], root, api)
    api.pull_matches = original
    assert JobStore(root).get(state["run_id"])["status"] == "publication_failed"
