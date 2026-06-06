"""Durable EDLI no-submit receipt ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from src.decision_kernel.canonicalization import stable_hash
from src.events.reactor import EventSubmissionReceipt
from src.types.market_price import MarketPrice, compute_alpha_gap_from_market_price

SCHEMA_VERSION = 1


class EdliReceiptHashDriftError(RuntimeError):
    """Raised when a duplicate receipt key recomputes to different proof bytes."""


class EdliNoSubmitReceiptLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_idempotent(
        self,
        receipt: EventSubmissionReceipt,
        *,
        decision_time: datetime,
        created_at: datetime | None = None,
    ) -> str:
        if receipt.side_effect_status != "NO_SUBMIT":
            raise ValueError("edli_no_submit_receipts only accepts NO_SUBMIT receipts")
        if not receipt.proof_accepted:
            raise ValueError("edli_no_submit_receipts only accepts proof-accepted receipts")
        receipt_json = _receipt_json(receipt)
        receipt_hash = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
        projection_hash = _projection_hash(receipt)
        receipt_id = _receipt_id(receipt)
        created = (created_at or decision_time).astimezone(timezone.utc).isoformat()
        existing = self.conn.execute(
            """
            SELECT receipt_id, receipt_hash, projection_hash
            FROM edli_no_submit_receipts
            WHERE receipt_id = ?
               OR (event_id = ? AND final_intent_id = ?)
            LIMIT 1
            """,
            (receipt_id, receipt.event_id, receipt.final_intent_id),
        ).fetchone()
        if existing is not None:
            existing_receipt_id = str(existing["receipt_id"] if isinstance(existing, sqlite3.Row) else existing[0])
            existing_hash = str(existing["receipt_hash"] if isinstance(existing, sqlite3.Row) else existing[1])
            if existing_hash == receipt_hash:
                existing_projection_hash = existing["projection_hash"] if isinstance(existing, sqlite3.Row) else existing[2]
                if existing_projection_hash in (None, ""):
                    self.conn.execute(
                        "UPDATE edli_no_submit_receipts SET projection_hash = ? WHERE receipt_id = ?",
                        (projection_hash, existing_receipt_id),
                    )
                elif str(existing_projection_hash) != projection_hash:
                    raise EdliReceiptHashDriftError(
                        "EDLI_RECEIPT_PROJECTION_HASH_DRIFT:"
                        f"event_id={receipt.event_id}:final_intent_id={receipt.final_intent_id}:"
                        f"existing_projection_hash={existing_projection_hash}:new_projection_hash={projection_hash}"
                    )
                return existing_receipt_id
            raise EdliReceiptHashDriftError(
                "EDLI_RECEIPT_HASH_DRIFT:"
                f"event_id={receipt.event_id}:final_intent_id={receipt.final_intent_id}:"
                f"existing_hash={existing_hash}:new_hash={receipt_hash}"
            )
        self.conn.execute(
            """
            INSERT INTO edli_no_submit_receipts (
                receipt_id, event_id, causal_snapshot_id, decision_time,
                family_id, candidate_id, condition_id, token_id, direction,
                executable_snapshot_id, final_intent_id, side_effect_status,
                q_live, q_lcb_5pct, c_fee_adjusted, c_cost_95pct, p_fill_lcb,
                trade_score, fdr_family_id, fdr_hypothesis_count,
                kelly_cost_basis_id, kelly_decision_id, risk_decision_id, kelly_size_usd,
                projection_hash, receipt_json, receipt_hash, created_at, schema_version,
                mainstream_agreement_pass, mainstream_agreement_fail_reason,
                mainstream_point, mainstream_delta, mainstream_bin_label,
                mainstream_source, mainstream_fetched_at_utc,
                alpha_gap
            ) VALUES (
                :receipt_id, :event_id, :causal_snapshot_id, :decision_time,
                :family_id, :candidate_id, :condition_id, :token_id, :direction,
                :executable_snapshot_id, :final_intent_id, :side_effect_status,
                :q_live, :q_lcb_5pct, :c_fee_adjusted, :c_cost_95pct, :p_fill_lcb,
                :trade_score, :fdr_family_id, :fdr_hypothesis_count,
                :kelly_cost_basis_id, :kelly_decision_id, :risk_decision_id, :kelly_size_usd,
                :projection_hash, :receipt_json, :receipt_hash, :created_at, :schema_version,
                :mainstream_agreement_pass, :mainstream_agreement_fail_reason,
                :mainstream_point, :mainstream_delta, :mainstream_bin_label,
                :mainstream_source, :mainstream_fetched_at_utc,
                :alpha_gap
            )
            """,
            {
                "receipt_id": receipt_id,
                "event_id": receipt.event_id,
                "causal_snapshot_id": receipt.causal_snapshot_id,
                "decision_time": decision_time.astimezone(timezone.utc).isoformat(),
                "family_id": receipt.family_id,
                "candidate_id": receipt.candidate_id,
                "condition_id": receipt.condition_id,
                "token_id": receipt.token_id,
                "direction": receipt.direction,
                "executable_snapshot_id": receipt.executable_snapshot_id,
                "final_intent_id": receipt.final_intent_id,
                "side_effect_status": receipt.side_effect_status,
                "q_live": receipt.q_live,
                "q_lcb_5pct": receipt.q_lcb_5pct,
                "c_fee_adjusted": receipt.c_fee_adjusted,
                "c_cost_95pct": receipt.c_cost_95pct,
                "p_fill_lcb": receipt.p_fill_lcb,
                "trade_score": receipt.trade_score,
                "fdr_family_id": receipt.fdr_family_id,
                "fdr_hypothesis_count": receipt.fdr_hypothesis_count,
                "kelly_cost_basis_id": receipt.kelly_cost_basis_id,
                "kelly_decision_id": receipt.kelly_decision_id,
                "risk_decision_id": receipt.risk_decision_id,
                "kelly_size_usd": receipt.kelly_size_usd,
                "projection_hash": projection_hash,
                "receipt_json": receipt_json,
                "receipt_hash": receipt_hash,
                "created_at": created,
                "schema_version": SCHEMA_VERSION,
                "mainstream_agreement_pass": (
                    int(receipt.mainstream_agreement_pass)
                    if receipt.mainstream_agreement_pass is not None
                    else None
                ),
                "mainstream_agreement_fail_reason": receipt.mainstream_agreement_fail_reason,
                "mainstream_point": receipt.mainstream_point,
                "mainstream_delta": receipt.mainstream_delta,
                "mainstream_bin_label": receipt.mainstream_bin_label,
                "mainstream_source": receipt.mainstream_source,
                "mainstream_fetched_at_utc": receipt.mainstream_fetched_at_utc,
                # B2 (PR-4, 2026-06-03): edge-axis column.
                # NULL when c_fee_adjusted is NULL (no executable quote — fail-closed).
                # Routed through compute_alpha_gap_from_market_price so that passing
                # c_cost_95pct (C95Price) where c_fee_adjusted (MarketPrice) is expected
                # raises TypeError at this write boundary — the confusion is unconstructable.
                "alpha_gap": (
                    receipt.alpha_gap
                    if receipt.alpha_gap is not None
                    else (
                        compute_alpha_gap_from_market_price(
                            receipt.q_live,
                            MarketPrice(receipt.c_fee_adjusted),
                        )
                        if receipt.q_live is not None and receipt.c_fee_adjusted is not None
                        else None
                    )
                ),
            },
        )
        return receipt_id


def _receipt_id(receipt: EventSubmissionReceipt) -> str:
    stable = {
        "event_id": receipt.event_id,
        "final_intent_id": receipt.final_intent_id,
        "side_effect_status": receipt.side_effect_status,
    }
    digest = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"edli_no_submit:{digest}"


_MAINSTREAM_GATE_FIELDS = frozenset({
    "mainstream_agreement_pass",
    "mainstream_agreement_fail_reason",
    "mainstream_point",
    "mainstream_delta",
    "mainstream_bin_label",
    "mainstream_source",
    "mainstream_fetched_at_utc",
})


def _receipt_json(receipt: EventSubmissionReceipt) -> str:
    payload: dict[str, Any] = asdict(receipt)
    payload.pop("decision_proof_bundle", None)
    # BUG-2 fix (#135): omit mainstream_* fields when the gate was NOT evaluated
    # (all None) so receipt_hash is byte-identical to pre-gate baseline when the
    # flag is OFF. Presence of the fields with null values changes the JSON and
    # therefore the hash — breaking shadow-inertness / triggering EdliReceiptHashDrift
    # on retry for pre-existing shadow receipts. Mirror the decision_proof_bundle
    # exclusion pattern: drop the block entirely when not populated.
    if all(payload.get(k) is None for k in _MAINSTREAM_GATE_FIELDS):
        for k in _MAINSTREAM_GATE_FIELDS:
            payload.pop(k, None)
    # B2 (PR-4, 2026-06-03): alpha_gap — omit when None for hash stability.
    # Receipts without an executable quote (c_fee_adjusted=NULL) had no alpha_gap
    # before B2; including "alpha_gap: null" would change their hash and trigger
    # EdliReceiptHashDrift on all pre-B2 shadow receipts.  When the gap IS
    # computed (both q_live and c_fee_adjusted present), include it so backfill
    # and audit tooling can recover the value from the blob.
    alpha_gap_val = payload.get("alpha_gap")
    if alpha_gap_val is None:
        # Compute from q_live/c_fee_adjusted in case the dataclass field was not
        # pre-populated (e.g., receipts constructed before B2 field was added).
        q_live_val = payload.get("q_live")
        c_fee_val = payload.get("c_fee_adjusted")
        if q_live_val is not None and c_fee_val is not None:
            alpha_gap_val = float(q_live_val) - float(c_fee_val)
    if alpha_gap_val is not None:
        payload["alpha_gap"] = alpha_gap_val
    else:
        payload.pop("alpha_gap", None)
    # #120: q_source — omit when None for hash stability (pre-#120 receipts had no
    # such key; "q_source: null" would change the JSON/hash and trigger
    # EdliReceiptHashDrift on retry of every existing shadow receipt). When set,
    # persist it so the serving calibrator is recoverable from the blob forever.
    if payload.get("q_source") is None:
        payload.pop("q_source", None)
    if payload.get("opportunity_book") is None:
        payload.pop("opportunity_book", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _projection_hash(receipt: EventSubmissionReceipt) -> str:
    return stable_hash(
        {
            "event_id": receipt.event_id,
            "final_intent_id": receipt.final_intent_id,
            "side_effect_status": receipt.side_effect_status,
            "proof_accepted": receipt.proof_accepted,
            "submitted": receipt.submitted,
            "executable_snapshot_id": receipt.executable_snapshot_id,
        }
    )
