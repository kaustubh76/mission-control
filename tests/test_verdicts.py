"""runtime.verdicts — the verdict store the dashboard badges from. Asserts: record() merges
survival/forward under [strategy][kind] without clobbering the other kind or other strategies;
the write is atomic (no leftover .tmp) and canonical (sorted, indent=2); load() degrades to {}
on missing + corrupt files."""

from __future__ import annotations

import json

from ictbot.runtime import verdicts


def _redirect(tmp_path, monkeypatch):
    f = tmp_path / "strategy_gates.json"
    monkeypatch.setattr(verdicts, "VERDICTS_FILE", f)
    return f


def test_record_merges_kinds_without_clobber(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    verdicts.record("dual_momentum", "survival", {"passed": True})
    verdicts.record("dual_momentum", "forward", {"status": "evaluated"})
    data = verdicts.load()
    assert data["dual_momentum"]["survival"] == {"passed": True}
    assert data["dual_momentum"]["forward"] == {"status": "evaluated"}
    # re-recording one kind must leave the OTHER kind intact
    verdicts.record("dual_momentum", "survival", {"passed": False})
    data = verdicts.load()
    assert data["dual_momentum"]["survival"] == {"passed": False}
    assert data["dual_momentum"]["forward"] == {"status": "evaluated"}


def test_record_keeps_strategies_separate(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    verdicts.record("a", "survival", {"passed": True})
    verdicts.record("b", "survival", {"passed": False})
    assert set(verdicts.load()) == {"a", "b"}


def test_atomic_write_no_tmp_and_canonical_json(tmp_path, monkeypatch):
    f = _redirect(tmp_path, monkeypatch)
    verdicts.record("z", "survival", {"passed": True})
    verdicts.record("a", "forward", {"status": "x"})
    assert f.exists()
    assert not f.with_suffix(".json.tmp").exists()  # os.replace cleaned the tmp
    raw = f.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert list(parsed) == ["a", "z"]  # sort_keys=True
    assert raw == json.dumps(parsed, indent=2, sort_keys=True) + "\n"


def test_load_degrades_on_missing(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    assert verdicts.load() == {}


def test_load_degrades_on_corrupt(tmp_path, monkeypatch):
    f = _redirect(tmp_path, monkeypatch)
    f.write_text("{not valid json", encoding="utf-8")
    assert verdicts.load() == {}
