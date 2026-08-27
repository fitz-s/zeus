"""Decision Chain: every cycle records what happened AND why things didn't happen.

Blueprint v2 §3: NoTradeCase is not optional. When Zeus doesn't trade, it must
record WHY with the same rigor as when it does trade.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.architecture.decorators import capability, protects
from src.config import get_mode
from src.contracts.semantic_types import Direction, RejectionStage, DirectionAlias

logger = logging.getLogger(__name__)


LEGACY_SETTLEMENT_CONTRACT_VERSION = "decision_log.settlement.v1"

# --------------------------------------------------------------------------
# Bounded-by-construction inline retention (2026-08-25, operator redirect:
# storage must be bounded by construction, not periodic cleanup -- see
# docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md item 13).
#
# Piggybacked in the SAME transaction as every decision_log INSERT (both
# store_artifact and store_settlement_records below): after inserting a row
# of a given mode, opportunistically deletes up to _INLINE_EXPIRE_LIMIT rows
# of that SAME mode older than that mode's retention window. No commit here
# -- matches the existing "caller owns the commit" contract, so this is part
# of whatever transaction the caller is already managing. Bounded by
# construction: fires once per insert, so as long as writes continue the
# backlog cannot grow unbounded; if writes ever stopped, no expiry would be
# needed either. scripts/migrations/202608_decision_log_retention.py (PR
# #510) remains available as the one-time backlog-drain tool for rows
# written before this inline mechanism existed; its companion launchd plist
# is optional in steady state.
#
# Per-mode windows carried forward from the PR #510 consumer-window audit:
# 7 days is safe for every consumer except the tier0 preregistered-study
# anchor (protected below, indefinitely) and full auction receipts, which
# settlement_skill_attribution's unbounded-in-principle backfill (verified
# 2026-08-25: currently zero at-risk rows, but a live invariant, not a
# structural guarantee) argues for the wider 30-day margin.
_MODE_RETENTION_DAYS: dict[str, int] = {
    "global_single_order_auction": 30,
    "global_single_order_auction_delta": 7,
    "global_single_order_auction_duplicate": 7,
    "global_single_order_auction_preflight": 7,
    "exit_monitor": 7,
}
_DEFAULT_MODE_RETENTION_DAYS = 30
_INLINE_EXPIRE_LIMIT = 50

# Tier0 preregistered selection-lift study anchor (PR #510): a
# global_single_order_auction row whose artifact_json.summary.
# selection_epoch_identity matches a tier0_candidate_set_provenance row is
# retained indefinitely. Exported so scripts/migrations/
# 202608_decision_log_retention.py imports this single definition rather
# than duplicating the SQL text.
TIER0_EXCEPT_CLAUSE = """
      AND NOT (
        mode = 'global_single_order_auction'
        AND EXISTS (
          SELECT 1 FROM tier0_candidate_set_provenance t
          WHERE t.selection_epoch_identity = json_extract(
            decision_log.artifact_json, '$.summary.selection_epoch_identity'
          )
        )
      )
"""


def _inline_expire_decision_log(conn, mode: str, *, exclude_id: "int | None" = None) -> None:
    """Opportunistically delete up to _INLINE_EXPIRE_LIMIT expired decision_log
    rows of the given mode. ``exclude_id`` is the row just inserted by the
    caller in this same call -- excluded so a legitimately old-timestamped
    write (a backfill/catch-up insert, or a row whose own timestamp happens
    to already be outside the window) is never deleted by the very insert
    that created it. Never raises -- a bug here must not block a legitimate
    decision_log write; failures are logged and swallowed so the caller's
    insert/commit proceeds unaffected.
    """
    try:
        keep_days = _MODE_RETENTION_DAYS.get(mode, _DEFAULT_MODE_RETENTION_DAYS)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=keep_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        except_clause = ""
        if mode == "global_single_order_auction":
            has_tier0 = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='tier0_candidate_set_provenance'"
            ).fetchone() is not None
            if has_tier0:
                except_clause = TIER0_EXCEPT_CLAUSE
        exclude_clause = "AND id != ?" if exclude_id is not None else ""
        params: tuple = (cutoff, mode)
        if exclude_id is not None:
            params += (exclude_id,)
        params += (_INLINE_EXPIRE_LIMIT,)
        # The canonical table is hundreds of GB. Ordering by id made SQLite
        # choose a full table scan while this caller already owned the write
        # transaction, starving exit/cancel journals. The timestamp predicate
        # and order are deliberately index-compatible and fail closed if the
        # canonical schema index is unavailable.
        conn.execute(
            f"""
            DELETE FROM decision_log WHERE id IN (
                SELECT id FROM decision_log INDEXED BY idx_decision_log_ts
                WHERE timestamp < ? AND mode = ?
                {exclude_clause}
                {except_clause}
                ORDER BY timestamp, id LIMIT ?
            )
            """,
            params,
        )
    except Exception:  # noqa: BLE001 - inline expiry must never block a real write
        logger.exception("_inline_expire_decision_log failed for mode=%s (write unaffected)", mode)


@dataclass
class NoTradeCase:
    """Records why a trade was NOT made. Blueprint v2 §3."""
    decision_id: str
    city: str
    target_date: str
    range_label: str
    direction: DirectionAlias
    rejection_stage: str
    strategy_key: str = ""
    strategy: str = ""
    edge_source: str = ""
    availability_status: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    best_edge: float = 0.0
    model_prob: float = 0.0
    market_price: float = 0.0
    decision_snapshot_id: str = ""
    selected_method: str = ""
    settlement_semantics_json: str = ""
    epistemic_context_json: str = ""
    edge_context_json: str = ""
    applied_validations: list[str] = field(default_factory=list)
    bin_labels: list[str] = field(default_factory=list)
    p_raw_vector: list[float] = field(default_factory=list)
    p_cal_vector: list[float] = field(default_factory=list)
    p_market_vector: list[float] = field(default_factory=list)
    alpha: float = 0.0
    market_hours_open: float | None = None
    agreement: str = ""
    timestamp: str = ""
    
    def __post_init__(self):
        """CRITICAL: Enforce Enum strictness via coercion."""
        if self.direction and not isinstance(self.direction, Direction):
            self.direction = Direction(self.direction)
        if self.rejection_stage and not isinstance(self.rejection_stage, RejectionStage):
            self.rejection_stage = RejectionStage(self.rejection_stage)


@dataclass
class MonitorResult:
    """Per-position per-cycle exit evaluation record."""
    position_id: str
    fresh_prob: float | None
    fresh_edge: float | None
    should_exit: bool
    exit_reason: str = ""
    neg_edge_count: int = 0


@dataclass
class ExitRecord:
    """Per-position durable exit stage record embedded in the cycle artifact."""

    trade_id: str
    exit_reason: str
    exit_price: float
    outcome: str
    timestamp: str = ""


@dataclass
class CycleArtifact:
    """One per cycle. Links all decisions. Blueprint v2 §3."""
    mode: str
    started_at: str
    completed_at: str = ""
    skipped_reason: str = ""
    trade_cases: list[dict] = field(default_factory=list)
    no_trade_cases: list[NoTradeCase] = field(default_factory=list)
    monitor_results: list[MonitorResult] = field(default_factory=list)
    exit_cases: list[ExitRecord] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def add_no_trade(self, ntc: NoTradeCase):
        self.no_trade_cases.append(ntc)

    def add_monitor_result(self, mr: MonitorResult):
        self.monitor_results.append(mr)

    def add_trade(self, trade_info: dict):
        self.trade_cases.append(trade_info)

    def add_exit(self, trade_id: str, exit_reason: str, exit_price: float, outcome: str, timestamp: str = ""):
        self.exit_cases.append(
            ExitRecord(
                trade_id=trade_id,
                exit_reason=exit_reason,
                exit_price=exit_price,
                outcome=outcome,
                timestamp=timestamp,
            )
        )


@dataclass
class SettlementRecord:
    """Decision-log record for a realized settlement outcome."""

    trade_id: str
    city: str
    target_date: str
    range_label: str
    direction: str
    p_posterior: float
    outcome: int
    pnl: float
    decision_snapshot_id: str = ""
    strategy_key: str = ""
    edge_source: str = ""
    strategy: str = ""
    settled_at: str = ""
    contract_version: str = LEGACY_SETTLEMENT_CONTRACT_VERSION


@capability("decision_artifact_write", lease=True)
@protects("INV-04", "INV-08")
def store_artifact(conn, artifact: CycleArtifact, env: str = "") -> "int | None":
    """Store cycle artifact to decision_log table.

    Returns the inserted row's decision_log.id (for DT#1 / INV-17 tracking),
    or None if the id cannot be determined.

    NOTE (DT#1): Does NOT commit internally. The caller owns the commit.
    When called via commit_then_export(), commit_then_export() issues conn.commit()
    after this returns. Standalone callers (e.g. scripts) must commit explicitly.
    """
    from src.config import get_mode as _get_mode
    now = datetime.now(timezone.utc).isoformat()
    env = _get_mode()
    cursor = conn.execute("""
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        artifact.mode, artifact.started_at, artifact.completed_at,
        json.dumps(asdict(artifact), default=str), now, env,
    ))
    _inline_expire_decision_log(conn, artifact.mode, exclude_id=cursor.lastrowid)
    return cursor.lastrowid


def store_settlement_records(
    conn,
    records: list[SettlementRecord | dict],
    *,
    source: str = "harvester",
) -> None:
    """Store settlement outcomes in decision_log for downstream risk metrics."""
    if not records:
        return

    from src.config import get_mode as _get_mode
    now = datetime.now(timezone.utc).isoformat()
    env = _get_mode()

    serialized_records: list[dict] = []
    for record in records:
        payload = asdict(record) if isinstance(record, SettlementRecord) else dict(record)
        payload.setdefault("contract_version", LEGACY_SETTLEMENT_CONTRACT_VERSION)
        serialized_records.append(payload)

    artifact = {
        "mode": "settlement",
        "started_at": now,
        "completed_at": now,
        "summary": {
            "count": len(records),
            "source": source,
        },
        "settlements": serialized_records,
    }
    settlement_cursor = conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("settlement", now, now, json.dumps(artifact, default=str), now, env),
    )
    _inline_expire_decision_log(conn, "settlement", exclude_id=settlement_cursor.lastrowid)
    # NOTE (DT#1): No internal commit. Caller owns the commit.
    # Standalone callers (harvester, tests) must conn.commit() after this returns.


def query_settlement_records(
    conn,
    limit: int = 50,
    *,
    city: str | None = None,
    target_date: str | None = None,
) -> list[dict]:
    """Load settlement records, preferring canonical stage events over legacy blobs."""
    from src.state.db import query_authoritative_settlement_rows

    return query_authoritative_settlement_rows(
        conn,
        limit=limit,
        city=city,
        target_date=target_date,
    )



def query_no_trade_cases(
    conn,
    city: str = None,
    hours: int = 24,
    *,
    not_before: str | None = None,
) -> list[dict]:
    """Query recent NoTradeCase entries for telemetry."""
    query_env = get_mode()
    if not_before:
        try:
            cutoff_dt = datetime.fromisoformat(str(not_before).replace("Z", "+00:00"))
        except ValueError:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    else:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    sql = """
        SELECT artifact_json, timestamp FROM decision_log
        WHERE env = ?
        ORDER BY timestamp DESC
    """
    params: list[object] = [query_env]
    if not_before is None:
        sql += "\n        LIMIT 200"
    rows = conn.execute(sql, params).fetchall()

    results = []
    for r in rows:
        try:
            recorded_at = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if recorded_at <= cutoff_dt:
            continue
        artifact = json.loads(r["artifact_json"])
        for ntc in artifact.get("no_trade_cases", []):
            if city is None or ntc.get("city") == city:
                results.append(ntc)
    return results


def query_learning_surface_summary(
    conn,
    *,
    hours: int = 24,
    settlement_limit: int = 50,
    execution_limit: int = 200,
    not_before: str | None = None,
) -> dict:
    from src.state.db import query_authoritative_settlement_rows, query_execution_event_summary

    settlement_query_limit = None if not_before is not None else settlement_limit
    execution_query_limit = None if not_before is not None else execution_limit
    settlements = query_authoritative_settlement_rows(
        conn,
        limit=settlement_query_limit,
        not_before=not_before,
    )
    no_trades = query_no_trade_cases(conn, hours=hours, not_before=not_before)
    execution_summary = query_execution_event_summary(
        conn,
        limit=execution_query_limit,
        not_before=not_before,
    )
    metric_ready_settlements = [row for row in settlements if row.get("metric_ready", False)]

    by_strategy: dict[str, dict] = {}
    for row in metric_ready_settlements:
        strategy = str(row.get("strategy_key") or row.get("strategy") or "unclassified")
        bucket = by_strategy.setdefault(
            strategy,
            {
                "settlement_count": 0,
                "settlement_pnl": 0.0,
                "settlement_accuracy": None,
                "settlement_wins": 0,
                "no_trade_count": 0,
                "no_trade_stage_counts": {},
                "entry_attempted": 0,
                "entry_filled": 0,
                "entry_rejected": 0,
            },
        )
        bucket["settlement_count"] += 1
        bucket["settlement_pnl"] += float(row.get("pnl", 0.0) or 0.0)
        if row.get("outcome") == 1:
            bucket["settlement_wins"] += 1

    for strategy, bucket in by_strategy.items():
        count = bucket["settlement_count"]
        bucket["settlement_pnl"] = round(bucket["settlement_pnl"], 2)
        bucket["settlement_accuracy"] = round(bucket["settlement_wins"] / count, 4) if count else None
        bucket.pop("settlement_wins", None)

    for strategy, execution_bucket in execution_summary.get("by_strategy", {}).items():
        bucket = by_strategy.setdefault(
            strategy,
            {
                "settlement_count": 0,
                "settlement_pnl": 0.0,
                "settlement_accuracy": None,
                "no_trade_count": 0,
                "no_trade_stage_counts": {},
                "entry_attempted": 0,
                "entry_filled": 0,
                "entry_rejected": 0,
            },
        )
        bucket["entry_attempted"] = execution_bucket.get("entry_attempted", 0)
        bucket["entry_filled"] = execution_bucket.get("entry_filled", 0)
        bucket["entry_rejected"] = execution_bucket.get("entry_rejected", 0)

    no_trade_stage_counts: dict[str, int] = {}
    availability_status_counts: dict[str, int] = {}
    for case in no_trades:
        stage = str(case.get("rejection_stage") or "UNKNOWN")
        no_trade_stage_counts[stage] = no_trade_stage_counts.get(stage, 0) + 1
        availability_status = str(case.get("availability_status") or "")
        if availability_status:
            availability_status_counts[availability_status] = availability_status_counts.get(availability_status, 0) + 1
        strategy = str(case.get("strategy_key") or case.get("strategy") or "")
        if not strategy and availability_status:
            strategy = "__availability_unattributed__"
        if strategy:
            bucket = by_strategy.setdefault(
                strategy,
                {
                    "settlement_count": 0,
                    "settlement_pnl": 0.0,
                    "settlement_accuracy": None,
                    "no_trade_count": 0,
                    "no_trade_stage_counts": {},
                    "entry_attempted": 0,
                    "entry_filled": 0,
                    "entry_rejected": 0,
                },
            )
            bucket["no_trade_count"] += 1
            stage_counts = bucket.setdefault("no_trade_stage_counts", {})
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

    degraded_settlements = sum(1 for row in settlements if row.get("is_degraded", False))
    return {
        "settlement_sample_size": len(metric_ready_settlements),
        "settlement_degraded_count": degraded_settlements,
        "no_trade_stage_counts": no_trade_stage_counts,
        "availability_status_counts": availability_status_counts,
        "execution": execution_summary,
        "by_strategy": by_strategy,
    }


def query_lifecycle_funnel_report(
    conn,
    *,
    hours: int = 24,
    not_before: str | None = None,
) -> dict:
    """Derived operator visibility for evaluated -> selected -> rejected/submitted -> filled -> learned."""
    stage_keys = ("evaluated", "selected", "rejected", "submitted", "filled", "learned")
    counts = {key: 0 for key in stage_keys}
    rejection_breakdown = {
        "pre_entry_no_trade": 0,
        "post_selection_entry_rejected": 0,
    }
    by_strategy: dict[str, dict] = {}
    source_errors: list[dict] = []

    def _bucket(strategy: str) -> dict:
        key = strategy or "unclassified"
        return by_strategy.setdefault(key, {stage: 0 for stage in stage_keys})

    def _strategy_from_case(case: dict) -> str:
        strategy = str(case.get("strategy_key") or case.get("strategy") or "").strip()
        if strategy:
            return strategy
        if str(case.get("availability_status") or "").strip():
            return "__availability_unattributed__"
        return "unclassified"

    try:
        no_trade_cases = query_no_trade_cases(conn, hours=hours, not_before=not_before)
    except Exception as exc:
        no_trade_cases = []
        source_errors.append({
            "source": "decision_log.no_trade_cases",
            "error_type": type(exc).__name__,
            "error": str(exc),
        })

    for case in no_trade_cases:
        strategy_bucket = _bucket(_strategy_from_case(case))
        counts["evaluated"] += 1
        counts["rejected"] += 1
        rejection_breakdown["pre_entry_no_trade"] += 1
        strategy_bucket["evaluated"] += 1
        strategy_bucket["rejected"] += 1

    event_mapping = {
        "POSITION_OPEN_INTENT": ("evaluated", "selected"),
        "ENTRY_ORDER_POSTED": ("submitted",),
        "ENTRY_ORDER_FILLED": ("filled",),
        "ENTRY_ORDER_REJECTED": ("rejected",),
        "SETTLED": ("learned",),
    }
    event_rows = []
    try:
        filters = []
        params: list[object] = []
        if not_before is not None:
            filters.append("occurred_at >= ?")
            params.append(not_before)
        else:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
            filters.append("occurred_at >= ?")
            params.append(cutoff_dt.isoformat())
        where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
        event_rows = conn.execute(
            f"""
            SELECT event_type, strategy_key
            FROM position_events
            {where_clause}
            ORDER BY occurred_at DESC, sequence_no DESC
            """,
            params,
        ).fetchall()
    except Exception as exc:
        source_errors.append({
            "source": "position_events",
            "error_type": type(exc).__name__,
            "error": str(exc),
        })

    for row in event_rows:
        try:
            event_type = str(row["event_type"])
            strategy = str(row["strategy_key"] or "unclassified")
        except (TypeError, KeyError, IndexError):
            event_type = str(row[0]) if row else ""
            strategy = str(row[1] or "unclassified") if row and len(row) > 1 else "unclassified"
        stages = event_mapping.get(event_type)
        if stages is None:
            continue
        strategy_bucket = _bucket(strategy)
        if event_type == "ENTRY_ORDER_REJECTED":
            rejection_breakdown["post_selection_entry_rejected"] += 1
        for stage in stages:
            counts[stage] += 1
            strategy_bucket[stage] += 1

    relationships = {
        "selected_lte_evaluated": counts["selected"] <= counts["evaluated"],
        "submitted_lte_selected": counts["submitted"] <= counts["selected"],
        "filled_lte_submitted": counts["filled"] <= counts["submitted"],
        "learned_lte_filled": counts["learned"] <= counts["filled"],
    }
    observed_total = sum(counts.values())
    if source_errors:
        status = "partial" if observed_total else "query_error"
    else:
        status = "observed" if observed_total else "certified_empty"

    return {
        "status": status,
        "authority": "derived_operator_visibility",
        "counts": counts,
        "rejection_breakdown": rejection_breakdown,
        "relationships": relationships,
        "by_strategy": by_strategy,
        "certification": {
            "empty_trade_tables_certified": observed_total == 0 and not source_errors,
            "canonical_event_source": "position_events",
            "no_trade_source": "decision_log.no_trade_cases",
        },
        "source_errors": source_errors,
    }


def load_entry_evidence(
    conn,
    runtime_trade_id: str,
):
    """Load the entry-time DecisionEvidence envelope from position_events.

    T4.2/Wave31 D4 gate read side (pairs with T4.1b write side at
    ``src/engine/lifecycle_events.py``): scans the canonical event stream
    for the earliest ``ENTRY_ORDER_POSTED`` event on ``runtime_trade_id``,
    extracts the ``decision_evidence_envelope`` key from its parsed
    ``details`` payload, and rehydrates via
    ``DecisionEvidence.from_json``.

    Returns None when any of:
    - No ``ENTRY_ORDER_POSTED`` event exists for this trade_id (position
      predates canonical emission; legacy pre-T4.1b entry).
    - The event exists but its payload lacks ``decision_evidence_envelope``
      (e.g. the ``src/execution/exit_lifecycle.py`` legacy-backfill path
      emits ``decision_evidence_reason`` sentinel instead).
    - Payload is malformed or the envelope fails from_json validation
      (``UnknownContractVersionError`` / ``ValueError``).

    Wave31 callers treat None as insufficient authority for statistical D4
    exits; cycle_runtime blocks those exits before intent construction.
    Legacy positions and backfilled events have known-missing evidence by
    design, distinguishable via the reason sentinel when post-hoc investigation
    needs to separate ``missing-because-legacy`` from ``missing-because-bug``.
    """
    from src.contracts.decision_evidence import (
        DecisionEvidence,
        UnknownContractVersionError,
    )
    from src.state.db import query_position_events

    events = query_position_events(conn, runtime_trade_id, limit=50)
    for event in events:
        event_type = event.get("event_type")
        if event_type not in {"ENTRY_ORDER_POSTED", "MANUAL_OVERRIDE_APPLIED"}:
            continue
        details = event.get("details")
        if not isinstance(details, dict):
            continue
        if event_type == "MANUAL_OVERRIDE_APPLIED" and details.get("repair_type") != "entry_decision_evidence_backfill":
            continue
        envelope = details.get("decision_evidence_envelope")
        if not isinstance(envelope, str) or not envelope:
            # ENTRY_ORDER_POSTED without envelope = legacy-backfill or
            # pre-T4.1b; absence is informative (skip audit).
            continue
        try:
            return DecisionEvidence.from_json(envelope)
        except (ValueError, UnknownContractVersionError):
            continue
    return None
