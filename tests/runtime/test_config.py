import os

import pytest
from pydantic import ValidationError

from repopilot.runtime.config import Check, RuntimeSettings, load_settings, load_task


def test_ds_file_config_is_private_and_process_environment_wins(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DS_API_KEY=synthetic-file-secret\nDS_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.delenv("DS_BASE_URL", raising=False)
    monkeypatch.setenv("DS_API_KEY", "synthetic-process-secret")
    monkeypatch.setenv("DS_MODEL", "process-model")
    before = dict(os.environ)
    settings = load_settings(path)
    assert settings.model == "process-model"
    assert settings.api_key.get_secret_value() == "synthetic-process-secret"
    assert "synthetic-process-secret" not in repr(settings)
    assert "synthetic-process-secret" not in settings.model_dump_json()
    assert dict(os.environ) == before


def test_missing_credentials_and_unsafe_endpoint_are_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("DS_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        load_settings(tmp_path / "absent.env")
    with pytest.raises(ValidationError):
        RuntimeSettings(api_key="synthetic", base_url="https://user:secret@example.test")
    with pytest.raises(ValidationError):
        RuntimeSettings(api_key="synthetic", budget_cny=float("inf"))


def test_task_paths_are_relative_to_manifest_and_old_contract_is_rejected(tmp_path):
    (tmp_path / "repo").mkdir()
    path = tmp_path / "task.yaml"
    path.write_text("id: sample\nrepository: repo\nrequest: inspect\n", encoding="utf-8")
    assert load_task(path).repository == (tmp_path / "repo").resolve()
    path.write_text("schema_version: '1.0'\nid: sample\nrepository: repo\nrequest: inspect\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_task(path)


def test_check_must_be_an_argv_array_and_not_a_windows_shell_script():
    with pytest.raises(ValidationError):
        Check(argv="python -m pytest")
    with pytest.raises(ValidationError):
        Check(argv=["run.cmd"])


def test_native_deepseek_payload_uses_provider_parameters_without_sdk_retries():
    from langchain_core.messages import HumanMessage
    from repopilot.runtime.agent import make_model

    settings = RuntimeSettings(api_key="synthetic-private")
    model = make_model(settings)
    payload = model._get_request_payload([HumanMessage(content="probe")])
    assert payload["max_tokens"] == settings.max_output_tokens
    assert "max_completion_tokens" not in payload
    assert payload["extra_body"]["thinking"] == {"type": "disabled"}
    assert model.max_retries == 0
