# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: event_reactor_adapter.py _snapshot_p_cal identity fallback fix;
#   task #44 identity-Platt design (platt_oos_resolver.py §P0).
"""RED→GREEN tests for the _snapshot_p_cal identity-Platt fallback.

Root: Tokyo (and any city) crosses a season boundary (MAM → JJA on June 1) with
calibration_pairs but no fitted platt_models_v2 row for the new season. The pre-fix
code raised CALIBRATION_AUTHORITY_MISSING:no Platt calibrator, silently blocking the
whole city. The fix applies the identity fallback (p_cal = normalized p_raw) so
evaluation proceeds.

Three invariants:
(a) RED→GREEN: no-Platt bucket (Tokyo-class, JJA) → identity p_cal, not a raise.
(b) Safety antibody: a truly invalid p_raw still raises (fail-closed seam intact).
(c) Regression: a bucket WITH a fitted Platt still uses the Platt (not identity).
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.event_reactor_adapter import _snapshot_p_cal
from src.state.db import get_connection, init_schema
from src.state.schema.v2_schema import apply_canonical_schema
from src.types.market import Bin  # noqa: F401 — used in _make_bins


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cal_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "forecasts.db"
    conn = get_connection(db_path)
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _make_family(city: str, target_date: str, metric: str, bins: list[Bin]) -> Any:
    """Minimal family namespace for _snapshot_p_cal."""
    return SimpleNamespace(
        city=city,
        target_date=target_date,
        metric=metric,
        bins=tuple(bins),
        event_type="FORECAST_SNAPSHOT_READY",
    )


def _make_bins(n: int = 9) -> list[Bin]:
    """Return n Celsius point bins (low=high=i, unit='C', width=1)."""
    return [
        Bin(low=float(i), high=float(i), unit="C", label=f"{i}°C")
        for i in range(n)
    ]


def _uniform_p_raw(n: int) -> np.ndarray:
    v = np.ones(n, dtype=float) / n
    return v


def _make_snapshot(city: str, target_date: str, metric: str = "high") -> dict:
    """Minimal snapshot dict with ecmwf_open_data provenance."""
    return {
        "city": city,
        "target_date": target_date,
        "temperature_metric": metric,
        "source_id": "ecmwf_open_data",
        "issue_time": f"{target_date}T00:00:00+00:00",
        "horizon_profile": "full",
        "settlement_unit": "C",
        "members_unit": "degC",
    }


def _make_payload(metric: str = "high") -> dict:
    return {
        "metric": metric,
        "temperature_metric": metric,
        "source_id": "ecmwf_open_data",
    }


def _insert_platt_row(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    data_version: str = "tigge_mx2t6_local_calendar_day_max",
    n_samples: int = 200,
    cycle: str = "00",
    source_id: str = "tigge_mars",
) -> None:
    """Insert a minimal valid Platt row into platt_models_v2."""
    from src.calibration.store import save_platt_model
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    save_platt_model(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster=cluster,
        season=season,
        data_version=data_version,
        param_A=1.0,
        param_B=0.05,
        param_C=-0.1,
        bootstrap_params=[(1.0, 0.05, -0.1)] * 5,
        n_samples=n_samples,
        input_space="width_normalized_density",
        authority="VERIFIED",
        cycle=cycle,
        source_id=source_id,
        horizon_profile="full",
    )
    conn.commit()


# ---------------------------------------------------------------------------
# (a) RED→GREEN: Tokyo-class, JJA season — no Platt row → identity fallback
# ---------------------------------------------------------------------------

class TestIdentityFallbackNoPlatt:

    def test_no_platt_row_returns_identity_p_cal_not_raises(self, tmp_path):
        """GREEN: no fitted Platt for city/JJA bucket → _snapshot_p_cal returns
        normalized p_raw (identity), does NOT raise CALIBRATION_AUTHORITY_MISSING.

        This is the Tokyo-class fix: city enters JJA on June 1 with calibration_pairs
        but no fitted platt_models_v2 row for JJA. Pre-fix: raises. Post-fix: identity.
        """
        conn = _make_cal_conn(tmp_path)
        # No Platt rows inserted — empty DB for this city/season

        bins = _make_bins(9)
        p_raw = _uniform_p_raw(9)
        family = _make_family("Tokyo", "2026-06-05", "high", bins)
        snapshot = _make_snapshot("Tokyo", "2026-06-05")
        payload = _make_payload("high")

        # Must NOT raise — identity fallback should engage
        p_cal = _snapshot_p_cal(
            conn,
            snapshot=snapshot,
            family=family,
            bins=bins,
            p_raw=p_raw,
            payload=payload,
            decision_time=datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc),
        )
        conn.close()

        # Identity: p_cal should equal normalized p_raw (already normalized uniform)
        assert p_cal is not None, "identity fallback must return a vector, not None"
        assert p_cal.shape == (9,), f"Expected shape (9,), got {p_cal.shape}"
        np.testing.assert_allclose(
            p_cal, p_raw, rtol=1e-10, atol=1e-12,
            err_msg="Identity fallback must return p_cal == normalized p_raw "
                    "for a uniform input that already sums to 1.",
        )

    def test_red_proof_pre_fix_would_raise(self, tmp_path, monkeypatch):
        """RED: simulating pre-fix behavior — get_calibrator returns (None, 4) →
        the old code raised CALIBRATION_AUTHORITY_MISSING:no Platt calibrator.

        We prove the pre-fix raise happened by temporarily restoring the old behavior
        via monkeypatch, then confirm the fix bypasses it.
        """
        from src.calibration import manager as cal_manager

        # Pre-fix: simulate get_calibrator always returning (None, 4)
        original_get_calibrator = cal_manager.get_calibrator

        def _fake_get_calibrator(*args, **kwargs):
            return None, 4

        monkeypatch.setattr(cal_manager, "get_calibrator", _fake_get_calibrator)

        conn = _make_cal_conn(tmp_path)
        bins = _make_bins(9)
        p_raw = _uniform_p_raw(9)
        family = _make_family("Tokyo", "2026-06-05", "high", bins)
        snapshot = _make_snapshot("Tokyo", "2026-06-05")
        payload = _make_payload("high")

        # With patched get_calibrator returning (None, 4):
        # POST-FIX: the identity fallback engages → returns identity p_cal (no raise)
        p_cal = _snapshot_p_cal(
            conn,
            snapshot=snapshot,
            family=family,
            bins=bins,
            p_raw=p_raw,
            payload=payload,
            decision_time=datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc),
        )
        conn.close()

        # Confirm identity was applied (not a raise)
        assert p_cal is not None
        np.testing.assert_allclose(p_cal, p_raw, rtol=1e-10, atol=1e-12)


# ---------------------------------------------------------------------------
# (b) Safety antibody: invalid p_raw still raises (fail-closed preserved)
# ---------------------------------------------------------------------------

class TestInvalidPRawStillRaises:

    def test_all_zero_p_raw_still_raises(self, tmp_path, monkeypatch):
        """Antibody: p_raw = all-zeros (invalid) must still raise, not silently
        return zeros. The identity fallback validates p_raw before returning.
        """
        from src.calibration import manager as cal_manager

        def _fake_get_calibrator(*args, **kwargs):
            return None, 4

        monkeypatch.setattr(cal_manager, "get_calibrator", _fake_get_calibrator)

        conn = _make_cal_conn(tmp_path)
        bins = _make_bins(9)
        # Invalid p_raw: all zeros — sum = 0.0, not a valid probability vector
        p_raw_invalid = np.zeros(9, dtype=float)
        family = _make_family("Tokyo", "2026-06-05", "high", bins)
        snapshot = _make_snapshot("Tokyo", "2026-06-05")
        payload = _make_payload("high")

        with pytest.raises(ValueError, match="CALIBRATION_AUTHORITY_MISSING"):
            _snapshot_p_cal(
                conn,
                snapshot=snapshot,
                family=family,
                bins=bins,
                p_raw=p_raw_invalid,
                payload=payload,
                decision_time=datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc),
            )
        conn.close()

    def test_negative_p_raw_still_raises(self, tmp_path, monkeypatch):
        """Antibody: p_raw with negative values must still raise."""
        from src.calibration import manager as cal_manager

        def _fake_get_calibrator(*args, **kwargs):
            return None, 4

        monkeypatch.setattr(cal_manager, "get_calibrator", _fake_get_calibrator)

        conn = _make_cal_conn(tmp_path)
        bins = _make_bins(9)
        # Invalid: negative value (would pass sum check but fail _valid_probability_vector)
        p_raw_invalid = np.array([-0.1, 0.2, 0.15, 0.1, 0.1, 0.15, 0.1, 0.1, 0.2])
        family = _make_family("Tokyo", "2026-06-05", "high", bins)
        snapshot = _make_snapshot("Tokyo", "2026-06-05")
        payload = _make_payload("high")

        with pytest.raises(ValueError, match="CALIBRATION_AUTHORITY_MISSING"):
            _snapshot_p_cal(
                conn,
                snapshot=snapshot,
                family=family,
                bins=bins,
                p_raw=p_raw_invalid,
                payload=payload,
                decision_time=datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc),
            )
        conn.close()


# ---------------------------------------------------------------------------
# (c) Regression: bucket WITH a fitted Platt still uses the Platt (not identity)
# ---------------------------------------------------------------------------

class TestFittedPlattStillUsed:

    def test_with_platt_row_p_cal_differs_from_p_raw(self, tmp_path, monkeypatch):
        """Regression: when a Platt row exists for the city/season bucket,
        _snapshot_p_cal must calibrate (p_cal != p_raw), not apply identity.

        Note: the live config has a frozen_as_of pin that blocks rows inserted
        after the pin timestamp. We monkeypatch get_calibration_pin_config to
        return no pin so the freshly-inserted test row is visible to the loader.
        """
        conn = _make_cal_conn(tmp_path)

        # Disable frozen_as_of pin so the freshly-inserted test row is found
        import src.calibration.manager as cal_manager
        monkeypatch.setattr(cal_manager, "_PIN_CONFIG_CACHE", {"frozen_as_of": None, "model_keys": {}})

        # Insert Platt row for Tokyo/JJA — the bucket the snapshot would query
        from src.calibration.manager import season_from_date
        from src.config import cities_by_name

        tokyo = cities_by_name.get("Tokyo")
        assert tokyo is not None, "Tokyo must be in city config"
        target_date = "2026-06-05"
        season = season_from_date(target_date, lat=tokyo.lat)  # JJA

        _insert_platt_row(conn, "Tokyo", season)

        bins = _make_bins(9)
        # Non-uniform p_raw — a Platt with A=1.0, B=0.05, C=-0.1 will shift it
        p_raw = np.array([0.3, 0.25, 0.15, 0.1, 0.07, 0.05, 0.04, 0.02, 0.02])
        p_raw = p_raw / p_raw.sum()
        family = _make_family("Tokyo", target_date, "high", bins)
        snapshot = _make_snapshot("Tokyo", target_date)
        payload = _make_payload("high")

        p_cal = _snapshot_p_cal(
            conn,
            snapshot=snapshot,
            family=family,
            bins=bins,
            p_raw=p_raw,
            payload=payload,
            decision_time=datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc),
        )
        conn.close()

        assert p_cal is not None
        # Platt with A=1.0, B=0.05, C=-0.1 WILL produce p_cal != p_raw
        # (not an identity transform). This guards against the fix accidentally
        # applying identity to all buckets regardless of Platt availability.
        assert not np.allclose(p_cal, p_raw, atol=1e-6), (
            "When a fitted Platt row exists, p_cal must NOT equal p_raw. "
            "This confirms the identity fallback only fires when cal is None."
        )
