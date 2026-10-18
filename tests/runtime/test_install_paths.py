"""配置来源、可写运行目录和已有源码路径保持的契约。"""

from pathlib import Path

import pytest

from repopilot.core import paths
from repopilot.core.config import load_settings
from repopilot.delivery.forge import load_token


@pytest.fixture(autouse=True)
def clear_overrides(monkeypatch):
    for name in ("REPOPILOT_ENV_FILE", "REPOPILOT_RUNTIME_DIR", "DS_API_KEY", "REPOPILOT_API_KEY",
                 "GH_TOKEN", "GITHUB_TOKEN", "GL_TOKEN", "GITLAB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_source_checkout_preserves_old_locations():
    assert paths.source_checkout()
    assert paths.default_env_file() == paths.PROJECT_ROOT / ".env"
    assert paths.default_runtime_root() == paths.PROJECT_ROOT / ".local/runtime"


def test_installed_configuration_uses_launch_directory_without_searching_parents(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "source_checkout", lambda: False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DS_API_KEY=synthetic\nGH_TOKEN=synthetic-gh\nGITLAB_TOKEN=synthetic-gl\n")
    assert load_settings().api_key.get_secret_value() == "synthetic"
    assert load_token("github") == "synthetic-gh"
    assert load_token("gitlab") == "synthetic-gl"
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    assert paths.default_env_file() == child / ".env"


def test_explicit_env_file_and_runtime_dir_are_shared_and_side_effect_free(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "private.env"
    config.write_text("DS_API_KEY=synthetic\nGH_TOKEN=synthetic-gh\nGITLAB_TOKEN=synthetic-gl\n")
    monkeypatch.setenv("REPOPILOT_ENV_FILE", "private.env")
    monkeypatch.setenv("REPOPILOT_RUNTIME_DIR", "state")
    assert load_settings().api_key.get_secret_value() == "synthetic"
    assert load_token("github") == "synthetic-gh"
    assert load_token("gitlab") == "synthetic-gl"
    assert paths.default_runtime_root() == tmp_path / "state"
    assert not (tmp_path / "state").exists()
    monkeypatch.setenv("DS_API_KEY", "synthetic-process")
    assert load_settings().api_key.get_secret_value() == "synthetic-process"


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_installed_defaults_live_in_user_data_not_site_packages(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(paths, "source_checkout", lambda: False)
    monkeypatch.setattr(paths.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    expected = {"win32": "AppData/Local/RepoPilot/runtime",
                "darwin": "Library/Application Support/RepoPilot/runtime",
                "linux": ".local/share/repopilot/runtime"}[platform]
    assert paths.default_runtime_root() == tmp_path / expected


def test_linux_xdg_and_windows_localappdata_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "source_checkout", lambda: False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.default_runtime_root() == tmp_path / "repopilot/runtime"
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert paths.default_runtime_root().is_absolute()
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.default_runtime_root() == tmp_path / "RepoPilot/runtime"
