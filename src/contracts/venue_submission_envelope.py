# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
"""Polymarket V2 submission provenance envelope.

The envelope pins the evidence Zeus needs around a venue submission without
binding downstream phases to a particular SDK method shape.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Optional


_DECIMAL_FIELDS = {"tick_size", "min_order_size", "price", "size"}
_BYTES_FIELDS = {"signed_order"}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


@dataclass(frozen=True)
class VenueSubmissionEnvelope:
    """Immutable provenance contract for one Polymarket V2 submission."""

    SCHEMA_VERSION: ClassVar[int] = 1

    sdk_package: str
    sdk_version: str
    host: str
    chain_id: int
    funder_address: str
    condition_id: str
    question_id: str
    yes_token_id: str
    no_token_id: str
    selected_outcome_token_id: str
    outcome_label: str
    side: str
    price: Decimal
    size: Decimal
    order_type: str
    post_only: bool
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool
    fee_details: dict[str, Any]
    canonical_pre_sign_payload_hash: str
    signed_order: Optional[bytes]
    signed_order_hash: Optional[str]
    raw_request_hash: str
    raw_response_json: Optional[str]
    order_id: Optional[str]
    trade_ids: tuple[str, ...]
    transaction_hashes: tuple[str, ...]
    error_code: Optional[str]
    error_message: Optional[str]
    captured_at: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"VenueSubmissionEnvelope schema_version must be {self.SCHEMA_VERSION}, got {self.schema_version}"
            )
        if self.outcome_label not in {"YES", "NO"}:
            raise ValueError(f"outcome_label must be YES or NO, got {self.outcome_label!r}")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError(f"side must be BUY or SELL, got {self.side!r}")
        if len(self.canonical_pre_sign_payload_hash) != 64:
            raise ValueError("canonical_pre_sign_payload_hash must be sha256 hex")
        if len(self.raw_request_hash) != 64:
            raise ValueError("raw_request_hash must be sha256 hex")
        if self.signed_order_hash is not None and len(self.signed_order_hash) != 64:
            raise ValueError("signed_order_hash must be sha256 hex when present")

    def with_updates(self, **changes: Any) -> "VenueSubmissionEnvelope":
        return replace(self, **changes)

    @property
    def compatibility_placeholder_reason(self) -> str:
        reasons: list[str] = []
        if self.condition_id.startswith("legacy:"):
            reasons.append("legacy_condition_id")
        if self.question_id == "legacy-compat":
            reasons.append("legacy_question_id")
        if self.yes_token_id == self.no_token_id:
            reasons.append("collapsed_yes_no_token_identity")
        return ",".join(reasons)

    @property
    def is_compatibility_placeholder(self) -> bool:
        return bool(self.compatibility_placeholder_reason)

    def assert_live_submit_bound(self) -> None:
        """Fail closed unless the envelope is bound to real market identity."""

        reason = self.compatibility_placeholder_reason
        if reason:
            raise ValueError(
                "compatibility submission envelope cannot authorize live submit: "
                f"{reason}"
            )
        expected_token = self.yes_token_id if self.outcome_label == "YES" else self.no_token_id
        if self.selected_outcome_token_id != expected_token:
            raise ValueError(
                "selected_outcome_token_id does not match outcome_label token"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sdk_package": self.sdk_package,
            "sdk_version": self.sdk_version,
            "host": self.host,
            "chain_id": self.chain_id,
            "funder_address": self.funder_address,
            "condition_id": self.condition_id,
            "question_id": self.question_id,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "selected_outcome_token_id": self.selected_outcome_token_id,
            "outcome_label": self.outcome_label,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "order_type": self.order_type,
            "post_only": self.post_only,
            "tick_size": self.tick_size,
            "min_order_size": self.min_order_size,
            "neg_risk": self.neg_risk,
            "fee_details": self.fee_details,
            "canonical_pre_sign_payload_hash": self.canonical_pre_sign_payload_hash,
            "signed_order": self.signed_order,
            "signed_order_hash": self.signed_order_hash,
            "raw_request_hash": self.raw_request_hash,
            "raw_response_json": self.raw_response_json,
            "order_id": self.order_id,
            "trade_ids": list(self.trade_ids),
            "transaction_hashes": list(self.transaction_hashes),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "captured_at": self.captured_at,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            default=_json_default,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VenueSubmissionEnvelope":
        data = dict(payload)
        for field in _DECIMAL_FIELDS:
            if field in data and not isinstance(data[field], Decimal):
                data[field] = Decimal(str(data[field]))
        for field in _BYTES_FIELDS:
            if isinstance(data.get(field), str):
                data[field] = base64.b64decode(data[field].encode("ascii"))
        if "trade_ids" in data:
            data["trade_ids"] = tuple(data["trade_ids"] or ())
        if "transaction_hashes" in data:
            data["transaction_hashes"] = tuple(data["transaction_hashes"] or ())
        return cls(**data)

    @classmethod
    def from_json(cls, payload: str) -> "VenueSubmissionEnvelope":
        return cls.from_dict(json.loads(payload))
