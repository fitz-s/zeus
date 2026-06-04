# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: METRIC-CROSSING DEFECT fix — fetch_mainstream_point hardcoded
#   temperature_2m_max for ALL markets, grading LOW markets against the daily HIGH.
#   HIGH and LOW are physically different quantities (daily max vs daily min of the
#   ensemble temperature series); the mainstream gate must fetch the matching field.
#   RELATIONSHIP TEST: the metric flowing into the gate MUST select the Open-Meteo
#   daily field — LOW -> temperature_2m_min, HIGH -> temperature_2m_max — and the
#   extracted point MUST be the matching value, never the opposite extremum.
import src.data.mainstream_forecast_source as mfs

# Capture the REAL implementation at import time — BEFORE the conftest autouse
# `_forbid_live_mainstream` fixture replaces the module attribute. We test the real
# metric-selection logic against a patched HTTP seam (openmeteo_client.fetch), so the
# conftest's no-live-network intent is satisfied without dialing out.
_REAL_FETCH = mfs.fetch_mainstream_point


def _fake_resp():
    # Paris Jun5: HIGH 20, LOW 13 — the exact pair from the live Paris LOW receipt
    # that was graded 13°C-bin vs 20°C (high) mainstream_point.
    return {
        "daily": {
            "time": ["2026-06-04", "2026-06-05", "2026-06-06"],
            "temperature_2m_max": [19.0, 20.0, 21.0],
            "temperature_2m_min": [12.0, 13.0, 14.0],
        }
    }


def _patch_fetch(monkeypatch):
    captured = {}

    def _fake_fetch(url, params, endpoint_label=None):
        captured["params"] = params
        return _fake_resp()

    import src.data.openmeteo_client as oc
    monkeypatch.setattr(oc, "fetch", _fake_fetch)
    return captured


def test_low_market_fetches_daily_min_not_max(monkeypatch):
    captured = _patch_fetch(monkeypatch)
    snap = _REAL_FETCH("Paris", "2026-06-05", metric="low")
    assert snap is not None, "Paris is a known city; must return a snapshot"
    # The Open-Meteo daily variable requested MUST be the min for a LOW market.
    assert captured["params"]["daily"] == "temperature_2m_min", (
        f"LOW market must request temperature_2m_min, got {captured['params']['daily']}"
    )
    # And the extracted point MUST be the LOW (13.0), never the HIGH (20.0).
    assert snap["point"] == 13.0, f"LOW point must be 13.0 (the min), got {snap['point']}"


def test_high_market_fetches_daily_max(monkeypatch):
    captured = _patch_fetch(monkeypatch)
    snap = _REAL_FETCH("Paris", "2026-06-05", metric="high")
    assert snap is not None
    assert captured["params"]["daily"] == "temperature_2m_max"
    assert snap["point"] == 20.0, f"HIGH point must be 20.0 (the max), got {snap['point']}"


def test_metric_is_required_no_silent_high_default(monkeypatch):
    # The defect was a silent HIGH-only default. metric must be explicit so a LOW
    # market can NEVER be graded against the daily max by omission.
    _patch_fetch(monkeypatch)
    import pytest
    with pytest.raises(TypeError):
        _REAL_FETCH("Paris", "2026-06-05")  # no metric -> must fail loud
