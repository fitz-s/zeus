# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Authority basis: phase6_contract.md R-BA..R-BD, day0_signal_router.py reference skeleton
"""Day0Router — routes Day0SignalInputs to Day0HighSignal or Day0LowNowcastSignal.

Causality gate: LOW + causality_status not in {OK, N/A_CAUSAL_DAY_ALREADY_STARTED} → raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import numpy as np

from src.signal.day0_high_signal import Day0HighSignal
from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal
from src.types.metric_identity import MetricIdentity

if TYPE_CHECKING:
    from src.types import Day0TemporalContext, SolarDay

_LOW_ALLOWED_CAUSALITY = frozenset({"OK", "N/A_CAUSAL_DAY_ALREADY_STARTED"})


@dataclass(frozen=True)
class Day0SignalInputs:
    """Full-fidelity inputs for Day0 signal routing.

    Carries all fields needed by both simple (R-BA..R-BG tests) and rich
    (evaluator/monitor_refresh callsites) consumers. Rich fields default to None.
    """
    temperature_metric: MetricIdentity
    current_temp: float
    hours_remaining: float
    observed_high_so_far: float | None
    observed_low_so_far: float | None
    member_maxes_remaining: np.ndarray | None
    member_mins_remaining: np.ndarray | None
    causality_status: str = "OK"
    unit: str = "F"
    # Rich fields used by evaluator/monitor callsites
    observation_source: str = ""
    observation_time: str | None = None
    current_utc_timestamp: str | None = None
    temporal_context: "Day0TemporalContext | None" = None
    round_fn: "Callable | None" = None
    precision: float = 1.0


class Day0Router:
    """Central Day0 dispatcher. Replaces direct Day0Signal construction at callsites."""

    @staticmethod
    def route(inputs: Day0SignalInputs) -> Day0HighSignal | Day0LowNowcastSignal:
        if inputs.temperature_metric.is_low():
            if inputs.causality_status not in _LOW_ALLOWED_CAUSALITY:
                raise ValueError(
                    f"Unsupported LOW Day0 causality_status: {inputs.causality_status!r}. "
                    f"Allowed: {sorted(_LOW_ALLOWED_CAUSALITY)}"
                )
            return Day0LowNowcastSignal(
                observed_low_so_far=inputs.observed_low_so_far,  # type: ignore[arg-type]
                member_mins_remaining=inputs.member_mins_remaining,  # type: ignore[arg-type]
                current_temp=inputs.current_temp,
                hours_remaining=inputs.hours_remaining,
                unit=inputs.unit,
            )
        return Day0HighSignal(
            observed_high_so_far=inputs.observed_high_so_far,  # type: ignore[arg-type]
            member_maxes_remaining=inputs.member_maxes_remaining,  # type: ignore[arg-type]
            current_temp=inputs.current_temp,
            hours_remaining=inputs.hours_remaining,
            unit=inputs.unit,
            observation_source=inputs.observation_source,
            observation_time=inputs.observation_time,
            current_utc_timestamp=inputs.current_utc_timestamp,
            temporal_context=inputs.temporal_context,
            round_fn=inputs.round_fn,
            precision=inputs.precision,
        )
