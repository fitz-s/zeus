"""Pure EDLI live bin inference state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

UTC = timezone.utc


class LiveInferenceBlocked(ValueError):
    """Raised when an event cannot enter live inference."""


@dataclass(frozen=True)
class LiveBinState:
    probabilities: dict[str, float]
    as_of: datetime

    def normalized(self) -> "LiveBinState":
        return LiveBinState(normalize_probabilities(self.probabilities), self.as_of)


def normalize_probabilities(values: Mapping[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value)) for value in values.values())
    if total <= 0:
        raise LiveInferenceBlocked("zero probability mass after boundary")
    return {key: max(0.0, float(value)) / total for key, value in values.items()}


def assert_available_at(*, available_at: datetime, decision_time: datetime) -> None:
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        raise LiveInferenceBlocked("available_at must be timezone-aware")
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise LiveInferenceBlocked("decision_time must be timezone-aware")
    if available_at.astimezone(UTC) > decision_time.astimezone(UTC):
        raise LiveInferenceBlocked("available_at after decision_time")


def apply_day0_mask(state: LiveBinState, mask: Mapping[str, float]) -> LiveBinState:
    return LiveBinState(
        normalize_probabilities(
            {key: state.probabilities.get(key, 0.0) * float(mask.get(key, 0.0)) for key in state.probabilities}
        ),
        state.as_of,
    )


def apply_orderbook_event_v1(state: LiveBinState, _event: object) -> LiveBinState:
    """Orderbook data is executable-cost evidence in EDLI v1; it does not change q."""

    return state
