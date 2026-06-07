# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Lock simple-switch read surface for Open-Meteo ECMWF IFS 9km + AIFS sampled-2t integration.
# Reuse: Run before enabling replacement forecast shadow/veto flags in a live daemon config.
# Authority basis: Operator-directed replacement forecast worktree integration; read-only/reversible until promotion.
"""Replacement forecast live switch surface tests."""

from __future__ import annotations

import sqlite3

import pytest

from src.data.replacement_forecast_live_switch_surface import (
    CURRENT_DATA_FACT_FILE,
    CURRENT_SOURCE_FACT_FILE,
    PROHIBITED_SIMPLE_SWITCH_WRITES,
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    REQUIRED_TRADE_TABLES,
    REQUIRED_WORLD_TABLES,
    ReplacementForecastLiveSwitchInput,
    build_replacement_forecast_live_switch_input_from_current_state,
    build_replacement_forecast_live_switch_report,
    default_replacement_forecast_live_switch_inventory,
)
from src.data.replacement_forecast_runtime_policy import (
    SHADOW_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def _policy():
    return resolve_replacement_forecast_runtime_policy(
        {
            SHADOW_FLAG: True,
            VETO_FLAG: True,
            "openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled": False,
            "openmeteo_ecmwf_ifs9_aifs_soft_anchor_kelly_increase_enabled": False,
            "openmeteo_ecmwf_ifs9_aifs_soft_anchor_direction_flip_enabled": False,
        }
    )


def _request(**overrides) -> ReplacementForecastLiveSwitchInput:
    params = {
        "runtime_policy": _policy(),
        "available_files": tuple(REQUIRED_LIVE_READ_FILES),
        "forecast_tables": tuple(REQUIRED_FORECAST_TABLES),
        "world_tables": tuple(REQUIRED_WORLD_TABLES),
        "trade_tables": tuple(REQUIRED_TRADE_TABLES),
        "enabled_evidence_gates": tuple(REQUIRED_EVIDENCE_GATES),
        "proposed_write_tables": (),
        "source_fact_status": "CURRENT_FOR_LIVE",
        "data_fact_status": "CURRENT_FOR_LIVE",
    }
    params.update(overrides)
    return ReplacementForecastLiveSwitchInput(**params)


def test_live_switch_surface_ready_only_for_read_only_current_inventory() -> None:
    report = build_replacement_forecast_live_switch_report(_request())

    assert report.simple_switch_ready is True
    assert report.reversible is True
    assert report.live_trade_authority is False
    assert report.reason_codes == ("REPLACEMENT_SWITCH_READ_ONLY_REVERSIBLE_READY",)
    assert "forecast_posteriors" in report.readable_forecast_tables
    assert "executable_market_snapshots" in report.readable_trade_tables
    assert "settlement_outcomes" in report.prohibited_write_tables


def test_live_switch_blocks_stale_current_fact_surfaces() -> None:
    report = build_replacement_forecast_live_switch_report(
        _request(source_fact_status="STALE_FOR_LIVE", data_fact_status="STALE_FOR_LIVE")
    )

    assert report.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_SOURCE_FACTS_STALE" in report.reason_codes
    assert "REPLACEMENT_SWITCH_DATA_FACTS_STALE" in report.reason_codes


def test_live_switch_reports_missing_files_tables_and_evidence_gates() -> None:
    report = build_replacement_forecast_live_switch_report(
        _request(
            available_files=("config/settings.json",),
            forecast_tables=("ensemble_snapshots",),
            world_tables=(),
            trade_tables=(),
            enabled_evidence_gates=("runtime_policy_allows_shadow_or_veto",),
        )
    )

    assert report.status == "BLOCKED"
    assert "state/zeus-forecasts.db" in report.missing_files
    assert "forecast_posteriors" in report.missing_tables
    assert "executable_market_snapshots" in report.missing_tables
    assert "same_clob_market_snapshot_bound" in report.missing_evidence_gates


def test_live_switch_forbids_simple_switch_writes_to_truth_training_or_orders() -> None:
    report = build_replacement_forecast_live_switch_report(
        _request(proposed_write_tables=("forecast_posteriors", "settlement_outcomes", "venue_commands"))
    )

    assert report.status == "BLOCKED"
    assert report.reversible is False
    assert report.proposed_forbidden_writes == ("settlement_outcomes", "venue_commands")
    assert "REPLACEMENT_SWITCH_FORBIDDEN_WRITES_PROPOSED" in report.reason_codes


def test_live_switch_inventory_uses_full_replacement_identity_only() -> None:
    inventory = default_replacement_forecast_live_switch_inventory()

    assert inventory["prohibited_simple_switch_writes"] == tuple(PROHIBITED_SIMPLE_SWITCH_WRITES)
    assert inventory["current_source_fact_file"] == (CURRENT_SOURCE_FACT_FILE,)
    assert inventory["current_data_fact_file"] == (CURRENT_DATA_FACT_FILE,)
    with pytest.raises(ValueError, match="full replacement identity"):
        _request(available_files=("short_" + "h" + "3_alias",))


def _touch_required_files(root) -> None:
    for relative in REQUIRED_LIVE_READ_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.name.endswith(".db"):
            path.write_text("test\n", encoding="utf-8")
    for relative in (CURRENT_SOURCE_FACT_FILE, CURRENT_DATA_FACT_FILE):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Status: CURRENT_FOR_LIVE\n", encoding="utf-8")


def _create_tables(db_path, tables: tuple[str, ...]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for table in tables:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")


def test_live_switch_current_state_inventory_reads_real_files_tables_and_fact_status(tmp_path) -> None:
    _touch_required_files(tmp_path)
    _create_tables(tmp_path / "state/zeus-forecasts.db", tuple(REQUIRED_FORECAST_TABLES))
    _create_tables(tmp_path / "state/zeus-world.db", tuple(REQUIRED_WORLD_TABLES))
    _create_tables(tmp_path / "state/zeus_trades.db", tuple(REQUIRED_TRADE_TABLES))

    request = build_replacement_forecast_live_switch_input_from_current_state(
        tmp_path,
        runtime_policy=_policy(),
        enabled_evidence_gates=tuple(REQUIRED_EVIDENCE_GATES),
    )
    report = build_replacement_forecast_live_switch_report(request)

    assert request.available_files == tuple(REQUIRED_LIVE_READ_FILES)
    assert request.forecast_tables == tuple(REQUIRED_FORECAST_TABLES)
    assert request.world_tables == tuple(REQUIRED_WORLD_TABLES)
    assert request.trade_tables == tuple(REQUIRED_TRADE_TABLES)
    assert request.source_fact_status == "CURRENT_FOR_LIVE"
    assert request.data_fact_status == "CURRENT_FOR_LIVE"
    assert report.status == "SIMPLE_SWITCH_READY"


def test_live_switch_current_state_inventory_fails_closed_on_missing_state(tmp_path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/settings.json").write_text("{}\n", encoding="utf-8")

    request = build_replacement_forecast_live_switch_input_from_current_state(
        tmp_path,
        runtime_policy=_policy(),
    )
    report = build_replacement_forecast_live_switch_report(request)

    assert request.available_files == ("config/settings.json",)
    assert request.forecast_tables == ()
    assert request.source_fact_status == "STALE_FOR_LIVE"
    assert request.data_fact_status == "STALE_FOR_LIVE"
    assert report.status == "BLOCKED"
    assert "REPLACEMENT_SWITCH_SOURCE_FACTS_STALE" in report.reason_codes
    assert "REPLACEMENT_SWITCH_MISSING_READ_TABLES" in report.reason_codes
