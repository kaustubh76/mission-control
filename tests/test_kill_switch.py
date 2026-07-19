"""
C3 (ROADMAP §C3) — kill switch unit tests.

Verifies:
  - is_engaged() reflects sentinel-file presence
  - engage() creates the sentinel and rewrites .env atomically
  - release() removes the sentinel only (does NOT re-enable live)
  - engage() preserves unrelated .env lines
  - engage() with no .env still flips the in-process sentinel
"""

from __future__ import annotations

import pytest

from ictbot.runtime import kill_switch as ks


@pytest.fixture(autouse=True)
def _isolated_files(tmp_path, monkeypatch):
    """Redirect both sentinel + .env into a tmp dir per test."""
    sentinel = tmp_path / "data" / "KILL_SWITCH_ENGAGED"
    envfile = tmp_path / ".env"
    monkeypatch.setattr(ks, "KILL_SENTINEL", sentinel)
    monkeypatch.setattr(ks, "ENV_FILE", envfile)
    return {"sentinel": sentinel, "env": envfile, "tmp": tmp_path}


def test_is_engaged_false_when_no_sentinel(_isolated_files):
    assert ks.is_engaged() is False


def test_engage_creates_sentinel(_isolated_files):
    ks.engage()
    assert ks.is_engaged() is True
    assert _isolated_files["sentinel"].exists()


def test_release_removes_sentinel(_isolated_files):
    ks.engage()
    ks.release()
    assert ks.is_engaged() is False


def test_release_is_idempotent(_isolated_files):
    # Releasing twice doesn't raise FileNotFoundError.
    ks.release()
    ks.release()


def test_engage_rewrites_existing_env_key(_isolated_files):
    _isolated_files["env"].write_text(
        "OTHER=keep\nENABLE_LIVE_TRADING=true\nKEEP_THIS=value\n", encoding="utf-8"
    )
    ks.engage()
    content = _isolated_files["env"].read_text(encoding="utf-8")
    assert "ENABLE_LIVE_TRADING=false" in content
    assert "OTHER=keep" in content
    assert "KEEP_THIS=value" in content
    # No duplicate insertion.
    assert content.count("ENABLE_LIVE_TRADING=") == 1


def test_engage_appends_when_key_absent(_isolated_files):
    _isolated_files["env"].write_text("OTHER=keep\n", encoding="utf-8")
    ks.engage()
    content = _isolated_files["env"].read_text(encoding="utf-8")
    assert "OTHER=keep" in content
    assert "ENABLE_LIVE_TRADING=false" in content


def test_engage_without_env_file_still_engages(_isolated_files):
    # No .env on disk → kill switch still works for in-process callers.
    assert not _isolated_files["env"].exists()
    ks.engage()
    assert ks.is_engaged() is True
    # We deliberately don't create a .env from scratch — too surprising.
    assert not _isolated_files["env"].exists()


def test_engage_records_reason(_isolated_files):
    ks.engage(reason="cap-breach")
    body = _isolated_files["sentinel"].read_text(encoding="utf-8")
    assert "cap-breach" in body


def test_engage_uses_atomic_rename(_isolated_files):
    # After engage the .env.tmp shouldn't linger as a hung file.
    _isolated_files["env"].write_text("ENABLE_LIVE_TRADING=true\n", encoding="utf-8")
    ks.engage()
    assert not (_isolated_files["tmp"] / ".env.tmp").exists()


# --- B1: public rewrite_env_key (persist a one-shot key like the minted AGENT_ID) --- #
def test_rewrite_env_key_appends_new_key(_isolated_files):
    _isolated_files["env"].write_text("OTHER=keep\n", encoding="utf-8")
    ks.rewrite_env_key("AGENT_ID", "42")
    content = _isolated_files["env"].read_text(encoding="utf-8")
    assert "AGENT_ID=42" in content
    assert "OTHER=keep" in content  # unrelated lines preserved


def test_rewrite_env_key_replaces_existing_and_is_atomic(_isolated_files):
    _isolated_files["env"].write_text("AGENT_ID=0\nKEEP=x\n", encoding="utf-8")
    ks.rewrite_env_key("AGENT_ID", "7")
    content = _isolated_files["env"].read_text(encoding="utf-8")
    assert "AGENT_ID=7" in content
    assert content.count("AGENT_ID=") == 1  # replaced, not duplicated
    assert "KEEP=x" in content
    assert not (_isolated_files["tmp"] / ".env.tmp").exists()  # atomic, no tmp left


def test_rewrite_env_key_noop_without_env_file(_isolated_files):
    # No .env on disk -> no surprise-create (mirrors the kill switch's contract).
    assert not _isolated_files["env"].exists()
    ks.rewrite_env_key("AGENT_ID", "9")
    assert not _isolated_files["env"].exists()
