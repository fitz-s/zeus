"""Decision Chain: every cycle records what happened AND why things didn't happen.

Blueprint v2 §3: NoTradeCase is not optional. When Zeus doesn't trade, it must
record WHY with the same rigor as when it does trade.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

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
    rejection_reasons: list[str] = field(default_factory=list)
    best_edge: float = 0.0
    model_prob: float = 0.0
    market_price: float = 0.0
    decision_snapshot_id: str = ""
    selected_method: str = ""
    applied_validations: list[str] = field(default_factory=list)
    bin_labels: list[str] = field(default_factory=list)
    p_raw_vector: list[float] = field(default_factory=list)
    p_cal_vector: list[float] = field(default_factory=list)
    p_market_vector: list[float] = field(default_factory=list)
    alpha: float = 0.0
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
    edge_source: str = ""
    strategy: str = ""
    settled_at: str = ""
    contract_version: str = LEGACY_SETTLEMENT_CONTRACT_VERSION


def store_artifact(conn, artifact: CycleArtifact, env: str = "") -> None:
    """Store cycle artifact to decision_log table."""
    from src.config import settings
    now = datetime.now(timezone.utc).isoformat()
    env = env or settings.mode
    conn.execute("""
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        artifact.mode, artifact.started_at, artifact.completed_at,
        json.dumps(asdict(artifact), default=str), now, env,
    ))
    conn.commit()


def store_settlement_records(
    conn,
    records: list[SettlementRecord | dict],
    *,
    source: str = "harvester",
) -> None:
    """Store settlement outcomes in decision_log for downstream risk metrics."""
    if not records:
        return

    from src.config import settings
    now = datetime.now(timezone.utc).isoformat()
    env = settings.mode

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
    conn.commit()


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
    limit: int = 50,
    *,
    city: str | None = None,
    target_date: str | None = None,
) -> list[dict]:
    """Load recent settlement records written into legacy decision_log blobs only."""
    rows = conn.execute(
        """
        SELECT artifact_json, timestamp FROM decision_log
        WHERE mode = 'settlement'
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (max(limit * 10, 50),),
    ).fetchall()

    results: list[dict] = []
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
            if city is not None and normalized["city"] != city:
                continue
            if target_date is not None and normalized["target_date"] != target_date:
                continue
            results.append(normalized)
            if len(results) >= limit:
                return results[:limit]
    return results[:limit]


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
        "edge_source": str(record.get("edge_source") or ""),
        "strategy": str(record.get("strategy") or ""),
        "settled_at": str(record.get("settled_at") or artifact_timestamp or ""),
        "winning_bin": record.get("winning_bin"),
        "position_bin": record.get("position_bin") or record.get("range_label"),
        "won": _coerce_bool(record.get("won")),
        "exit_price": _coerce_float(record.get("exit_price")),
        "exit_reason": str(record.get("exit_reason") or ""),
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
        "learning_snapshot_ready": bool(normalized["decision_snapshot_id"]),
    })
    return normalized


def query_no_trade_cases(conn, city: str = None, hours: int = 24) -> list[dict]:
    """Query recent NoTradeCase entries for diagnostics."""
    cutoff = datetime.now(timezone.utc).isoformat()
    rows = conn.execute("""
        SELECT artifact_json FROM decision_log
        WHERE timestamp > datetime('now', ?)
        ORDER BY timestamp DESC LIMIT 100
    """, (f"-{hours} hours",)).fetchall()

    results = []
    for r in rows:
        artifact = json.loads(r["artifact_json"])
        for ntc in artifact.get("no_trade_cases", []):
            if city is None or ntc.get("city") == city:
                results.append(ntc)
    return results
