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
LEGACY_SETTLEMENT_REQUIRED_FIELDS = (
    "trade_id",
    "city",
    "target_date",
    "range_label",
    "direction",
    "p_posterior",
    "outcome",
    "pnl",
    "settled_at",
)
LEGACY_CANONICAL_GAP_FIELDS = (
    "winning_bin",
    "position_bin",
    "won",
    "exit_price",
    "exit_reason",
)


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _coerce_float(value) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value) -> Optional[int]:
    if _is_missing(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value) -> Optional[bool]:
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


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
    fresh_prob: float
    fresh_edge: float
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
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("settlement", now, now, json.dumps(artifact, default=str), now, env),
    )
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



def query_legacy_settlement_records(
    conn,
    limit: int | None = 50,
    *,
    city: str | None = None,
    target_date: str | None = None,
    env: str | None = None,
    not_before: str | None = None,
) -> list[dict]:
    """Load recent settlement records written into legacy decision_log blobs only."""
    query_env = str(env or get_mode())
    sql = """
        SELECT artifact_json, timestamp, env FROM decision_log
        WHERE mode = 'settlement'
          AND env = ?
        ORDER BY timestamp DESC
        """
    params: list[object] = [query_env]
    if limit is not None:
        sql += "\n        LIMIT ?"
        params.append(max(limit * 10, 50))
    rows = conn.execute(sql, params).fetchall()

    results: list[dict] = []
    cutoff_dt = None
    if not_before:
        try:
            cutoff_dt = datetime.fromisoformat(str(not_before).replace("Z", "+00:00"))
        except ValueError:
            cutoff_dt = None
    for row in rows:
        try:
            artifact = json.loads(row["artifact_json"])
        except json.JSONDecodeError:
            continue

        artifact_source = str(artifact.get("summary", {}).get("source") or "")
        artifact_timestamp = str(row["timestamp"] or "")
        for record in artifact.get("settlements", []):
            normalized = _normalize_legacy_settlement_record(
                record,
                artifact_source=artifact_source,
                artifact_timestamp=artifact_timestamp,
            )
            if normalized is None:
                continue
            if cutoff_dt is not None:
                try:
                    settled_at = datetime.fromisoformat(str(normalized["settled_at"]).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if settled_at < cutoff_dt:
                    continue
            if city is not None and normalized["city"] != city:
                continue
            if target_date is not None and normalized["target_date"] != target_date:
                continue
            results.append(normalized)
            if limit is not None and len(results) >= limit:
                return results[:limit]
    return results[:limit] if limit is not None else results


def _normalize_legacy_settlement_record(
    record: dict,
    *,
    artifact_source: str = "",
    artifact_timestamp: str = "",
) -> Optional[dict]:
    if not isinstance(record, dict):
        return None

    normalized = {
        "trade_id": str(record.get("trade_id") or ""),
        "city": str(record.get("city") or ""),
        "target_date": str(record.get("target_date") or ""),
        "range_label": str(record.get("range_label") or ""),
        "direction": str(record.get("direction") or ""),
        "p_posterior": _coerce_float(record.get("p_posterior")),
        "outcome": _coerce_int(record.get("outcome")),
        "pnl": _coerce_float(record.get("pnl")),
        "decision_snapshot_id": str(record.get("decision_snapshot_id") or ""),
        "strategy_key": str(record.get("strategy_key") or ""),
        "edge_source": str(record.get("edge_source") or ""),
        "strategy": str(record.get("strategy") or ""),
        "settled_at": str(record.get("settled_at") or artifact_timestamp or ""),
        "winning_bin": record.get("winning_bin"),
        "position_bin": record.get("position_bin") or record.get("range_label"),
        "won": _coerce_bool(record.get("won")),
        "exit_price": _coerce_float(record.get("exit_price")),
        "exit_reason": str(record.get("exit_reason") or ""),
        "settlement_authority": "LEGACY_UNKNOWN",
        "settlement_truth_source": "decision_log",
        "settlement_market_slug": "",
        "settlement_temperature_metric": "",
        "settlement_source": "",
        "settlement_value": None,
        "source": "decision_log",
        "authority_level": "legacy_decision_log_fallback",
        "contract_version": str(record.get("contract_version") or LEGACY_SETTLEMENT_CONTRACT_VERSION),
        "producer_source": artifact_source or str(record.get("source") or ""),
    }
    missing_required = [
        field for field in LEGACY_SETTLEMENT_REQUIRED_FIELDS
        if _is_missing(normalized.get(field))
    ]
    if missing_required:
        return None

    contract_missing_fields = [
        field for field in LEGACY_CANONICAL_GAP_FIELDS
        if _is_missing(record.get(field))
    ]
    degraded_reasons = ["legacy_decision_log_fallback"]
    if not normalized["decision_snapshot_id"]:
        degraded_reasons.append("missing_decision_snapshot_id")
    normalized.update({
        "is_degraded": True,
        "degraded_reason": "; ".join(degraded_reasons),
        "contract_missing_fields": contract_missing_fields,
        "canonical_payload_complete": False,
        "learning_snapshot_ready": False,
        "metric_ready": False,
    })
    return normalized


def query_no_trade_cases(
    conn,
    city: str = None,
    hours: int = 24,
    *,
    not_before: str | None = None,
) -> list[dict]:
    """Query recent NoTradeCase entries for diagnostics."""
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


def load_entry_evidence(
    conn,
    runtime_trade_id: str,
):
    """Load the entry-time DecisionEvidence envelope from position_events.

    T4.2-Phase1 (D4 audit-only read side, pairs with T4.1b write side at
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

    T4.2-Phase1 callers treat None as "skip symmetry audit" — the
    asymmetry signal is only meaningful when both entry and exit evidence
    exist. Legacy positions and backfilled events have known-missing
    evidence by design, distinguishable via the reason sentinel when
    needed (T4.2-Phase2 exit gate will use the sentinel to separate
    ``missing-because-legacy`` from ``missing-because-bug``).
    """
    from src.contracts.decision_evidence import (
        DecisionEvidence,
        UnknownContractVersionError,
    )
    from src.state.db import query_position_events

    events = query_position_events(conn, runtime_trade_id, limit=50)
    for event in events:
        if event.get("event_type") != "ENTRY_ORDER_POSTED":
            continue
        details = event.get("details")
        if not isinstance(details, dict):
            continue
        envelope = details.get("decision_evidence_envelope")
        if not isinstance(envelope, str) or not envelope:
            # ENTRY_ORDER_POSTED without envelope = legacy-backfill or
            # pre-T4.1b; absence is informative (skip audit).
            return None
        try:
            return DecisionEvidence.from_json(envelope)
        except (ValueError, UnknownContractVersionError):
            return None
    return None
