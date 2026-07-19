"""runtime.stability_grades — the JSON sidecar the dashboard badges from. Merge semantics (a
partial `--arm` run updates only those arms without wiping the rest), atomic + canonical write,
and degrade-to-{} on missing/corrupt."""

from __future__ import annotations

import json

from ictbot.runtime import stability_grades as sg


def test_record_merges_without_wiping(tmp_path):
    f = tmp_path / "stab.json"
    sg.record({"breakout": {"grade": "ROBUST", "ts": "t1"}}, path=f)
    sg.record({"dual_momentum": {"grade": "ROBUST", "ts": "t2"}}, path=f)
    assert set(sg.load(f)) == {"breakout", "dual_momentum"}  # merged, not wiped
    sg.record({"breakout": {"grade": "FRAGILE", "ts": "t3"}}, path=f)
    assert sg.load(f)["breakout"]["grade"] == "FRAGILE"  # overwrites its own arm
    assert sg.load(f)["dual_momentum"]["grade"] == "ROBUST"  # other arm untouched


def test_atomic_write_no_tmp_and_canonical(tmp_path):
    f = tmp_path / "stab.json"
    sg.record({"z": {"grade": "ROBUST"}}, path=f)
    sg.record({"a": {"grade": "UNSTABLE"}}, path=f)
    assert not f.with_suffix(".json.tmp").exists()
    raw = f.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert list(parsed) == ["a", "z"]  # sort_keys
    assert raw == json.dumps(parsed, indent=2, sort_keys=True) + "\n"


def test_load_degrades_on_missing_and_corrupt(tmp_path):
    assert sg.load(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert sg.load(bad) == {}
