"""仓库托管平台的中立接入层：识别来源、加载凭证、发布已批准版本。

GitHub 与 GitLab 的流程（取 Issue、取基线、推分支、建草稿合并请求）相同，差别只在
REST 路径与字段名，因此把流程留在这里，平台差异放在各自的 provider 里。

凭证只用于宿主进程发起的 HTTP 与 git 推送，不进入任务、模型或检查环境；错误信息
不回显响应正文，避免把令牌带回日志。
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx

from repopilot.core.paths import default_env_file
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp
from repopilot.orchestration.state import approval_digest, attach, canonical_hash
from repopilot.execution.workspace import run_git

# 各平台的注册表：host 用于默认实例，provider 名用于错误码与 token 变量。
GITHUB_HOST = "github.com"
GITLAB_HOST = "gitlab.com"
SUPPORTED_PROVIDERS = ("github", "gitlab")


class ForgeError(RuntimeError):
    """平台接入失败的统一基类；具体平台再用自己的子类保留错误码前缀。"""

    def __init__(self, message, *, retry_after=0, status=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


@dataclass(frozen=True)
class Origin:
    """一个远端仓库的来源：哪个平台、哪个实例、哪个项目路径。"""

    provider: str
    host: str
    repository: str


def repository_source_key(origin):
    """来源身份包含平台、实例和项目；不能只用项目名跨实例去重。"""
    return f"{origin.provider}:{origin.host.lower()}/{origin.repository.lower()}"


def issue_source_key(url):
    parsed = parse_issue_url(url)
    origin = Origin(parsed['provider'], parsed['host'], parsed['repository'])
    return f"{repository_source_key(origin)}#{parsed['number']}"


def parse_issue_url(url):
    """严格解析 Issue 链接，返回 {provider, host, repository, number}。

    只接受 https 且不带凭证、查询串或片段的链接；GitLab 的 Issue 路径带 `/-/issues/`
    标记，因此可以从链接本身判断平台，不需要用户额外声明。
    """
    url = str(url or "")
    gitlab = re.fullmatch(
        r"https://([A-Za-z0-9.-]+)/([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)/-/issues/([1-9][0-9]*)/?", url)
    if gitlab:
        host, repository, number = gitlab.group(1), gitlab.group(2), int(gitlab.group(3))
        provider = "gitlab"
    else:
        github = re.fullmatch(
            r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?", url)
        if not github:
            raise ForgeError("use_supported_issue_url")
        provider, host, repository, number = "github", GITHUB_HOST, github.group(1), int(github.group(2))
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
        raise ForgeError("use_supported_issue_url")
    return {"provider": provider, "host": host, "repository": repository, "number": number}


def parse_remote(remote):
    """把 git remote 转成 Origin；只接受 https 与 scp 风格的两种写法。"""
    remote = str(remote or "").strip()
    scp = re.fullmatch(r"git@([A-Za-z0-9.-]+):(.+)", remote)
    if scp:
        host, path = scp.group(1), scp.group(2)
    else:
        parts = urlsplit(remote)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query:
            raise ForgeError("unsupported_origin")
        host, path = parts.hostname, parts.path.lstrip("/")
    repository = path.removesuffix(".git").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", repository):
        raise ForgeError("invalid_origin")
    return Origin(provider_for_host(host), host.lower(), repository)


def provider_for_host(host):
    host = str(host or "").lower()
    if host == GITHUB_HOST:
        return "github"
    if host == GITLAB_HOST or "gitlab" in host:
        return "gitlab"
    raise ForgeError("unsupported_origin_host")


def remote_origin(path):
    remote = run_git(path, "remote", "get-url", "origin").stdout.decode().strip()
    return parse_remote(remote)


TOKEN_VARIABLES = {"github": ("GH_TOKEN", "GITHUB_TOKEN"), "gitlab": ("GITLAB_TOKEN", "GL_TOKEN")}


def load_token(provider, env_file=None):
    """按平台读取令牌：进程环境整体优先于 .env；缺失时明确失败而不是空令牌重试。"""
    if provider not in TOKEN_VARIABLES:
        raise ForgeError("unsupported_provider")
    names = TOKEN_VARIABLES[provider]
    for name in names:
        token = os.environ.get(name)
        if token and token.strip():
            return token.strip()
    env_file = default_env_file() if env_file is None else Path(env_file)
    values = dotenv_values(env_file, interpolate=False) if env_file.exists() else {}
    for name in names:
        token = values.get(name)
        if token and token.strip():
            return token.strip()
    raise ForgeError(f"{provider}_token_required")


def open_forge(subject, *, transport=None, token=None, env_file=None):
    """按 Issue 链接或 Origin 打开对应平台的客户端；凭证默认从环境/.env 读取。"""
    if isinstance(subject, Origin):
        origin = subject
    elif "://" in str(subject):
        parsed = parse_issue_url(subject)
        origin = Origin(parsed["provider"], parsed["host"], parsed["repository"])
    else:
        origin = parse_remote(subject)
    token = token or load_token(origin.provider, env_file)
    if origin.provider == "github":
        from .github import GitHub
        return GitHub(token, host=origin.host, transport=transport)
    from .gitlab import GitLab
    return GitLab(token, host=origin.host, transport=transport)


def publish(run_id, root, forge, *, note=None):
    """只发布审批绑定的版本；重试先核对远端分支与合并请求，避免重复创建副作用。

    平台差异通过 provider 的钩子体现：仓库元数据、提交 SHA、推送、查找/创建草稿
    合并请求、以及对返回结果的校验。流程本身与平台无关。
    """
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        if state["status"] in {"published", "merged"}:
            return {"run_id": run_id, "status": "published", "pull_request": state["pull_request"]}
        approval = state.get("approval") or {}
        if (state["status"] not in {"approved", "publication_failed", "publishing"} or
                not approval.get("approved") or approval.get("commit") != state["reviewed_commit"] or
                approval.get("digest") != approval_digest(state)):
            raise StateError("matching_human_approval_required")
        if not state.get("issue"):
            raise StateError("issue_required")
        workspace = attach(state, store.root)
        workspace.prepare().assert_clean(approval["commit"])
        from repopilot.orchestration import knowledge
        from repopilot.knowledge.documents import KnowledgeFault
        try:
            knowledge.validate(state, store.root)
        except KnowledgeFault as exc:
            if not state.get("publication"):
                raise
            from .reconciliation import reconcile
            reconcile(state, store, forge, workspace)
            # 只有远端只读核对已经落盘，才可退出publish并接受新资料。
            knowledge.pause(state, store.root, str(exc))
            store.save(state)
            return {"run_id": run_id, "status": state["status"], "stop_reason": state["stop_reason"],
                    "publication": state["publication"], "human_requests": state["human_requests"]}
        issue = state["issue"]
        repository = issue["repository"]
        if remote_origin(workspace.repository()).repository.lower() != repository.lower():
            raise forge.fail("issue_repository_mismatch")
        if canonical_hash(forge.issue(issue["url"])) != canonical_hash(issue):
            raise forge.fail("issue_changed_reapproval_required")
        metadata = forge.repository_metadata(repository)
        base = metadata["default_branch"]
        if metadata["full_name"].lower() != repository.lower():
            raise forge.fail("repository_identity_mismatch")
        remote_base = forge.commit_sha(repository, base)
        if remote_base != state["base_commit"]:
            raise forge.fail("remote_base_changed_rerun_required")
        if not state.get("publication"):
            state["publication"] = {"base": base, "branch": f"feature-repopilot-{run_id}", "public_note": note,
                                    "commit": approval["commit"], "approval": dict(approval)}
            store.save(state)
        elif state["publication"]["base"] != base:
            raise forge.fail("default_branch_changed")
        publication = state["publication"]
        marker = f"<!-- repopilot-run:{run_id} -->"
        state["status"], state["in_flight"] = "publishing", "publish"
        store.save(state)
        try:
            pull = forge.find_pull_request(repository, publication["branch"], publication["base"], marker)
            if pull is None:
                forge.push(workspace.repository(), repository, publication["branch"], approval["commit"])
                # 推送期间也可能有先前的请求返回远端，创建前再核对一次。
                pull = forge.find_pull_request(repository, publication["branch"], publication["base"], marker)
            if pull is None:
                checks = "\n".join(f"- `{item['name']}`: exit {item['exit_code']}" for item in state["verified"])
                pull = forge.create_pull_request(
                    repository, title=issue["title"][:200],
                    branch=publication["branch"], base=publication["base"],
                    body=(f"Closes {issue['url']}\n\nImplements: {issue['title']}\n\n"
                          f"Latest implementation / review follow-up:\n{state['report']['summary']}\n\n"
                          f"Independent checks:\n{checks}\n\nReview rounds: {len(state['history'])}; "
                          f"rework attempts: {state['reworks']}.\n\n"
                          f"Reviewed and human-approved commit: `{approval['commit']}`\n\n"
                          f"{publication.get('public_note') or ''}\n\n{marker}"))
            if not forge.pull_matches(pull, repository, approval["commit"]):
                raise forge.fail("published_head_mismatch")
            # PR/MR可能仍返回旧提交，复用时也必须独立读取当前分支，不能靠push间接检查。
            current_head = forge.branch_head(workspace.repository(), repository, publication["branch"])
            if current_head is None:
                raise forge.fail("remote_branch_missing")
            if current_head != approval["commit"]:
                raise forge.fail("remote_branch_conflict")
            state["pull_request"] = {"url": pull["url"], "number": pull["number"],
                                     "commit": approval["commit"], "at": timestamp()}
            state["status"], state["in_flight"], state["stop_reason"] = "published", None, "draft_pr_created"
            store.save(state)
        except Exception as exc:
            state["status"], state["in_flight"] = "publication_failed", None
            if isinstance(exc, ForgeError):
                state["stop_reason"] = str(exc)
            store.save(state)
            raise
        return {"run_id": run_id, "status": "published", "pull_request": state["pull_request"]}


class Forge:
    """两个平台共用的 HTTP 客户端外壳；子类只需声明路径前缀与请求头。"""

    name = ""
    api_base = ""
    path_prefix = ""
    Error = ForgeError

    def __init__(self, token, *, host, transport=None):
        self.token = token
        self.host = host
        self.client = httpx.Client(base_url=self.api_base, timeout=25,
                                   follow_redirects=False, transport=transport,
                                   headers=self.headers())

    def headers(self):
        raise NotImplementedError

    def allowed_paths(self):
        """只允许固定 API 前缀；能力探测额外需要当前令牌身份。"""
        return (self.path_prefix, "/user")

    def fail(self, code, *, retry_after=0):
        return self.Error(code, retry_after=retry_after)

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        # 不接收任意 URL，避免把鉴权头发送到任务指定的服务器。
        if (not any(path.startswith(prefix) for prefix in self.allowed_paths())
                or "://" in path or ".." in path):
            raise self.Error("invalid_api_path")
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise self.Error(f"{self.name}_transport_error") from None
        if response.status_code not in {200, 201}:
            raise self.Error(f"{self.name}_http_{response.status_code}",
                             retry_after=self.retry_after(response), status=response.status_code)
        try:
            return response.json()
        except ValueError:
            raise self.Error(f"{self.name}_invalid_response") from None

    def viewer(self):
        """读取令牌身份；这是只读预检，不代表有评论或建单写权限。"""
        value = self.request("GET", "/user")
        login = str(value.get("login") or value.get("username") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", login):
            raise self.Error(f"{self.name}_viewer_invalid")
        return login

    def preflight(self, issue_url, *, owner=None):
        """只读探查平台能力、令牌身份和登记负责人，不做任何写入。"""
        parsed = parse_issue_url(issue_url)
        if parsed["provider"] != self.name or parsed["host"].lower() != self.host.lower():
            raise self.Error("issue_identity_mismatch")
        repository, number = parsed["repository"], parsed["number"]
        viewer = self.viewer()
        features = self.probe_issue_features(repository, number)
        return {"provider": self.name, "host": self.host, "repository": repository,
                "issue": {"number": number, "url": issue_url},
                "viewer": viewer, "registered_owner": owner,
                "comments": features["comments"], "native_sub_issues": features["native_sub_issues"],
                "issue_creation": features["issue_creation"], "write_verified": False,
                "checked_at": timestamp()}

    def probe_issue_features(self, repository, number):
        raise NotImplementedError

    def find_issue_comment(self, repository, number, marker):
        for item in self.issue_comments(repository, number):
            if marker in str(item.get("body") or ""):
                return item
        return None

    def retry_after(self, response):
        """读取限流提示；解析失败按 0 处理，不影响错误本身。"""
        try:
            return max(0, min(86400, int(response.headers.get("Retry-After", "0"))))
        except ValueError:
            return 0


def git_auth_environment(host, username, token):
    """用 basic 认证头推送；令牌不进入命令参数、远端 URL 或 git 配置文件。"""
    import base64
    encoded = base64.b64encode(f"{username}:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.https://{host}/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}"}
