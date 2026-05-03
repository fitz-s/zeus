# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: RERUN_PLAN_v2.md §5 F1 (Platt loader frozen-as-of pin)
"""Tests for ``load_platt_model_v2(frozen_as_of=, model_key=)``.

The pin parameters are the F1 forward-fix from
``docs/operations/task_2026-05-03_ddd_implementation_plan/RERUN_PLAN_v2.md``.

Pre-fix: a future mass-refit produces new platt_models_v2 rows with
``recorded_at = CURRENT_TIMESTAMP`` and the loader picks the newest
``fitted_at`` row, taking over live serving silently.

Post-fix:
- ``frozen_as_of`` adds ``AND recorded_at <= ?`` so rows recorded after the
  blessed snapshot are excluded.
- ``model_key`` overrides all match filters (still gated by
  ``is_active=1 AND authority='VERIFIED'``) for explicit per-bucket pin.
- Both default to None → legacy behavior preserved.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.calibration.store import load_platt_model_v2, save_platt_model_v2
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test_platt_v2_frozen.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    init_schema(c)
    apply_v2_schema(c)
    yield c
    c.close()


def _save_one(
    conn,
    *,
    cluster: str = "NYC",
    fitted_at: str = "2026-04-01T00:00:00Z",
    recorded_at: str = "2026-04-01 00:00:00",
    A: float = 1.0,
    is_active: int = 1,
    authority: str = "VERIFIED",
):
    """Save one row, then patch fitted_at, recorded_at, is_active."""
    save_platt_model_v2(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster=cluster,
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        param_A=A,
        param_B=0.05,
        param_C=-0.5,
        bootstrap_params=[(A, 0.05, -0.5)],
        n_samples=1500,
        brier_insample=0.009,
        input_space="width_normalized_density",
        authority=authority,
    )
    conn.execute(
        """
        UPDATE platt_models_v2
        SET fitted_at = ?, recorded_at = ?, is_active = ?
        WHERE temperature_metric = 'high'
          AND cluster = ? AND season = 'DJF'
          AND data_version = ?
        """,
        (fitted_at, recorded_at, is_active, cluster, HIGH_LOCALDAY_MAX.data_version),
    )
    conn.commit()


def _read_model_key(conn, cluster: str) -> str:
    return conn.execute(
        """
        SELECT model_key FROM platt_models_v2
        WHERE temperature_metric = 'high' AND cluster = ? AND season = 'DJF'
        """,
        (cluster,),
    ).fetchone()["model_key"]


# ── frozen_as_of behavior (single-row scenarios) ─────────────────────────────


def test_frozen_as_of_admits_row_recorded_before_pin(conn):
    _save_one(conn, recorded_at="2026-04-01 00:00:00", A=1.0)
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        frozen_as_of="2026-04-15 00:00:00",
    )
    assert pinned is not None
    assert pinned["A"] == 1.0


def test_frozen_as_of_excludes_row_recorded_after_pin(conn):
    """The core F1 protection: a row recorded after the operator-blessed
    snapshot is hidden from live serving."""
    _save_one(conn, recorded_at="2026-05-02 00:00:00", A=99.0)
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        frozen_as_of="2026-04-15 00:00:00",
    )
    assert pinned is None, (
        "frozen_as_of='2026-04-15' must exclude the 2026-05-02 row "
        "(F1 forward-fix: future mass-refits cannot silently take over)"
    )


def test_frozen_as_of_unset_returns_row(conn):
    """No pin → legacy behavior: any is_active=VERIFIED row is eligible."""
    _save_one(conn, recorded_at="2026-05-02 00:00:00", A=99.0)
    legacy = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
    )
    assert legacy is not None
    assert legacy["A"] == 99.0


def test_frozen_as_of_with_data_version_none_path(conn):
    """frozen_as_of also works on the legacy path (data_version=None)."""
    _save_one(conn, recorded_at="2026-05-02 00:00:00", A=42.0)
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        # data_version omitted → legacy SELECT path
        frozen_as_of="2026-04-15 00:00:00",
    )
    assert pinned is None
    admitted = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        frozen_as_of="2026-12-31 23:59:59",
    )
    assert admitted is not None
    assert admitted["A"] == 42.0


# ── model_key pin behavior ───────────────────────────────────────────────────


def test_model_key_pin_matches_exact_row(conn):
    """model_key pin returns the exact row regardless of cluster/season filters."""
    _save_one(conn, cluster="NYC", A=1.0)
    nyc_key = _read_model_key(conn, "NYC")
    # Even with mismatched cluster/season args, model_key wins
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="DOES_NOT_EXIST",
        season="WRONG",
        data_version="anything",
        model_key=nyc_key,
    )
    assert pinned is not None
    assert pinned["A"] == 1.0


def test_model_key_pin_returns_none_for_unknown_key(conn):
    _save_one(conn, A=1.0)
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        model_key="nonexistent-key",
    )
    assert pinned is None


def test_model_key_pin_still_requires_authority_verified(conn):
    """model_key cannot rescue an UNVERIFIED row (fail-CLOSED)."""
    _save_one(conn, A=1.0, authority="UNVERIFIED")
    nyc_key = _read_model_key(conn, "NYC")
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        model_key=nyc_key,
    )
    assert pinned is None


def test_model_key_pin_still_requires_is_active(conn):
    """model_key cannot rescue an inactive row (fail-CLOSED)."""
    _save_one(conn, A=1.0, is_active=0)
    nyc_key = _read_model_key(conn, "NYC")
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        model_key=nyc_key,
    )
    assert pinned is None


def test_model_key_takes_precedence_over_frozen_as_of(conn):
    """model_key wins; frozen_as_of is bypassed when model_key is provided.

    Operator's blessed model_key is THE row to use, regardless of recorded_at.
    """
    _save_one(conn, recorded_at="2026-05-02 00:00:00", A=42.0)
    nyc_key = _read_model_key(conn, "NYC")
    # frozen_as_of would normally exclude the row (recorded_at > pin),
    # but model_key bypasses match filters
    pinned = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        frozen_as_of="2026-04-01 00:00:00",
        model_key=nyc_key,
    )
    assert pinned is not None
    assert pinned["A"] == 42.0


# ── manager-level config helper ──────────────────────────────────────────────


def test_get_calibration_pin_config_default_is_safe(monkeypatch, tmp_path):
    """Without a calibration.pin section, returns safe defaults."""
    from src.calibration import manager as mgr

    fake_root = tmp_path / "fake_root"
    (fake_root / "src" / "calibration").mkdir(parents=True)
    (fake_root / "config").mkdir()
    (fake_root / "config" / "settings.json").write_text(
        '{"calibration": {"method": "platt"}}'
    )

    mgr._PIN_CONFIG_CACHE = None
    monkeypatch.setattr(mgr, "__file__", str(fake_root / "src" / "calibration" / "manager.py"))

    pin = mgr.get_calibration_pin_config()
    assert pin == {"frozen_as_of": None, "model_keys": {}}
    mgr._PIN_CONFIG_CACHE = None


def test_get_calibration_pin_config_reads_frozen_and_model_keys(monkeypatch, tmp_path):
    """frozen_as_of + model_keys round-trip through settings.json."""
    from src.calibration import manager as mgr

    fake_root = tmp_path / "fake_root"
    (fake_root / "src" / "calibration").mkdir(parents=True)
    (fake_root / "config").mkdir()
    (fake_root / "config" / "settings.json").write_text(
        """
        {
          "calibration": {
            "method": "platt",
            "pin": {
              "frozen_as_of": "2026-05-03 12:00:00",
              "model_keys": {
                "high:NYC:DJF": "abc123",
                "low:NYC:DJF": "def456"
              }
            }
          }
        }
        """
    )

    mgr._PIN_CONFIG_CACHE = None
    monkeypatch.setattr(mgr, "__file__", str(fake_root / "src" / "calibration" / "manager.py"))

    pin = mgr.get_calibration_pin_config()
    assert pin["frozen_as_of"] == "2026-05-03 12:00:00"
    assert pin["model_keys"]["high:NYC:DJF"] == "abc123"
    assert pin["model_keys"]["low:NYC:DJF"] == "def456"
    mgr._PIN_CONFIG_CACHE = None


def test_resolve_pin_for_bucket_returns_per_bucket_keys(monkeypatch, tmp_path):
    """_resolve_pin_for_bucket finds the right model_key for the queried bucket."""
    from src.calibration import manager as mgr

    fake_root = tmp_path / "fake_root"
    (fake_root / "src" / "calibration").mkdir(parents=True)
    (fake_root / "config").mkdir()
    (fake_root / "config" / "settings.json").write_text(
        """
        {
          "calibration": {
            "pin": {
              "frozen_as_of": "2026-05-03 12:00:00",
              "model_keys": {
                "high:NYC:DJF": "k1",
                "low:LON:JJA": "k2"
              }
            }
          }
        }
        """
    )

    mgr._PIN_CONFIG_CACHE = None
    monkeypatch.setattr(mgr, "__file__", str(fake_root / "src" / "calibration" / "manager.py"))

    frz, key = mgr._resolve_pin_for_bucket("high", "NYC", "DJF")
    assert frz == "2026-05-03 12:00:00"
    assert key == "k1"

    frz2, key2 = mgr._resolve_pin_for_bucket("low", "LON", "JJA")
    assert key2 == "k2"

    # Bucket not in pin → frozen_as_of returned, model_key None
    frz3, key3 = mgr._resolve_pin_for_bucket("high", "TOKYO", "DJF")
    assert frz3 == "2026-05-03 12:00:00"
    assert key3 is None

    mgr._PIN_CONFIG_CACHE = None
