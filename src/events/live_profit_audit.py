"""EDLI live profit audit projection helpers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any

from src.decision_kernel.canonicalization import stable_hash
from src.state.schema.edli_live_profit_audit_schema import ensure_table

PROMOTION_ARTIFACT_SCHEMA = "edli_live_promotion_v1"


_AUDIT_FIELDS = (
    "event_id",
    "aggregate_id",
    "final_intent_id",
    "execution_command_id",
    "condition_id",
    "token_id",
    "direction",
    "side",
    "q_live",
    "q_lcb_5pct",
    "expected_cost_basis",
    "expected_fee",
    "expected_spread_cost",
    "visible_depth_fill_lcb",
    "order_policy",
    "native_token_side",
    "expected_edge",
    "kelly_size_usd",
    "live_cap_notional",
    "quote_seen_at",
    "quote_age_ms",
    "best_bid",
    "best_ask",
    "limit_price",
    "order_type",
    "time_in_force",
    "venue_order_id",
    "order_lifecycle_state",
    "avg_fill_price",
    "filled_size",
    "fees",
    "post_fill_mark",
    "settlement_outcome",
    "realized_edge",
    "edge_value_usd",
    "pnl_usd",
    "reject_reason",
    "expected_edge_source_certificate_hash",
    "cost_basis_source_certificate_hash",
    "fill_source_event_hash",
    "settlement_source_event_hash",
    "promotion_eligible",
)


@dataclass(frozen=True)
class LiveProfitPromotionSummary:
    canary_count: int
    confirmed_fill_count: int
    terminal_no_fill_count: int
    reconciled_no_order_count: int
    unresolved_unknowns: int
    realized_edge_bps: float
    median_realized_edge_bps_from_confirmed_fills: float
    aggregate_ids: tuple[str, ...] = ()
    audit_ids: tuple[str, ...] = ()
    execution_command_ids: tuple[str, ...] = ()
    execution_receipt_hashes: tuple[str, ...] = ()
    cap_transition_hashes: tuple[str, ...] = ()
    user_or_reconcile_event_hashes: tuple[str, ...] = ()
    source_summary_hash: str = ""
    db_user_version: int = 0

    def as_artifact(self) -> dict[str, Any]:
        return {
            "schema": PROMOTION_ARTIFACT_SCHEMA,
            "generated_at": _utc_now_iso(),
            "db_user_version": self.db_user_version,
            "canary_count": self.canary_count,
            "confirmed_fill_count": self.confirmed_fill_count,
            "terminal_no_fill_count": self.terminal_no_fill_count,
            "reconciled_no_order_count": self.reconciled_no_order_count,
            "unresolved_unknowns": self.unresolved_unknowns,
            "realized_edge_bps": self.realized_edge_bps,
            "median_realized_edge_bps_from_confirmed_fills": self.median_realized_edge_bps_from_confirmed_fills,
            "aggregate_ids": list(self.aggregate_ids),
            "audit_ids": list(self.audit_ids),
            "execution_command_ids": list(self.execution_command_ids),
            "execution_receipt_hashes": list(self.execution_receipt_hashes),
            "cap_transition_hashes": list(self.cap_transition_hashes),
            "user_or_reconcile_event_hashes": list(self.user_or_reconcile_event_hashes),
            "source_summary_hash": self.source_summary_hash,
        }


@dataclass(frozen=True)
class PromotionVerification:
    ok: bool
    reason: str = "OK"


# --- PR-2 (A): D2 ARM-gate artifact boot-binding (F1 Option C) ---------------
#
# The settlement-grounded ARM evidence artifact is PRODUCED by
# scripts/measure_arm_gate_settlement.py (PR-1) and ENFORCED here + at boot
# (src/main.py). Its existence + integrity is the antibody that makes
# "flip real_order_submit_enabled=true without proven after-cost edge" a BOOT
# FAILURE rather than a silent runtime path. Reusing the pure-verifier seam, the
# boot path and the producer can both be tested without git or a live daemon.
ARM_GATE_ARTIFACT_SCHEMA = "edli_arm_gate_v1"

# Required keys whose mere ABSENCE fails the artifact closed. Value-level law
# (SHA match, ev>0, coverage_licensed is True) is checked after presence.
_ARM_GATE_REQUIRED_FIELDS = (
    "schema",
    "commit_sha",
    "measurement_cmd_hash",
    "capital_weighted_ev",
    "production_n",
    "per_city_n",
    "ev_sigma",
    "date_coverage",
    "coverage_licensed",
)


@dataclass(frozen=True)
class ArmGateVerification:
    ok: bool
    reason: str = "OK"


def verify_edli_arm_gate_artifact(
    artifact: dict[str, Any] | None,
    *,
    head_sha: str | None,
) -> ArmGateVerification:
    """Pure verifier for the ARM-gate evidence artifact.

    Returns ``ok=False`` with a stable ``EDLI_LIVE_PROMOTION_ARTIFACT_*`` reason
    when the artifact is missing, malformed, stale (SHA != HEAD), shows a
    non-positive capital-weighted EV, or is not coverage-licensed. FAIL-CLOSED:
    any unexpected shape denies. The reason codes share the
    ``EDLI_LIVE_PROMOTION_ARTIFACT_`` prefix so the existing boot RuntimeError
    family (and its grep/alerting) covers them.
    """
    if artifact is None:
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_MISSING")
    if not isinstance(artifact, dict):
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_MALFORMED")

    for field in _ARM_GATE_REQUIRED_FIELDS:
        if field not in artifact:
            # production_n is the current operator-facing name. gate_pass_n is
            # accepted only as a deprecated alias for already-emitted artifacts.
            if field == "production_n" and "gate_pass_n" in artifact:
                continue
            return ArmGateVerification(
                False, f"EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_FIELD_MISSING:{field}"
            )

    if artifact.get("schema") != ARM_GATE_ARTIFACT_SCHEMA:
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_SCHEMA_INVALID")

    expected_sha = str(head_sha or "").strip()
    artifact_sha = str(artifact.get("commit_sha") or "").strip()
    if not expected_sha:
        # No HEAD SHA to compare against = cannot prove the measurement ran on
        # the code about to arm. Fail closed.
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_HEAD_SHA_UNAVAILABLE")
    if not artifact_sha:
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_COMMIT_SHA_MISSING")
    if artifact_sha != expected_sha:
        return ArmGateVerification(
            False,
            f"EDLI_LIVE_PROMOTION_ARM_GATE_COMMIT_SHA_MISMATCH:artifact={artifact_sha}:head={expected_sha}",
        )

    try:
        ev = float(artifact.get("capital_weighted_ev"))
    except (TypeError, ValueError):
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_EV_INVALID")
    if not (ev > 0.0):
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_EV_NOT_POSITIVE")

    # coverage_licensed MUST be the literal True (not a truthy 1/"true"/etc.):
    # a settlement-coverage license is a deliberate, unambiguous boolean.
    if artifact.get("coverage_licensed") is not True:
        return ArmGateVerification(False, "EDLI_LIVE_PROMOTION_ARM_GATE_COVERAGE_NOT_LICENSED")

    return ArmGateVerification(True)


class LiveProfitAuditLedger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        ensure_table(conn)

    def insert_record(self, **record: Any) -> str:
        now = _utc_now_iso()
        created_at = str(record.get("created_at") or now)
        normalized = {field: record.get(field) for field in _AUDIT_FIELDS}
        normalized["promotion_eligible"] = 1 if normalized.get("promotion_eligible") else 0
        missing = [
            field
            for field in ("event_id", "aggregate_id", "condition_id", "token_id", "order_lifecycle_state")
            if not normalized.get(field)
        ]
        if missing:
            raise ValueError("EDLI_LIVE_PROFIT_AUDIT_REQUIRED_FIELDS_MISSING:" + ",".join(missing))
        audit_id = str(
            record.get("audit_id")
            or _stable_audit_id(
                normalized["aggregate_id"],
                normalized.get("execution_command_id"),
                normalized["order_lifecycle_state"],
            )
        )
        self.conn.execute(
            """
            INSERT INTO edli_live_profit_audit (
                audit_id, event_id, aggregate_id, final_intent_id,
                execution_command_id, condition_id, token_id, direction, side,
                q_live, q_lcb_5pct, expected_cost_basis, expected_fee,
                expected_spread_cost, visible_depth_fill_lcb, order_policy,
                native_token_side, expected_edge,
                kelly_size_usd, live_cap_notional, quote_seen_at, quote_age_ms,
                best_bid, best_ask, limit_price, order_type, time_in_force,
                venue_order_id, order_lifecycle_state, avg_fill_price,
                filled_size, fees, post_fill_mark, settlement_outcome,
                realized_edge, edge_value_usd, pnl_usd, reject_reason,
                expected_edge_source_certificate_hash,
                cost_basis_source_certificate_hash, fill_source_event_hash,
                settlement_source_event_hash, promotion_eligible, created_at,
                schema_version
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(audit_id) DO UPDATE SET
                event_id = excluded.event_id,
                aggregate_id = excluded.aggregate_id,
                final_intent_id = excluded.final_intent_id,
                execution_command_id = excluded.execution_command_id,
                condition_id = excluded.condition_id,
                token_id = excluded.token_id,
                direction = excluded.direction,
                side = excluded.side,
                q_live = excluded.q_live,
                q_lcb_5pct = excluded.q_lcb_5pct,
                expected_cost_basis = excluded.expected_cost_basis,
                expected_fee = excluded.expected_fee,
                expected_spread_cost = excluded.expected_spread_cost,
                visible_depth_fill_lcb = excluded.visible_depth_fill_lcb,
                order_policy = excluded.order_policy,
                native_token_side = excluded.native_token_side,
                expected_edge = excluded.expected_edge,
                kelly_size_usd = excluded.kelly_size_usd,
                live_cap_notional = excluded.live_cap_notional,
                quote_seen_at = excluded.quote_seen_at,
                quote_age_ms = excluded.quote_age_ms,
                best_bid = excluded.best_bid,
                best_ask = excluded.best_ask,
                limit_price = excluded.limit_price,
                order_type = excluded.order_type,
                time_in_force = excluded.time_in_force,
                venue_order_id = excluded.venue_order_id,
                order_lifecycle_state = excluded.order_lifecycle_state,
                avg_fill_price = excluded.avg_fill_price,
                filled_size = excluded.filled_size,
                fees = excluded.fees,
                post_fill_mark = excluded.post_fill_mark,
                settlement_outcome = excluded.settlement_outcome,
                realized_edge = excluded.realized_edge,
                edge_value_usd = excluded.edge_value_usd,
                pnl_usd = excluded.pnl_usd,
                reject_reason = excluded.reject_reason,
                expected_edge_source_certificate_hash = excluded.expected_edge_source_certificate_hash,
                cost_basis_source_certificate_hash = excluded.cost_basis_source_certificate_hash,
                fill_source_event_hash = excluded.fill_source_event_hash,
                settlement_source_event_hash = excluded.settlement_source_event_hash,
                promotion_eligible = excluded.promotion_eligible,
                created_at = excluded.created_at,
                schema_version = excluded.schema_version
            """,
            (
                audit_id,
                *[normalized[field] for field in _AUDIT_FIELDS],
                created_at,
                int(record.get("schema_version") or 1),
            ),
        )
        return audit_id

    def promotion_summary(self) -> LiveProfitPromotionSummary:
        return promotion_summary(self.conn)


def promotion_summary(conn: sqlite3.Connection) -> LiveProfitPromotionSummary:
    return _promotion_summary_from_rows(conn, _canonical_promotion_rows(conn))


def verify_edli_live_promotion_artifact(
    conn: sqlite3.Connection,
    artifact: dict[str, Any],
    *,
    min_canary_count: int,
    max_unresolved_unknowns: int,
    min_realized_edge_bps: float,
) -> PromotionVerification:
    if artifact.get("schema") != PROMOTION_ARTIFACT_SCHEMA:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_ARTIFACT_SCHEMA_INVALID")
    rows = _canonical_promotion_rows(conn)
    summary = _promotion_summary_from_rows(conn, rows)
    aggregate_ids = tuple(row["aggregate_id"] for row in rows["audit_rows"])
    if aggregate_ids and len(rows["projection_rows"]) != len(set(aggregate_ids)):
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_PROJECTION_MISSING")
    if aggregate_ids and not rows["cap_transition_hashes"]:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_CAP_TRANSITION_MISSING")
    if aggregate_ids and not rows["user_or_reconcile_event_hashes"]:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_USER_OR_RECONCILE_MISSING")
    recomputed = summary.as_artifact()
    for volatile in ("generated_at",):
        recomputed.pop(volatile, None)
    comparable = dict(artifact)
    comparable.pop("generated_at", None)
    for field in (
        "db_user_version",
        "canary_count",
        "confirmed_fill_count",
        "terminal_no_fill_count",
        "reconciled_no_order_count",
        "unresolved_unknowns",
        "median_realized_edge_bps_from_confirmed_fills",
        "aggregate_ids",
        "audit_ids",
        "execution_command_ids",
        "execution_receipt_hashes",
        "cap_transition_hashes",
        "user_or_reconcile_event_hashes",
        "source_summary_hash",
    ):
        if comparable.get(field) != recomputed.get(field):
            return PromotionVerification(False, f"EDLI_LIVE_PROMOTION_ARTIFACT_DB_MISMATCH:{field}")
    try:
        artifact_edge = float(comparable.get("realized_edge_bps", 0.0))
    except (TypeError, ValueError):
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_REALIZED_EDGE_INVALID")
    if abs(artifact_edge - float(recomputed.get("realized_edge_bps", 0.0))) > 1e-9:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_ARTIFACT_DB_MISMATCH:realized_edge_bps")
    row_verification = _verify_promotion_eligible_confirmed_rows(rows["audit_rows"])
    if row_verification is not None:
        return PromotionVerification(False, row_verification)
    fill_edge = float(comparable.get("median_realized_edge_bps_from_confirmed_fills", 0.0))
    if int(comparable.get("confirmed_fill_count", 0)) < min_canary_count:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_CANARY_COUNT_INSUFFICIENT")
    if int(comparable.get("unresolved_unknowns", 0)) > max_unresolved_unknowns:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_UNRESOLVED_UNKNOWN")
    if fill_edge <= min_realized_edge_bps:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_REALIZED_EDGE_INSUFFICIENT")
    if _pending_projection_count(conn, tuple(comparable.get("aggregate_ids") or ())) > 0:
        return PromotionVerification(False, "EDLI_LIVE_PROMOTION_PENDING_RECONCILE")
    return PromotionVerification(True)


def _verify_promotion_eligible_confirmed_rows(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        if row.get("order_lifecycle_state") != "CONFIRMED":
            continue
        if not str(row.get("expected_edge_source_certificate_hash") or "").strip():
            return "EDLI_LIVE_PROMOTION_EXPECTED_EDGE_PROVENANCE_MISSING"
        if not str(row.get("cost_basis_source_certificate_hash") or "").strip():
            return "EDLI_LIVE_PROMOTION_COST_BASIS_PROVENANCE_MISSING"
        if not str(row.get("fill_source_event_hash") or "").strip():
            return "EDLI_LIVE_PROMOTION_FILL_PROVENANCE_MISSING"
        if _float_or_none(row.get("realized_edge")) is not None and float(row.get("realized_edge") or 0.0) <= 0:
            return "EDLI_LIVE_PROMOTION_REALIZED_EDGE_INSUFFICIENT"
        if int(row.get("promotion_eligible") or 0) != 1:
            return "EDLI_LIVE_PROMOTION_CONFIRMED_FILL_NOT_PROMOTION_ELIGIBLE"
    return None


def record_edli_live_profit_audit_from_aggregate(conn: sqlite3.Connection, aggregate_id: str) -> str | None:
    rows = _aggregate_event_rows(conn, aggregate_id)
    if not rows:
        return None
    events = [(str(row["event_type"]), json.loads(str(row["payload_json"])), str(row["event_hash"])) for row in rows]
    pre_submit = _latest_payload(events, "PreSubmitRevalidated")
    if not pre_submit or not pre_submit.get("condition_id") or not pre_submit.get("token_id"):
        return None
    command = _latest_payload(events, "ExecutionCommandCreated") or {}
    lifecycle_type, lifecycle_payload = _latest_lifecycle(events)
    if not lifecycle_type:
        return None
    state = _audit_state(lifecycle_type, lifecycle_payload)
    if not state:
        return None
    event_hash = _latest_event_hash(events, lifecycle_type)
    fill_source_event_hash = event_hash if lifecycle_type == "UserTradeObserved" else None
    settlement_source_event_hash = event_hash if lifecycle_type == "Reconciled" else None
    expected_edge_source_certificate_hash = pre_submit.get("expected_edge_source_certificate_hash")
    cost_basis_source_certificate_hash = pre_submit.get("cost_basis_source_certificate_hash")
    expected_cost_basis = pre_submit.get("expected_cost_basis")
    computed = compute_realized_edge_from_authorities(
        conn=conn,
        cost_model_cert_hash=str(cost_basis_source_certificate_hash or ""),
        expected_edge_cert_hash=str(expected_edge_source_certificate_hash or ""),
        fill_event_hash=str(fill_source_event_hash or ""),
        pre_submit=pre_submit,
        fill_payload=lifecycle_payload,
    )
    if computed is not None:
        expected_cost_basis = computed["expected_cost_basis"]
    promotion_eligible = (
        state == "CONFIRMED"
        and lifecycle_type == "UserTradeObserved"
        and lifecycle_payload.get("fill_authority_state") == "FILL_CONFIRMED"
        and bool(expected_edge_source_certificate_hash)
        and bool(cost_basis_source_certificate_hash)
        and bool(fill_source_event_hash)
        and expected_cost_basis is not None
        and computed is not None
        and computed["realized_edge"] > 0
    )
    return LiveProfitAuditLedger(conn).insert_record(
        event_id=pre_submit.get("event_id"),
        aggregate_id=aggregate_id,
        final_intent_id=pre_submit.get("final_intent_id"),
        execution_command_id=command.get("execution_command_id") or lifecycle_payload.get("execution_command_id"),
        condition_id=pre_submit.get("condition_id"),
        token_id=pre_submit.get("token_id"),
        direction=pre_submit.get("direction"),
        side=pre_submit.get("side"),
        expected_cost_basis=expected_cost_basis,
        expected_fee=pre_submit.get("expected_fee"),
        expected_spread_cost=pre_submit.get("expected_spread_cost"),
        visible_depth_fill_lcb=pre_submit.get("visible_depth_fill_lcb"),
        order_policy=pre_submit.get("order_policy"),
        native_token_side=pre_submit.get("native_token_side"),
        expected_edge=pre_submit.get("expected_edge"),
        quote_seen_at=pre_submit.get("quote_seen_at"),
        quote_age_ms=pre_submit.get("quote_age_ms"),
        best_bid=pre_submit.get("current_best_bid"),
        best_ask=pre_submit.get("current_best_ask"),
        limit_price=pre_submit.get("limit_price"),
        order_type=pre_submit.get("order_type"),
        time_in_force=pre_submit.get("time_in_force"),
        venue_order_id=lifecycle_payload.get("venue_order_id"),
        order_lifecycle_state=state,
        avg_fill_price=(computed or {}).get("avg_fill_price") or lifecycle_payload.get("avg_fill_price") or lifecycle_payload.get("fill_price"),
        filled_size=(computed or {}).get("filled_size") or lifecycle_payload.get("filled_size") or lifecycle_payload.get("size"),
        fees=(computed or {}).get("fees") if computed is not None else lifecycle_payload.get("fees"),
        realized_edge=(computed or {}).get("realized_edge"),
        edge_value_usd=(computed or {}).get("edge_value_usd"),
        pnl_usd=lifecycle_payload.get("pnl_usd") if settlement_source_event_hash else None,
        reject_reason=lifecycle_payload.get("reason_code") or lifecycle_payload.get("reject_reason"),
        expected_edge_source_certificate_hash=expected_edge_source_certificate_hash,
        cost_basis_source_certificate_hash=cost_basis_source_certificate_hash,
        fill_source_event_hash=fill_source_event_hash,
        settlement_source_event_hash=settlement_source_event_hash,
        promotion_eligible=1 if promotion_eligible else 0,
    )


def compute_realized_edge_from_authorities(
    *,
    conn: sqlite3.Connection,
    cost_model_cert_hash: str,
    expected_edge_cert_hash: str,
    fill_event_hash: str,
    pre_submit: dict[str, Any],
    fill_payload: dict[str, Any],
) -> dict[str, float] | None:
    """Compute realized edge from cost-basis and fill authority fields.

    This intentionally refuses to use a caller-supplied realized_edge value.
    Promotion-eligible audit rows need a cost-basis authority hash, expected-edge
    authority hash, fill event hash, and typed fill economics.
    """

    if not cost_model_cert_hash or not expected_edge_cert_hash or not fill_event_hash:
        return None
    cost_payload = _load_verified_cost_model_payload(conn, cost_model_cert_hash)
    edge_payload = _load_verified_certificate_payload(conn, expected_edge_cert_hash)
    if cost_payload is None or edge_payload is None:
        return None
    if not _certificate_identity_matches_pre_submit(cost_payload, pre_submit):
        return None
    if not _certificate_identity_matches_pre_submit(edge_payload, pre_submit):
        return None
    side = str(pre_submit.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        return None
    expected_cost_basis = _expected_cost_basis_from_cost_payload(cost_payload)
    if expected_cost_basis is None:
        return None
    q_live = _float_or_none(edge_payload.get("q_live"))
    if q_live is None:
        q_live = _float_or_none(edge_payload.get("expected_probability"))
    if q_live is None:
        q_live = _float_or_none(edge_payload.get("p_posterior"))
    if q_live is None:
        q_live = _float_or_none(pre_submit.get("expected_probability"))
    if q_live is None:
        q_live = _float_or_none(pre_submit.get("q_live"))
    if q_live is None:
        return None
    avg_fill_price = _float_or_none(fill_payload.get("avg_fill_price") or fill_payload.get("fill_price"))
    filled_size = _float_or_none(fill_payload.get("filled_size") or fill_payload.get("size"))
    fees = _float_or_none(fill_payload.get("fees")) or 0.0
    if avg_fill_price is None or filled_size is None or filled_size <= 0:
        return None
    fee_per_share = fees / filled_size
    if side == "BUY":
        realized_edge = q_live - avg_fill_price - fee_per_share
    else:
        realized_edge = avg_fill_price - q_live - fee_per_share
    return {
        "avg_fill_price": avg_fill_price,
        "filled_size": filled_size,
        "fees": fees,
        "expected_cost_basis": expected_cost_basis,
        "realized_edge": realized_edge,
        "edge_value_usd": realized_edge * filled_size,
    }


def _load_verified_certificate_payload(conn: sqlite3.Connection, certificate_hash: str) -> dict[str, Any] | None:
    if not certificate_hash or not _table_exists(conn, "decision_certificates"):
        return None
    row = conn.execute(
        """
        SELECT payload_json
        FROM decision_certificates
        WHERE certificate_hash = ?
          AND verifier_status = 'VERIFIED'
        """,
        (certificate_hash,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["payload_json"] if isinstance(row, sqlite3.Row) else row[0]))
    except json.JSONDecodeError:
        return None


def _load_verified_cost_model_payload(conn: sqlite3.Connection, cost_hash: str) -> dict[str, Any] | None:
    """Resolve the VERIFIED CostModelCertificate payload for an audit cost hash.

    Phase 3 W1 sub-fix 1 (2026-06-20): the value stamped into the audit row's
    ``cost_basis_source_certificate_hash`` is the cost authority's INNER
    ``cost_basis_hash`` (the identity hash of the CostBasis sub-object), NOT the
    persisted ``decision_certificates.certificate_hash``. Verified against the live
    DB: 0/682 distinct audit cost hashes match a ``certificate_hash`` row, while
    653/682 (96%) equal the ``cost_basis_hash`` payload field of a VERIFIED
    CostModelCertificate. The edge leg resolves because its stamped value
    (``actionable_certificate_hash``) IS a real certificate_hash; only the cost
    leg's key differs. This resolver keys on the field that the audit row actually
    carries — the CostModelCertificate's own ``cost_basis_hash`` — which is the
    authority's stable identity. A direct ``certificate_hash`` match is tried first
    so the resolver stays correct if upstream is ever changed to stamp the primary
    key (single source of truth, no behaviour change for that case).
    """
    if not cost_hash or not _table_exists(conn, "decision_certificates"):
        return None
    direct = _load_verified_certificate_payload(conn, cost_hash)
    if direct is not None:
        return direct
    row = conn.execute(
        """
        SELECT payload_json
        FROM decision_certificates
        WHERE certificate_type = 'CostModelCertificate'
          AND verifier_status = 'VERIFIED'
          AND json_extract(payload_json, '$.cost_basis_hash') = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (cost_hash,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["payload_json"] if isinstance(row, sqlite3.Row) else row[0]))
    except json.JSONDecodeError:
        return None


def _certificate_identity_matches_pre_submit(payload: dict[str, Any], pre_submit: dict[str, Any]) -> bool:
    for field in ("condition_id", "token_id"):
        if str(payload.get(field) or "") != str(pre_submit.get(field) or ""):
            return False
    for field in ("side", "direction", "native_token_side", "order_policy"):
        value = payload.get(field)
        if value in (None, ""):
            continue
        expected = pre_submit.get(field)
        if field in {"side", "direction", "native_token_side"}:
            if str(value).upper() != str(expected or "").upper():
                return False
        elif str(value) != str(expected or ""):
            return False
    return True


def _expected_cost_basis_from_cost_payload(cost_payload: dict[str, Any]) -> float | None:
    """Derive the expected per-share cost basis from a CostModelCertificate payload.

    Phase 3 W1 sub-fix 2 (2026-06-20): the realized-edge cost leg previously read
    ``cost_payload.get("expected_cost_basis")`` — a key that exists in NO live
    CostModelCertificate (verified against the live DB: the cert payload keys are
    exactly ``c_fee_adjusted / c_cost_95pct / price_fee_deducted / cost_source /
    execution_price_type / cost_basis_hash / cost_basis_id / condition_id /
    token_id``, with ``expected_cost_basis`` absent on every row). The canonical
    expected per-share cost basis the model priced against is ``c_fee_adjusted``,
    the fee-adjusted MarketPrice — the SAME quantity the decision-time edge axis
    used (``alpha_gap = q_live - c_fee_adjusted``; see no_submit_receipts.py).
    ``price_fee_deducted`` is a BOOLEAN flag (whether the fee was already removed),
    never a price, so it is not a candidate. Returns None (fail-closed) when the
    cert carries no ``c_fee_adjusted``.
    """
    return _float_or_none(cost_payload.get("c_fee_adjusted"))


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _promotion_summary_from_rows(
    conn: sqlite3.Connection,
    rows: dict[str, list[dict[str, Any]]],
) -> LiveProfitPromotionSummary:
    confirmed_fill_rows = [
        row
        for row in rows["audit_rows"]
        if row.get("order_lifecycle_state") == "CONFIRMED" and int(row.get("promotion_eligible") or 0) == 1
    ]
    terminal_no_fill_count = int(
        sum(1 for row in rows["audit_rows"] if row.get("order_lifecycle_state") == "TERMINAL_NO_FILL")
    )
    reconciled_no_order_count = int(
        sum(
            1
            for row in rows["audit_rows"]
            if row.get("order_lifecycle_state") == "RECONCILED" and not row.get("avg_fill_price")
        )
    )
    canary_count = int(len(confirmed_fill_rows) + terminal_no_fill_count + reconciled_no_order_count)
    unresolved_unknowns = int(
        sum(1 for row in rows["audit_rows"] if row.get("order_lifecycle_state") in {"TIMEOUT_UNKNOWN", "POST_SUBMIT_UNKNOWN", "PENDING_RECONCILE"})
        + _pending_projection_count(conn, tuple(row["aggregate_id"] for row in rows["audit_rows"]))
    )
    realized_edges = [
        float(row["realized_edge"])
        for row in rows["audit_rows"]
        if row.get("realized_edge") is not None
    ]
    confirmed_fill_edges = [
        float(row["realized_edge"])
        for row in confirmed_fill_rows
        if row.get("realized_edge") is not None
    ]
    realized_edge_bps = float(median(realized_edges) * 10_000.0) if realized_edges else 0.0
    fill_edge_bps = float(median(confirmed_fill_edges) * 10_000.0) if confirmed_fill_edges else 0.0
    return LiveProfitPromotionSummary(
        canary_count=canary_count,
        confirmed_fill_count=len(confirmed_fill_rows),
        terminal_no_fill_count=terminal_no_fill_count,
        reconciled_no_order_count=reconciled_no_order_count,
        unresolved_unknowns=unresolved_unknowns,
        realized_edge_bps=realized_edge_bps,
        median_realized_edge_bps_from_confirmed_fills=fill_edge_bps,
        aggregate_ids=tuple(row["aggregate_id"] for row in rows["audit_rows"]),
        audit_ids=tuple(row["audit_id"] for row in rows["audit_rows"]),
        execution_command_ids=tuple(
            row["execution_command_id"] for row in rows["audit_rows"] if row.get("execution_command_id")
        ),
        execution_receipt_hashes=tuple(rows["execution_receipt_hashes"]),
        cap_transition_hashes=tuple(rows["cap_transition_hashes"]),
        user_or_reconcile_event_hashes=tuple(rows["user_or_reconcile_event_hashes"]),
        source_summary_hash=stable_hash(rows),
        db_user_version=int(conn.execute("PRAGMA user_version").fetchone()[0] or 0),
    )


def write_promotion_artifact(conn: sqlite3.Connection, path: str) -> LiveProfitPromotionSummary:
    ensure_table(conn)
    summary = promotion_summary(conn)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary.as_artifact(), fh, sort_keys=True)
        fh.write("\n")
    return summary


def _canonical_promotion_rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    if not _table_exists(conn, "edli_live_profit_audit"):
        return {
            "audit_rows": [],
            "event_rows": [],
            "projection_rows": [],
            "execution_receipt_hashes": [],
            "cap_transition_hashes": [],
            "user_or_reconcile_event_hashes": [],
        }
    audit_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM edli_live_profit_audit
            ORDER BY aggregate_id, audit_id
            """
        ).fetchall()
    ]
    aggregate_ids = tuple(row["aggregate_id"] for row in audit_rows)
    event_rows: list[dict[str, Any]] = []
    if aggregate_ids and _table_exists(conn, "edli_live_order_events"):
        event_rows = [
            {
                "aggregate_id": row["aggregate_id"],
                "event_sequence": row["event_sequence"],
                "event_type": row["event_type"],
                "event_hash": row["event_hash"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in conn.execute(
                """
                SELECT aggregate_id, event_sequence, event_type, event_hash, payload_json
                FROM edli_live_order_events
                ORDER BY aggregate_id, event_sequence
                """,
            ).fetchall()
            if row["aggregate_id"] in aggregate_ids
        ]
    return {
        "audit_rows": audit_rows,
        "event_rows": event_rows,
        "projection_rows": _projection_rows(conn, aggregate_ids),
        "execution_receipt_hashes": sorted(
            {
                str(row["payload"].get("execution_receipt_hash"))
                for row in event_rows
                if row["payload"].get("execution_receipt_hash")
            }
        ),
        "cap_transition_hashes": sorted(
            {str(row["event_hash"]) for row in event_rows if row["event_type"] == "CapTransitioned"}
        ),
        "user_or_reconcile_event_hashes": sorted(
            {
                str(row["event_hash"])
                for row in event_rows
                if row["event_type"] in {"UserOrderObserved", "UserTradeObserved", "Reconciled"}
            }
        ),
    }


def _projection_rows(conn: sqlite3.Connection, aggregate_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    if not aggregate_ids or not _table_exists(conn, "edli_live_order_projection"):
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT aggregate_id, event_id, final_intent_id, current_state, pending_reconcile, venue_order_id
            FROM edli_live_order_projection
            ORDER BY aggregate_id
            """,
        ).fetchall()
        if row["aggregate_id"] in aggregate_ids
    ]


def _pending_projection_count(conn: sqlite3.Connection, aggregate_ids: tuple[str, ...]) -> int:
    if not aggregate_ids or not _table_exists(conn, "edli_live_order_projection"):
        return 0
    return sum(
        1
        for row in conn.execute(
            """
            SELECT aggregate_id, pending_reconcile
            FROM edli_live_order_projection
            """
        ).fetchall()
        if row["aggregate_id"] in aggregate_ids and bool(row["pending_reconcile"])
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _aggregate_event_rows(conn: sqlite3.Connection, aggregate_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT *
            FROM edli_live_order_events
            WHERE aggregate_id = ?
            ORDER BY event_sequence ASC
            """,
            (aggregate_id,),
        ).fetchall()
    )


def _latest_payload(events: list[tuple[str, dict[str, Any], str]], event_type: str) -> dict[str, Any] | None:
    for current_type, payload, _event_hash in reversed(events):
        if current_type == event_type:
            return payload
    return None


def _latest_event_hash(events: list[tuple[str, dict[str, Any], str]], event_type: str) -> str | None:
    for current_type, _payload, event_hash in reversed(events):
        if current_type == event_type:
            return event_hash
    return None


def _latest_lifecycle(events: list[tuple[str, dict[str, Any], str]]) -> tuple[str | None, dict[str, Any]]:
    for event_type, payload, _event_hash in reversed(events):
        if event_type in {
            "VenueSubmitAcknowledged",
            "SubmitRejected",
            "SubmitUnknown",
            "UserTradeObserved",
            "Reconciled",
            "CapTransitioned",
        }:
            return event_type, payload
    return None, {}


def _audit_state(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type == "VenueSubmitAcknowledged":
        return "SUBMITTED"
    if event_type == "SubmitRejected":
        return "REJECTED"
    if event_type == "SubmitUnknown":
        return str(payload.get("submit_status") or "POST_SUBMIT_UNKNOWN")
    if event_type == "UserTradeObserved":
        if payload.get("fill_authority_state") == "FILL_CONFIRMED":
            return "CONFIRMED"
        return str(payload.get("trade_status") or "USER_TRADE_OBSERVED")
    if event_type == "Reconciled":
        if payload.get("venue_order_exists") is False or payload.get("cap_transition_recommendation") == "RELEASED":
            return "TERMINAL_NO_FILL"
        return "RECONCILED"
    if event_type == "CapTransitioned":
        return str(payload.get("to_status") or "CAP_TRANSITIONED")
    return None


def _stable_audit_id(aggregate_id: str, execution_command_id: Any, order_lifecycle_state: str) -> str:
    import hashlib

    material = f"{aggregate_id}|{execution_command_id or ''}|{order_lifecycle_state}"
    return "edli-live-profit-audit:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
