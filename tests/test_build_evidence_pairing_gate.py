# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P2 step-4 (P2_LEDGER_SEAM_FINDINGS_2026-05-29.md §wiring-plan).
#   The ledger's forecast↔settlement pairing must route through pair_residual so a
#   wrong-station / wrong-RV settlement is DROPPED (fail-closed), never emitted with a
#   collapsed lineage (D-J1). This is the pure-helper layer; a DB-integration test proves
#   build_evidence calls it end-to-end on the canonical schema.
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Relationship test for _pair_or_drop — wrong-station settlement returns None, not a collapsed-lineage evidence row.
# Reuse: Run after any change to _pair_or_drop, pair_residual, or ResidualKey construction.
"""Relationship test for the build_evidence pairing gate (_pair_or_drop).

Cross-module invariant: a forecast snapshot row + its TRUE settlement row form a keyed
residual; a forecast row + a wrong-station settlement (same city/date/metric — what the
legacy loose JOIN paired) returns None (dropped), it does not raise and does not emit.
"""

from __future__ import annotations

import json

# RED until _pair_or_drop is implemented in the script.
from scripts.build_ens_residual_evidence import _pair_or_drop
from src.contracts.residual_key import ResidualKey


def _forecast_row(**overrides) -> dict:
    base = dict(
        city="Chicago", temperature_metric="high", target_date="2026-05-20",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        issue_time="2026-05-18T00:00:00+00:00", source_cycle_time="2026-05-18T00:00:00+00:00",
        lead_hours=50.0, members_json=json.dumps([72.0, 74.0, 76.0]), members_unit="degF",
        forecast_window_start_utc="2026-05-20T05:00:00+00:00",
        forecast_window_end_utc="2026-05-21T05:00:00+00:00",
        settlement_station_id="KORD", settlement_unit="F", settlement_source_type="wu_icao",
    )
    base.update(overrides)
    return base


def _settlement_row(**overrides) -> dict:
    base = dict(
        city="Chicago", temperature_metric="high", target_date="2026-05-20",
        settlement_value=77.0,
        settlement_source="https://www.wunderground.com/history/daily/us/il/chicago/KORD",
        provenance_json=json.dumps({"data_version": "wu_icao_history_v1"}),
    )
    base.update(overrides)
    return base


def test_true_pair_returns_residual_key():
    rk = _pair_or_drop(_forecast_row(), _settlement_row(), claimed_unit="F")
    assert isinstance(rk, ResidualKey)
    assert rk.source_kind == "opendata_live"
    assert rk.product == "mx2t3"
    assert rk.target.settlement_station == "KORD"


def test_wrong_station_pair_returns_none_not_raise():
    """D-J1: forecast claims KORD, the city's settlement is KMDW — same city/date/metric so the
    legacy loose JOIN paired them. The gate DROPS it (None), fail-closed, no exception."""
    rk = _pair_or_drop(
        _forecast_row(settlement_station_id="KORD"),
        _settlement_row(
            settlement_source="https://www.wunderground.com/history/daily/us/il/chicago/KMDW"),
        claimed_unit="F",
    )
    assert rk is None


def test_wrong_date_pair_returns_none():
    rk = _pair_or_drop(
        _forecast_row(target_date="2026-05-20"),
        _settlement_row(target_date="2026-05-21"),
        claimed_unit="F",
    )
    assert rk is None


def test_incomplete_settlement_returns_none():
    """Settlement missing provenance data_version (authority) -> cannot define its RV -> drop."""
    rk = _pair_or_drop(
        _forecast_row(),
        _settlement_row(provenance_json=json.dumps({})),
        claimed_unit="F",
    )
    assert rk is None


def test_unknown_forecast_lineage_returns_none():
    """A forecast whose data_version has no recognized lineage prefix must drop, not crash."""
    rk = _pair_or_drop(
        _forecast_row(data_version="unknown_source_mx2t3_v1"),
        _settlement_row(),
        claimed_unit="F",
    )
    assert rk is None
