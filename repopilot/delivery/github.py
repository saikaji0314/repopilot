"""固定 GitHub REST 接口；凭证只用于宿主进程，不进入任务、模型或检查环境。"""

import base64
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from dotenv import dotenv_values
import httpx

from repopilot.core.config import PROJECT_ROOT, Task
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp
from repopilot.orchestration.state import approval_digest, attach, canonical_hash
from repopilot.execution.workspace import run_git


class GitHubError(RuntimeError):
    def __init__(self, message, *, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def load_token(env_file=PROJECT_ROOT / ".env"):
    values = dotenv_values(env_file, interpolate=False) if env_file.exists() else {}
    token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or
             values.get("GH_TOKEN") or values.get("GITHUB_TOKEN"))
    if not token or not token.strip():
        raise GitHubError("github_token_required")
    return token.strip()


def parse_issue(url):
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?", url)
    if not match:
        raise GitHubError("use_github_issue_url")
    return match[1], int(match[2])


def remote_repository(path):
    remote = run_git(path, "remote", "get-url", "origin").stdout.decode().strip()
    if remote.startswith("git@github.com:"):
        name = remote[len("git@github.com:"):]
    else:
        url = urlsplit(remote)
        if url.scheme != "https" or url.hostname != "github.com" or url.username or url.password:
            raise GitHubError("github_origin_required")
        name = url.path.lstrip("/")
    name = name.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise GitHubError("invalid_origin")
    return name


class GitHub:
    def __init__(self, token, *, transport=None):
        self.token = token
        self.client = httpx.Client(base_url="https://api.github.com", timeout=25,
            follow_redirects=False, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "RepoPilot"})

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        # 不接收任意 URL，避免把鉴权头发送到任务指定的服务器。
        if not path.startswith("/repos/") or "://" in path or ".." in path:
            raise GitHubError("invalid_api_path")
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise GitHubError("github_transport_error") from None
        if response.status_code not in {200, 201}:
            try:
                delay = max(0, min(86400, int(response.headers.get("Retry-After", "0"))))
                if response.headers.get("X-RateLimit-Remaining") == "0":
                    import time
                    delay = max(delay, min(86400, max(0, int(response.headers.get("X-RateLimit-Reset", "0")) - int(time.time()))))
            except ValueError:
                delay = 0
            raise GitHubError(f"github_http_{response.status_code}", retry_after=delay)
        try:
            return response.json()
        except ValueError:
            raise GitHubError("github_invalid_response") from None

    def issue(self, url):
        repository, number = parse_issue(url)
        value = self.request("GET", f"/repos/{repository}/issues/{number}")
        if value.get("pull_request") or value.get("state") != "open":
            raise GitHubError("open_issue_required")
        if value.get("number") != number or value.get("html_url", "").rstrip("/") != url.rstrip("/"):
            raise GitHubError("issue_identity_mismatch")
        return {"repository": repository, "number": number, "url": value["html_url"],
                "title": value["title"], "body": value.get("body") or ""}

    def intake(self, url, template):
        issue = self.issue(url)
        if remote_repository(template.repository).lower() != issue["repository"].lower():
            raise GitHubError("issue_repository_mismatch")
        # 验证命令和路径始终来自本机可信模板，Issue 只贡献需求文本。
        task = Task.model_validate({**template.model_dump(),
            "id": f"issue-{issue['number']}",
            "request": f"{template.request}\n\nGitHub Issue: {issue['url']}\n{issue['title']}\n\n{issue['body']}"})
        return task, issue

    def open_issues(self, repository, *, label=None, since=None):
        """分页枚举开放 Issue；超过扫描上限报错，不静默把截断当完整结果。"""
        result = []
        for page in range(1, 11):
            params = {"state": "open", "sort": "created", "direction": "asc", "per_page": 100, "page": page}
            if label:
                params["labels"] = label
            if since:
                params["since"] = since
            values = self.request("GET", f"/repos/{repository}/issues", params=params)
            if not isinstance(values, list):
                raise GitHubError("github_invalid_response")
            result.extend(v for v in values if "pull_request" not in v)
            if len(values) < 100:
                return result
        raise GitHubError("issue_scan_limit_reached")

    def push(self, workspace, repository, branch, commit):
        # 密钥不进入命令参数、远程 URL 或 git config 文件。
        encoded = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        environment = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                       "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}"}
        url = f"https://github.com/{repository}.git"
        result = run_git(workspace, "ls-remote", url, f"refs/heads/{branch}", extra_env=environment)
        refs = result.stdout.decode().split()
        if refs:
            if refs[0] != commit:
                raise GitHubError("remote_branch_conflict")
            return
        try:
            run_git(workspace, "-c", "core.hooksPath=", "push", "--no-verify", url,
                    f"{commit}:refs/heads/{branch}", extra_env=environment)
        except Exception:
            # 不回显 git 诊断，避免远端错误信息带回凭证。
            raise GitHubError("github_push_failed_reconcile_before_retry") from None

    def prepare_base(self, repository_path, repository):
        """取远端默认分支对象，不切换或重置用户原工作树。"""
        if remote_repository(repository_path).lower() != repository.lower():
            raise GitHubError("issue_repository_mismatch")
        metadata = self.request("GET", f"/repos/{repository}")
        if metadata["full_name"].lower() != repository.lower():
            raise GitHubError("repository_identity_mismatch")
        branch = metadata["default_branch"]
        commit = self.request("GET", f"/repos/{repository}/commits/{quote(branch, safe='')}")["sha"]
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise GitHubError("invalid_remote_commit")
        encoded = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        environment = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                       "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}"}
        try:
            run_git(repository_path, "fetch", "--no-tags", "--no-write-fetch-head",
                    f"https://github.com/{repository}.git", f"refs/heads/{branch}", extra_env=environment)
            run_git(repository_path, "cat-file", "-e", f"{commit}^{{commit}}")
        except Exception:
            raise GitHubError("github_base_fetch_failed") from None
        return commit


def publish(run_id, root, github, *, note=None):
    """只发布审批绑定的版本；重试先核对远端分支及 PR，避免重复创建。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        if state["status"] == "published":
            return {"run_id": run_id, "status": "published", "pull_request": state["pull_request"]}
        approval = state.get("approval") or {}
        if (state["status"] not in {"approved", "publication_failed", "publishing"} or
                not approval.get("approved") or approval.get("commit") != state["reviewed_commit"] or
                approval.get("digest") != approval_digest(state)):
            raise StateError("matching_human_approval_required")
        if not state.get("issue"):
            raise StateError("github_issue_required")
        workspace = attach(state, store.root)
        workspace.prepare().assert_clean(approval["commit"])
        issue = state["issue"]
        repository = issue["repository"]
        if remote_repository(workspace.repository()).lower() != repository.lower():
            raise GitHubError("issue_repository_mismatch")
        if canonical_hash(github.issue(issue["url"])) != canonical_hash(issue):
            raise GitHubError("issue_changed_reapproval_required")
        metadata = github.request("GET", f"/repos/{repository}")
        base = metadata["default_branch"]
        if metadata["full_name"].lower() != repository.lower():
            raise GitHubError("repository_identity_mismatch")
        remote_base = github.request("GET", f"/repos/{repository}/commits/{quote(base, safe='')}")["sha"]
        if remote_base != state["base_commit"]:
            raise GitHubError("remote_base_changed_rerun_required")
        if not state.get("publication"):
            state["publication"] = {"base": base, "branch": f"feature-repopilot-{run_id}", "public_note": note}
            store.save(state)
        elif state["publication"]["base"] != base:
            raise GitHubError("default_branch_changed")
        publication = state["publication"]
        marker = f"<!-- repopilot-run:{run_id} -->"
        state["status"], state["in_flight"] = "publishing", "publish"
        store.save(state)
        try:
            github.push(workspace.repository(), repository, publication["branch"], approval["commit"])
            owner = repository.split("/")[0]
            candidates = github.request("GET", f"/repos/{repository}/pulls", params={
                "state": "all", "head": f"{owner}:{publication['branch']}", "base": publication["base"], "per_page": 100})
            if candidates:
                if len(candidates) != 1 or marker not in (candidates[0].get("body") or ""):
                    raise GitHubError("pull_request_conflict")
                pr = candidates[0]
            else:
                checks = "\n".join(f"- `{item['name']}`: exit {item['exit_code']}" for item in state["verified"])
                pr = github.request("POST", f"/repos/{repository}/pulls", json={
                    "title": issue["title"][:200], "head": publication["branch"], "base": publication["base"],
                    "draft": True, "body": f"Closes {issue['url']}\n\nImplements: {issue['title']}\n\n"
                    f"Latest implementation / review follow-up:\n{state['report']['summary']}\n\n"
                    f"Independent checks:\n{checks}\n\nReview rounds: {len(state['history'])}; rework attempts: {state['reworks']}.\n\n"
                    f"Reviewed and human-approved commit: `{approval['commit']}`\n\n"
                    f"{publication.get('public_note') or ''}\n\n{marker}"})
            if (pr["head"]["sha"] != approval["commit"] or
                    pr["head"]["repo"]["full_name"].lower() != repository.lower() or
                    not pr["html_url"].startswith(f"https://github.com/{repository}/pull/")):
                raise GitHubError("published_head_mismatch")
            state["pull_request"] = {"url": pr["html_url"], "number": pr["number"], "commit": approval["commit"], "at": timestamp()}
            state["status"], state["in_flight"], state["stop_reason"] = "published", None, "draft_pr_created"
            store.save(state)
        except Exception:
            state["status"], state["in_flight"] = "publication_failed", None
            store.save(state)
            raise
        return {"run_id": run_id, "status": "published", "pull_request": state["pull_request"]}
