# Created: 2026-04-18
# Last reused or audited: 2026-05-18
# Authority basis: phase6_contract.md R-BA..R-BD, day0_signal_router.py reference skeleton

# LIMITATION (F3 PR 3 — Path A pattern from PR #170, operator-confirmed 2026-05-18):
# NewType does NOT block `Celsius + Fahrenheit` arithmetic. Hot-loop math sites
# inside this module intentionally retain `float` annotation; the typed boundary
# protects callers from passing untyped values. Full category-impossibility
# requires frozen-dataclass wrappers — deferred to feasibility study (#119).
#
# Signal/Evaluator layer boundary note (F3 PR 3, 2026-05-18):
# Temperature values in Day0SignalInputs and signal classes (observed_high_so_far,
# observed_low_so_far, current_temp, member_maxes_remaining, etc.) are intentionally
# annotated as `float` — NOT `Celsius` or `Fahrenheit` — because these parameters
# are unit-polymorphic at runtime: Dallas markets flow °F, London markets flow °C
# through the same code paths. The unit is carried as a separate `unit: str = "F"`
# field. Statically annotating these as `Celsius` would be incorrect for Fahrenheit
# cities and would generate a false type error at Fahrenheit call sites.
#
# The NewType migration (PR #170 contracts layer, PR #171 ingest layer) applies only
# to sites that are STATICALLY unit-stable: METAR is always °C, WU adapters are
# labelled at the request boundary. No such static site exists in the signal or
# evaluator layers — the typed boundary is enforced by the ingest adapters upstream.
# See src/types/temperature.py for the Celsius/Fahrenheit NewType definitions.
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
                observation_source=inputs.observation_source,
                observation_time=inputs.observation_time,
                current_utc_timestamp=inputs.current_utc_timestamp,
                temporal_context=inputs.temporal_context,
                round_fn=inputs.round_fn,
                precision=inputs.precision,
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
