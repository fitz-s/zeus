# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md Phase B5 end-to-end relationship test for the entry-forecast chain.
"""End-to-end relationship test for the entry-forecast chain.

This is the Fitz-style relationship test the orphan chain has been
missing: it exercises the full producer-readiness → promotion-evidence
→ calibration-transfer → rollout-gate → entry-readiness writer →
``get_entry_readiness`` flow in one go, with an in-memory DB and the
real production modules. It does NOT touch any daemon hot-path file;
it composes the orphan modules directly.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from src.config import (
    EntryForecastCalibrationPolicyId,
    EntryForecastRolloutMode,
    entry_forecast_config,
)
from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.control.entry_forecast_promotion_evidence_io import (
    read_promotion_evidence,
    write_promotion_evidence,
)
from src.control.entry_forecast_rollout import (
    EntryForecastPromotionEvidence,
    evaluate_entry_forecast_rollout_gate,
)
from src.data.calibration_transfer_policy import evaluate_calibration_transfer_policy
from src.data.entry_readiness_writer import (
    ENTRY_FORECAST_STRATEGY_KEY,
    write_entry_readiness,
)
from src.data.forecast_target_contract import ForecastTargetScope
from src.data.live_entry_status import LiveEntryForecastStatus
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema
from src.state.readiness_repo import get_entry_readiness, write_readiness_state
from src.state.schema.v2_schema import apply_v2_schema

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _scope() -> ForecastTargetScope:
    return ForecastTargetScope(
        city_id="LONDON",
        city_name="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_cycle_time=_utc(2026, 5, 3, 0),
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        target_window_start_utc=_utc(2026, 5, 7, 23),
        target_window_end_utc=_utc(2026, 5, 8, 23),
        required_step_hours=(120, 126, 132),
        market_refs=("condition-123",),
    )


def _seed_producer_readiness(conn: sqlite3.Connection) -> str:
    """Seed a LIVE_ELIGIBLE producer-readiness row matching the scope."""

    readiness_id = "producer-readiness-london-2026-05-08-high"
    write_readiness_state(
        conn,
        readiness_id=readiness_id,
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=_utc(2026, 5, 3, 9),
        city_id="LONDON",
        city="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        source_run_id="source-run-1",
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        token_ids_json=[],
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        reason_codes_json=["PRODUCER_COVERAGE_READY"],
        expires_at=_utc(2026, 5, 3, 18),
        dependency_json={},
        provenance_json={"source_run_coverage_id": "coverage-1"},
    )
    return readiness_id


def _ready_status() -> LiveEntryForecastStatus:
    return LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=4,
        producer_readiness_count=4,
        producer_live_eligible_count=4,
    )


def test_full_chain_aligned_writes_live_eligible_entry_readiness(tmp_path: Path) -> None:
    """All gates aligned ⇒ writer lands LIVE_ELIGIBLE row that the
    reader can consume.

    Stages:
    1. Seed producer-readiness row (DB).
    2. Persist promotion-evidence atomically (disk).
    3. Re-read promotion-evidence from disk.
    4. Evaluate calibration-transfer policy (in-memory).
    5. Evaluate rollout gate (in-memory) using the loaded evidence.
    6. Hand all three verdicts to the entry-readiness writer.
    7. Read back via ``get_entry_readiness`` and confirm LIVE_ELIGIBLE.
    """

    conn = _conn()
    producer_readiness_id = _seed_producer_readiness(conn)

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-2026-05-03",
        g1_evidence_id="g1-2026-05-03",
        status_snapshot=_ready_status(),
        calibration_promotion_approved=True,
        canary_success_evidence_id="canary-success-1",
    )
    evidence_path = tmp_path / "promotion_evidence.json"
    write_promotion_evidence(evidence, path=evidence_path)
    loaded_evidence = read_promotion_evidence(path=evidence_path)
    assert loaded_evidence == evidence

    calibration = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id=cfg.source_id,
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        live_promotion_approved=True,
    )
    assert calibration.status == "LIVE_ELIGIBLE"

    rollout = evaluate_entry_forecast_rollout_gate(config=cfg, evidence=loaded_evidence)
    assert rollout.status == "LIVE_ELIGIBLE"
    assert rollout.may_submit_live_orders is True

    result = write_entry_readiness(
        conn,
        scope=_scope(),
        rollout_decision=rollout,
        calibration_decision=calibration,
        promotion_evidence=loaded_evidence,
        config=cfg,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        producer_readiness_id=producer_readiness_id,
        computed_at=_utc(2026, 5, 3, 12),
    )
    assert result.status == "LIVE_ELIGIBLE"

    row = get_entry_readiness(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        source_id=cfg.source_id,
        track="mx2t6_high_full_horizon",
        strategy_key=ENTRY_FORECAST_STRATEGY_KEY,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        now_utc=_utc(2026, 5, 3, 13),
    )
    assert row.get("status") == "LIVE_ELIGIBLE"
    dependency = json.loads(row["dependency_json"])
    assert dependency == {"producer_readiness_id": producer_readiness_id}


def test_chain_with_missing_evidence_blocks_live(tmp_path: Path) -> None:
    """Promotion-evidence file absent ⇒ rollout gate BLOCKED ⇒ writer lands BLOCKED."""

    conn = _conn()
    producer_readiness_id = _seed_producer_readiness(conn)

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    evidence_path = tmp_path / "absent.json"
    loaded_evidence = read_promotion_evidence(path=evidence_path)
    assert loaded_evidence is None

    calibration = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id=cfg.source_id,
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        live_promotion_approved=True,
    )

    rollout = evaluate_entry_forecast_rollout_gate(config=cfg, evidence=loaded_evidence)
    assert rollout.status == "BLOCKED"
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in rollout.reason_codes

    result = write_entry_readiness(
        conn,
        scope=_scope(),
        rollout_decision=rollout,
        calibration_decision=calibration,
        promotion_evidence=loaded_evidence,
        config=cfg,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        producer_readiness_id=producer_readiness_id,
        computed_at=_utc(2026, 5, 3, 12),
    )
    assert result.status == "BLOCKED"
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in result.reason_codes

    row = get_entry_readiness(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        source_id=cfg.source_id,
        track="mx2t6_high_full_horizon",
        strategy_key=ENTRY_FORECAST_STRATEGY_KEY,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        now_utc=_utc(2026, 5, 3, 13),
    )
    assert row.get("status") == "BLOCKED"


def test_chain_with_calibration_unapproved_lands_blocked() -> None:
    """Calibration unapproved ⇒ writer adds LIVE_REQUIRES_CALIBRATION_APPROVAL."""

    conn = _conn()
    producer_readiness_id = _seed_producer_readiness(conn)

    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-2026-05-03",
        g1_evidence_id="g1-2026-05-03",
        status_snapshot=_ready_status(),
        calibration_promotion_approved=True,
        canary_success_evidence_id="canary-success-1",
    )
    rollout = evaluate_entry_forecast_rollout_gate(config=cfg, evidence=evidence)
    assert rollout.status == "LIVE_ELIGIBLE"

    calibration = evaluate_calibration_transfer_policy(
        config=cfg,
        source_id=cfg.source_id,
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        live_promotion_approved=False,
    )
    assert calibration.status == "SHADOW_ONLY"

    result = write_entry_readiness(
        conn,
        scope=_scope(),
        rollout_decision=rollout,
        calibration_decision=calibration,
        promotion_evidence=evidence,
        config=cfg,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        producer_readiness_id=producer_readiness_id,
        computed_at=_utc(2026, 5, 3, 12),
    )
    assert result.status == "BLOCKED"
    assert "ENTRY_READINESS_LIVE_REQUIRES_CALIBRATION_APPROVAL" in result.reason_codes
