# Created: 2026-10-01
# Lifecycle: created=2026-10-01; last_reviewed=2026-10-01; last_reused=never
# Purpose: Pin the Open-Meteo per-job burn-rate alarm (k x own trailing median) and its
#          live_health forecast_pipeline signal. Observability only, never a fetch gate.
# Reuse: Run when changing the unit ledger or live_health forecast_pipeline surface.
# Authority basis: incident 2026-10-01 (NBM standard_meta_stamped at 30-57x for five hours).
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.control import live_health
from src.data.openmeteo_response_store import BURN_SPIKE_K, OpenMeteoResponseStore, burn_spikes

NOW = datetime(2026, 10, 1, 6, 30, tzinfo=timezone.utc)
JOB = "bayes_precision_fusion_ncep_nbm_conus_standard_meta_stamped"


def _ledger(path: Path, *, current_units: int) -> None:
    store = OpenMeteoResponseStore(path)
    for hours_ago in range(1, 7 * 24):
        at = (NOW - timedelta(hours=hours_ago)).timestamp()
        store._ledger_add(JOB, at, 20, 0, 0)
        store._ledger_add("steady_job", at, 50, 0, 0)
    store._ledger_add(JOB, NOW.timestamp(), current_units, 0, 0)
    store._ledger_add("steady_job", NOW.timestamp(), 60, 0, 0)
    store._db().close()


def test_alarm_fires_on_a_tenfold_job_only(tmp_path: Path) -> None:
    path = tmp_path / "openmeteo_response_store.db"
    _ledger(path, current_units=20 * 10 + 1)

    [spike] = burn_spikes(path, now=NOW.timestamp())
    assert spike["job"] == JOB and spike["trailing_median"] == 20
    assert spike["ratio"] > BURN_SPIKE_K


def test_normal_burn_is_quiet(tmp_path: Path) -> None:
    path = tmp_path / "openmeteo_response_store.db"
    _ledger(path, current_units=20 * 7)  # the largest normal wave in the 09-26..10-02 ledger

    assert burn_spikes(path, now=NOW.timestamp()) == []


def test_spike_degrades_forecast_pipeline_signal(tmp_path: Path) -> None:
    _ledger(tmp_path / "openmeteo_response_store.db", current_units=1019)

    surface = live_health._openmeteo_burn_spike_surface(tmp_path, NOW)

    assert surface["ok"] is False
    assert surface["issue"].startswith(f"OPENMETEO_BURN_SPIKE[{JOB}]")
    assert surface["spikes"][0]["units"] == 1019


def test_missing_ledger_is_not_a_spike(tmp_path: Path) -> None:
    assert live_health._openmeteo_burn_spike_surface(tmp_path, NOW) == {
        "ok": True, "issue": None, "evaluated": False,
    }
