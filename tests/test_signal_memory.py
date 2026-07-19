import json

from ictbot import settings as config
from ictbot.runtime import signal_memory as sm


def test_load_returns_empty_when_missing(tmp_path, monkeypatch):
    fake = tmp_path / "missing.json"
    monkeypatch.setattr(config, "SIGNAL_FILE", fake)
    monkeypatch.setattr(sm, "SIGNAL_FILE", fake)
    assert sm.load_last_signal() == {}


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    fake = tmp_path / "ls.json"
    monkeypatch.setattr(config, "SIGNAL_FILE", fake)
    monkeypatch.setattr(sm, "SIGNAL_FILE", fake)

    sm.save_last_signal({"signal": "BTC/USDT:USDT_BUY"})
    assert sm.load_last_signal() == {"signal": "BTC/USDT:USDT_BUY"}
    # also verify it's valid JSON on disk
    assert json.loads(fake.read_text()) == {"signal": "BTC/USDT:USDT_BUY"}


def test_corrupt_file_returns_empty(tmp_path, monkeypatch):
    fake = tmp_path / "bad.json"
    fake.write_text("not-json{{{")
    monkeypatch.setattr(config, "SIGNAL_FILE", fake)
    monkeypatch.setattr(sm, "SIGNAL_FILE", fake)
    assert sm.load_last_signal() == {}
