# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: operator directive "加数据" (add CWA/HKO station-forecast data to the
#   live forecast cycle); src/data/station_forecast_adapter.py single_runs persist contract;
#   config/station_forecast_sources.json adapter_kind dispatch seam.
"""Config-driven live station-forecast ingest dispatcher wiring.

The adapter already exposes per-source live ingest functions (``ingest_cwa_township_live``,
``ingest_hko_fnd_live``). The MISSING seam is the one the live download cycle calls: a
config-driven dispatcher that ingests every ENABLED station source, routes by ``adapter_kind``,
and is per-source fail-soft so one provider outage never starves the others.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data import station_forecast_adapter as adapter


def _write_config(root: Path, sources: dict) -> None:
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "station_forecast_sources.json").write_text(
        json.dumps({"sources": sources}), encoding="utf-8"
    )


_CWA_SPEC = {
    "enabled": True,
    "adapter_kind": "cwa_township_json",
    "city": "Taipei",
    "metric": "high",
    "location_name": "松山區",
    "element_name": "最高溫度",
    "endpoint": "https://example.invalid/cwa",
}
_HKO_SPEC = {
    "enabled": True,
    "adapter_kind": "hko_fnd_json",
    "city": "Hong Kong",
    "metric": "high",
    "endpoint": "https://example.invalid/hko",
}

_CONN = object()  # sentinel; ingest fns are monkeypatched so the conn is never touched


def test_dispatch_routes_only_enabled_sources_by_adapter_kind(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(
        adapter, "ingest_cwa_township_live",
        lambda conn, **kw: (calls.append("cwa"), 7)[1],
    )
    monkeypatch.setattr(
        adapter, "ingest_hko_fnd_live",
        lambda conn, **kw: (calls.append("hko"), 9)[1],
    )
    _write_config(tmp_path, {"cwa_township": dict(_CWA_SPEC), "hko_fnd": {**_HKO_SPEC, "enabled": False}})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result == {"cwa_township": 7}
    assert calls == ["cwa"]  # disabled hko never dispatched


def test_dispatch_passes_city_and_metric_from_spec(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        adapter, "ingest_cwa_township_live",
        lambda conn, **kw: (seen.update(kw), 3)[1],
    )
    _write_config(tmp_path, {"cwa_township": dict(_CWA_SPEC)})

    adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert seen.get("city") == "Taipei"
    assert seen.get("metric") == "high"


def test_dispatch_fail_soft_one_source_error_does_not_abort_others(monkeypatch, tmp_path):
    def _boom(conn, **kw):
        raise RuntimeError("CWA network down")

    monkeypatch.setattr(adapter, "ingest_cwa_township_live", _boom)
    monkeypatch.setattr(adapter, "ingest_hko_fnd_live", lambda conn, **kw: 9)
    _write_config(tmp_path, {"cwa_township": dict(_CWA_SPEC), "hko_fnd": dict(_HKO_SPEC)})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result.get("hko_fnd") == 9  # surviving source still ran
    assert "cwa_township" not in result  # errored source omitted, not crashing the cycle


def test_dispatch_unknown_adapter_kind_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "ingest_cwa_township_live", lambda conn, **kw: 1)
    monkeypatch.setattr(adapter, "ingest_hko_fnd_live", lambda conn, **kw: 1)
    _write_config(tmp_path, {"mystery": {"enabled": True, "adapter_kind": "nonexistent_kind", "city": "X", "metric": "high"}})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result == {}  # no dispatch, no crash


def test_dispatch_empty_or_all_disabled_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "ingest_cwa_township_live", lambda conn, **kw: 1)
    monkeypatch.setattr(adapter, "ingest_hko_fnd_live", lambda conn, **kw: 1)
    _write_config(tmp_path, {"cwa_township": {**_CWA_SPEC, "enabled": False}})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result == {}


# ---------------------------------------------------------------------------
# Download-cycle helper seam (replacement_forecast_production._ingest_station_forecasts_live):
# opens the forecast-DB conn from cfg, delegates to the dispatcher, fail-soft.
# ---------------------------------------------------------------------------


def test_cycle_helper_returns_none_when_forecast_db_missing():
    from src.data import replacement_forecast_production as prod

    assert prod._ingest_station_forecasts_live({"forecast_db": None}) is None


def test_cycle_helper_delegates_to_dispatcher_and_closes_conn(monkeypatch):
    from src.data import replacement_forecast_production as prod

    closed = {"v": False}

    class _FakeConn:
        isolation_level = ""

        def close(self):
            closed["v"] = True

    monkeypatch.setattr("src.state.db._connect", lambda p, **kw: _FakeConn())
    monkeypatch.setattr(
        "src.data.station_forecast_adapter.ingest_enabled_station_sources_live",
        lambda conn, **kw: {"cwa_township": 5, "hko_fnd": 9},
    )

    out = prod._ingest_station_forecasts_live({"forecast_db": "/tmp/does_not_matter.db"})

    assert out == {"cwa_township": 5, "hko_fnd": 9}
    assert closed["v"] is True


def test_cycle_helper_fail_soft_on_connect_error(monkeypatch):
    from src.data import replacement_forecast_production as prod

    def _boom(p, **kw):
        raise RuntimeError("db open failed")

    monkeypatch.setattr("src.state.db._connect", _boom)

    # Must swallow and return None, never propagate into the download cycle.
    assert prod._ingest_station_forecasts_live({"forecast_db": "/tmp/x.db"}) is None


# ---------------------------------------------------------------------------
# CWA key resolution tolerance: the secret file key was silently mis-cased once
# (CWA_API_KEY vs documented cwa_api_key) -> CWA went to a silent 0-row no-op.
# Resolver must accept either casing from the file so it never silently no-ops again.
# ---------------------------------------------------------------------------


def _write_secret(root: Path, blob: dict) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "cwa_secret.json").write_text(json.dumps(blob), encoding="utf-8")


def test_resolve_cwa_key_accepts_documented_lowercase_file_key(tmp_path):
    _write_secret(tmp_path, {"cwa_api_key": "FAKE-LOWER"})
    assert adapter.resolve_cwa_api_key(environ={}, root=tmp_path) == "FAKE-LOWER"


def test_resolve_cwa_key_accepts_uppercase_file_key(tmp_path):
    _write_secret(tmp_path, {"CWA_API_KEY": "FAKE-UPPER"})
    assert adapter.resolve_cwa_api_key(environ={}, root=tmp_path) == "FAKE-UPPER"


# ---------------------------------------------------------------------------
# Re-home guard (2026-07-20): the 2026-06-11 download-lane migration orphaned the station ingest
# call (it lived only in the descheduled forecast-live _replacement_forecast_download_cycle, so
# cwa_township/hko_fnd went dark 2026-07-17). It is now re-homed onto ingest_main's availability
# poll via the due-gated wrapper _ingest_station_forecasts_if_due. These guard both.
# ---------------------------------------------------------------------------


def test_due_gate_fetches_then_skips_within_interval_then_fetches_again(monkeypatch):
    from src.data import replacement_forecast_production as prod

    calls = {"n": 0}
    monkeypatch.setattr(prod, "_ingest_station_forecasts_live", lambda cfg: (calls.__setitem__("n", calls["n"] + 1), {"cwa_township": 7})[1])
    monkeypatch.setattr(prod, "_last_station_ingest_monotonic", None)

    first = prod._ingest_station_forecasts_if_due({})       # due (never run) -> fetch
    gated = prod._ingest_station_forecasts_if_due({})        # within interval -> skip
    # rewind the stored monotonic stamp past the interval to simulate elapsed time
    prod._last_station_ingest_monotonic -= (prod._STATION_INGEST_MIN_INTERVAL_S + 1)
    again = prod._ingest_station_forecasts_if_due({})        # interval elapsed -> fetch

    assert first == {"cwa_township": 7}
    assert gated is None
    assert again == {"cwa_township": 7}
    assert calls["n"] == 2  # provider hit exactly twice, never on the gated tick


def test_availability_poll_is_wired_to_station_ingest():
    """Regression guard: the live availability-poll lane must call the station ingest wrapper so the
    2026-07-17 orphaning cannot recur silently."""
    import inspect

    from src import ingest_main

    src = inspect.getsource(ingest_main._replacement_availability_poll_tick)
    assert "_ingest_station_forecasts_if_due" in src
