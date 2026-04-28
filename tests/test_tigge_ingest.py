# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F3.yaml
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Lock R3 F3 TIGGE ingest stub gates and registry integration.
# Reuse: Run when changing TIGGE source gating, forecast-source registry entries, or ForecastIngestProtocol adapters.
"""R3 F3 tests for the dormant TIGGE ingest stub."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from src.config import City
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.forecast_source_registry import active_sources, get_source
from src.data.tigge_client import (
    ENV_FLAG_NAME,
    PAYLOAD_PATH_ENV,
    TIGGEIngest,
    TIGGEIngestFetchNotConfigured,
    TIGGEIngestNotEnabled,
)
from src.signal.ensemble_signal import EnsembleSignal


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="US-Northeast",
    settlement_unit="F",
    wu_station="KLGA",
)
TARGET_DATE = date(2026, 1, 15)
NYC_SEMANTICS = SettlementSemantics.default_wu_fahrenheit("KLGA")


def _write_tigge_decision(root: Path, *, payload_path: str | None = None) -> Path:
    path = (
        root
        / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence"
        / "tigge_ingest_decision_2026-04-27.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "operator-approved TIGGE ingest test artifact\n"
    if payload_path is not None:
        body += f"payload_path: {payload_path}\n"
    path.write_text(body)
    return path


def _times(target_date: date, timezone_name: str, total_hours: int = 24) -> list[str]:
    tz = ZoneInfo(timezone_name)
    start = datetime.combine(target_date, time.min, tzinfo=tz)
    return [
        (start + timedelta(hours=i)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for i in range(total_hours)
    ]


def test_tigge_construction_allowed_when_gate_closed(tmp_path):
    ingest = TIGGEIngest(root=tmp_path, environ={})

    health = ingest.health_check()

    assert health.source_id == "tigge"
    assert health.ok is False
    assert ENV_FLAG_NAME in health.message


def test_tigge_fetch_raises_when_gate_closed(tmp_path):
    called = False

    def fail_if_called(run_init_utc, lead_hours):  # pragma: no cover - proves no I/O
        nonlocal called
        called = True
        raise AssertionError("payload_fetcher must not be called while gate is closed")

    ingest = TIGGEIngest(root=tmp_path, environ={}, payload_fetcher=fail_if_called)

    with pytest.raises(TIGGEIngestNotEnabled) as excinfo:
        ingest.fetch(datetime(2026, 1, 1, tzinfo=timezone.utc), [0, 6, 12])

    msg = str(excinfo.value)
    assert "tigge_ingest_decision_*.md" in msg
    assert f"{ENV_FLAG_NAME}=1" in msg
    assert called is False


def test_tigge_fetch_succeeds_when_artifact_AND_env_flag_present(tmp_path):
    _write_tigge_decision(tmp_path)
    payload_calls: list[tuple[datetime, tuple[int, ...]]] = []

    def fake_payload(run_init_utc: datetime, lead_hours: tuple[int, ...]):
        payload_calls.append((run_init_utc, lead_hours))
        return {
            "model": "tigge",
            "run_init_utc": run_init_utc.isoformat(),
            "lead_hours": list(lead_hours),
            "ensemble_members": [{"member": 0, "values": [1.0, 2.0]}],
        }

    run_init = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ingest = TIGGEIngest(
        root=tmp_path,
        environ={ENV_FLAG_NAME: "1"},
        payload_fetcher=fake_payload,
    )

    bundle = ingest.fetch(run_init, [0, 6, 12])

    assert payload_calls == [(run_init, (0, 6, 12))]
    assert bundle.source_id == "tigge"
    assert bundle.authority_tier == "FORECAST"
    assert bundle.run_init_utc == run_init
    assert tuple(bundle.lead_hours) == (0, 6, 12)
    assert len(bundle.raw_payload_hash) == 64
    assert tuple(bundle.ensemble_members) == ({"member": 0, "values": [1.0, 2.0]},)


def test_tigge_open_gate_without_payload_configuration_fails_closed(tmp_path):
    _write_tigge_decision(tmp_path)
    ingest = TIGGEIngest(root=tmp_path, environ={ENV_FLAG_NAME: "1"})

    with pytest.raises(TIGGEIngestFetchNotConfigured, match=PAYLOAD_PATH_ENV):
        ingest.fetch(datetime(2026, 1, 1, tzinfo=timezone.utc), [0, 6])


def test_tigge_fetch_uses_operator_declared_payload_path_without_code_change(tmp_path):
    payload_path = tmp_path / "state/tigge/operator_payload.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(
        """
        {
          "source_id": "tigge",
          "times": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"],
          "ensemble_members": [
            {"member": 0, "values": [41.0, 42.0]},
            {"member": 1, "values": [40.5, 41.5]}
          ]
        }
        """
    )
    _write_tigge_decision(tmp_path, payload_path="state/tigge/operator_payload.json")
    run_init = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ingest = TIGGEIngest(root=tmp_path, environ={ENV_FLAG_NAME: "1"})

    bundle = ingest.fetch(run_init, [0, 1])

    assert bundle.source_id == "tigge"
    assert tuple(bundle.lead_hours) == (0, 1)
    assert tuple(bundle.ensemble_members) == (
        {"member": 0, "values": [41.0, 42.0]},
        {"member": 1, "values": [40.5, 41.5]},
    )


def test_tigge_fetch_uses_env_payload_path_as_switch_configuration(tmp_path):
    _write_tigge_decision(tmp_path)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"source_id":"tigge","ensemble_members":[[1.0,2.0]]}')
    ingest = TIGGEIngest(
        root=tmp_path,
        environ={ENV_FLAG_NAME: "1", PAYLOAD_PATH_ENV: str(payload_path)},
    )

    bundle = ingest.fetch(datetime(2026, 1, 1, tzinfo=timezone.utc), [0, 1])

    assert tuple(bundle.ensemble_members) == ([1.0, 2.0],)


def test_tigge_registered_in_source_registry():
    spec = get_source("tigge")

    assert spec.ingest_class is TIGGEIngest
    assert spec.tier == "experimental"
    assert spec.requires_operator_decision is True
    assert spec.requires_api_key is True
    assert spec.env_flag_name == ENV_FLAG_NAME
    assert spec.enabled_by_default is False


def test_ensemble_signal_does_not_consume_TIGGE_when_gated(tmp_path):
    closed_sources = {source.source_id for source in active_sources(root=tmp_path, environ={})}
    assert "tigge" not in closed_sources

    members = np.full((51, 24), 40.0, dtype=np.float64)
    times = _times(TARGET_DATE, NYC.timezone)
    baseline = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
    # Loading the TIGGE class/registry must not alter signal math or source
    # selection; signal consumes member arrays, not dormant source classes.
    assert TIGGEIngest.source_id == "tigge"
    after = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)

    np.testing.assert_array_equal(after.member_extrema, baseline.member_extrema)
    np.testing.assert_array_equal(after.member_maxes_settled, baseline.member_maxes_settled)
