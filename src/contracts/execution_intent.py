from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping

from src.contracts.semantic_types import Direction

if TYPE_CHECKING:
    from src.contracts.slippage_bps import SlippageBps


_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def _context_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text


def _context_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_payload_hash(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX_CHARS for char in value)


@dataclass(frozen=True)
class DecisionSourceContext:
    """Compact decision-source evidence consumed by execution capability proof.

    This is intentionally not a source-routing policy. Evaluator decides source
    authority before selection; execution only verifies that the accepted
    decision's source/timing evidence survived the handoff to live submit.
    """

    source_id: str = ""
    model_family: str = ""
    forecast_issue_time: str = ""
    forecast_valid_time: str = ""
    forecast_fetch_time: str = ""
    forecast_available_at: str = ""
    raw_payload_hash: str = ""
    degradation_level: str = ""
    forecast_source_role: str = ""
    authority_tier: str = ""
    decision_time: str = ""
    decision_time_status: str = ""

    @classmethod
    def from_forecast_context(cls, context: Mapping[str, object] | None) -> "DecisionSourceContext | None":
        if not isinstance(context, Mapping):
            return None
        return cls(
            source_id=_context_text(context.get("forecast_source_id") or context.get("source_id")),
            model_family=_context_text(context.get("model_family") or context.get("model")),
            forecast_issue_time=_context_text(context.get("forecast_issue_time") or context.get("issue_time")),
            forecast_valid_time=_context_text(context.get("forecast_valid_time") or context.get("valid_time")),
            forecast_fetch_time=_context_text(context.get("forecast_fetch_time") or context.get("fetch_time")),
            forecast_available_at=_context_text(context.get("forecast_available_at") or context.get("available_at")),
            raw_payload_hash=_context_text(context.get("raw_payload_hash")),
            degradation_level=_context_text(context.get("degradation_level")),
            forecast_source_role=_context_text(context.get("forecast_source_role")),
            authority_tier=_context_text(context.get("authority_tier")),
            decision_time=_context_text(context.get("decision_time") or context.get("decision_time_utc")),
            decision_time_status=_context_text(context.get("decision_time_status")),
        )

    def integrity_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        required_fields = {
            "source_id": self.source_id,
            "model_family": self.model_family,
            "forecast_issue_time": self.forecast_issue_time,
            "forecast_valid_time": self.forecast_valid_time,
            "forecast_fetch_time": self.forecast_fetch_time,
            "forecast_available_at": self.forecast_available_at,
            "raw_payload_hash": self.raw_payload_hash,
            "degradation_level": self.degradation_level,
            "forecast_source_role": self.forecast_source_role,
            "authority_tier": self.authority_tier,
            "decision_time": self.decision_time,
            "decision_time_status": self.decision_time_status,
        }
        for field, value in required_fields.items():
            if not value:
                errors.append(f"missing_{field}")

        if self.raw_payload_hash and not _valid_payload_hash(self.raw_payload_hash):
            errors.append("invalid_raw_payload_hash")

        parsed_times = {
            "forecast_issue_time": _context_time(self.forecast_issue_time),
            "forecast_valid_time": _context_time(self.forecast_valid_time),
            "forecast_fetch_time": _context_time(self.forecast_fetch_time),
            "forecast_available_at": _context_time(self.forecast_available_at),
            "decision_time": _context_time(self.decision_time),
        }
        for field, parsed in parsed_times.items():
            if required_fields.get(field) and parsed is None:
                errors.append(f"invalid_{field}")

        decision_time = parsed_times["decision_time"]
        issue_time = parsed_times["forecast_issue_time"]
        fetch_time = parsed_times["forecast_fetch_time"]
        available_at = parsed_times["forecast_available_at"]
        if decision_time is not None:
            if issue_time is not None and issue_time > decision_time:
                errors.append("forecast_issue_after_decision")
            if fetch_time is not None and fetch_time > decision_time:
                errors.append("forecast_fetch_after_decision")
            if available_at is not None and available_at > decision_time:
                errors.append("forecast_available_after_decision")
        if issue_time is not None and fetch_time is not None and issue_time > fetch_time:
            errors.append("forecast_issue_after_fetch_time")
        if issue_time is not None and available_at is not None and issue_time > available_at:
            errors.append("forecast_issue_after_available_at")
        if available_at is not None and fetch_time is not None and available_at > fetch_time:
            errors.append("forecast_available_after_fetch_time")

        if self.forecast_source_role and self.forecast_source_role != "entry_primary":
            errors.append(f"forecast_role_not_entry_primary:{self.forecast_source_role}")
        if self.degradation_level and self.degradation_level != "OK":
            errors.append(f"forecast_degraded:{self.degradation_level}")
        if self.authority_tier and self.authority_tier != "FORECAST":
            errors.append(f"authority_not_forecast:{self.authority_tier}")
        if self.decision_time_status and self.decision_time_status != "OK":
            errors.append(f"decision_time_status_not_ok:{self.decision_time_status}")

        return tuple(errors)

    def capability_details(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "model_family": self.model_family,
            "forecast_issue_time": self.forecast_issue_time,
            "forecast_valid_time": self.forecast_valid_time,
            "forecast_fetch_time": self.forecast_fetch_time,
            "forecast_available_at": self.forecast_available_at,
            "raw_payload_hash": self.raw_payload_hash,
            "degradation_level": self.degradation_level,
            "forecast_source_role": self.forecast_source_role,
            "authority_tier": self.authority_tier,
            "decision_time": self.decision_time,
            "decision_time_status": self.decision_time_status,
        }


@dataclass(frozen=True)
class ExecutionIntent:
    """Replaces loose size_usd passing and limits. 
    
    Contains toxicity budgets, sandbox flags, and 
    everything risk-related from the Adverse Execution Plane.
    """
    direction: Direction
    target_size_usd: float
    limit_price: float
    toxicity_budget: float
    # Slice P3.3 (PR #19 phase 3, 2026-04-26): typed slippage budget.
    # Pre-fix `max_slippage: float` was unit-ambiguous (caller read 0.02 as
    # either 0.02 bps or 2% — the type system couldn't distinguish) AND
    # had zero readers in src/, making it a dead budget that nobody
    # enforced. Promoting to SlippageBps gives the magnitude an explicit
    # unit (bps) and an explicit direction semantic (adverse limit).
    # Enforcement (actually rejecting fills above this budget) remains a
    # separate follow-on packet — P3.3 closes the typing seam first.
    max_slippage: "SlippageBps"
    is_sandbox: bool
    market_id: str
    token_id: str
    timeout_seconds: int
    decision_edge: float = 0.0  # T5.a 2026-04-23: field was read at src/execution/executor.py:136,428 but missing from dataclass, latent TypeError on live entry; paired default maintains backward compatibility.
    # U1: executable CLOB snapshot citation required by venue_command_repo
    # before persistence/submission. Do not populate this from forecast
    # decision_snapshot_id; it is market/execution truth, not model truth.
    executable_snapshot_id: str = ""
    executable_snapshot_min_tick_size: Decimal | str | None = None
    executable_snapshot_min_order_size: Decimal | str | None = None
    executable_snapshot_neg_risk: bool | None = None
    # R3 A2 allocation metadata. These fields are intentionally typed on the
    # production intent boundary so per-event / per-resolution-window /
    # correlated-exposure caps do not depend on test-only dynamic attributes.
    event_id: str = ""
    resolution_window: str = "default"
    correlation_key: str = ""
    decision_source_context: DecisionSourceContext | None = None

    def __post_init__(self) -> None:
        # Slice P3-fix1 (post-review BLOCKER from critic M1 + code-reviewer
        # M3, 2026-04-26): runtime isinstance check on max_slippage.
        # Pre-fix1, the type annotation was a string (forward-ref via
        # TYPE_CHECKING), so passing a raw float silently stored as float
        # — the typing seam was illusory. Now the dataclass refuses
        # construction at runtime; the antibody is universal across
        # production callers + every test fixture.
        from src.contracts.slippage_bps import SlippageBps
        if not isinstance(self.max_slippage, SlippageBps):
            raise TypeError(
                f"ExecutionIntent.max_slippage must be SlippageBps, "
                f"got {type(self.max_slippage).__name__} "
                f"(value={self.max_slippage!r}). Per P3.3 + P3-fix1, "
                f"raw floats are no longer accepted at the boundary."
            )
        normalized_event = str(self.event_id or self.market_id or "").strip()
        normalized_window = str(self.resolution_window or "default").strip() or "default"
        normalized_correlation = str(self.correlation_key or normalized_event or self.market_id or "").strip()
        object.__setattr__(self, "event_id", normalized_event)
        object.__setattr__(self, "resolution_window", normalized_window)
        object.__setattr__(self, "correlation_key", normalized_correlation)
        decision_source_context = self.decision_source_context
        if decision_source_context is not None and not isinstance(
            decision_source_context, DecisionSourceContext
        ):
            if isinstance(decision_source_context, Mapping):
                decision_source_context = DecisionSourceContext.from_forecast_context(
                    decision_source_context
                )
            if not isinstance(decision_source_context, DecisionSourceContext):
                raise TypeError(
                    "ExecutionIntent.decision_source_context must be "
                    "DecisionSourceContext or forecast-context mapping"
                )
            object.__setattr__(
                self,
                "decision_source_context",
                decision_source_context,
            )
