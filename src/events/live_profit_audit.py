"""EDLI live profit audit projection helpers.

# Created: 2026-05-26
# Last audited: 2026-06-22
# Authority basis: q-provenance stamping for settlement skill attribution (lifecycle-alpha mission).
#   2026-06-22: stamp expected_edge from the ActionableTradeCertificate's
#   qkernel_execution_economics.edge_lcb (previously read from PreSubmitRevalidated,
#   which never carries it -> 0/1651 live rows NULL). Closes the ex-ante side of the
#   decision->settlement audit loop. Consult REQ-20260622-021129 (Pro, HIGH).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from statistics import median
from typing import Any

from src.decision_kernel.canonicalization import stable_hash
from src.state.schema.edli_live_profit_audit_schema import ensure_table



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
    "learning_eligible",
)


class LiveProfitAuditLedger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        ensure_table(conn)

    def insert_record(self, **record: Any) -> str:
        now = _utc_now_iso()
        created_at = str(record.get("created_at") or now)
        normalized = {field: record.get(field) for field in _AUDIT_FIELDS}
        normalized["learning_eligible"] = 1 if normalized.get("learning_eligible") else 0
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
        # LX-E packet (2026-07-13, docs/rebuild/local_ledger_excision_2026-07-12.md
        # Round-2 delta adjudication "mutable learning receipts"): archive the
        # CURRENT row's full pre-image before the ON CONFLICT DO UPDATE below can
        # overwrite it — a rerun must never silently destroy the corpus that
        # produced a historical model decision. edli_live_profit_audit itself
        # keeps its existing single-row-per-natural-key read contract.
        from src.state.append_only_supersession import archive_row_before_overwrite

        archive_row_before_overwrite(
            self.conn,
            table="edli_live_profit_audit",
            key_column="audit_id",
            key_value=audit_id,
            supersessions_table="edli_live_profit_audit_supersessions",
            new_id=audit_id,
            now_iso=created_at,
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
                settlement_source_event_hash, learning_eligible, created_at,
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
                learning_eligible = excluded.learning_eligible,
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
    # Stamp q_live / q_lcb_5pct from the expected-edge certificate so the
    # settlement grader can attribute SKILL_WIN vs LUCKY_WIN.  The cert payload
    # (ActionableTradeCertificate) carries both keys directly.  We reuse the
    # existing helper to avoid a second DB connection (INV-37).
    edge_cert_payload = _load_verified_certificate_payload(
        conn, str(expected_edge_source_certificate_hash or "")
    )
    q_live_stamp = _float_or_none((edge_cert_payload or {}).get("q_live"))
    q_lcb_5pct_stamp = _float_or_none((edge_cert_payload or {}).get("q_lcb_5pct"))
    # Stamp expected_edge (decision-time ex-ante edge) from the SAME edge cert.
    # The live PreSubmitRevalidated payload carries NO expected_edge (verified
    # 0/1651 live rows), so the canonical source is the ActionableTradeCertificate's
    # qkernel_execution_economics.edge_lcb — the LCB edge (payoff_q_lcb - cost) the
    # decision actually acted on (== the cert's action_score / trade_score). Without
    # it the settlement audit has no ex-ante edge to compare realized outcome
    # against. Leaves None when the cert / field is unavailable (fail-safe; mirrors
    # the q_live / q_lcb_5pct stamping). INV-37: reuses the loaded cert payload, no
    # new DB connection.
    _edge_economics = (edge_cert_payload or {}).get("qkernel_execution_economics")
    expected_edge_stamp = (
        _float_or_none(_edge_economics.get("edge_lcb"))
        if isinstance(_edge_economics, dict)
        else None
    )
    learning_eligible = (
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
        q_live=q_live_stamp,
        q_lcb_5pct=q_lcb_5pct_stamp,
        expected_cost_basis=expected_cost_basis,
        expected_fee=pre_submit.get("expected_fee"),
        expected_spread_cost=pre_submit.get("expected_spread_cost"),
        visible_depth_fill_lcb=pre_submit.get("visible_depth_fill_lcb"),
        order_policy=pre_submit.get("order_policy"),
        native_token_side=pre_submit.get("native_token_side"),
        expected_edge=expected_edge_stamp,
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
        learning_eligible=1 if learning_eligible else 0,
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
    cost_payload = _load_verified_cost_model_payload(conn, cost_model_cert_hash, pre_submit)
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


def _load_verified_cost_model_payload(
    conn: sqlite3.Connection,
    cost_hash: str,
    pre_submit: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve the VERIFIED CostModelCertificate payload for an audit cost hash.

    Phase 3 W1 sub-fix 1 (2026-06-20): the value stamped into the audit row's
    ``cost_basis_source_certificate_hash`` is the cost authority's INNER
    ``cost_basis_hash`` (the identity hash of the CostBasis sub-object), NOT the
    persisted ``decision_certificates.certificate_hash``. Verified against the live
    DB: 0/682 distinct audit cost hashes match a ``certificate_hash`` row, while
    653/682 (96%) equal the ``cost_basis_hash`` payload field of a VERIFIED
    CostModelCertificate. The edge leg resolves because its stamped value
    (``actionable_certificate_hash``) IS a real certificate_hash; only the cost
    leg's key differs.

    Re-review fix (2026-06-21): the resolution is type-filtered to
    ``CostModelCertificate`` on BOTH the direct (certificate_hash) and the inner-hash
    paths — a bare certificate_hash match could otherwise return a different cert type
    (e.g. FinalIntentCertificate, which on the live DB shares the inner
    ``cost_basis_hash`` field but lacks ``c_fee_adjusted``). And the inner-hash path is
    IDENTITY-FIRST: multiple VERIFIED CostModelCertificates can share one inner
    ``cost_basis_hash``, so we iterate all of them and return the one whose
    condition_id/token_id/side identity matches the audit fill — never ``rowid``-latest,
    which could return a sibling with the wrong identity and leave a resolvable row NULL.
    """
    if not cost_hash or not _table_exists(conn, "decision_certificates"):
        return None
    # Direct path: the stamped value IS a CostModelCertificate primary key
    # (forward-compatible if upstream is ever changed to stamp the real hash).
    direct = conn.execute(
        """
        SELECT payload_json
        FROM decision_certificates
        WHERE certificate_hash = ?
          AND certificate_type = 'CostModelCertificate'
          AND verifier_status = 'VERIFIED'
        """,
        (cost_hash,),
    ).fetchone()
    if direct is not None:
        payload = _payload_json_or_none(direct)
        if payload is not None:
            return payload
    # Inner-hash path: resolve by the CostModelCertificate's own ``cost_basis_hash``
    # field, choosing the identity-matching cert (not the newest).
    candidates = conn.execute(
        """
        SELECT payload_json
        FROM decision_certificates
        WHERE certificate_type = 'CostModelCertificate'
          AND verifier_status = 'VERIFIED'
          AND json_extract(payload_json, '$.cost_basis_hash') = ?
        ORDER BY rowid DESC
        """,
        (cost_hash,),
    ).fetchall()
    fallback: dict[str, Any] | None = None
    for cand in candidates:
        payload = _payload_json_or_none(cand)
        if payload is None:
            continue
        if _certificate_identity_matches_pre_submit(payload, pre_submit):
            return payload
        if fallback is None:
            fallback = payload
    # No identity match: return the newest candidate so the caller's authority check
    # (which re-validates identity) rejects it deterministically — never a silent
    # wrong-identity acceptance, and never None when at least one candidate exists.
    return fallback


def _payload_json_or_none(row: Any) -> dict[str, Any] | None:
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
            "OrderLifecycleProjected",
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
    if event_type == "OrderLifecycleProjected":
        return str(payload.get("order_lifecycle_state") or "ORDER_LIFECYCLE_PROJECTED")
    return None


def _stable_audit_id(aggregate_id: str, execution_command_id: Any, order_lifecycle_state: str) -> str:
    import hashlib

    material = f"{aggregate_id}|{execution_command_id or ''}|{order_lifecycle_state}"
    return "edli-live-profit-audit:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
