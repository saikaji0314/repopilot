"""平台评论／子Issue能力与写接口的协议测试；HTTP全部模拟，不代表真实平台可用。"""

import json

import httpx

from repopilot.delivery.github import GitHub
from repopilot.delivery.gitlab import GitLab


GITHUB_URL = "https://github.com/owner/repo/issues/1"
GITLAB_URL = "https://gitlab.com/group/project/-/issues/3"


def test_github_preflight_probes_identity_comments_subissues_and_permissions():
    def handler(request):
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "repopilot-bot"})
        if path == "/repos/owner/repo/issues/1/comments":
            return httpx.Response(200, json=[])
        if path == "/repos/owner/repo/issues/1/sub_issues":
            return httpx.Response(200, json=[])
        if path == "/repos/owner/repo":
            return httpx.Response(200, json={"full_name": "owner/repo", "default_branch": "main",
                                             "permissions": {"push": True}})
        return httpx.Response(404, json={})
    api = GitHub("synthetic", transport=httpx.MockTransport(handler))
    result = api.preflight(GITHUB_URL, owner="owner-login")
    assert result["provider"] == "github" and result["repository"] == "owner/repo"
    assert result["issue"]["number"] == 1
    assert result["viewer"] == "repopilot-bot" and result["registered_owner"] == "owner-login"
    assert result["comments"]["status"] == "supported"
    assert result["native_sub_issues"]["status"] == "supported"
    assert result["issue_creation"]["status"] == "supported"
    assert result["write_verified"] is False, "只读预检不能声称已证明写权限"
    api.close()


def test_github_preflight_marks_missing_subissues_as_unsupported():
    def handler(request):
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "bot"})
        if path.endswith("/comments"):
            return httpx.Response(200, json=[])
        if path.endswith("/sub_issues"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"permissions": {}})
    api = GitHub("synthetic", transport=httpx.MockTransport(handler))
    result = api.preflight(GITHUB_URL)
    assert result["native_sub_issues"]["status"] == "unsupported"
    assert result["issue_creation"]["status"] == "unknown"
    api.close()


def test_github_comment_create_issue_and_subissue_payloads():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 10, "html_url": "https://github.com/owner/repo/issues/1#issuecomment-10",
                "body": "answer", "user": {"login": "owner-login", "type": "User"},
                "created_at": "2026-10-27T00:00:00Z"}])
        if request.url.path.endswith("/comments"):
            return httpx.Response(201, json={"id": 11, "html_url": "https://github.com/owner/repo/issues/1#issuecomment-11",
                "body": "question", "user": {"login": "repopilot-bot", "type": "Bot"},
                "created_at": "2026-10-27T00:00:00Z"})
        if request.url.path.endswith("/sub_issues"):
            return httpx.Response(201, json={"parent_issue_id": 5})
        return httpx.Response(201, json={"id": 99, "number": 2, "html_url": "https://github.com/owner/repo/issues/2",
                                         "title": "sub"})
    api = GitHub("synthetic", transport=httpx.MockTransport(handler))
    values = api.issue_comments("owner/repo", 1)
    assert values[0]["author"] == "owner-login" and values[0]["author_type"] == "User"
    posted = api.add_issue_comment("owner/repo", 1, "question")
    assert posted["id"] == 11 and posted["author_type"] == "Bot"
    created = api.create_issue("owner/repo", title="sub", body="body")
    assert created["id"] == 99 and created["number"] == 2
    linked = api.link_sub_issue("owner/repo", 1, created["id"])
    assert linked["native"] is True
    payloads = [json.loads(content) for method, path, content in seen if content]
    assert {"body": "question"} in payloads
    assert {"title": "sub", "body": "body"} in payloads
    assert {"sub_issue_id": 99} in payloads
    api.close()


def test_github_find_issue_by_marker_uses_body_marker():
    marker = "<!-- repopilot-subtask:run:first -->"

    def handler(request):
        return httpx.Response(200, json=[
            {"id": 1, "number": 5, "html_url": "https://github.com/owner/repo/issues/5",
             "title": "skip", "body": "no marker"},
            {"id": 2, "number": 6, "html_url": "https://github.com/owner/repo/issues/6",
             "title": "found", "body": f"body {marker}"},
            {"id": 3, "number": 7, "html_url": "https://github.com/owner/repo/pull/7",
             "title": "pr", "body": marker, "pull_request": {}},
        ])
    api = GitHub("synthetic", transport=httpx.MockTransport(handler))
    found = api.find_issue_by_marker("owner/repo", marker)
    assert found["number"] == 6 and found["id"] == 2
    api.close()


def test_gitlab_preflight_marks_linked_mapping_and_checks_reporter_access():
    def handler(request):
        raw = request.url.raw_path.split(b"?")[0].decode()
        if raw.endswith("/api/v4/user"):
            return httpx.Response(200, json={"username": "repopilot-bot"})
        if raw.endswith("/issues/3/notes"):
            return httpx.Response(200, json=[])
        if raw.endswith("/projects/group%2Fproject"):
            return httpx.Response(200, json={"path_with_namespace": "group/project", "default_branch": "main",
                                             "permissions": {"project_access": {"access_level": 30}}})
        return httpx.Response(404, json={})
    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler))
    result = api.preflight(GITLAB_URL, owner="owner-login")
    assert result["comments"]["status"] == "supported"
    assert result["native_sub_issues"]["status"] == "unsupported"
    assert result["issue_creation"]["status"] == "supported"
    assert api.link_sub_issue("group/project", 3, 99)["native"] is False
    api.close()


def test_gitlab_comment_and_issue_payloads_use_description_and_note_url():
    seen = []

    def handler(request):
        raw = request.url.raw_path.split(b"?")[0].decode()
        seen.append((request.method, raw, request.content))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 7, "body": "answer", "system": False,
                "author": {"username": "owner-login"}, "created_at": "2026-10-27T00:00:00Z"}])
        if raw.endswith("/notes"):
            return httpx.Response(201, json={"id": 8, "body": "question", "system": False,
                "author": {"username": "repopilot-bot"}, "created_at": "2026-10-27T00:00:00Z"})
        return httpx.Response(201, json={"id": 21, "iid": 4,
                                         "web_url": "https://gitlab.com/group/project/-/issues/4",
                                         "title": "sub", "description": "body"})
    api = GitLab("synthetic-token", transport=httpx.MockTransport(handler))
    values = api.issue_comments("group/project", 3)
    assert values[0]["author"] == "owner-login"
    assert values[0]["url"] == "https://gitlab.com/group/project/-/issues/3#note_7"
    posted = api.add_issue_comment("group/project", 3, "question")
    assert posted["id"] == 8 and posted["url"].endswith("#note_8")
    created = api.create_issue("group/project", title="sub", body="body")
    assert created["number"] == 4 and created["id"] == 21
    payloads = [json.loads(content) for method, path, content in seen if content]
    assert {"body": "question"} in payloads
    assert {"title": "sub", "description": "body"} in payloads
    api.close()
