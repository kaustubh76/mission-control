"""settings._resolve_data_dir — the ALLOCATOR_DATA_DIR override that isolates a per-arm forward
paper track from the production data/ tree (so it never clobbers the dashboard's SIM journal)."""

from __future__ import annotations

from pathlib import Path

from ictbot.settings import _resolve_data_dir


def test_default_when_unset():
    root = Path("/repo")
    assert _resolve_data_dir(None, root) == root / "data"
    assert _resolve_data_dir("", root) == root / "data"  # empty string = unset


def test_override_redirects_data_dir():
    assert _resolve_data_dir("/tmp/forward/dual_momentum", Path("/repo")) == Path(
        "/tmp/forward/dual_momentum"
    )
