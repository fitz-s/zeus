# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md Phase B3 entry_readiness writer with cross-gate enforcement.
"""Pure writer that turns gate decisions into ``readiness_state`` rows.

DAEMON ACTIVATION: NOT YET WIRED. This module is importable but is not
imported from any daemon hot-path file (``src/main.py``,
``src/ingest_main.py``, ``src/engine/*``, ``src/execution/*``,
``src/state/db.py`` runtime callers, ``scripts/healthcheck.py``
``result["healthy"]`` predicate). Phase C will register a single import
site behind an operator-controlled feature flag. See
``docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md``.

The write contract is intentionally narrow: the caller hands in
``ForecastTargetScope`` plus the three already-evaluated gate decisions
(rollout, calibration transfer, plus the producer-readiness id this row
depends on), and the writer enforces the cross-gate invariants at write
time so that the ``readiness_state`` row cannot land in a configuration
that would let the live evaluator path size into orders without all
gates aligned.

Cross-gate invariants enforced at write time:
1. ``LIVE_ELIGIBLE`` requires all of: ``rollout_decision.may_submit_live_orders``
   AND ``calibration_decision.live_promotion_approved``
   AND ``promotion_evidence.calibration_promotion_approved``.
2. ``SHADOW_ONLY`` requires the rollout decision to be at minimum
   ``SHADOW_ONLY`` or higher, AND the calibration decision to be at
   least ``SHADOW_ONLY`` (i.e. the policy + source-id + version mapping
   succeeded). Mismatch → ``BLOCKED``.
3. ``BLOCKED`` is the safe fallthrough; any failure in 1 or 2 → BLOCKED
   with merged ``reason_codes`` from both gates.

**Auditing semantics**: ``readiness_state.status='LIVE_ELIGIBLE'`` for
``strategy_key='entry_forecast'`` rows is **necessary but not sufficient**
for live submission. ``read_executable_forecast`` further validates
producer-readiness alignment (``source_run_id``, ``expires_at``)
downstream of the readiness row. Operators inspecting ``readiness_state``
directly should treat ``LIVE_ELIGIBLE`` rows as "passed the writer's
3-input gate combinator at write time" — actual live submission requires
the read-side validation to also pass. The shadow function
:func:`src.data.entry_forecast_shadow.evaluate_entry_forecast_shadow`
runs the stricter producer-readiness alignment check; the writer's
``_decide_status_and_reasons`` is intentionally subsumption-not-equivalent
to the shadow function for performance reasons (writer runs per-candidate;
shadow runs as a separate verifier).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import EntryForecastConfig
from src.control.entry_forecast_rollout import (
    EntryForecastPromotionEvidence,
    EntryForecastRolloutDecision,
)
from src.data.calibration_transfer_policy import CalibrationTransferDecision
from src.data.forecast_target_contract import ForecastTargetScope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.readiness_repo import write_readiness_state
from src.types.metric_identity import MetricIdentity, source_family_from_data_version

ENTRY_FORECAST_STRATEGY_KEY = "entry_forecast"


@dataclass(frozen=True)
class EntryReadinessWriteResult:
    readiness_id: str
    status: str
    reason_codes: tuple[str, ...]
    expires_at: datetime | None


def _merge_reasons(*groups: tuple[str, ...]) -> tuple[str, ...]:
    seen: list[str] = []
    for group in groups:
        for reason in group:
            if reason not in seen:
                seen.append(reason)
    return tuple(seen)


def _decide_status_and_reasons(
    *,
    rollout_decision: EntryForecastRolloutDecision,
    calibration_decision: CalibrationTransferDecision,
    promotion_evidence: EntryForecastPromotionEvidence | None,
) -> tuple[str, tuple[str, ...]]:
    """Combine the three gate verdicts into a single readiness status."""

    rollout_reasons = tuple(rollout_decision.reason_codes)
    calibration_reasons = tuple(calibration_decision.reason_codes)

    # BLOCKED whenever any gate said BLOCKED.
    if rollout_decision.status == "BLOCKED" or calibration_decision.status == "BLOCKED":
        return "BLOCKED", _merge_reasons(rollout_reasons, calibration_reasons)

    # LIVE_ELIGIBLE requires all three approvals (gate + transfer +
    # operator-attested calibration promotion). Missing evidence ⇒ BLOCKED.
    if rollout_decision.may_submit_live_orders:
        if not calibration_decision.live_promotion_approved:
            return "BLOCKED", _merge_reasons(
                rollout_reasons,
                calibration_reasons,
                ("ENTRY_READINESS_LIVE_REQUIRES_CALIBRATION_APPROVAL",),
            )
        if promotion_evidence is None or not promotion_evidence.calibration_promotion_approved:
            return "BLOCKED", _merge_reasons(
                rollout_reasons,
                calibration_reasons,
                ("ENTRY_READINESS_LIVE_REQUIRES_PROMOTION_EVIDENCE",),
            )
        return "LIVE_ELIGIBLE", _merge_reasons(rollout_reasons, calibration_reasons)

    # CANARY treated as SHADOW for readiness purposes (canary is a
    # live-with-restricted-bankroll regime; the readiness row itself
    # does not unblock canary sizing — that gate lives elsewhere).
    return "SHADOW_ONLY", _merge_reasons(rollout_reasons, calibration_reasons)


def _provenance_payload(
    *,
    rollout_decision: EntryForecastRolloutDecision,
    calibration_decision: CalibrationTransferDecision,
    promotion_evidence: EntryForecastPromotionEvidence | None,
    config: EntryForecastConfig,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rollout_mode": config.rollout_mode.value,
        "rollout_status": rollout_decision.status,
        "rollout_reason_codes": list(rollout_decision.reason_codes),
        "calibration_status": calibration_decision.status,
        "calibration_policy_id": calibration_decision.policy_id,
        "calibration_data_version": calibration_decision.calibration_data_version,
        "calibration_live_promotion_approved": calibration_decision.live_promotion_approved,
        "calibration_reason_codes": list(calibration_decision.reason_codes),
    }
    if promotion_evidence is None:
        payload["promotion_evidence"] = None
    else:
        payload["promotion_evidence"] = {
            "operator_approval_id": promotion_evidence.operator_approval_id,
            "g1_evidence_id": promotion_evidence.g1_evidence_id,
            "calibration_promotion_approved": promotion_evidence.calibration_promotion_approved,
            "canary_success_evidence_id": promotion_evidence.canary_success_evidence_id,
        }
    return payload


def _metric_identity_for_scope(scope: ForecastTargetScope) -> MetricIdentity:
    source_family = source_family_from_data_version(scope.data_version)
    if source_family is None:
        raise ValueError(f"unknown forecast data_version for entry readiness: {scope.data_version!r}")
    return MetricIdentity.for_metric_with_source_family(scope.temperature_metric, source_family)


def _producer_matches_scope(
    row: dict[str, Any],
    *,
    scope: ForecastTargetScope,
    config: EntryForecastConfig,
    track: str,
) -> bool:
    return (
        row.get("scope_type") == "city_metric"
        and row.get("strategy_key") == PRODUCER_READINESS_STRATEGY_KEY
        and row.get("city_id") == scope.city_id
        and row.get("city_timezone") == scope.city_timezone
        and row.get("target_local_date") == scope.target_local_date.isoformat()
        and row.get("temperature_metric") == scope.temperature_metric
        and row.get("data_version") == scope.data_version
        and row.get("source_id") == config.source_id
        and row.get("track") == track
    )


def _producer_readiness_tables(conn) -> tuple[str, ...]:
    attached = {row[1] for row in conn.execute("PRAGMA database_list")}
    if "forecasts" in attached:
        try:
            row = conn.execute(
                "SELECT name FROM forecasts.sqlite_master WHERE type = 'table' AND name = 'readiness_state'"
            ).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        return ("forecasts.readiness_state",) if row is not None else ()
    if "world" in attached:
        try:
            row = conn.execute(
                "SELECT name FROM world.sqlite_master WHERE type = 'table' AND name = 'readiness_state'"
            ).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        return ("world.readiness_state",) if row is not None else ()
    return ("main.readiness_state",)


def _producer_row_for_scope(
    conn,
    *,
    scope: ForecastTargetScope,
    config: EntryForecastConfig,
    track: str,
    producer_readiness_id: str,
) -> dict[str, Any] | None:
    tables = _producer_readiness_tables(conn)
    for table in tables:
        row = conn.execute(
            f"""
            SELECT * FROM {table}
            WHERE readiness_id = ?
              AND strategy_key = ?
            LIMIT 1
            """,
            (producer_readiness_id, PRODUCER_READINESS_STRATEGY_KEY),
        ).fetchone()
        if row is not None:
            candidate = dict(row)
            if _producer_matches_scope(candidate, scope=scope, config=config, track=track):
                return candidate

    for table in tables:
        row = conn.execute(
            f"""
            SELECT * FROM {table}
            WHERE scope_type = 'city_metric'
              AND strategy_key = ?
              AND city_id = ?
              AND city_timezone = ?
              AND target_local_date = ?
              AND temperature_metric = ?
              AND data_version = ?
              AND source_id = ?
              AND track = ?
            ORDER BY computed_at DESC, recorded_at DESC
            LIMIT 1
            """,
            (
                PRODUCER_READINESS_STRATEGY_KEY,
                scope.city_id,
                scope.city_timezone,
                scope.target_local_date.isoformat(),
                scope.temperature_metric,
                scope.data_version,
                config.source_id,
                track,
            ),
        ).fetchone()
        if row is not None:
            return dict(row)
    return None


def write_entry_readiness(
    conn,
    *,
    scope: ForecastTargetScope,
    rollout_decision: EntryForecastRolloutDecision,
    calibration_decision: CalibrationTransferDecision,
    promotion_evidence: EntryForecastPromotionEvidence | None,
    config: EntryForecastConfig,
    market_family: str,
    condition_id: str,
    producer_readiness_id: str,
    computed_at: datetime,
    live_eligible_ttl: timedelta = timedelta(hours=3),
    readiness_id: str | None = None,
) -> EntryReadinessWriteResult:
    """Write a single ``strategy_key='entry_forecast'`` readiness row.

    Refuses to write ``LIVE_ELIGIBLE`` unless every gate is aligned.
    Always writes a row (BLOCKED / SHADOW_ONLY / LIVE_ELIGIBLE) so the
    reader has a deterministic blocker code rather than ambiguity from
    a missing row.
    """

    if computed_at.tzinfo is None or computed_at.utcoffset() is None:
        raise ValueError("computed_at must be timezone-aware")
    computed_utc = computed_at.astimezone(timezone.utc)

    status, reason_codes = _decide_status_and_reasons(
        rollout_decision=rollout_decision,
        calibration_decision=calibration_decision,
        promotion_evidence=promotion_evidence,
    )

    expires_at: datetime | None = None
    if status == "LIVE_ELIGIBLE":
        expires_at = computed_utc + live_eligible_ttl

    final_readiness_id = readiness_id or f"entry-readiness-{uuid.uuid4().hex[:12]}"

    track = config.high_track if scope.temperature_metric == "high" else config.low_track
    identity = _metric_identity_for_scope(scope)
    producer = _producer_row_for_scope(
        conn,
        scope=scope,
        config=config,
        track=track,
        producer_readiness_id=producer_readiness_id,
    )
    final_producer_readiness_id = producer_readiness_id
    source_run_id: str | None = None
    physical_quantity = identity.physical_quantity
    observation_field = identity.observation_field
    if producer is None:
        if status == "LIVE_ELIGIBLE":
            status = "BLOCKED"
            reason_codes = _merge_reasons(reason_codes, ("ENTRY_READINESS_PRODUCER_MISSING",))
            expires_at = None
    else:
        final_producer_readiness_id = str(producer["readiness_id"])
        physical_quantity = str(producer["physical_quantity"])
        observation_field = str(producer["observation_field"])
        source_run_id = str(producer["source_run_id"]) if producer.get("source_run_id") else None
        if status == "LIVE_ELIGIBLE" and producer.get("status") != "LIVE_ELIGIBLE":
            status = "BLOCKED"
            reason_codes = _merge_reasons(reason_codes, ("ENTRY_READINESS_PRODUCER_NOT_LIVE_ELIGIBLE",))
            expires_at = None

    write_readiness_state(
        conn,
        readiness_id=final_readiness_id,
        scope_type="city_metric",
        status=status,
        computed_at=computed_utc,
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity=physical_quantity,
        observation_field=observation_field,
        data_version=scope.data_version,
        source_id=config.source_id,
        track=track,
        source_run_id=source_run_id,
        market_family=market_family,
        condition_id=condition_id,
        token_ids_json=[],
        strategy_key=ENTRY_FORECAST_STRATEGY_KEY,
        reason_codes_json=list(reason_codes),
        expires_at=expires_at,
        dependency_json={"producer_readiness_id": final_producer_readiness_id},
        provenance_json=_provenance_payload(
            rollout_decision=rollout_decision,
            calibration_decision=calibration_decision,
            promotion_evidence=promotion_evidence,
            config=config,
        ),
    )

    return EntryReadinessWriteResult(
        readiness_id=final_readiness_id,
        status=status,
        reason_codes=reason_codes,
        expires_at=expires_at,
    )
