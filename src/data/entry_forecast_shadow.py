"""Shadow evaluation boundary for executable entry forecasts.

DAEMON ACTIVATION: NOT YET WIRED. This module is importable but is not
imported from any daemon hot-path file (``src/main.py``,
``src/ingest_main.py``, ``src/engine/*``, ``src/execution/*``,
``src/state/db.py`` runtime callers, ``scripts/healthcheck.py``
``result["healthy"]`` predicate). Phase C will register a single import
site behind an operator-controlled feature flag. See
``docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.config import EntryForecastConfig
from src.control.entry_forecast_rollout import EntryForecastRolloutDecision
from src.data.calibration_transfer_policy import evaluate_calibration_transfer_policy
from src.data.executable_forecast_reader import read_executable_forecast_snapshot
from src.data.forecast_fetch_plan import track_for_metric
from src.data.forecast_target_contract import ForecastTargetScope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY


@dataclass(frozen=True)
class EntryForecastShadowDecision:
    status: str
    reason_codes: tuple[str, ...]
    snapshot_id: int | None
    source_run_id: str | None
    producer_readiness_id: str | None
    calibration_data_version: str | None

    @property
    def live_eligible(self) -> bool:
        return self.status == "LIVE_ELIGIBLE"


def _parse_reasons(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ("READINESS_REASON_CODES_MALFORMED",)
    if not isinstance(parsed, list):
        return ("READINESS_REASON_CODES_MALFORMED",)
    return tuple(str(item) for item in parsed if str(item))


def _is_expired(value: object, *, now_utc: datetime) -> bool:
    if not isinstance(value, str) or not value:
        return True
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return True
    return parsed.astimezone(timezone.utc) <= now_utc.astimezone(timezone.utc)


def _latest_producer_readiness(
    conn: sqlite3.Connection,
    *,
    scope: ForecastTargetScope,
    config: EntryForecastConfig,
    track: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM readiness_state
        WHERE strategy_key = ?
          AND city_id = ?
          AND city_timezone = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND source_id = ?
          AND track = ?
          AND data_version = ?
        ORDER BY computed_at DESC, recorded_at DESC
        LIMIT 1
        """,
        (
            PRODUCER_READINESS_STRATEGY_KEY,
            scope.city_id,
            scope.city_timezone,
            scope.target_local_date.isoformat(),
            scope.temperature_metric,
            config.source_id,
            track,
            scope.data_version,
        ),
    ).fetchone()
    return dict(row) if row else None


def evaluate_entry_forecast_shadow(
    conn: sqlite3.Connection,
    *,
    scope: ForecastTargetScope,
    config: EntryForecastConfig,
    now_utc: datetime,
    live_calibration_promotion_approved: bool = False,
    rollout_decision: EntryForecastRolloutDecision | None = None,
) -> EntryForecastShadowDecision:
    """Evaluate the calibration + producer-readiness side of the live cutover.

    When ``rollout_decision`` is omitted (default), the function returns
    ``SHADOW_ONLY`` even on a clean producer + calibration alignment —
    the rollout gate must clear separately before live sizing is
    authorized. Pass an :class:`EntryForecastRolloutDecision` to let the
    function fold the rollout verdict into the final status; this is the
    path the entry-readiness writer composes (see
    :mod:`src.data.entry_readiness_writer`).
    """
    track = track_for_metric(config, scope.temperature_metric)
    snapshot_result = read_executable_forecast_snapshot(
        conn,
        scope=scope,
        source_id=config.source_id,
        source_transport=config.source_transport.value,
        now_utc=now_utc,
    )
    if not snapshot_result.ok or snapshot_result.snapshot is None:
        return EntryForecastShadowDecision(
            status="BLOCKED",
            reason_codes=(snapshot_result.reason_code,),
            snapshot_id=None,
            source_run_id=None,
            producer_readiness_id=None,
            calibration_data_version=None,
        )

    producer = _latest_producer_readiness(conn, scope=scope, config=config, track=track)
    if producer is None:
        return EntryForecastShadowDecision(
            status="BLOCKED",
            reason_codes=("PRODUCER_READINESS_MISSING",),
            snapshot_id=snapshot_result.snapshot.snapshot_id,
            source_run_id=snapshot_result.snapshot.source_run_id,
            producer_readiness_id=None,
            calibration_data_version=None,
        )
    if producer.get("source_run_id") != snapshot_result.snapshot.source_run_id:
        return EntryForecastShadowDecision(
            status="BLOCKED",
            reason_codes=("PRODUCER_SOURCE_RUN_MISMATCH",),
            snapshot_id=snapshot_result.snapshot.snapshot_id,
            source_run_id=snapshot_result.snapshot.source_run_id,
            producer_readiness_id=str(producer["readiness_id"]),
            calibration_data_version=None,
        )
    producer_reasons = _parse_reasons(producer.get("reason_codes_json"))
    if producer.get("status") != "LIVE_ELIGIBLE":
        return EntryForecastShadowDecision(
            status=str(producer.get("status") or "UNKNOWN_BLOCKED"),
            reason_codes=producer_reasons or ("PRODUCER_READINESS_NOT_LIVE_ELIGIBLE",),
            snapshot_id=snapshot_result.snapshot.snapshot_id,
            source_run_id=snapshot_result.snapshot.source_run_id,
            producer_readiness_id=str(producer["readiness_id"]),
            calibration_data_version=None,
        )
    if _is_expired(producer.get("expires_at"), now_utc=now_utc):
        return EntryForecastShadowDecision(
            status="BLOCKED",
            reason_codes=("PRODUCER_READINESS_EXPIRED",),
            snapshot_id=snapshot_result.snapshot.snapshot_id,
            source_run_id=snapshot_result.snapshot.source_run_id,
            producer_readiness_id=str(producer["readiness_id"]),
            calibration_data_version=None,
        )

    calibration = evaluate_calibration_transfer_policy(
        config=config,
        source_id=config.source_id,
        forecast_data_version=scope.data_version,
        live_promotion_approved=live_calibration_promotion_approved,
    )
    if calibration.status != "LIVE_ELIGIBLE":
        return EntryForecastShadowDecision(
            status="SHADOW_ONLY" if calibration.status == "SHADOW_ONLY" else calibration.status,
            reason_codes=calibration.reason_codes,
            snapshot_id=snapshot_result.snapshot.snapshot_id,
            source_run_id=snapshot_result.snapshot.source_run_id,
            producer_readiness_id=str(producer["readiness_id"]),
            calibration_data_version=calibration.calibration_data_version,
        )
    if rollout_decision is not None and rollout_decision.may_submit_live_orders:
        return EntryForecastShadowDecision(
            status="LIVE_ELIGIBLE",
            reason_codes=tuple(rollout_decision.reason_codes)
            or ("ENTRY_FORECAST_LIVE_APPROVED",),
            snapshot_id=snapshot_result.snapshot.snapshot_id,
            source_run_id=snapshot_result.snapshot.source_run_id,
            producer_readiness_id=str(producer["readiness_id"]),
            calibration_data_version=calibration.calibration_data_version,
        )
    return EntryForecastShadowDecision(
        status="SHADOW_ONLY",
        reason_codes=(
            (
                "ENTRY_FORECAST_ROLLOUT_BLOCKED"
                if config.rollout_mode.value == "blocked"
                else "ENTRY_FORECAST_ROLLOUT_GATE_REQUIRED"
            ),
        ),
        snapshot_id=snapshot_result.snapshot.snapshot_id,
        source_run_id=snapshot_result.snapshot.source_run_id,
        producer_readiness_id=str(producer["readiness_id"]),
        calibration_data_version=calibration.calibration_data_version,
    )
