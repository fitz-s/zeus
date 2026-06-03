# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: docs/operations/FORECAST_COLD_ROOT_UNIVERSAL_2026-06-02.md
#   grid→point representativeness offset loader + reactor hook.
#   Per-(city,season) offset applied to ENS member maxes BEFORE p_raw.
#   Flag-gated: edli_v1.edli_grid_representativeness_correction_enabled (default OFF).
"""Relationship tests for grid representativeness offset loader and reactor hook.

TDD order: RED (all fail when src/calibration/grid_representativeness.py absent
and the hook is not in event_reactor_adapter.py) → implement → GREEN.

T1: get_offset returns activated dict for activated (city, season); returns None for
    non-activated or absent entry.
T2: With flag ON, _maybe_apply_grid_representativeness_correction warms F-city members
    by offset_c*1.8 and sets payload['_edli_grid_corrected']=True.
T3: With flag ON, CONTROL city (activated=False) → members unchanged, applied=False.
T4: With flag OFF (default) → members unchanged, applied=False (shadow-safe).
T5: C-city: activated offset warms members by offset_c (no ×1.8).
"""

from __future__ import annotations

import types
import unittest.mock as mock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_city(name: str, lat: float = 37.7749, settlement_unit: str = "C"):
    """Minimal city object matching src.config.City duck-type used by the hook."""
    city = types.SimpleNamespace(name=name, lat=lat, settlement_unit=settlement_unit)
    return city


def _make_family(city_name: str, target_date: str = "2026-05-15", metric: str = "high"):
    return types.SimpleNamespace(city=city_name, target_date=target_date, metric=metric)


def _patched_settings(flag_on: bool):
    """Return a settings dict with the grid-correction flag at the requested value."""
    return {"edli_v1": {"edli_grid_representativeness_correction_enabled": flag_on}}


# ---------------------------------------------------------------------------
# T1: loader — get_offset correctness
# ---------------------------------------------------------------------------

class TestGetOffset:
    def test_activated_city_season_returns_dict(self):
        """San Francisco MAM is activated in the real offset table."""
        from src.calibration.grid_representativeness import get_offset

        result = get_offset("San Francisco", "MAM")
        assert result is not None
        assert result["activated"] is True
        assert isinstance(result["offset_c"], float)
        # SF MAM should be clearly negative (cold bias ~-3.3°C)
        assert result["offset_c"] < -1.0

    def test_non_activated_returns_none(self):
        """Seattle all seasons are activated=False in the real offset table."""
        from src.calibration.grid_representativeness import get_offset

        result = get_offset("Seattle", "MAM")
        # Must return None (or dict with activated=False) — either is FAIL-CLOSED.
        # The contract is: return None when not usable, dict when activated=True.
        assert result is None

    def test_absent_city_returns_none(self):
        from src.calibration.grid_representativeness import get_offset

        result = get_offset("NotARealCity", "MAM")
        assert result is None

    def test_absent_season_returns_none(self):
        from src.calibration.grid_representativeness import get_offset

        result = get_offset("San Francisco", "XXX")
        assert result is None


# ---------------------------------------------------------------------------
# T2: hook — F-city activation warms by offset_c * 1.8
# ---------------------------------------------------------------------------

class TestHookFCity:
    def test_f_city_activated_warms_members_and_sets_flag(self):
        """
        F-city (SF, settlement_unit='F') with activated entry and flag ON:
          corrected_mean ≈ raw_mean - offset_c * 1.8
        payload['_edli_grid_corrected'] = True
        """
        from src.engine.event_reactor_adapter import _maybe_apply_grid_representativeness_correction

        city = _make_city("San Francisco", lat=37.7749, settlement_unit="F")
        family = _make_family("San Francisco", target_date="2026-05-15")  # MAM
        raw = np.full(50, 70.0)  # members in °F
        payload: dict = {}
        snapshot: dict = {}

        with mock.patch(
            "src.engine.event_reactor_adapter.settings",
            _patched_settings(flag_on=True),
        ):
            corrected, applied = _maybe_apply_grid_representativeness_correction(
                raw, snapshot=snapshot, family=family, city=city, payload=payload
            )

        assert applied is True
        assert payload.get("_edli_grid_corrected") is True

        # Get the actual offset_c for SF MAM from the real table
        from src.calibration.grid_representativeness import get_offset
        entry = get_offset("San Francisco", "MAM")
        assert entry is not None
        offset_c = entry["offset_c"]
        expected_mean = 70.0 - offset_c * 1.8
        assert abs(float(corrected.mean()) - expected_mean) < 1e-9


# ---------------------------------------------------------------------------
# T3: hook — control city (not activated) → unchanged
# ---------------------------------------------------------------------------

class TestHookControlCity:
    def test_non_activated_city_unchanged_flag_on(self):
        """Seattle MAM is activated=False → members must be returned unchanged."""
        from src.engine.event_reactor_adapter import _maybe_apply_grid_representativeness_correction

        city = _make_city("Seattle", lat=47.6062, settlement_unit="F")
        family = _make_family("Seattle", target_date="2026-05-15")  # MAM
        raw = np.full(50, 60.0)
        payload: dict = {}
        snapshot: dict = {}

        with mock.patch(
            "src.engine.event_reactor_adapter.settings",
            _patched_settings(flag_on=True),
        ):
            corrected, applied = _maybe_apply_grid_representativeness_correction(
                raw, snapshot=snapshot, family=family, city=city, payload=payload
            )

        assert applied is False
        assert payload.get("_edli_grid_corrected") is not True
        np.testing.assert_array_equal(corrected, raw)


# ---------------------------------------------------------------------------
# T4: hook — flag OFF → byte-identical to input (shadow-safe)
# ---------------------------------------------------------------------------

class TestHookFlagOff:
    def test_flag_off_members_unchanged(self):
        """With flag OFF (default), even an activated city must return raw members."""
        from src.engine.event_reactor_adapter import _maybe_apply_grid_representativeness_correction

        city = _make_city("San Francisco", lat=37.7749, settlement_unit="F")
        family = _make_family("San Francisco", target_date="2026-05-15")  # MAM
        raw = np.full(50, 70.0)
        payload: dict = {}
        snapshot: dict = {}

        with mock.patch(
            "src.engine.event_reactor_adapter.settings",
            _patched_settings(flag_on=False),
        ):
            corrected, applied = _maybe_apply_grid_representativeness_correction(
                raw, snapshot=snapshot, family=family, city=city, payload=payload
            )

        assert applied is False
        assert payload.get("_edli_grid_corrected") is not True
        np.testing.assert_array_equal(corrected, raw)

    def test_default_absent_flag_treated_as_off(self):
        """If edli_grid_representativeness_correction_enabled absent → treated as OFF."""
        from src.engine.event_reactor_adapter import _maybe_apply_grid_representativeness_correction

        city = _make_city("San Francisco", lat=37.7749, settlement_unit="F")
        family = _make_family("San Francisco", target_date="2026-05-15")
        raw = np.full(50, 70.0)
        payload: dict = {}
        snapshot: dict = {}

        # settings without the flag key at all
        with mock.patch(
            "src.engine.event_reactor_adapter.settings",
            {"edli_v1": {}},
        ):
            corrected, applied = _maybe_apply_grid_representativeness_correction(
                raw, snapshot=snapshot, family=family, city=city, payload=payload
            )

        assert applied is False
        np.testing.assert_array_equal(corrected, raw)


# ---------------------------------------------------------------------------
# T5: hook — C-city: corrected by offset_c (no ×1.8)
# ---------------------------------------------------------------------------

class TestHookCCity:
    def test_c_city_activated_warms_by_offset_c(self):
        """
        Amsterdam MAM is activated=True in offset table (C-settled city).
        corrected_mean ≈ raw_mean - offset_c  (no ×1.8 factor)
        """
        from src.engine.event_reactor_adapter import _maybe_apply_grid_representativeness_correction
        from src.calibration.grid_representativeness import get_offset

        entry = get_offset("Amsterdam", "MAM")
        assert entry is not None, "Amsterdam MAM must be activated in real table"
        offset_c = entry["offset_c"]

        city = _make_city("Amsterdam", lat=52.3676, settlement_unit="C")
        family = _make_family("Amsterdam", target_date="2026-05-15")  # MAM
        raw = np.full(50, 20.0)  # members in °C
        payload: dict = {}
        snapshot: dict = {}

        with mock.patch(
            "src.engine.event_reactor_adapter.settings",
            _patched_settings(flag_on=True),
        ):
            corrected, applied = _maybe_apply_grid_representativeness_correction(
                raw, snapshot=snapshot, family=family, city=city, payload=payload
            )

        assert applied is True
        expected_mean = 20.0 - offset_c  # no ×1.8 for C-city
        assert abs(float(corrected.mean()) - expected_mean) < 1e-9
        # Must NOT have applied the F-unit scale
        assert abs(float(corrected.mean()) - (20.0 - offset_c * 1.8)) > 1e-6


# ---------------------------------------------------------------------------
# T6: codex P1 metric gate — LOW family must NOT receive the HIGH offset.
#     The offset table is fit for metric='high' ONLY (_meta.metric='high').
#     get_offset must fail closed (return None) for any non-high metric, and the
#     hook must pass family.metric through so a LOW family never gets a HIGH shift.
# ---------------------------------------------------------------------------

class TestMetricGate:
    def test_get_offset_low_metric_fails_closed(self):
        """get_offset(..., metric='low') must return None even for a city/season
        that IS activated for 'high'. The HIGH offset is the wrong physical
        quantity for a LOW member array."""
        from src.calibration.grid_representativeness import get_offset

        # Pick a city the table activates (any activated entry works for the
        # contract; the gate fires before the table lookup matters).
        assert get_offset("San Francisco", "MAM", metric="low") is None
        assert get_offset("Amsterdam", "MAM", metric="low") is None
        # Unknown / non-high metrics also fail closed.
        assert get_offset("San Francisco", "MAM", metric="LOW") is None
        assert get_offset("San Francisco", "MAM", metric="dewpoint") is None

    def test_get_offset_high_metric_is_the_gated_path(self):
        """Sanity: metric='high' is the only path that may return data; the gate
        itself does not block 'high'. (Whether a specific entry resolves depends
        on the table; here we only assert the gate is metric='high'-permissive
        by confirming a non-high call is None while a high call is not blocked
        BY THE METRIC GATE — we mock get_offset's table to isolate the gate.)"""
        import unittest.mock as _mock
        import src.calibration.grid_representativeness as gr

        fake_table = {
            "_meta": {"metric": "high"},
            "cities": {"TestCity": {"MAM": {"offset_c": -2.0, "activated": True}}},
        }
        with _mock.patch.object(gr, "_load_table", return_value=fake_table):
            # high → returns the entry (gate permits)
            hi = gr.get_offset("TestCity", "MAM", metric="high")
            assert hi is not None and hi["offset_c"] == -2.0
            # low → gated to None even though the entry exists & is activated
            lo = gr.get_offset("TestCity", "MAM", metric="low")
            assert lo is None

    def test_hook_low_family_does_not_apply_offset(self):
        """The reactor hook must pass family.metric to get_offset so a LOW family
        never receives the HIGH offset — members returned UNCHANGED, applied=False,
        and the _edli_grid_corrected flag NOT set."""
        from src.engine.event_reactor_adapter import _maybe_apply_grid_representativeness_correction
        import src.calibration.grid_representativeness as gr
        import unittest.mock as _mock

        # Table that WOULD activate this city for 'high'. If the hook failed to
        # pass metric, it would apply the HIGH offset to the LOW array (the bug).
        fake_table = {
            "_meta": {"metric": "high"},
            "cities": {"San Francisco": {"MAM": {"offset_c": -3.0, "activated": True}}},
        }
        city = _make_city("San Francisco", lat=37.7749, settlement_unit="F")
        family = _make_family("San Francisco", target_date="2026-05-15", metric="low")
        raw = np.full(50, 60.0)
        payload: dict = {}
        snapshot: dict = {}

        with _mock.patch.object(gr, "_load_table", return_value=fake_table), mock.patch(
            "src.engine.event_reactor_adapter.settings",
            _patched_settings(flag_on=True),
        ):
            corrected, applied = _maybe_apply_grid_representativeness_correction(
                raw, snapshot=snapshot, family=family, city=city, payload=payload
            )

        assert applied is False, "LOW family must NOT receive the HIGH grid offset"
        assert payload.get("_edli_grid_corrected") is not True
        np.testing.assert_array_equal(corrected, raw)
