"""固定 GitHub REST 接口；凭证只用于宿主进程，不进入任务、模型或检查环境。"""

from pathlib import Path
import re
from urllib.parse import quote

from repopilot.core.config import PROJECT_ROOT, Task
from repopilot.execution.workspace import run_git
from .forge import (GITHUB_HOST, Forge, ForgeError, git_auth_environment, load_token as load_forge_token,
                    parse_issue_url, publish as publish_through)


class GitHubError(ForgeError):
    """GitHub 接入失败；保留原有的错误码前缀与异常类型。"""


def load_token(env_file=None):
    return load_forge_token("github", env_file)


def parse_issue(url):
    try:
        parsed = parse_issue_url(url)
    except ForgeError:
        raise GitHubError("use_github_issue_url") from None
    if parsed["provider"] != "github":
        raise GitHubError("use_github_issue_url")
    return parsed["repository"], parsed["number"]


def remote_repository(path):
    from .forge import remote_origin
    origin = remote_origin(path)
    if origin.provider != "github":
        raise GitHubError("github_origin_required")
    return origin.repository


class GitHub(Forge):
    name = "github"
    api_base = "https://api.github.com"
    path_prefix = "/repos/"
    Error = GitHubError

    def __init__(self, token, *, host=GITHUB_HOST, transport=None):
        super().__init__(token, host=host, transport=transport)

    def headers(self):
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "RepoPilot"}

    def retry_after(self, response):
        """GitHub 用 X-RateLimit-Reset 表达重置时刻，Retry-After 未必出现。"""
        import time
        try:
            delay = max(0, min(86400, int(response.headers.get("Retry-After", "0"))))
            if response.headers.get("X-RateLimit-Remaining") == "0":
                reset = max(0, int(response.headers.get("X-RateLimit-Reset", "0")) - int(time.time()))
                delay = max(delay, min(86400, reset))
            return delay
        except ValueError:
            return 0

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

    def branch_head(self, workspace, repository, branch):
        """只读核对分支，缺失时返回None；不推送也不创建PR。"""
        environment = git_auth_environment(self.host, "x-access-token", self.token)
        url = f"https://{self.host}/{repository}.git"
        result = run_git(workspace, "ls-remote", url, f"refs/heads/{branch}", extra_env=environment)
        refs = result.stdout.decode().split()
        return refs[0] if refs else None

    def push(self, workspace, repository, branch, commit):
        # 密钥不进入命令参数、远程 URL 或 git config 文件。
        current = self.branch_head(workspace, repository, branch)
        if current:
            if current != commit:
                raise GitHubError("remote_branch_conflict")
            return
        environment = git_auth_environment(self.host, "x-access-token", self.token)
        url = f"https://{self.host}/{repository}.git"
        try:
            run_git(workspace, "-c", "core.hooksPath=", "push", "--no-verify", url,
                    f"{commit}:refs/heads/{branch}", extra_env=environment)
        except Exception:
            # 不回显 git 诊断，避免远端错误信息带回凭证。
            raise GitHubError("github_push_failed_reconcile_before_retry") from None

    def repository_metadata(self, repository):
        value = self.request("GET", f"/repos/{repository}")
        return {"full_name": value["full_name"], "default_branch": value["default_branch"]}

    def commit_sha(self, repository, ref):
        commit = self.request("GET", f"/repos/{repository}/commits/{quote(ref, safe='')}")["sha"]
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise GitHubError("invalid_remote_commit")
        return commit

    def find_pull_request(self, repository, branch, base, marker):
        owner = repository.split("/")[0]
        candidates = self.request("GET", f"/repos/{repository}/pulls", params={
            "state": "all", "head": f"{owner}:{branch}", "base": base, "per_page": 100})
        if not candidates:
            return None
        if len(candidates) != 1 or marker not in (candidates[0].get("body") or ""):
            raise GitHubError("pull_request_conflict")
        value = candidates[0]
        return {"url": value["html_url"], "number": value["number"],
                "head_sha": value["head"]["sha"],
                "head_repository": value["head"]["repo"]["full_name"]}

    def create_pull_request(self, repository, *, title, branch, base, body):
        value = self.request("POST", f"/repos/{repository}/pulls", json={
            "title": title, "head": branch, "base": base, "draft": True, "body": body})
        return {"url": value["html_url"], "number": value["number"],
                "head_sha": value["head"]["sha"],
                "head_repository": value["head"]["repo"]["full_name"]}

    def probe_issue_features(self, repository, number):
        """只读探测评论、原生子Issue和建单权限；写权限无法在不写入时证明。"""
        return {"comments": self._probe(
                    f"GET /repos/{repository}/issues/{number}/comments",
                    lambda: self.request("GET", f"/repos/{repository}/issues/{number}/comments",
                                         params={"per_page": 1}), absent={404, 410}),
                "native_sub_issues": self._probe(
                    f"GET /repos/{repository}/issues/{number}/sub_issues",
                    lambda: self.request("GET", f"/repos/{repository}/issues/{number}/sub_issues",
                                         params={"per_page": 1}), absent={404, 410, 415}),
                "issue_creation": self._issue_creation(repository)}

    def _probe(self, label, call, *, absent):
        try:
            call()
            return {"status": "supported", "evidence": f"{label} returned 200"}
        except GitHubError as exc:
            if exc.status in absent:
                return {"status": "unsupported", "evidence": f"{label} returned {exc.status}"}
            if exc.status in {401, 403}:
                return {"status": "unknown", "evidence": f"{label} returned {exc.status}"}
            raise

    def _issue_creation(self, repository):
        value = self.request("GET", f"/repos/{repository}")
        permissions = value.get("permissions") or {}
        allowed = any(bool(permissions.get(name)) for name in ("admin", "maintain", "push", "triage"))
        return {"status": "supported" if allowed else "unknown",
                "evidence": f"GET /repos/{repository} permissions" if allowed else
                            f"GET /repos/{repository} does not prove issues write"}

    @staticmethod
    def _comment(value):
        user = value.get("user") or {}
        return {"id": value.get("id"), "url": value.get("html_url"),
                "body": value.get("body") or "", "author": user.get("login") or "",
                "author_type": user.get("type") or "", "created_at": value.get("created_at"),
                "system": False}

    def issue_comments(self, repository, number):
        """按时间升序读取评论；超过扫描上限明确失败，不把截断当完整答复。"""
        result = []
        for page in range(1, 6):
            values = self.request("GET", f"/repos/{repository}/issues/{number}/comments",
                                  params={"per_page": 100, "page": page})
            if not isinstance(values, list):
                raise GitHubError("github_invalid_response")
            result.extend(self._comment(item) for item in values)
            if len(values) < 100:
                return result
        raise GitHubError("issue_comment_scan_limit_reached")

    def add_issue_comment(self, repository, number, body):
        value = self.request("POST", f"/repos/{repository}/issues/{number}/comments",
                             json={"body": body})
        return self._comment(value)

    def create_issue(self, repository, *, title, body):
        value = self.request("POST", f"/repos/{repository}/issues",
                             json={"title": title, "body": body})
        return {"id": value.get("id"), "number": value.get("number"), "url": value.get("html_url"),
                "title": value.get("title") or title}

    def link_sub_issue(self, repository, parent, child_id):
        value = self.request("POST", f"/repos/{repository}/issues/{parent}/sub_issues",
                             json={"sub_issue_id": child_id})
        return {"native": True, "relationship": value.get("parent_issue_id") if isinstance(value, dict) else None}

    def find_issue_by_marker(self, repository, marker):
        """按标记查找已创建的子Issue；扫描不完整时失败，避免重复创建。"""
        for page in range(1, 6):
            values = self.request("GET", f"/repos/{repository}/issues",
                                  params={"state": "all", "sort": "created", "direction": "desc",
                                          "per_page": 100, "page": page})
            if not isinstance(values, list):
                raise GitHubError("github_invalid_response")
            for item in values:
                if "pull_request" in item:
                    continue
                text = f"{item.get('title') or ''}\n{item.get('body') or ''}"
                if marker in text:
                    return {"id": item.get("id"), "number": item.get("number"),
                            "url": item.get("html_url"), "title": item.get("title") or "",
                            "state": item.get("state")}
            if len(values) < 100:
                return None
        raise GitHubError("issue_scan_limit_reached")

    def pull_matches(self, pull, repository, commit):
        return (pull["head_sha"] == commit and
                pull["head_repository"].lower() == repository.lower() and
                pull["url"].startswith(f"https://{self.host}/{repository}/pull/"))

    def prepare_base(self, repository_path, repository):
        """取远端默认分支对象，不切换或重置用户原工作树。"""
        if remote_repository(repository_path).lower() != repository.lower():
            raise GitHubError("issue_repository_mismatch")
        metadata = self.repository_metadata(repository)
        if metadata["full_name"].lower() != repository.lower():
            raise GitHubError("repository_identity_mismatch")
        branch = metadata["default_branch"]
        commit = self.commit_sha(repository, branch)
        environment = git_auth_environment(self.host, "x-access-token", self.token)
        try:
            run_git(repository_path, "fetch", "--no-tags", "--no-write-fetch-head",
                    f"https://{self.host}/{repository}.git", f"refs/heads/{branch}", extra_env=environment)
            run_git(repository_path, "cat-file", "-e", f"{commit}^{{commit}}")
        except Exception:
            raise GitHubError("github_base_fetch_failed") from None
        return commit


def publish(run_id, root, github, *, note=None):
    """保留原函数名与调用方式；发布流程本身已由 forge 统一实现。"""
    return publish_through(run_id, root, github, note=note)
