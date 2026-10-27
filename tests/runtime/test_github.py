"""GitHub 协议及副作用恢复测试；HTTP 为模拟，不代表真实 PR 已创建。"""

import httpx
import pytest

from repopilot.delivery.github import GitHub, GitHubError, parse_issue, publish
from repopilot.storage.jobs import JobStore, StateError
import repopilot.orchestration.workflow as workflow
from repopilot.execution.workspace import run_git
from tests.runtime.test_review_loop import setup, FakeModel, review_approved
from tests.runtime.test_workflow import implementation


URL = "https://github.com/owner/repo/issues/1"
ISSUE = {"repository": "owner/repo", "number": 1, "url": URL, "title": "change", "body": "需求"}


@pytest.mark.parametrize("url", ["http://github.com/a/b/issues/1", "https://evil.test/a/b/issues/1",
    "https://github.com/a/b/pull/1", "https://github.com/a/b/issues/0", URL + "?x=1"])
def test_issue_url_is_strict(url):
    with pytest.raises(GitHubError):
        parse_issue(url)


def test_http_errors_do_not_expose_body_or_token():
    api = GitHub("synthetic-secret", transport=httpx.MockTransport(
        lambda r: httpx.Response(403, text="synthetic-secret")))
    with pytest.raises(GitHubError, match="github_http_403") as error:
        api.issue(URL)
    assert "synthetic-secret" not in str(error.value)
    with pytest.raises(GitHubError, match="invalid_api_path"):
        api.request("GET", "https://evil.test")
    api.close()


def test_issue_intake_keeps_local_checks_and_rejects_other_repo(tmp_path):
    task, settings, root = setup(tmp_path)
    run_git(task.repository, "remote", "add", "origin", "https://github.com/owner/repo.git")
    def handler(request):
        assert request.url.host == "api.github.com"
        return httpx.Response(200, json={"number": 1, "html_url": URL, "state": "open", "title": "需求", "body": "只读任务文本"})
    api = GitHub("synthetic", transport=httpx.MockTransport(handler))
    derived, issue = api.intake(URL, task)
    assert derived.checks == task.checks and derived.repository == task.repository
    assert "只读任务文本" in derived.request
    run_git(task.repository, "remote", "set-url", "origin", "https://github.com/other/repo.git")
    with pytest.raises(GitHubError, match="issue_repository_mismatch"):
        api.intake(URL, task)
    api.close()


class FakeGitHub:
    """在 provider 钩子层模拟；发布流程本身与平台无关，由 forge.publish 覆盖。"""

    def __init__(self, state):
        self.state, self.prs, self.posts, self.pushes = state, [], 0, 0
        self.lose_response = False
        self.current_issue = ISSUE
        self.remote_head = None

    def issue(self, url):
        return self.current_issue

    def push(self, path, repo, branch, commit):
        self.pushes += 1
        self.remote_head = commit

    def branch_head(self, path, repo, branch):
        return self.remote_head

    def fail(self, code, *, retry_after=0):
        return GitHubError(code, retry_after=retry_after)

    def repository_metadata(self, repository):
        return {"full_name": "owner/repo", "default_branch": "main"}

    def commit_sha(self, repository, ref):
        return self.state["base_commit"]

    def find_pull_request(self, repository, branch, base, marker):
        if not self.prs:
            return None
        assert len(self.prs) == 1 and marker in self.prs[0]["body"]
        return self._pull(self.prs[0])

    def create_pull_request(self, repository, *, title, branch, base, body):
        self.posts += 1
        pr = {"number": 2, "html_url": "https://github.com/owner/repo/pull/2", "body": body,
              "head": {"sha": self.state["reviewed_commit"], "repo": {"full_name": "owner/repo"}}}
        self.prs.append(pr)
        if self.lose_response:
            raise GitHubError("github_transport_error")
        return self._pull(pr)

    @staticmethod
    def _pull(pr):
        return {"url": pr["html_url"], "number": pr["number"],
                "head_sha": pr["head"]["sha"], "head_repository": pr["head"]["repo"]["full_name"]}

    def pull_matches(self, pull, repository, commit):
        return (pull["head_sha"] == commit and pull["head_repository"] == "owner/repo" and
                pull["url"].startswith("https://github.com/owner/repo/pull/"))


def github_candidate(tmp_path):
    task, settings, root = setup(tmp_path)
    run_git(task.repository, "remote", "add", "origin", "https://github.com/owner/repo.git")
    result = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[*implementation(), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    state = JobStore(root).get(result["run_id"])
    return state, root, FakeGitHub(state)


def approve(state, root):
    workflow.decide(state["run_id"], root, commit=state["reviewed_commit"], approved=True, reason="synthetic approval")


def test_publish_requires_approval_and_is_idempotent(tmp_path):
    state, root, api = github_candidate(tmp_path)
    with pytest.raises(StateError, match="matching_human_approval_required"):
        publish(state["run_id"], root, api)
    assert api.pushes == 0
    approve(state, root)
    result = publish(state["run_id"], root, api)
    assert result["status"] == "published" and api.posts == 1
    assert publish(state["run_id"], root, api) == result
    assert api.posts == 1 and api.pushes == 1


def test_lost_pr_response_is_reconciled_without_duplicate_post(tmp_path):
    state, root, api = github_candidate(tmp_path)
    approve(state, root)
    api.lose_response = True
    with pytest.raises(GitHubError):
        publish(state["run_id"], root, api)
    assert JobStore(root).get(state["run_id"])["status"] == "publication_failed"
    result = publish(state["run_id"], root, api)
    assert result["status"] == "published" and api.posts == 1


def test_issue_edit_blocks_publication(tmp_path):
    state, root, api = github_candidate(tmp_path)
    approve(state, root)
    api.current_issue = ISSUE | {"body": "需求变化"}
    with pytest.raises(GitHubError, match="issue_changed_reapproval_required"):
        publish(state["run_id"], root, api)
    assert api.pushes == 0 and api.posts == 0


def test_issue_deduplication_avoids_second_model_run(tmp_path):
    state, root, api = github_candidate(tmp_path)
    from repopilot.core.config import Task, RuntimeSettings
    result = workflow.start(Task.model_validate(state["task"]), RuntimeSettings(api_key="synthetic"), root,
        issue=ISSUE, model=FakeModel(responses=[]))
    assert result["run_id"] == state["run_id"]
    assert result["model_calls"] == 3


def test_git_push_reuses_exact_head_and_rejects_conflicting_branch(tmp_path, monkeypatch):
    import repopilot.delivery.github as module
    from repopilot.execution.workspace import Workspace, head_commit
    from tests.runtime.test_review_loop import make_repo
    source = make_repo(tmp_path)
    workspace = Workspace(source, tmp_path / "worktrees").prepare()
    (workspace.path / "app.py").write_text("value = 2\n")
    commit = workspace.commit("feat: synthetic")['commit']
    remote = tmp_path / "remote.git"
    run_git(source, "init", "--bare", str(remote))
    calls = []
    def local_git(path, *args, **kwargs):
        calls.append((args, kwargs))
        args = tuple(str(remote) if a == "https://github.com/owner/repo.git" else a for a in args)
        return run_git(path, *args, **kwargs)
    monkeypatch.setattr(module, "run_git", local_git)
    api = GitHub("synthetic-token")
    api.push(workspace.path, "owner/repo", "codex/test", commit)
    api.push(workspace.path, "owner/repo", "codex/test", commit)
    assert sum("push" in args for args, _ in calls) == 1
    assert all("synthetic-token" not in str(args) for args, _ in calls)
    assert all("GIT_CONFIG_VALUE_0" in kwargs['extra_env'] for args, kwargs in calls)
    run_git(remote, "update-ref", "refs/heads/codex/test", head_commit(source))
    with pytest.raises(GitHubError, match="remote_branch_conflict"):
        api.push(workspace.path, "owner/repo", "codex/test", commit)
    api.close()


def test_fetch_remote_base_does_not_checkout_or_reset_source(tmp_path, monkeypatch):
    import repopilot.delivery.github as module
    from repopilot.execution.workspace import Workspace, head_commit, source_snapshot
    from tests.runtime.test_review_loop import make_repo
    source = make_repo(tmp_path)
    run_git(source, 'remote', 'add', 'origin', 'https://github.com/owner/repo.git')
    before = source_snapshot(source)
    work = Workspace(source, tmp_path / 'worktrees').prepare()
    (work.path / 'app.py').write_text('value = 2\n')
    remote_sha = work.commit('feat: remote change')['commit']
    remote = tmp_path / 'remote.git'
    run_git(source, 'init', '--bare', str(remote))
    run_git(work.path, 'push', str(remote), f'{remote_sha}:refs/heads/main')
    def local_git(path, *args, **kwargs):
        return run_git(path, *(str(remote) if a == 'https://github.com/owner/repo.git' else a for a in args), **kwargs)
    monkeypatch.setattr(module, 'run_git', local_git)
    def handler(request):
        return httpx.Response(200, json={'sha': remote_sha} if '/commits/' in request.url.path else
                              {'full_name': 'owner/repo', 'default_branch': 'main'})
    api = GitHub('synthetic', transport=httpx.MockTransport(handler))
    assert api.prepare_base(source, 'owner/repo') == remote_sha
    assert source_snapshot(source) == before
    assert head_commit(source) != remote_sha
    api.close()
