"""Immutable reference from one selected order to its durable global receipt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping


GLOBAL_AUCTION_RECEIPT_SCHEMA_VERSION = 22
GLOBAL_AUCTION_RECEIPT_SUPPORTED_SCHEMA_VERSIONS = frozenset({21, 22})
# Capital evidence must never mix receipts produced by different feasible-set,
# comparison, or sizing laws.  This identity is shared by the auction writer,
# decision certificate, shadow grader, and RiskGuard cohorting boundary.
CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION = (
    "global_single_order_authority_q_expected_growth_v3"
)
GLOBAL_AUCTION_RECEIPT_MODES = frozenset(
    {
        "global_single_order_auction",
        "global_single_order_auction_delta",
        "global_single_order_auction_duplicate",
    }
)
_EXECUTION_BINDING_VERSION = "global-auction-execution-binding-v1"
_EXECUTION_BINDING_VERSION_V22 = "global-auction-execution-binding-v2"
_ARTIFACT_SUMMARY_HASH_FIELD = "artifact_summary_hash"
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_EXECUTION_BINDING_FIELDS = (
    "schema_version",
    "selection_epoch_identity",
    "selection_cut_at_utc",
    "decision_at_utc",
    "full_scope_identity",
    "book_epoch_identity",
    "wealth_witness_identity",
    "wealth_economic_identity",
    "winner_event_id",
    "winner_candidate_id",
    "winner_actuation_identity",
    "payload_identity",
    "decision_payload_identity",
    "audit_context_sha256",
    "book_native_side_states_sha256",
    "candidate_evaluations_sha256",
    "buy_minimum_marketable_repairs_sha256",
    "holding_auction_coverage_sha256",
)
_EXECUTION_BINDING_V22_FIELDS = _EXECUTION_BINDING_FIELDS + (
    "global_selection_revision",
    "portfolio_wealth",
)
_EXECUTION_BINDING_TEXT_FIELDS = (
    "selection_epoch_identity",
    "full_scope_identity",
    "book_epoch_identity",
    "wealth_witness_identity",
    "wealth_economic_identity",
)
_EXECUTION_BINDING_TIMESTAMP_FIELDS = (
    "selection_cut_at_utc",
    "decision_at_utc",
)
_EXECUTION_BINDING_HASH_FIELDS = (
    "payload_identity",
    "decision_payload_identity",
    "audit_context_sha256",
    "book_native_side_states_sha256",
    "candidate_evaluations_sha256",
    "buy_minimum_marketable_repairs_sha256",
    "holding_auction_coverage_sha256",
)
_EXECUTION_BINDING_WINNER_FIELDS = (
    "winner_event_id",
    "winner_candidate_id",
    "winner_actuation_identity",
)
GLOBAL_SELL_EXECUTION_MODES = frozenset({"TAKER_LIMIT", "MAKER_REST"})


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"GLOBAL_AUCTION_RECEIPT_{field.upper()}_MISSING")
    return text


def _hash_text(value: object, field: str) -> str:
    text = _required_text(value, field)
    if _HEX_64.fullmatch(text) is None:
        raise ValueError(f"GLOBAL_AUCTION_RECEIPT_{field.upper()}_INVALID")
    return text


def _required_timestamp(value: object, field: str) -> str:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"GLOBAL_AUCTION_RECEIPT_{field.upper()}_INVALID"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"GLOBAL_AUCTION_RECEIPT_{field.upper()}_INVALID")
    return text


def _assert_v22_capital_fields(summary: Mapping[str, Any]) -> None:
    _required_text(
        summary.get("global_selection_revision"),
        "global_selection_revision",
    )
    wealth = summary.get("portfolio_wealth")
    if not isinstance(wealth, Mapping):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_PORTFOLIO_WEALTH_MISSING")
    for field in (
        "ledger_snapshot_id",
        "position_set_hash",
        "collateral_authority",
    ):
        _required_text(wealth.get(field), f"portfolio_wealth_{field}")
    values: dict[str, Decimal] = {}
    for field in (
        "wealth_floor_usd",
        "wealth_ceiling_usd",
        "spendable_cash_usd",
        "reservations_usd",
    ):
        text = _required_text(wealth.get(field), f"portfolio_wealth_{field}")
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(
                f"GLOBAL_AUCTION_RECEIPT_PORTFOLIO_WEALTH_{field.upper()}_INVALID"
            ) from exc
        if not value.is_finite():
            raise ValueError(
                f"GLOBAL_AUCTION_RECEIPT_PORTFOLIO_WEALTH_{field.upper()}_INVALID"
            )
        values[field] = value
    if values["wealth_floor_usd"] <= 0:
        raise ValueError(
            "GLOBAL_AUCTION_RECEIPT_PORTFOLIO_WEALTH_FLOOR_INVALID"
        )
    if values["wealth_ceiling_usd"] < values["wealth_floor_usd"]:
        raise ValueError(
            "GLOBAL_AUCTION_RECEIPT_PORTFOLIO_WEALTH_BOUNDS_INVALID"
        )
    if values["spendable_cash_usd"] < 0 or values["reservations_usd"] < 0:
        raise ValueError(
            "GLOBAL_AUCTION_RECEIPT_PORTFOLIO_WEALTH_CAPITAL_INVALID"
        )


def _assert_execution_binding_fields(summary: Mapping[str, Any]) -> None:
    schema_version = summary.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in GLOBAL_AUCTION_RECEIPT_SUPPORTED_SCHEMA_VERSIONS
    ):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_SCHEMA_VERSION_INVALID")
    for field in _EXECUTION_BINDING_TEXT_FIELDS:
        _required_text(summary.get(field), field)
    timestamps = {
        field: _required_timestamp(summary.get(field), field)
        for field in _EXECUTION_BINDING_TIMESTAMP_FIELDS
    }
    cut_at = datetime.fromisoformat(
        timestamps["selection_cut_at_utc"].replace("Z", "+00:00")
    )
    decision_at = datetime.fromisoformat(
        timestamps["decision_at_utc"].replace("Z", "+00:00")
    )
    if decision_at < cut_at:
        raise ValueError("GLOBAL_AUCTION_RECEIPT_DECISION_TIME_PRECEDES_CUT")
    for field in _EXECUTION_BINDING_HASH_FIELDS:
        _hash_text(summary.get(field), field)
    if schema_version == 22:
        _assert_v22_capital_fields(summary)
    winners = tuple(
        str(summary.get(field) or "").strip()
        for field in _EXECUTION_BINDING_WINNER_FIELDS
    )
    if any(winners) and not all(winners):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_WINNER_BINDING_INCOMPLETE")


def global_auction_execution_binding_hash(summary: Mapping[str, Any]) -> str:
    """Hash the complete compact-row witness that binds a winner to its cut."""

    _assert_execution_binding_fields(summary)
    schema_version = summary["schema_version"]
    fields = (
        _EXECUTION_BINDING_V22_FIELDS
        if schema_version == 22
        else _EXECUTION_BINDING_FIELDS
    )
    payload = {field: summary[field] for field in fields}
    payload["binding_version"] = (
        _EXECUTION_BINDING_VERSION_V22
        if schema_version == 22
        else _EXECUTION_BINDING_VERSION
    )
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def global_auction_artifact_summary_hash(summary: Mapping[str, Any]) -> str:
    """Hash the exact persisted summary independently of logical compaction."""

    payload = dict(summary)
    payload.pop(_ARTIFACT_SUMMARY_HASH_FIELD, None)
    encoded = json.dumps(
        payload,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def assert_global_auction_summary_integrity(summary: Mapping[str, Any]) -> None:
    """Require both actionable binding and exact stored-summary integrity."""

    expected_binding_hash = global_auction_execution_binding_hash(summary)
    stored_binding_hash = _hash_text(
        summary.get("execution_binding_hash"),
        "execution_binding_hash",
    )
    if stored_binding_hash != expected_binding_hash:
        raise ValueError("GLOBAL_AUCTION_RECEIPT_EXECUTION_BINDING_HASH_MISMATCH")
    stored_summary_hash = _hash_text(
        summary.get(_ARTIFACT_SUMMARY_HASH_FIELD),
        _ARTIFACT_SUMMARY_HASH_FIELD,
    )
    if stored_summary_hash != global_auction_artifact_summary_hash(summary):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_ARTIFACT_SUMMARY_HASH_MISMATCH")
    _hash_text(summary.get("receipt_hash"), "receipt_hash")


@dataclass(frozen=True)
class GlobalAuctionReceiptRef:
    """Exact durable receipt identity committed by an actionable certificate."""

    decision_log_id: int
    decision_log_mode: str
    receipt_hash: str
    execution_binding_hash: str
    artifact_summary_hash: str
    schema_version: int
    winner_event_id: str
    winner_candidate_id: str
    winner_actuation_identity: str
    selection_epoch_identity: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.decision_log_id, bool)
            or not isinstance(self.decision_log_id, int)
            or self.decision_log_id <= 0
        ):
            raise ValueError("GLOBAL_AUCTION_RECEIPT_DECISION_LOG_ID_INVALID")
        if self.decision_log_mode not in GLOBAL_AUCTION_RECEIPT_MODES:
            raise ValueError("GLOBAL_AUCTION_RECEIPT_DECISION_LOG_MODE_INVALID")
        if self.schema_version not in GLOBAL_AUCTION_RECEIPT_SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError("GLOBAL_AUCTION_RECEIPT_SCHEMA_VERSION_INVALID")
        _hash_text(self.receipt_hash, "receipt_hash")
        _hash_text(self.execution_binding_hash, "execution_binding_hash")
        _hash_text(self.artifact_summary_hash, "artifact_summary_hash")
        _required_text(self.winner_event_id, "winner_event_id")
        _required_text(self.winner_candidate_id, "winner_candidate_id")
        _required_text(self.winner_actuation_identity, "winner_actuation_identity")
        _required_text(self.selection_epoch_identity, "selection_epoch_identity")

    def as_payload(self) -> dict[str, object]:
        return {
            "decision_log_id": self.decision_log_id,
            "decision_log_mode": self.decision_log_mode,
            "receipt_hash": self.receipt_hash,
            "execution_binding_hash": self.execution_binding_hash,
            "artifact_summary_hash": self.artifact_summary_hash,
            "schema_version": self.schema_version,
            "winner_event_id": self.winner_event_id,
            "winner_candidate_id": self.winner_candidate_id,
            "winner_actuation_identity": self.winner_actuation_identity,
            "selection_epoch_identity": self.selection_epoch_identity,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "GlobalAuctionReceiptRef":
        if not isinstance(payload, Mapping):
            raise ValueError("GLOBAL_AUCTION_RECEIPT_REF_MISSING")
        expected = {
            "decision_log_id",
            "decision_log_mode",
            "receipt_hash",
            "execution_binding_hash",
            "artifact_summary_hash",
            "schema_version",
            "winner_event_id",
            "winner_candidate_id",
            "winner_actuation_identity",
            "selection_epoch_identity",
        }
        if set(payload) != expected:
            raise ValueError("GLOBAL_AUCTION_RECEIPT_REF_FIELDS_INVALID")
        decision_log_id = payload["decision_log_id"]
        schema_version = payload["schema_version"]
        if isinstance(decision_log_id, bool) or not isinstance(decision_log_id, int):
            raise ValueError("GLOBAL_AUCTION_RECEIPT_DECISION_LOG_ID_INVALID")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise ValueError("GLOBAL_AUCTION_RECEIPT_SCHEMA_VERSION_INVALID")
        return cls(
            decision_log_id=decision_log_id,
            decision_log_mode=str(payload["decision_log_mode"]),
            receipt_hash=str(payload["receipt_hash"]),
            execution_binding_hash=str(payload["execution_binding_hash"]),
            artifact_summary_hash=str(payload["artifact_summary_hash"]),
            schema_version=schema_version,
            winner_event_id=str(payload["winner_event_id"]),
            winner_candidate_id=str(payload["winner_candidate_id"]),
            winner_actuation_identity=str(payload["winner_actuation_identity"]),
            selection_epoch_identity=str(payload["selection_epoch_identity"]),
        )

    def assert_matches_actuation(
        self,
        *,
        winner_event_id: object,
        winner_candidate_id: object,
        winner_actuation_identity: object,
        selection_epoch_identity: object,
    ) -> None:
        expected = (
            ("winner_event_id", self.winner_event_id, winner_event_id),
            ("winner_candidate_id", self.winner_candidate_id, winner_candidate_id),
            (
                "winner_actuation_identity",
                self.winner_actuation_identity,
                winner_actuation_identity,
            ),
            (
                "selection_epoch_identity",
                self.selection_epoch_identity,
                selection_epoch_identity,
            ),
        )
        for field, stored, current in expected:
            if stored != str(current or "").strip():
                raise ValueError(
                    f"GLOBAL_AUCTION_RECEIPT_{field.upper()}_MISMATCH"
                )


@dataclass(frozen=True)
class GlobalSellReceiptClosure:
    """Typed, immutable binding from a global SELL command to its receipt.

    The receipt reference is deliberately repeated alongside the command
    identity.  This makes the command boundary self-describing while the
    duplicated winner fields provide a cheap, strict anti-confusion check.
    """

    receipt_ref: GlobalAuctionReceiptRef
    position_id: str
    condition_id: str
    token_id: str
    action: str
    execution_mode: str
    winner_event_id: str
    winner_candidate_id: str
    winner_actuation_identity: str
    selection_epoch_identity: str

    def __post_init__(self) -> None:
        if type(self.receipt_ref) is not GlobalAuctionReceiptRef:
            raise ValueError("GLOBAL_SELL_RECEIPT_REF_INVALID")
        for field in ("position_id", "condition_id", "token_id"):
            value = getattr(self, field)
            if type(value) is not str or not value.strip():
                raise ValueError(f"GLOBAL_SELL_RECEIPT_{field.upper()}_INVALID")
        if type(self.action) is not str or self.action != "SELL":
            raise ValueError("GLOBAL_SELL_RECEIPT_ACTION_INVALID")
        if type(self.execution_mode) is not str or self.execution_mode not in GLOBAL_SELL_EXECUTION_MODES:
            raise ValueError("GLOBAL_SELL_RECEIPT_EXECUTION_MODE_INVALID")
        for field in (
            "winner_event_id",
            "winner_candidate_id",
            "winner_actuation_identity",
            "selection_epoch_identity",
        ):
            value = getattr(self, field)
            if type(value) is not str or not value.strip():
                raise ValueError(f"GLOBAL_SELL_RECEIPT_{field.upper()}_INVALID")
        if self.winner_event_id != self.receipt_ref.winner_event_id:
            raise ValueError("GLOBAL_SELL_RECEIPT_WINNER_EVENT_ID_MISMATCH")
        if self.winner_candidate_id != self.receipt_ref.winner_candidate_id:
            raise ValueError("GLOBAL_SELL_RECEIPT_WINNER_CANDIDATE_ID_MISMATCH")
        if self.winner_actuation_identity != self.receipt_ref.winner_actuation_identity:
            raise ValueError("GLOBAL_SELL_RECEIPT_WINNER_ACTUATION_IDENTITY_MISMATCH")
        if self.selection_epoch_identity != self.receipt_ref.selection_epoch_identity:
            raise ValueError("GLOBAL_SELL_RECEIPT_SELECTION_EPOCH_IDENTITY_MISMATCH")

    def as_payload(self) -> dict[str, object]:
        return {
            "receipt_ref": self.receipt_ref.as_payload(),
            "position_id": self.position_id,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "action": self.action,
            "execution_mode": self.execution_mode,
            "winner_event_id": self.winner_event_id,
            "winner_candidate_id": self.winner_candidate_id,
            "winner_actuation_identity": self.winner_actuation_identity,
            "selection_epoch_identity": self.selection_epoch_identity,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "GlobalSellReceiptClosure":
        if not isinstance(payload, Mapping):
            raise ValueError("GLOBAL_SELL_RECEIPT_CLOSURE_MISSING")
        expected = {
            "receipt_ref",
            "position_id",
            "condition_id",
            "token_id",
            "action",
            "execution_mode",
            "winner_event_id",
            "winner_candidate_id",
            "winner_actuation_identity",
            "selection_epoch_identity",
        }
        if set(payload) != expected:
            raise ValueError("GLOBAL_SELL_RECEIPT_CLOSURE_FIELDS_INVALID")
        return cls(
            receipt_ref=GlobalAuctionReceiptRef.from_payload(payload["receipt_ref"]),
            position_id=payload["position_id"],
            condition_id=payload["condition_id"],
            token_id=payload["token_id"],
            action=payload["action"],
            execution_mode=payload["execution_mode"],
            winner_event_id=payload["winner_event_id"],
            winner_candidate_id=payload["winner_candidate_id"],
            winner_actuation_identity=payload["winner_actuation_identity"],
            selection_epoch_identity=payload["selection_epoch_identity"],
        )

    def assert_matches_command(
        self,
        *,
        position_id: object,
        token_id: object,
        side: object,
        envelope: object,
    ) -> None:
        """Require exact command/envelope identity and execution mode."""

        if type(position_id) is not str or position_id != self.position_id:
            raise ValueError("GLOBAL_SELL_RECEIPT_POSITION_ID_MISMATCH")
        if type(token_id) is not str or token_id != self.token_id:
            raise ValueError("GLOBAL_SELL_RECEIPT_TOKEN_ID_MISMATCH")
        if type(side) is not str or side != self.action:
            raise ValueError("GLOBAL_SELL_RECEIPT_SIDE_MISMATCH")
        if envelope is None:
            raise ValueError("GLOBAL_SELL_RECEIPT_ENVELOPE_MISSING")
        def _field(name: str) -> object:
            if isinstance(envelope, Mapping):
                return envelope.get(name)
            # sqlite3.Row is mapping-like but does not register as Mapping and
            # exposes values through subscription rather than attributes.
            keys = getattr(envelope, "keys", None)
            if callable(keys) and name in keys():
                return envelope[name]
            return getattr(envelope, name, None)
        if str(_field("condition_id") or "") != self.condition_id:
            raise ValueError("GLOBAL_SELL_RECEIPT_CONDITION_ID_MISMATCH")
        if str(_field("selected_outcome_token_id") or "") != self.token_id:
            raise ValueError("GLOBAL_SELL_RECEIPT_ENVELOPE_TOKEN_ID_MISMATCH")
        if str(_field("side") or "") != self.action:
            raise ValueError("GLOBAL_SELL_RECEIPT_ENVELOPE_SIDE_MISMATCH")
        order_type = str(_field("order_type") or "").strip().upper()
        raw_post_only = _field("post_only")
        if type(raw_post_only) is bool:
            post_only = raw_post_only
        elif type(raw_post_only) is int and raw_post_only in (0, 1):
            post_only = bool(raw_post_only)
        else:
            raise ValueError("GLOBAL_SELL_RECEIPT_POST_ONLY_INVALID")
        if self.execution_mode == "TAKER_LIMIT":
            valid = order_type == "FAK" and not post_only
        else:
            valid = order_type in {"GTC", "GTD"} and post_only
        if not valid:
            raise ValueError("GLOBAL_SELL_RECEIPT_EXECUTION_MODE_MISMATCH")

    # Alias retained for callers that name this operation as a binding check.
    assert_command_binding = assert_matches_command


def global_auction_receipt_ref_from_summary(
    *,
    decision_log_id: int,
    decision_log_mode: str,
    summary: Mapping[str, Any],
) -> GlobalAuctionReceiptRef:
    """Validate a stored winner summary and construct its exact row reference.

    ``receipt_hash`` is the content identity of the uncompressed logical receipt
    and is copied into delta/duplicate rows. ``execution_binding_hash`` is the
    locally recomputable closure over every field needed to bind the actionable
    winner to that logical cut.
    """

    schema_version = summary.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_SCHEMA_VERSION_INVALID")
    assert_global_auction_summary_integrity(summary)
    stored_binding_hash = _hash_text(
        summary.get("execution_binding_hash"), "execution_binding_hash"
    )
    return GlobalAuctionReceiptRef(
        decision_log_id=decision_log_id,
        decision_log_mode=decision_log_mode,
        receipt_hash=_hash_text(summary.get("receipt_hash"), "receipt_hash"),
        execution_binding_hash=stored_binding_hash,
        artifact_summary_hash=_hash_text(
            summary.get("artifact_summary_hash"), "artifact_summary_hash"
        ),
        schema_version=schema_version,
        winner_event_id=_required_text(
            summary.get("winner_event_id"), "winner_event_id"
        ),
        winner_candidate_id=_required_text(
            summary.get("winner_candidate_id"), "winner_candidate_id"
        ),
        winner_actuation_identity=_required_text(
            summary.get("winner_actuation_identity"),
            "winner_actuation_identity",
        ),
        selection_epoch_identity=_required_text(
            summary.get("selection_epoch_identity"),
            "selection_epoch_identity",
        ),
    )


def assert_global_auction_receipt_artifact(
    *,
    expected: GlobalAuctionReceiptRef,
    decision_log_id: int,
    decision_log_mode: str,
    artifact_json: object,
) -> None:
    """Re-read one decision_log row and require exact certificate equality."""

    actual = global_auction_receipt_ref_from_artifact(
        decision_log_id=decision_log_id,
        decision_log_mode=decision_log_mode,
        artifact_json=artifact_json,
    )
    if actual != expected:
        raise ValueError("GLOBAL_AUCTION_RECEIPT_REF_MISMATCH")


def global_auction_receipt_ref_from_artifact(
    *,
    decision_log_id: int,
    decision_log_mode: str,
    artifact_json: object,
) -> GlobalAuctionReceiptRef:
    """Parse one decision_log artifact and validate its compact winner binding."""

    try:
        artifact = (
            json.loads(artifact_json)
            if isinstance(artifact_json, str)
            else artifact_json
        )
        summary = artifact["summary"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("GLOBAL_AUCTION_RECEIPT_ARTIFACT_INVALID") from exc
    if not isinstance(summary, Mapping):
        raise ValueError("GLOBAL_AUCTION_RECEIPT_SUMMARY_INVALID")
    return global_auction_receipt_ref_from_summary(
        decision_log_id=decision_log_id,
        decision_log_mode=decision_log_mode,
        summary=summary,
    )
