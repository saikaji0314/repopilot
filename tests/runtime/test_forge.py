"""平台识别与凭证路由测试；不访问网络，也不代表已在真实 GitLab 上跑通。"""

import pytest

from repopilot.delivery.forge import (ForgeError, Origin, load_token, open_forge, parse_issue_url,
                                      parse_remote, provider_for_host)


@pytest.mark.parametrize("url,provider,repository,number", [
    ("https://github.com/owner/repo/issues/7", "github", "owner/repo", 7),
    ("https://gitlab.com/group/project/-/issues/3", "gitlab", "group/project", 3),
    ("https://gitlab.com/group/sub/project/-/issues/12", "gitlab", "group/sub/project", 12),
    ("https://gitlab.example.com/team/app/-/issues/1/", "gitlab", "team/app", 1),
])
def test_issue_url_is_parsed_per_platform(url, provider, repository, number):
    parsed = parse_issue_url(url)
    assert (parsed["provider"], parsed["repository"], parsed["number"]) == (provider, repository, number)


@pytest.mark.parametrize("url", [
    "http://gitlab.com/group/project/-/issues/1",          # 只接受 https
    "https://gitlab.com/group/project/issues/1",           # 缺少 /-/
    "https://gitlab.com/group/project/-/issues/0",         # 编号从 1 开始
    "https://gitlab.com/group/project/-/merge_requests/1", # MR 不是 Issue
    "https://github.com/owner/repo/pull/1",
    "https://gitlab.com/group/project/-/issues/1?x=1",
    "https://user:pass@gitlab.com/group/project/-/issues/1",
    "not-a-url",
])
def test_unsupported_issue_urls_are_rejected(url):
    with pytest.raises(ForgeError):
        parse_issue_url(url)


@pytest.mark.parametrize("remote,expected", [
    ("https://github.com/owner/repo.git", Origin("github", "github.com", "owner/repo")),
    ("git@github.com:owner/repo.git", Origin("github", "github.com", "owner/repo")),
    ("https://gitlab.com/group/sub/project.git", Origin("gitlab", "gitlab.com", "group/sub/project")),
    ("git@gitlab.com:group/project.git", Origin("gitlab", "gitlab.com", "group/project")),
    ("https://gitlab.example.com/team/app", Origin("gitlab", "gitlab.example.com", "team/app")),
])
def test_remote_origin_is_detected_by_host(remote, expected):
    assert parse_remote(remote) == expected


@pytest.mark.parametrize("remote", [
    "https://user:token@github.com/owner/repo.git",   # 凭证不进入仓库标识
    "http://gitlab.com/group/project.git",            # 只接受 https
    "https://example.test/owner/repo.git",            # 未知平台
    "ssh://git@github.com/owner/repo.git",            # 未支持的写法
])
def test_unsafe_or_unknown_remotes_are_rejected(remote):
    with pytest.raises(ForgeError):
        parse_remote(remote)


def test_provider_for_host_matches_self_hosted_gitlab():
    assert provider_for_host("gitlab.com") == "gitlab"
    assert provider_for_host("gitlab.example.com") == "gitlab"
    assert provider_for_host("github.com") == "github"
    with pytest.raises(ForgeError, match="unsupported_origin_host"):
        provider_for_host("example.test")


def test_token_is_read_per_provider_and_missing_token_fails(tmp_path, monkeypatch):
    # 宿主机可能已导出真实令牌；测试先清空，避免把本机凭证带进断言。
    for name in ("GITLAB_TOKEN", "GL_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("GITLAB_TOKEN=file-token\nGH_TOKEN=github-token\n", encoding="utf-8")
    assert load_token("gitlab", env) == "file-token"
    assert load_token("github", env) == "github-token"
    monkeypatch.setenv("GL_TOKEN", "process-token")
    assert load_token("gitlab", env) == "process-token", "进程环境优先于文件"
    monkeypatch.delenv("GL_TOKEN")
    env.write_text("GH_TOKEN=github-token\n", encoding="utf-8")
    with pytest.raises(ForgeError, match="gitlab_token_required"):
        load_token("gitlab", env)


def test_open_forge_routes_by_issue_url_and_remote(monkeypatch):
    opened = []

    class FakeGitHub:
        def __init__(self, token, *, host, transport=None):
            opened.append(("github", host, token))

    class FakeGitLab:
        def __init__(self, token, *, host, transport=None):
            opened.append(("gitlab", host, token))

    monkeypatch.setattr("repopilot.delivery.github.GitHub", FakeGitHub)
    monkeypatch.setattr("repopilot.delivery.gitlab.GitLab", FakeGitLab)
    open_forge("https://github.com/owner/repo/issues/1", token="t")
    open_forge("https://gitlab.com/group/project/-/issues/2", token="t")
    open_forge("git@gitlab.com:group/project.git", token="t")
    assert opened == [("github", "github.com", "t"),
                      ("gitlab", "gitlab.com", "t"),
                      ("gitlab", "gitlab.com", "t")]
