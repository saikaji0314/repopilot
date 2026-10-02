from repopilot.repository_snapshot import collect_source_files


def test_snapshot_includes_allowed_text_and_excludes_sensitive_files(tmp_path):
    (tmp_path / "app.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "schema.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "oracle.yaml").write_text("answer: secret", encoding="utf-8")
    (tmp_path / "data.db").write_bytes(b"not-a-real-db")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "hidden.py").write_text("SECRET = True", encoding="utf-8")

    snapshot = collect_source_files(tmp_path)

    assert "app.py" in snapshot
    assert "print('ok')" in snapshot
    assert "schema.sql" in snapshot
    assert "SELECT 1;" in snapshot
    assert "SECRET=value" not in snapshot
    assert "answer: secret" not in snapshot
    assert "hidden.py" not in snapshot
