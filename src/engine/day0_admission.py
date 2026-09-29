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

# Gate 10 (ask-repricing veto) shape. The lookback ends AT the decision instant and
# excludes it: [T - window, T). The count is of DISTINCT top-ask values the held token
# showed in that window, so a book that never moved counts 1 and can never veto.
DAY0_ASK_REPRICING_WINDOW_MINUTES = 10
DAY0_ASK_REPRICING_MIN_DISTINCT = 2


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
    # ask-repricing veto (gate 10). The number of DISTINCT top-ask values the HELD
    # token showed in [T - 10 min, T), T being the sealed book's capture instant. None
    # on every candidate the trade DB cannot be read for (no connection, no token id,
    # no snapshots in the window) — the gate is then inert, its dormant default.
    held_ask_distinct_count_10min: int | None = None
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
    #       config/settings.json's edli section has no per-city Day0 field from
    #       which a genuine allowlist could be derived.
    #   (b) The operator explicitly killed staged/probe Day0 rollout twice, BEFORE this
    #       module was even created: 2026-06-09 ("全部打开") and 2026-06-12
    #       ("现在就解除这些限制") made Day0 live with no parallel observation stage. This
    #       module's authority-basis header dates it 2026-06-17 — five days AFTER that final
    #       word — so a per-city stage gate was never a live policy this module could have
    #       inherited; it was speculative scaffolding for a promotion design the operator had
    #       already foreclosed. Wiring a "defaults to all configured cities" allowlist instead
    #       of deleting would resurrect exactly the probe/staging machinery the operator
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

    # 9) DELETED 2026-09-29: the station diurnal-residual nowcast veto. Evidence that
    # improves the probability belongs in the probability: the residual now enters every
    # Day0 simplex row as the survival-preserving mixture of
    # src/calibration/day0_diurnal_residual.py (docs/authority/
    # replacement_final_form_2026_06_09.md §1e), so a selected winner is priced with it
    # and is never rejected by it afterwards.

    # 10) the held token's ask was already being repriced in the 10 minutes before we
    # decided — we are lifting an ask the market is currently walking away from.
    #
    # Measured on window A (2026-07-20 -> 2026-09-04, tx_hash-deduped fills, chain
    # truth, cluster bootstrap by decision date). The day0_nowcast_entry lane loses as a
    # whole: n=315, net -$489, 6/7 ISO weeks negative. Split on this predicate it is not
    # one lane but two populations:
    #
    #   distinct asks >= 2  n=95   net -$382.53 on $678.89   net/cost -0.563   7/7 weeks -
    #   distinct asks <  2  n=220  net -$106.57 on $1447.66  net/cost -0.074
    #
    # The removed set is 78% of the lane's loss on 32% of its entries; its 5% bootstrap
    # LCB is -$646, so the sign is not a small-sample artifact. Train W30-33 / test
    # W34-36: the removed set is n=50, net -$179.12, net/cost -0.679, LCB -$290,
    # negative in 3/3 held-out weeks, and the kept set beats the full lane by +0.276
    # net/cost out of sample.
    #
    # WHY, not just what: post-fill markout for these entries keeps falling for hours
    # (240 m: -12.9c) while quiet-book entries are flat. The repricing IS the market
    # reacting to the same information we just acted on; by the time we cross, the ask
    # we lift is the stale side of a move already in progress. That is why the cut is
    # an ADMISSION rule and not an exit or sizing rule — the entry itself is the error.
    #
    # Threshold: >= 2 distinct values, i.e. the book moved at all. The stricter >= 3
    # alternative was measured on the same chain truth and is kept on record here:
    #
    #   >= 2  removes n=95  net -$382.53  (78% of loss)  LCB -$646  7/7 wk  test -$179.12
    #   >= 3  removes n=39  net -$270.40  (55% of loss)  LCB -$448  5/6 wk  test -$103.04
    #
    # >= 3 makes fewer false removals (5 vs 23), but those are dust and are already
    # netted into the figures above; >= 2 avoids $112 more loss pooled and $76 more in
    # the test window, so it is the threshold that ships.
    #
    # SCOPE is day0 by construction, not by choice: this predicate is only reachable
    # from the Day0 seam. It does NOT transfer — on forecast_qkernel_entry the same cut
    # removes a set that is POSITIVE out of sample (W34-36: n=29, +$16.85), so applying
    # it there would delete winners. The gate lives behind the DAY0_EVENT_TYPE guard at
    # the top of this function, which is what keeps that from happening.
    #
    # No count (no trade-DB read, no token id, no snapshots) leaves the gate inert.
    if (
        ctx.held_ask_distinct_count_10min is not None
        and ctx.held_ask_distinct_count_10min >= DAY0_ASK_REPRICING_MIN_DISTINCT
    ):
        return "DAY0_ASK_REPRICING_VETO"

    return None
