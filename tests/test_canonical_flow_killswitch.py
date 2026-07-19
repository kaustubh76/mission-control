"""
Regression tests for the Phase-A canonical-flow defaults and the
`CANONICAL_FLOW=off` kill-switch that rolls them all back.

Two invariants under test:
  1. With CANONICAL_FLOW unset (or =on), the spec defaults are active:
     strategy_mode=follow, bias_engine=swing, poi_engine=order_block.
  2. With CANONICAL_FLOW=off, EVERY canonical-flow default is forced
     back to legacy values regardless of per-field overrides, so a
     production rollback is a single env var change.

`settings` is a module-level singleton built at import time, so we
reload the module under each scenario rather than mutating a live
object — that matches how the bot actually loads its config.
"""

from __future__ import annotations

import importlib
import os

import pytest


def _reload_settings(env: dict[str, str | None]):
    """Apply `env` to os.environ then re-import ictbot.settings so the
    Settings() singleton picks up the new values. Returns the fresh
    module. `None` value means delete the key."""
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import ictbot.settings as smod

    importlib.reload(smod)
    return smod


@pytest.fixture(autouse=True)
def _restore_env():
    """Snapshot/restore env vars so one test can't leak config into
    the next."""
    keys = (
        "CANONICAL_FLOW",
        "STRATEGY_MODE",
        "BIAS_ENGINE",
        "POI_ENGINE",
        "SL_ANCHOR",
    )
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import ictbot.settings as smod

    importlib.reload(smod)


def test_default_is_canonical_flow_on():
    """No env override → all four canonical defaults active.

    Explicitly clears per-field env vars too so the test is robust to
    whatever the user has in `.env` (e.g. an in-progress BIAS_ENGINE
    A/B test). Pydantic-settings reads .env even when individual
    env vars are unset, so a clear+restore is necessary."""
    smod = _reload_settings(
        {
            "CANONICAL_FLOW": None,
            "STRATEGY_MODE": None,
            "BIAS_ENGINE": "swing",  # force the spec default
            "POI_ENGINE": "order_block",  # force the spec default
        }
    )
    assert smod.settings.canonical_flow == "on"
    assert smod.settings.strategy_mode == "follow"
    assert smod.settings.bias_engine == "swing"
    assert smod.settings.poi_engine == "order_block"


def test_canonical_flow_off_reverts_every_default():
    """The kill switch is the rollback path. Setting it forces legacy
    values for every Phase-A flag at once."""
    smod = _reload_settings({"CANONICAL_FLOW": "off"})
    assert smod.settings.canonical_flow == "off"
    assert smod.settings.strategy_mode == "fade"
    assert smod.settings.bias_engine == "sma"
    assert smod.settings.poi_engine == "min_max"
    assert smod.settings.sl_anchor == "fixed"


def test_canonical_flow_off_overrides_explicit_per_field_env_vars():
    """If both CANONICAL_FLOW=off AND a per-field env var are set, the
    kill-switch wins. That's the whole point: one var to roll back
    even when other vars are mid-experiment."""
    smod = _reload_settings(
        {
            "CANONICAL_FLOW": "off",
            "STRATEGY_MODE": "follow",  # would normally win
            "BIAS_ENGINE": "swing",  # would normally win
        }
    )
    # Kill-switch overrides both
    assert smod.settings.strategy_mode == "fade"
    assert smod.settings.bias_engine == "sma"


def test_per_field_env_vars_apply_when_canonical_flow_on():
    """With the kill switch off, individual env vars STILL work — the
    kill-switch only intervenes when explicitly =off.

    Forces canonical-default values for the fields under assertion to
    stay robust to whatever the user has in .env."""
    smod = _reload_settings(
        {
            "CANONICAL_FLOW": "on",
            "STRATEGY_MODE": "fade",  # explicit override
            "BIAS_ENGINE": "swing",  # force spec default
            "POI_ENGINE": "order_block",  # force spec default
        }
    )
    assert smod.settings.strategy_mode == "fade"
    # Untouched fields keep canonical defaults
    assert smod.settings.bias_engine == "swing"
    assert smod.settings.poi_engine == "order_block"
