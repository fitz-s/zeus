"""Durable EDLI no-submit receipt ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from src.events.reactor import EventSubmissionReceipt

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
        receipt_id = _receipt_id(receipt)
        created = (created_at or decision_time).astimezone(timezone.utc).isoformat()
        existing = self.conn.execute(
            """
            SELECT receipt_id, receipt_hash
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
                receipt_json, receipt_hash, created_at, schema_version
            ) VALUES (
                :receipt_id, :event_id, :causal_snapshot_id, :decision_time,
                :family_id, :candidate_id, :condition_id, :token_id, :direction,
                :executable_snapshot_id, :final_intent_id, :side_effect_status,
                :q_live, :q_lcb_5pct, :c_fee_adjusted, :c_cost_95pct, :p_fill_lcb,
                :trade_score, :fdr_family_id, :fdr_hypothesis_count,
                :kelly_cost_basis_id, :kelly_decision_id, :risk_decision_id, :kelly_size_usd,
                :receipt_json, :receipt_hash, :created_at, :schema_version
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
                "receipt_json": receipt_json,
                "receipt_hash": receipt_hash,
                "created_at": created,
                "schema_version": SCHEMA_VERSION,
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


def _receipt_json(receipt: EventSubmissionReceipt) -> str:
    payload: dict[str, Any] = asdict(receipt)
    payload.pop("decision_proof_bundle", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
