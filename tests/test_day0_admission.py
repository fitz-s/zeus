# Created: 2026-06-17
# Last reused/audited: 2026-07-19
# Authority basis: operator delta-package v2 (real_upgrade #3) — Day0 live admission circuit breakers.
"""Contract tests for day0_live_admission_rejection_reason (7 gates + admit + bypass).

M-3 (Day0 first-principles audit 2026-07-18): `in_post_extreme_quiet_window`
(former gate 6) was deleted — see the commit body and day0_admission.py's gate
6 comment for why it was judged redundant with the strict quote>observation
ordering gate + the H-2 submit-time hard-fact re-check.

M-13 (receipt-persistence audit 2026-07-19): the former gate 1, city_allowlist,
was deleted along with the ``city``/``city_allowlist`` fields — see
day0_admission.py's deleted-gate-1 comment. The sole live call site built the
allowlist from the candidate's own city, so the gate could never fire; no real
per-city stage concept exists, and the operator's 06-09/06-12 directives already
foreclosed staged/canary Day0 rollout before this module was even created.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.engine.day0_admission import (
    Day0AdmissionContext,
    day0_live_admission_rejection_reason,
)

T = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)


def _ctx(**kw) -> Day0AdmissionContext:
    base = dict(
        event_type="DAY0_EXTREME_UPDATED", metric="high",
        settlement_source_type="wu_icao", fast_obs_supported=True,
        source_health_state="OK_FAST_AND_WU", execution_mode="maker",
        quote_time_utc=T, latest_observation_available_at_utc=T - timedelta(minutes=5),
        in_final_localday_noentry_window=False,
        selected_bin_edge_distance_quanta=3.0, edge_survives_one_bin_stress=True,
    )
    base.update(kw)
    return Day0AdmissionContext(**base)


def test_admissible_returns_none() -> None:
    assert day0_live_admission_rejection_reason(_ctx()) is None


def test_non_day0_event_bypasses() -> None:
    assert day0_live_admission_rejection_reason(_ctx(event_type="FORECAST_SNAPSHOT_READY")) is None


def test_low_metric_is_live_by_default() -> None:
    assert day0_live_admission_rejection_reason(_ctx(metric="low")) is None


def test_metric_not_in_stage_when_stage_override_excludes_it() -> None:
    assert (
        day0_live_admission_rejection_reason(
            _ctx(metric="low", metric_allowlist=frozenset({"high"}))
        )
        == "DAY0_METRIC_NOT_IN_STAGE"
    )


def test_fast_obs_unsupported() -> None:
    assert day0_live_admission_rejection_reason(_ctx(fast_obs_supported=False)) == "DAY0_FAST_OBS_UNSUPPORTED"


def test_source_health_not_admissible() -> None:
    assert day0_live_admission_rejection_reason(_ctx(source_health_state="OK_WU_ONLY")) == "DAY0_SOURCE_HEALTH_NOT_ADMISSIBLE"


def test_quote_time_missing() -> None:
    assert day0_live_admission_rejection_reason(_ctx(quote_time_utc=None)) == "DAY0_QUOTE_TIME_MISSING"


def test_quote_stale_vs_observation() -> None:
    stale = _ctx(quote_time_utc=T - timedelta(minutes=30), latest_observation_available_at_utc=T)
    assert day0_live_admission_rejection_reason(stale) == "DAY0_QUOTE_STALE_VS_OBSERVATION"


def test_quote_equal_to_observation_rejects_strict_ordering() -> None:
    # M-12: quote == observation availability cannot have priced the post-update
    # book; the ordering property is STRICT (quote > observation).
    equal = _ctx(quote_time_utc=T, latest_observation_available_at_utc=T)
    assert day0_live_admission_rejection_reason(equal) == "DAY0_QUOTE_STALE_VS_OBSERVATION"
    newer = _ctx(quote_time_utc=T, latest_observation_available_at_utc=T - timedelta(seconds=1))
    assert day0_live_admission_rejection_reason(newer) is None


def test_one_bin_edge_fragile() -> None:
    assert day0_live_admission_rejection_reason(
        _ctx(selected_bin_edge_distance_quanta=0.5, edge_survives_one_bin_stress=False)
    ) == "DAY0_ONE_BIN_EDGE_FRAGILE"
    # survives stress -> not rejected on this gate
    assert day0_live_admission_rejection_reason(
        _ctx(selected_bin_edge_distance_quanta=0.5, edge_survives_one_bin_stress=True)
    ) is None


def test_final_localday_noentry() -> None:
    assert day0_live_admission_rejection_reason(_ctx(in_final_localday_noentry_window=True)) == "DAY0_FINAL_LOCALDAY_NOENTRY"


def test_taker_entry_forbidden_until_calibrated() -> None:
    assert day0_live_admission_rejection_reason(_ctx(execution_mode="taker")) == "DAY0_TAKER_ENTRY_FORBIDDEN"
    assert day0_live_admission_rejection_reason(_ctx(execution_mode="auto_cross")) == "DAY0_TAKER_ENTRY_FORBIDDEN"
    # maker allowed; and if maker_only relaxed, taker passes this gate
    assert day0_live_admission_rejection_reason(_ctx(execution_mode="taker", maker_only_required=False)) is None


# ---------------------------------------------------------------------------
# Gate 9 DELETED 2026-09-29 — the diurnal-residual evidence moved INTO q
# (src/calibration/day0_diurnal_residual.py mixture). Antibody: the admission
# context cannot carry a nowcast verdict at all, so no post-selection predicate
# can reject a winner on it.


def test_admission_context_has_no_nowcast_veto_inputs() -> None:
    import dataclasses

    from src.contracts.rejection_reasons import is_registered_rejection_reason

    fields = {field.name for field in dataclasses.fields(Day0AdmissionContext)}
    assert not fields & {"nowcast_q_held", "nowcast_basis", "decision_price_held"}
    assert not is_registered_rejection_reason("DAY0_DIURNAL_NOWCAST_VETO")


# ---------------------------------------------------------------------------
# Gate 10 — the held-token ask-repricing veto (2026-09-05).
#
# Measured on window A (2026-07-20..09-04, chain truth, tx_hash-deduped fills):
# day0 entries whose HELD token's ask took >= 2 distinct values in the prior 10
# minutes are n=95 / net -$382.53 (net/cost -0.563, 7/7 ISO weeks negative), the
# rest n=220 / -$106.57. Post-fill markout keeps falling for hours on the former
# and is flat on the latter — we are lifting an ask the market is already walking
# away from. The COUNT is the whole gate: absent count, inert gate.


def test_ask_repricing_veto_fires_at_two_distinct_asks() -> None:
    assert (
        day0_live_admission_rejection_reason(_ctx(held_ask_distinct_count_10min=2))
        == "DAY0_ASK_REPRICING_VETO"
    )


def test_ask_repricing_veto_fires_above_the_threshold_too() -> None:
    # The rule is "the book moved at all", so more movement cannot admit.
    assert (
        day0_live_admission_rejection_reason(_ctx(held_ask_distinct_count_10min=7))
        == "DAY0_ASK_REPRICING_VETO"
    )


def test_ask_repricing_veto_silent_on_a_quiet_book() -> None:
    # One distinct ask means the book never repriced; zero means it was quoted
    # but never priced. Neither is evidence of a market moving away from us.
    assert day0_live_admission_rejection_reason(_ctx(held_ask_distinct_count_10min=1)) is None
    assert day0_live_admission_rejection_reason(_ctx(held_ask_distinct_count_10min=0)) is None


def test_ask_repricing_veto_inert_without_a_count() -> None:
    # No trade-DB read, no token id, or no snapshots in the window: the stamp
    # leaves the key absent and the gate must not invent a verdict from silence.
    assert day0_live_admission_rejection_reason(_ctx(held_ask_distinct_count_10min=None)) is None
    assert day0_live_admission_rejection_reason(_ctx()) is None


def test_ask_repricing_veto_runs_after_the_existing_gates() -> None:
    # Additive: a structural refusal still reports itself.
    assert (
        day0_live_admission_rejection_reason(
            _ctx(execution_mode="taker", held_ask_distinct_count_10min=5)
        )
        == "DAY0_TAKER_ENTRY_FORBIDDEN"
    )


def test_ask_repricing_veto_never_applies_off_the_day0_lane() -> None:
    # The cut does NOT transfer: on forecast_qkernel_entry the same predicate
    # removes an out-of-sample POSITIVE set. The event-type guard is what keeps
    # this predicate day0-only, so it is a contract, not an accident.
    assert (
        day0_live_admission_rejection_reason(
            _ctx(event_type="FORECAST_SNAPSHOT_READY", held_ask_distinct_count_10min=9)
        )
        is None
    )


def test_ask_repricing_veto_reason_is_registered_in_the_k2_taxonomy() -> None:
    from src.contracts.rejection_reasons import (
        RejectionCategory,
        classify_rejection_reason,
        is_registered_rejection_reason,
    )

    reason = day0_live_admission_rejection_reason(_ctx(held_ask_distinct_count_10min=2))
    assert is_registered_rejection_reason(reason)
    assert classify_rejection_reason(reason) is RejectionCategory.DESIGNED_GATE
    # Terminal no-submit receipt, not a fail-open requeue, once wrapped.
    wrapped = f"DAY0_LIVE_ADMISSION_REJECTED:{reason}"
    assert is_registered_rejection_reason(wrapped)
    assert classify_rejection_reason(wrapped) is RejectionCategory.DESIGNED_GATE


def test_ask_repricing_threshold_and_window_are_the_measured_ones() -> None:
    from src.engine.day0_admission import (
        DAY0_ASK_REPRICING_MIN_DISTINCT,
        DAY0_ASK_REPRICING_WINDOW_MINUTES,
    )

    assert DAY0_ASK_REPRICING_WINDOW_MINUTES == 10
    assert DAY0_ASK_REPRICING_MIN_DISTINCT == 2
