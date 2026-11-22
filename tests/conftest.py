import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Every test gets its own data dir; nothing touches the real ./data."""
    monkeypatch.setenv("LETSCRAWL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LETSCRAWL_MIN_DELAY", "0")
    monkeypatch.setenv("PAGESPEED_API_KEY", "")
    monkeypatch.setenv("CRAWL_AUTH_USER", "")
    # the developer's own .env (AI keys, password…) must never change what a test sees
    for var in ("AI_PROVIDER", "AI_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "LETSCRAWL_PASSWORD", "LETSCRAWL_ALLOWED_HOSTS", "COPYSCAPE_USER", "COPYSCAPE_KEY"):
        monkeypatch.setenv(var, "")
    return tmp_path / "data"
