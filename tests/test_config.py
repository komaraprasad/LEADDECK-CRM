from app.config import get_settings, load_dotenv, DEFAULT_TOOL_URLS


def test_env_overrides_and_defaults():
    s = get_settings({"DATABASE_URL": "postgresql://x", "TOOL_URL_TALENTPULSE": "http://h:1/", "RENDER": "1"})
    assert s.database_url == "postgresql://x" and s.tool_urls["talentpulse"] == "http://h:1" and s.secure_cookies
    assert s.tool_urls["jobharvest"] == DEFAULT_TOOL_URLS["jobharvest"] and s.secret_key == ""


def test_dotenv_real_env_wins(tmp_path, monkeypatch):
    f = tmp_path / ".env"; f.write_text('# c\nFOO_X=from_file\nBAR_X="quoted"\n')
    monkeypatch.setenv("FOO_X", "from_env"); monkeypatch.delenv("BAR_X", raising=False)
    load_dotenv(f)
    import os
    assert os.environ["FOO_X"] == "from_env" and os.environ["BAR_X"] == "quoted"
    monkeypatch.delenv("BAR_X", raising=False)
