"""固定 GitLab REST v4 接口；凭证只用于宿主进程，不进入任务、模型或检查环境。

与 GitHub 的主要差异：
- 项目用 `namespace/project` 路径经 URL 编码后定位，允许子组；
- 认证头是 `PRIVATE-TOKEN`，推送用 basic 认证、用户名取 `oauth2`；
- Issue 状态是 `opened`，Web 路径带 `/-/issues/`；
- 合并请求（MR）用 `Draft:` 标题前缀表达草稿，没有 `draft` 布尔参数。
"""

from urllib.parse import quote
import re

from repopilot.core.config import PROJECT_ROOT, Task
from repopilot.execution.workspace import run_git
from .forge import (GITLAB_HOST, Forge, ForgeError, git_auth_environment,
                    load_token as load_forge_token, parse_issue_url, remote_origin)


class GitLabError(ForgeError):
    """GitLab 接入失败；错误码前缀为 gitlab_。"""


def load_token(env_file=PROJECT_ROOT / ".env"):
    return load_forge_token("gitlab", env_file)


class GitLab(Forge):
    name = "gitlab"
    path_prefix = "/projects/"
    Error = GitLabError

    def __init__(self, token, *, host=GITLAB_HOST, transport=None, api_base=None):
        self.api_base = api_base or f"https://{host}/api/v4"
        super().__init__(token, host=host, transport=transport)

    def headers(self):
        return {"PRIVATE-TOKEN": self.token, "Accept": "application/json", "User-Agent": "RepoPilot"}

    @staticmethod
    def project(repository):
        """项目路径要整段编码，子组的斜杠也属于标识的一部分。"""
        return quote(str(repository), safe="")

    def issue(self, url):
        parsed = parse_issue_url(url)
        if parsed["provider"] != "gitlab" or parsed["host"].lower() != self.host.lower():
            raise GitLabError("issue_identity_mismatch")
        repository, number = parsed["repository"], parsed["number"]
        value = self.request("GET", f"/projects/{self.project(repository)}/issues/{number}")
        if value.get("state") != "opened":
            raise GitLabError("open_issue_required")
        web_url = str(value.get("web_url") or "").rstrip("/")
        if value.get("iid") != number or web_url != url.rstrip("/"):
            raise GitLabError("issue_identity_mismatch")
        return {"repository": repository, "number": number, "url": web_url,
                "title": value["title"], "body": value.get("description") or ""}

    def intake(self, url, template):
        issue = self.issue(url)
        if remote_origin(template.repository).repository.lower() != issue["repository"].lower():
            raise GitLabError("issue_repository_mismatch")
        # 验证命令和路径始终来自本机可信模板，Issue 只贡献需求文本。
        task = Task.model_validate({**template.model_dump(),
            "id": f"issue-{issue['number']}",
            "request": f"{template.request}\n\nGitLab Issue: {issue['url']}\n{issue['title']}\n\n{issue['body']}"})
        return task, issue

    def open_issues(self, repository, *, label=None, since=None):
        """分页枚举开放 Issue；超过扫描上限报错，不把截断当完整结果。"""
        result = []
        for page in range(1, 11):
            params = {"state": "opened", "order_by": "created_at", "sort": "asc",
                      "per_page": 100, "page": page}
            if label:
                params["labels"] = label
            if since:
                params["created_after"] = since
            values = self.request("GET", f"/projects/{self.project(repository)}/issues", params=params)
            if not isinstance(values, list):
                raise GitLabError("gitlab_invalid_response")
            # 统一成与 GitHub 相同的字段，队列与轮询逻辑不需要区分平台。
            result.extend({"number": item["iid"], "html_url": item["web_url"],
                           "title": item.get("title") or "", "state": "open",
                           "created_at": item.get("created_at"),
                           "labels": [{"name": name} for name in item.get("labels") or []]}
                          for item in values if item.get("state") == "opened")
            if len(values) < 100:
                return result
        raise GitLabError("issue_scan_limit_reached")

    def branch_head(self, workspace, repository, branch):
        """只读核对分支，缺失时返回None。"""
        environment = git_auth_environment(self.host, "oauth2", self.token)
        url = f"https://{self.host}/{repository}.git"
        result = run_git(workspace, "ls-remote", url, f"refs/heads/{branch}", extra_env=environment)
        refs = result.stdout.decode().split()
        return refs[0] if refs else None

    def push(self, workspace, repository, branch, commit):
        current = self.branch_head(workspace, repository, branch)
        if current:
            if current != commit:
                raise GitLabError("remote_branch_conflict")
            return
        environment = git_auth_environment(self.host, "oauth2", self.token)
        url = f"https://{self.host}/{repository}.git"
        try:
            run_git(workspace, "-c", "core.hooksPath=", "push", "--no-verify", url,
                    f"{commit}:refs/heads/{branch}", extra_env=environment)
        except Exception:
            raise GitLabError("gitlab_push_failed_reconcile_before_retry") from None

    def repository_metadata(self, repository):
        value = self.request("GET", f"/projects/{self.project(repository)}")
        return {"full_name": value["path_with_namespace"], "default_branch": value["default_branch"]}

    def commit_sha(self, repository, ref):
        value = self.request("GET", f"/projects/{self.project(repository)}/repository/commits/"
                                    f"{quote(ref, safe='')}")
        commit = value.get("id") or ""
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise GitLabError("invalid_remote_commit")
        return commit

    def find_pull_request(self, repository, branch, base, marker):
        values = self.request("GET", f"/projects/{self.project(repository)}/merge_requests",
                              params={"state": "all", "source_branch": branch,
                                      "target_branch": base, "per_page": 100})
        if not values:
            return None
        if len(values) != 1 or marker not in (values[0].get("description") or ""):
            raise GitLabError("pull_request_conflict")
        item = values[0]
        return {"url": item["web_url"], "number": item["iid"],
                "head_sha": item.get("sha") or self.merge_request_head(repository, item["iid"])}

    def merge_request_head(self, repository, iid):
        value = self.request("GET", f"/projects/{self.project(repository)}/merge_requests/{iid}")
        return value.get("sha") or ""

    def create_pull_request(self, repository, *, title, branch, base, body):
        # GitLab 用标题前缀表示草稿，没有 draft 参数。
        value = self.request("POST", f"/projects/{self.project(repository)}/merge_requests", json={
            "source_branch": branch, "target_branch": base,
            "title": f"Draft: {title}", "description": body})
        return {"url": value["web_url"], "number": value["iid"],
                "head_sha": value.get("sha") or self.merge_request_head(repository, value["iid"])}

    def probe_issue_features(self, repository, number):
        """GitLab 没有可依赖的原生子Issue层级，因此明确标记为链接映射。"""
        return {"comments": self._probe(
                    f"GET /projects/.../issues/{number}/notes",
                    lambda: self.request("GET", f"/projects/{self.project(repository)}/issues/{number}/notes",
                                         params={"per_page": 1}), absent={404, 410}),
                "native_sub_issues": {"status": "unsupported",
                                      "evidence": "gitlab uses linked issue mapping; no native child hierarchy"},
                "issue_creation": self._issue_creation(repository)}

    def _probe(self, label, call, *, absent):
        try:
            call()
            return {"status": "supported", "evidence": f"{label} returned 200"}
        except GitLabError as exc:
            if exc.status in absent:
                return {"status": "unsupported", "evidence": f"{label} returned {exc.status}"}
            if exc.status in {401, 403}:
                return {"status": "unknown", "evidence": f"{label} returned {exc.status}"}
            raise

    def _issue_creation(self, repository):
        value = self.request("GET", f"/projects/{self.project(repository)}")
        access = ((value.get("permissions") or {}).get("project_access") or {}).get("access_level") or 0
        allowed = int(access) >= 20  # GitLab Reporter 及以上可以创建Issue
        return {"status": "supported" if allowed else "unknown",
                "evidence": f"GET /projects/... access_level={access}"}

    def _comment(self, repository, number, value):
        author = value.get("author") or {}
        return {"id": value.get("id"),
                "url": f"https://{self.host}/{repository}/-/issues/{number}#note_{value.get('id')}",
                "body": value.get("body") or "", "author": author.get("username") or "",
                "author_type": author.get("type") or "", "created_at": value.get("created_at"),
                "system": bool(value.get("system"))}

    def issue_comments(self, repository, number):
        result = []
        for page in range(1, 6):
            values = self.request("GET", f"/projects/{self.project(repository)}/issues/{number}/notes",
                                  params={"per_page": 100, "page": page})
            if not isinstance(values, list):
                raise GitLabError("gitlab_invalid_response")
            result.extend(self._comment(repository, number, item) for item in values)
            if len(values) < 100:
                return result
        raise GitLabError("issue_comment_scan_limit_reached")

    def add_issue_comment(self, repository, number, body):
        value = self.request("POST", f"/projects/{self.project(repository)}/issues/{number}/notes",
                             json={"body": body})
        return self._comment(repository, number, value)

    def create_issue(self, repository, *, title, body):
        value = self.request("POST", f"/projects/{self.project(repository)}/issues",
                             json={"title": title, "description": body})
        return {"id": value.get("id"), "number": value.get("iid"), "url": value.get("web_url"),
                "title": value.get("title") or title}

    def link_sub_issue(self, repository, parent, child_id):
        return {"native": False, "reason": "gitlab_native_sub_issues_unavailable"}

    def find_issue_by_marker(self, repository, marker):
        for page in range(1, 6):
            values = self.request("GET", f"/projects/{self.project(repository)}/issues",
                                  params={"state": "all", "order_by": "created_at", "sort": "desc",
                                          "per_page": 100, "page": page})
            if not isinstance(values, list):
                raise GitLabError("gitlab_invalid_response")
            for item in values:
                text = f"{item.get('title') or ''}\n{item.get('description') or ''}"
                if marker in text:
                    return {"id": item.get("id"), "number": item.get("iid"),
                            "url": item.get("web_url"), "title": item.get("title") or "",
                            "state": item.get("state")}
            if len(values) < 100:
                return None
        raise GitLabError("issue_scan_limit_reached")

    def pull_matches(self, pull, repository, commit):
        expected = f"https://{self.host}/{repository}/-/merge_requests/"
        return pull["head_sha"] == commit and pull["url"].startswith(expected)

    def prepare_base(self, repository_path, repository):
        """取远端默认分支对象，不切换或重置用户原工作树。"""
        if remote_origin(repository_path).repository.lower() != repository.lower():
            raise GitLabError("issue_repository_mismatch")
        metadata = self.repository_metadata(repository)
        if metadata["full_name"].lower() != repository.lower():
            raise GitLabError("repository_identity_mismatch")
        branch = metadata["default_branch"]
        commit = self.commit_sha(repository, branch)
        environment = git_auth_environment(self.host, "oauth2", self.token)
        try:
            run_git(repository_path, "fetch", "--no-tags", "--no-write-fetch-head",
                    f"https://{self.host}/{repository}.git", f"refs/heads/{branch}", extra_env=environment)
            run_git(repository_path, "cat-file", "-e", f"{commit}^{{commit}}")
        except Exception:
            raise GitLabError("gitlab_base_fetch_failed") from None
        return commit
