# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator delta-package v2 (real_upgrade #3) — pre-submit Day0 live admission
#   circuit breakers. These do NOT change q, edge, or Kelly; they decide whether an immature Day0
#   live lane is allowed to submit live capital. Applied in the final submit path, AFTER event
#   binding / selected proof and BEFORE Kelly / final intent. Scoped to DAY0_EXTREME_UPDATED events
#   only — non-day0 candidates pass through untouched (returns None).
"""day0_live_admission_rejection_reason — promotion circuit breakers for the Day0 live lane.

Pure predicate over assembled facts: returns a rejection-reason string (the FIRST failing gate) or
None when the candidate is admissible. The caller assembles the context from existing systems and,
on a non-None reason, records a NO-submit receipt with that reason instead of submitting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

DAY0_EVENT_TYPE = "DAY0_EXTREME_UPDATED"

# METAR fast-lane native settlement source types (HKO/NOAA/CWA are not WU-ICAO METAR-native).
_METAR_NATIVE_SOURCE_TYPES = frozenset({"wu_icao"})
# Execution modes that count as maker (resting) entry.
_MAKER_MODES = frozenset({"maker", "maker_only", "post_only", "rest", "rest_then_cross_pending"})


@dataclass(frozen=True, slots=True)
class Day0AdmissionContext:
    event_type: str
    metric: str
    settlement_source_type: str
    fast_obs_supported: bool
    source_health_state: str
    execution_mode: str
    # quote vs observation publication clock
    quote_time_utc: datetime | None
    latest_observation_available_at_utc: datetime | None
    # window flag (computed by the caller from temporal context; M-3 2026-07-18:
    # in_post_extreme_quiet_window was removed here — see gate 6 comment below)
    in_final_localday_noentry_window: bool
    # one-bin-edge fragility
    selected_bin_edge_distance_quanta: float
    edge_survives_one_bin_stress: bool
    # stage policy (the caller supplies the current stage's admissible metric/health set;
    # M-13 2026-07-19: city_allowlist REMOVED here — see the deleted gate 1 comment below)
    metric_allowlist: frozenset[str] = field(default_factory=lambda: frozenset({"high", "low"}))
    allowed_health_states: frozenset[str] = field(default_factory=lambda: frozenset({"OK_FAST_AND_WU", "OK_FAST_ONLY"}))
    maker_only_required: bool = True


def day0_live_admission_rejection_reason(ctx: Day0AdmissionContext) -> str | None:
    """First failing admission gate (a stable reason string) or None if admissible.

    Only DAY0_EXTREME_UPDATED candidates are gated; everything else returns None (not applicable).
    """
    if ctx.event_type != DAY0_EVENT_TYPE:
        return None

    # 1) city not allowlisted for the current stage.
    #
    # M-13 (receipt-persistence audit 2026-07-19, docs/evidence/capital_efficiency_2026_07_19/
    # nosubmit_gates.md): DELETED. The sole live call site
    # (event_reactor_adapter.py:_day0_live_submit_admission_rejection_reason) built
    # city_allowlist=frozenset({the candidate's own city}) — a tautology that can never
    # reject anything, the exact §3B "guard exists, tested in isolation, does nothing"
    # pattern the Day0 first-principles audit (M-3 above) already condemned once. Two
    # findings ruled out repair-in-place:
    #   (a) No real "stage" concept exists to source a genuine per-city allowlist from.
    #       config/settings.json's edli section has no per-city Day0 field at all; the only
    #       scope knob, edli_live_scope, is a LANE-TYPE switch (forecast vs day0), not a city
    #       list.
    #   (b) The operator explicitly killed staged/canary Day0 rollout twice, BEFORE this
    #       module was even created: edli_live_scope's own notes record 2026-06-09 ("全部打开"
    #       — promote Day0 into forecast_plus_day0) and 2026-06-12 ("现在就解除这些限制" — day0
    #       LIVE NOW, no shadow staging, no canary, "the 06-10 interim revert is over"). This
    #       module's authority-basis header dates it 2026-06-17 — five days AFTER that final
    #       word — so a per-city stage gate was never a live policy this module could have
    #       inherited; it was speculative scaffolding for a promotion design the operator had
    #       already foreclosed. Wiring a "defaults to all configured cities" allowlist instead
    #       of deleting would resurrect exactly the canary/staging machinery the operator
    #       twice rejected, just with an always-true default standing in for it.
    # metric_allowlist and allowed_health_states are UNCHANGED — both are real, currently-
    # exercised gates (metric/health do vary per candidate; city never could here), so gate 1
    # is the only tautology in this predicate.
    #
    # 2) metric not in the current stage set.
    if ctx.metric not in ctx.metric_allowlist:
        return "DAY0_METRIC_NOT_IN_STAGE"

    # 3) WU-ICAO METAR stage requires a fast-obs source for the city.
    if ctx.settlement_source_type in _METAR_NATIVE_SOURCE_TYPES and not ctx.fast_obs_supported:
        return "DAY0_FAST_OBS_UNSUPPORTED"

    # 4) source health not in the stage's admissible set.
    if ctx.source_health_state not in ctx.allowed_health_states:
        return "DAY0_SOURCE_HEALTH_NOT_ADMISSIBLE"

    # 5) quote must be STRICTLY newer than the latest observation it prices against.
    # M-12 (audit 2026-07-18): equality rejects too — a quote captured at the same
    # instant as the observation availability cannot have priced the post-update
    # book. This is the ordering property the retired day0_input_correctness module
    # specified (quote > observation, strict); the live gate now carries it.
    if ctx.quote_time_utc is None:
        return "DAY0_QUOTE_TIME_MISSING"
    if (
        ctx.latest_observation_available_at_utc is not None
        and ctx.quote_time_utc <= ctx.latest_observation_available_at_utc
    ):
        return "DAY0_QUOTE_STALE_VS_OBSERVATION"

    # 6) selected bin one rounding quantum from death and the edge does not survive a one-bin stress.
    #
    # M-3 (audit 2026-07-18): a former gate 6, `in_post_extreme_quiet_window`
    # ("let the absorbing update settle before pricing"), was hardcoded False
    # at the sole live call site and DELETED here rather than wired up.
    # Judgment: its original intent is now covered by two gates that did not
    # exist when it was written — the strict quote>observation ordering gate
    # above (commit 7eb03a29a) proves the QUOTE itself was captured after the
    # extreme's publication, and the submit-time hard-fact re-check at the
    # live call site (H-2, commit ceb55a796) re-derives bin-aliveness against
    # the CURRENT durable extreme at the exact submit instant — including any
    # settlement-source revision that has already landed in the DB by then. A
    # fixed N-minute "quiet window" would be a strictly weaker, heuristic
    # stand-in for what the re-check already proves exactly; keeping a dead
    # field that can never legitimately be wired to anything stronger than
    # what gate 6/H-2 already do would just be another guard that exists,
    # is tested in isolation, and does nothing (the audit's own §3B pattern).
    if ctx.selected_bin_edge_distance_quanta <= 1.0 and not ctx.edge_survives_one_bin_stress:
        return "DAY0_ONE_BIN_EDGE_FRAGILE"

    # 7) inside the final local-day no-entry window.
    if ctx.in_final_localday_noentry_window:
        return "DAY0_FINAL_LOCALDAY_NOENTRY"

    # 8) maker-only entry until the lane is calibrated (taker/auto-cross entry forbidden).
    if ctx.maker_only_required and ctx.execution_mode not in _MAKER_MODES:
        return "DAY0_TAKER_ENTRY_FORBIDDEN"

    return None
