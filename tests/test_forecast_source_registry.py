# Created: 2026-04-27
# Last reused/audited: 2026-04-29
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: Protect R3 F1 forecast-source registry gates and provenance stamping.
# Reuse: Run before forecast source, schema, ensemble fetch, or TIGGE gate changes.
# Authority basis: R3 F1 plus Phase 1D forecast source policy / Open-Meteo fallback gate.
"""R3 F1 forecast source registry and provenance antibodies."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from src.config import City
from src.contracts.settlement_semantics import SettlementSemantics
from src.data import ensemble_client, forecasts_append
from src.data.forecast_ingest_protocol import ForecastBundle
from src.data.forecast_source_registry import (
    ForecastSourceSpec,
    SourceNotEnabled,
    active_sources,
    calibration_source_id_for_lookup,
    gate_source,
    gate_source_role,
    stable_payload_hash,
)
from src.signal.ensemble_signal import EnsembleSignal
from src.state.db import init_schema


def _city() -> City:
    return City(
        name="Testopolis",
        lat=40.0,
        lon=-73.0,
        timezone="UTC",
        settlement_unit="F",
        cluster="test",
        wu_station="KAAA",
    )


def _memdb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_disabled_source_excluded_from_active_list() -> None:
    sources = {
        "enabled": ForecastSourceSpec(
            source_id="enabled",
            tier="primary",
            kind="forecast_table",
        ),
        "disabled": ForecastSourceSpec(
            source_id="disabled",
            tier="disabled",
            kind="forecast_table",
            enabled_by_default=False,
        ),
    }

    assert [source.source_id for source in active_sources(sources=sources)] == ["enabled"]


def test_operator_decision_gated_source_blocked_when_artifact_absent(tmp_path) -> None:
    with pytest.raises(SourceNotEnabled):
        gate_source(
            "tigge",
            environ={"ZEUS_TIGGE_INGEST_ENABLED": "1"},
            root=tmp_path,
        )


def test_operator_decision_gated_source_blocked_when_env_flag_unset(tmp_path) -> None:
    evidence = (
        tmp_path
        / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/"
        "tigge_ingest_decision_2026-04-27.md"
    )
    evidence.parent.mkdir(parents=True)
    evidence.write_text("operator approved fixture\n")

    with pytest.raises(SourceNotEnabled):
        gate_source("tigge", environ={}, root=tmp_path)


def test_openmeteo_live_ensemble_is_monitor_fallback_not_entry_primary() -> None:
    source = gate_source("openmeteo_ensemble_ecmwf_ifs025")

    assert source.tier != "primary"
    assert source.tier == "secondary"
    assert source.degradation_level == "DEGRADED_FORECAST_FALLBACK"
    assert "monitor_fallback" in source.allowed_roles
    assert "entry_primary" not in source.allowed_roles
    with pytest.raises(SourceNotEnabled, match="entry_primary"):
        gate_source_role(source, "entry_primary")


def test_gated_source_active_when_artifact_present_AND_env_flag_set(tmp_path) -> None:
    evidence = (
        tmp_path
        / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/"
        "tigge_ingest_decision_2026-04-27.md"
    )
    evidence.parent.mkdir(parents=True)
    evidence.write_text("operator approved fixture\n")

    source = gate_source(
        "tigge",
        environ={"ZEUS_TIGGE_INGEST_ENABLED": "1"},
        root=tmp_path,
    )

    assert source.source_id == "tigge"


def test_calibration_source_id_for_lookup_keeps_bucket_identity_explicit() -> None:
    assert calibration_source_id_for_lookup("tigge") == "tigge_mars"
    assert calibration_source_id_for_lookup("tigge_mars") == "tigge_mars"
    assert calibration_source_id_for_lookup("ecmwf_open_data") == "ecmwf_open_data"
    assert calibration_source_id_for_lookup("openmeteo_ensemble_ecmwf_ifs025") is None
    assert calibration_source_id_for_lookup("gfs025") is None
    assert calibration_source_id_for_lookup(None) is None


def test_tigge_gate_closed_does_not_call_fetch(monkeypatch) -> None:
    calls: list[str] = []

    def fake_get(*_args, **_kwargs):
        calls.append("http")
        raise AssertionError("network should not be called for gate-closed TIGGE")

    monkeypatch.setattr(ensemble_client.httpx, "get", fake_get)

    with pytest.raises(SourceNotEnabled):
        ensemble_client.fetch_ensemble(_city(), forecast_days=1, model="tigge")

    assert calls == []


def test_tigge_gate_open_routes_through_ingest_not_openmeteo(monkeypatch, tmp_path) -> None:
    from src.data import forecast_source_registry

    calls: list[str] = []

    def fake_get(*_args, **_kwargs):
        calls.append("http")
        raise AssertionError("TIGGE must use its registered ingest adapter, not Open-Meteo")

    payload = tmp_path / "state/tigge/operator_payload.json"
    payload.parent.mkdir(parents=True)
    payload.write_text(
        """
        {
          "source_id": "tigge",
          "times": ["2026-04-27T00:00:00Z", "2026-04-27T01:00:00Z"],
          "ensemble_members": [
            {"member": 0, "values": [70.0, 71.0]},
            {"member": 1, "values": [70.5, 71.5]}
          ]
        }
        """
    )
    evidence = (
        tmp_path
        / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/"
        "tigge_ingest_decision_2026-04-27.md"
    )
    evidence.parent.mkdir(parents=True)
    evidence.write_text("operator approved fixture\npayload_path: state/tigge/operator_payload.json\n")
    monkeypatch.setattr(forecast_source_registry, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("ZEUS_TIGGE_INGEST_ENABLED", "1")
    monkeypatch.setattr(ensemble_client.httpx, "get", fake_get)
    ensemble_client._clear_cache()

    result = ensemble_client.fetch_ensemble(_city(), forecast_days=1, model="tigge")

    assert calls == []
    assert result is not None
    assert result["source_id"] == "tigge"
    assert result["authority_tier"] == "FORECAST"
    assert result["n_members"] == 2
    assert result["members_hourly"].shape == (2, 2)


def test_tigge_gate_open_missing_payload_raises_typed_configuration_error(monkeypatch, tmp_path) -> None:
    from src.data import forecast_source_registry
    from src.data import tigge_client
    from src.data.tigge_client import TIGGEIngestFetchNotConfigured

    evidence = (
        tmp_path
        / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/"
        "tigge_ingest_decision_2026-04-27.md"
    )
    evidence.parent.mkdir(parents=True)
    evidence.write_text("operator approved fixture\n")
    monkeypatch.setattr(forecast_source_registry, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tigge_client, "_fetch_db_payload", lambda *args, **kwargs: None)
    monkeypatch.setenv("ZEUS_TIGGE_INGEST_ENABLED", "1")
    ensemble_client._clear_cache()

    with pytest.raises(TIGGEIngestFetchNotConfigured, match="payload"):
        ensemble_client.fetch_ensemble(_city(), forecast_days=1, model="tigge")


def test_ingest_protocol_requires_source_id_and_authority_tier() -> None:
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    digest = stable_payload_hash({"ok": True})

    bundle = ForecastBundle(
        source_id="ecmwf_previous_runs",
        run_init_utc=now,
        lead_hours=[24],
        captured_at=now,
        raw_payload_hash=digest,
        authority_tier="FORECAST",
    )
    assert bundle.source_id == "ecmwf_previous_runs"

    with pytest.raises(ValueError):
        ForecastBundle(
            source_id="",
            run_init_utc=now,
            lead_hours=[24],
            captured_at=now,
            raw_payload_hash=digest,
            authority_tier="FORECAST",
        )
    with pytest.raises(ValueError):
        ForecastBundle(
            source_id="ecmwf_previous_runs",
            run_init_utc=now,
            lead_hours=[24],
            captured_at=now,
            raw_payload_hash="not-a-sha",
            authority_tier="FORECAST",
        )
    with pytest.raises(ValueError):
        ForecastBundle(
            source_id="ecmwf_previous_runs",
            run_init_utc=now,
            lead_hours=[24],
            captured_at=now,
            raw_payload_hash=digest,
            authority_tier="SETTLEMENT",  # type: ignore[arg-type]
        )


def test_forecasts_append_persists_source_id_and_raw_payload_hash(monkeypatch) -> None:
    payload = {
        "hourly": {
            "time": ["2026-04-27T00:00", "2026-04-27T01:00"],
            "temperature_2m_previous_day1": [70.0, 75.0],
        }
    }
    expected_hash = stable_payload_hash(payload)

    class Config:
        model_retro_starts = {}

    monkeypatch.setattr(forecasts_append, "_get_exceptions_config", lambda: Config())
    monkeypatch.setattr(
        forecasts_append,
        "_fetch_previous_runs_chunk",
        lambda *_args, **_kwargs: payload,
    )

    conn = _memdb()
    try:
        stats = forecasts_append.append_forecasts_window(
            _city(),
            date(2026, 4, 27),
            date(2026, 4, 27),
            conn,
            rebuild_run_id="f1-test",
            models=("best_match",),
            leads=(1,),
            chunk_days=1,
            sleep_seconds=0,
        )
        row = conn.execute(
            """
            SELECT source, source_id, raw_payload_hash, captured_at, authority_tier
            FROM forecasts
            WHERE city = 'Testopolis'
            """
        ).fetchone()
    finally:
        conn.close()

    assert stats["fetched_rows"] == 1
    assert row["source"] == "openmeteo_previous_runs"
    assert row["source_id"] == "openmeteo_previous_runs"
    assert row["raw_payload_hash"] == expected_hash
    assert row["captured_at"]
    assert row["authority_tier"] == "FORECAST"


def test_ensemble_fetch_result_carries_registry_provenance(monkeypatch) -> None:
    city = _city()
    ensemble_client._clear_cache()
    monkeypatch.setattr(ensemble_client.quota_tracker, "can_call", lambda: True)
    monkeypatch.setattr(ensemble_client.quota_tracker, "record_call", lambda _label: None)

    hourly = {
        "time": ["2026-04-27T00:00", "2026-04-27T01:00"],
        "temperature_2m": [70.0, 71.0],
    }
    for idx in range(1, 51):
        hourly[f"temperature_2m_member{idx:02d}"] = [70.0 + idx / 100, 71.0 + idx / 100]
    payload = {"hourly": hourly}

    class Response:
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    monkeypatch.setattr(ensemble_client.httpx, "get", lambda *_args, **_kwargs: Response())

    result = ensemble_client.fetch_ensemble(
        city,
        forecast_days=1,
        model="gfs025",
        role="monitor_fallback",
    )

    assert result is not None
    assert result["source_id"] == "openmeteo_ensemble_gfs025"
    assert result["raw_payload_hash"] == stable_payload_hash(payload)
    assert result["authority_tier"] == "FORECAST"
    assert result["degradation_level"] == "DEGRADED_FORECAST_FALLBACK"
    assert result["forecast_source_role"] == "monitor_fallback"
    assert result["captured_at"]


def test_ensemble_signal_math_bit_identical_with_registry_metadata_only() -> None:
    city = _city()
    times = [
        (datetime(2026, 4, 27, tzinfo=timezone.utc) + timedelta(hours=h)).isoformat()
        for h in range(24)
    ]
    members = np.tile(np.arange(24, dtype=float), (51, 1))
    sem = SettlementSemantics.for_city(city)

    with_metadata = {
        "members_hourly": members,
        "times": times,
        "source_id": "openmeteo_ensemble_ecmwf_ifs025",
        "raw_payload_hash": stable_payload_hash({"fixture": True}),
        "authority_tier": "FORECAST",
    }
    signal_a = EnsembleSignal(
        with_metadata["members_hourly"],
        with_metadata["times"],
        city,
        date(2026, 4, 27),
        sem,
    )
    signal_b = EnsembleSignal(
        members.copy(),
        list(times),
        city,
        date(2026, 4, 27),
        sem,
    )

    np.testing.assert_array_equal(signal_a.member_extrema, signal_b.member_extrema)
