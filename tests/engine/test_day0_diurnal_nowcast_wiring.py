# Created: 2026-09-04
# Last reused or audited: 2026-09-19
# Authority basis: diurnal-residual study 2026-09-04 (REPORT.md §5) — the veto is only
#   real if the reactor actually assembles the nowcast context at the live submit seam
#   and stamps the verdict where an audit can find it.
"""Adapter-wiring tests for the Day0 diurnal-residual nowcast veto.

Covers the seam the unit tests cannot: the reactor reads the candidate's own running
extreme, local hour and bin label out of the payload it already holds (no DB read in the
hot path), stamps q/basis/fit_date onto the actionable payload BEFORE the certificate
seals it, and the admission predicate turns that stamp into the veto.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.engine.event_reactor_adapter as era
import src.engine.qkernel_spine_bridge as bridge
from src.calibration.day0_diurnal_residual import (
    J_MAX,
    SCHEMA_VERSION,
    DiurnalResidualNowcast,
)
from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.engine.event_reactor_adapter import (
    DAY0_NOWCAST_BASIS_KEY,
    DAY0_NOWCAST_FIT_DATE_KEY,
    DAY0_NOWCAST_Q_HELD_KEY,
    _day0_held_token_decision_price,
    _day0_live_submit_admission_rejection_reason,
    stamp_day0_diurnal_nowcast,
)
from src.engine.qkernel_spine_bridge import (
    Day0DiurnalNowcastCandidateBinding,
    Day0DiurnalNowcastContext,
)
from src.events.candidate_binding import (
    EventBoundCandidateFamily,
    MarketTopologyCandidate,
)
from src.solve.solver import OutcomeTokenBinding
from src.types.market import Bin

# Manila peaks at local 13 (Asia/Manila, UTC+8). 2026-07-02T02:20Z is local 10:20,
# so k = 13 - 10 = 3 — squarely inside the pre-peak cell the study localized.
DECISION_TIME = datetime(2026, 7, 2, 2, 20, tzinfo=timezone.utc)
FIT_DATE = "2026-07-01"


def _counts(**by_j: int) -> list[int]:
    counts = [0] * (J_MAX + 1)
    for key, value in by_j.items():
        counts[int(key[1:])] = value
    return counts


def _nowcast() -> DiurnalResidualNowcast:
    return DiurnalResidualNowcast(
        {
            "schema_version": SCHEMA_VERSION,
            "fit_date": FIT_DATE,
            "peak_hours": {"Manila": 13.0},
            "trough_hours": {"Manila": 3.0},
            "unit": {"Manila": "C"},
            # The floor bin (j=0) realises ~0.20 here: the study's finding, in miniature.
            "pooled": {"high|3": _counts(j0=200, j1=400, j2=300, j3=100)},
            "gap": {},
            "city": {},
        }
    )


# The floor point-bin is one rounding quantum from death, so gate 6
# (DAY0_ONE_BIN_EDGE_FRAGILE) fires on it before the veto is ever reached. The
# admission-seam tests therefore use the 32-33 range bin, which survives the one-bin
# stress and still spans the running extreme's own cell.
FLOOR_POINT_BIN = "Will the highest temperature in Manila be 32°C on July 2?"
FLOOR_RANGE_BIN = (
    "Will the highest temperature in Manila be between 32-33°C on July 2?"
)


def _action_payload(
    *,
    direction: str = "buy_yes",
    running: float = 32.0,
    bin_label: str = FLOOR_POINT_BIN,
) -> dict:
    return {
        "event_type": "DAY0_EXTREME_UPDATED",
        "city": "Manila",
        "target_date": "2026-07-02",
        "metric": "high",
        "temperature_metric": "high",
        "direction": direction,
        # The running-extreme (floor) bin — where the overconfidence lives.
        "bin_label": bin_label,
        "high_so_far": running,
        "low_so_far": 26.0,
        "rounded_value": running,
        "station_id": "RPLL",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "qkernel_execution_economics": {
            "global_expected_fill_price_before_fee": "0.90",
            "global_expected_cost_usd": "0.90",
            "global_target_shares": "1",
        },
    }


def _event() -> SimpleNamespace:
    payload = {
        "city": "Manila",
        "target_date": "2026-07-02",
        "metric": "high",
        "station_id": "RPLL",
        "settlement_source": "aviationweather_metar",
        "settlement_source_type": "wu_icao",
        "observation_available_at": "2026-07-02T02:06:24+00:00",
        "rounded_value": 32,
        "high_so_far": 32.0,
    }
    return SimpleNamespace(
        event_id="event-day0-nowcast",
        event_type="DAY0_EXTREME_UPDATED",
        causal_snapshot_id="metar-fast",
        payload_json=json.dumps(payload),
        payload=payload,
    )


def _witness() -> SimpleNamespace:
    return SimpleNamespace(
        quote_seen_at="2026-07-02T02:19:00+00:00",
        book_hash="book-day0",
        current_best_bid=0.89,
        current_best_ask=0.90,
        book_captured_at="2026-07-02T02:19:00+00:00",
        checked_at="2026-07-02T02:19:00+00:00",
    )


@pytest.fixture
def installed_nowcast(monkeypatch):
    """The loader mocked at the adapter's own import seam — no artifact on disk."""

    nowcast = _nowcast()
    monkeypatch.setattr(
        "src.calibration.day0_diurnal_residual.load_day0_diurnal_residual_nowcast",
        lambda **_kw: nowcast,
    )
    return nowcast


def test_stamp_records_q_basis_and_fit_date_on_the_actionable_payload(
    installed_nowcast,
) -> None:
    payload = _action_payload()

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    # buy_yes on the floor bin: P(D = 0) under the fitted cell.
    pmf, basis = installed_nowcast.pmf(city="Manila", metric="high", local_hour=10.0)
    assert payload[DAY0_NOWCAST_Q_HELD_KEY] == pytest.approx(pmf[0], abs=1e-12)
    assert payload[DAY0_NOWCAST_BASIS_KEY] == basis == "pooled"
    assert payload[DAY0_NOWCAST_FIT_DATE_KEY] == FIT_DATE


def test_buy_no_stamp_is_the_complement_of_the_bin_probability(
    installed_nowcast,
) -> None:
    payload = _action_payload(direction="buy_no")

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    pmf, _ = installed_nowcast.pmf(city="Manila", metric="high", local_hour=10.0)
    assert payload[DAY0_NOWCAST_Q_HELD_KEY] == pytest.approx(1.0 - pmf[0], abs=1e-12)


def test_admission_seam_vetoes_the_overpriced_floor_bin(installed_nowcast) -> None:
    payload = _action_payload(bin_label=FLOOR_RANGE_BIN)
    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )
    # We would pay 0.90 for a bin the residual distribution prices well below that.
    assert _day0_held_token_decision_price(payload) == pytest.approx(0.90)
    assert payload[DAY0_NOWCAST_Q_HELD_KEY] < 0.90

    reason = _day0_live_submit_admission_rejection_reason(
        event=_event(),
        actionable_payload=payload,
        authority_witness=_witness(),
        order_mode="maker",
        decision_time=DECISION_TIME,
    )

    assert reason == "DAY0_DIURNAL_NOWCAST_VETO"


def test_admission_seam_admits_when_the_price_is_below_the_nowcast(
    installed_nowcast,
) -> None:
    payload = _action_payload(bin_label=FLOOR_RANGE_BIN)
    payload["qkernel_execution_economics"] = {
        "global_expected_fill_price_before_fee": "0.05",
        "global_expected_cost_usd": "0.05",
        "global_target_shares": "1",
    }
    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    reason = _day0_live_submit_admission_rejection_reason(
        event=_event(),
        actionable_payload=payload,
        authority_witness=_witness(),
        order_mode="maker",
        decision_time=DECISION_TIME,
    )

    assert reason is None


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_admission_seam_vetoes_fee_cost_above_nowcast_with_lower_gross_anchor(
    installed_nowcast, direction,
) -> None:
    # Each side survives the existing +1 quantum stress before reaching the cost gate.
    bin_label = (
        FLOOR_RANGE_BIN if direction == "buy_yes" else
        "Will the highest temperature in Manila be between 34-35°C on July 2?"
    )
    payload = _action_payload(direction=direction, bin_label=bin_label)
    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )
    q_held = payload[DAY0_NOWCAST_Q_HELD_KEY]
    gross = q_held - 0.01
    all_in = q_held + 0.01
    payload["qkernel_execution_economics"] = {
        "global_expected_fill_price_before_fee": str(gross),
        "global_expected_cost_usd": str(all_in * 5),
        "global_target_shares": "5",
        "market_anchored_correction": {
            "applied": True,
            "p0": gross,
            "p0_basis": "GROSS_NATIVE_TOKEN_PRICE",
        },
    }
    assert gross < q_held < all_in
    assert _day0_live_submit_admission_rejection_reason(
        event=_event(),
        actionable_payload=payload,
        authority_witness=_witness(),
        order_mode="maker",
        decision_time=DECISION_TIME,
    ) == "DAY0_DIURNAL_NOWCAST_VETO"
    assert _day0_held_token_decision_price(payload) == pytest.approx(all_in)


def test_sealed_market_anchored_p0_wins_over_the_global_fill_price() -> None:
    payload = _action_payload()
    payload["qkernel_execution_economics"] = {
        "global_expected_fill_price_before_fee": "0.40",
        "market_anchored_correction": {"applied": True, "p0": 0.77},
    }

    assert _day0_held_token_decision_price(payload) == pytest.approx(0.77)

    payload["qkernel_execution_economics"]["market_anchored_correction"] = {
        "applied": False
    }
    # The pre-basis correction is a legacy all-in p0 fallback; an uncorrected
    # gross fill price alone is not a sealed economic cost authority.
    assert _day0_held_token_decision_price(payload) is None


def test_gross_correction_basis_cannot_substitute_for_sealed_cost() -> None:
    payload = _action_payload()
    economics = payload["qkernel_execution_economics"]
    economics.pop("global_expected_cost_usd")
    economics.pop("global_target_shares")
    economics["market_anchored_correction"] = {
        "applied": True,
        "p0": 0.77,
        "p0_basis": "GROSS_NATIVE_TOKEN_PRICE",
    }
    assert _day0_held_token_decision_price(payload) is None
    economics["market_anchored_correction"]["p0_basis"] = "FOREIGN_PRICE_BASIS"
    assert _day0_held_token_decision_price(payload) is None

    economics["global_expected_cost_usd"] = "0.90"
    economics["global_target_shares"] = "1"
    assert _day0_held_token_decision_price(payload) == pytest.approx(0.90)

    for invalid in (
        {"global_expected_cost_usd": "0.90"},
        {"global_target_shares": "1"},
        {"global_expected_cost_usd": "not-a-number", "global_target_shares": "1"},
        {"global_expected_cost_usd": "0.90", "global_target_shares": "not-a-number"},
        {"global_expected_cost_usd": "0", "global_target_shares": "1"},
        {"global_expected_cost_usd": "0.90", "global_target_shares": "0"},
    ):
        invalid_payload = _action_payload()
        invalid_economics = invalid_payload["qkernel_execution_economics"]
        invalid_economics.pop("global_expected_cost_usd")
        invalid_economics.pop("global_target_shares")
        invalid_economics.update(invalid)
        assert _day0_held_token_decision_price(invalid_payload) is None


def test_dormant_loader_leaves_the_payload_unstamped_and_the_gate_inert(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.calibration.day0_diurnal_residual.load_day0_diurnal_residual_nowcast",
        lambda **_kw: None,
    )
    payload = _action_payload(bin_label=FLOOR_RANGE_BIN)

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    assert DAY0_NOWCAST_Q_HELD_KEY not in payload
    reason = _day0_live_submit_admission_rejection_reason(
        event=_event(),
        actionable_payload=payload,
        authority_witness=_witness(),
        order_mode="maker",
        decision_time=DECISION_TIME,
    )
    assert reason is None


def test_loader_exception_fails_open_rather_than_blocking(monkeypatch) -> None:
    def _boom(**_kw):
        raise RuntimeError("artifact volume unavailable")

    monkeypatch.setattr(
        "src.calibration.day0_diurnal_residual.load_day0_diurnal_residual_nowcast",
        _boom,
    )
    payload = _action_payload()

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    assert DAY0_NOWCAST_Q_HELD_KEY not in payload


def test_unparseable_bin_label_leaves_the_gate_inert(installed_nowcast) -> None:
    payload = _action_payload()
    payload["bin_label"] = "some market question with no temperature in it"

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    assert DAY0_NOWCAST_Q_HELD_KEY not in payload


def test_non_day0_candidate_is_never_stamped(installed_nowcast) -> None:
    payload = _action_payload()
    payload["event_type"] = "FORECAST_SNAPSHOT_READY"

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    assert DAY0_NOWCAST_Q_HELD_KEY not in payload


def test_gap_conditioning_uses_the_payload_carrier_members_without_a_db_read(
    monkeypatch,
) -> None:
    """The NWP gap comes from the remaining-vector carrier already on the payload."""

    gap_counts = _counts(j0=10, j1=200, j2=200)
    nowcast = DiurnalResidualNowcast(
        {
            "schema_version": SCHEMA_VERSION,
            "fit_date": FIT_DATE,
            "peak_hours": {"Manila": 13.0},
            "trough_hours": {"Manila": 3.0},
            "unit": {"Manila": "C"},
            "pooled": {"high|3": _counts(j0=200, j1=400, j2=300, j3=100)},
            # Carrier median 34.0 vs running 32.0 -> gap +2.0 -> band index 3.
            "gap": {"high|3|3": gap_counts},
            "city": {},
        }
    )
    monkeypatch.setattr(
        "src.calibration.day0_diurnal_residual.load_day0_diurnal_residual_nowcast",
        lambda **_kw: nowcast,
    )
    payload = _action_payload()
    payload["day0_probability_authority"] = {
        "remaining_carrier_future_extremes_c": [33.5, 34.0, 34.5],
    }

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    assert payload[DAY0_NOWCAST_BASIS_KEY] == "gap"
    gapped, _ = nowcast.pmf(city="Manila", metric="high", local_hour=10.0, gap=2.0)
    assert payload[DAY0_NOWCAST_Q_HELD_KEY] == pytest.approx(gapped[0], abs=1e-12)
    # A hot gap makes the floor bin LESS likely than the pooled cell says.
    pooled, _ = nowcast.pmf(city="Manila", metric="high", local_hour=10.0)
    assert gapped[0] < pooled[0]


def test_stamped_fields_survive_onto_the_certificate_payload(installed_nowcast) -> None:
    """The stamp lands before the certificate seals its payload hash, so the veto is
    auditable on the certificate rather than only in the log line."""

    from src.decision_kernel.canonicalization import stable_hash

    payload = _action_payload()
    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )
    sealed_hash = stable_hash(payload)

    for key in (
        DAY0_NOWCAST_Q_HELD_KEY,
        DAY0_NOWCAST_BASIS_KEY,
        DAY0_NOWCAST_FIT_DATE_KEY,
    ):
        assert key in payload
    # The hash covers the stamp: dropping it changes the sealed identity.
    without = {k: v for k, v in payload.items() if k != DAY0_NOWCAST_Q_HELD_KEY}
    assert stable_hash(without) != sealed_hash


def test_hot_path_does_not_open_a_database_connection(
    installed_nowcast, monkeypatch
) -> None:
    import sqlite3

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("day0 nowcast wiring must not query a database")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    payload = _action_payload()

    stamp_day0_diurnal_nowcast(
        payload, event_payload=_event().payload, decision_time=DECISION_TIME
    )

    assert payload[DAY0_NOWCAST_Q_HELD_KEY] > 0.0


def _curve(*, token: str, side: str, levels: tuple[str, ...], fee: str) -> ExecutableCostCurve:
    return ExecutableCostCurve(
        token_id=token,
        side=side,
        snapshot_id=f"snapshot-{token}",
        book_hash=f"book-{token}",
        levels=tuple(
            BookLevel(price=Decimal(price), size=Decimal("10"))
            for price in levels
        ),
        fee_model=FeeModel(fee_rate=Decimal(fee)),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=30),
    )


def _nowcast_context(
    *,
    metric: str = "high",
    side: str = "YES",
    q_source: str = "day0_remaining_day",
    authority: str = "day0_remaining_day_global_probability_v1",
) -> Day0DiurnalNowcastContext:
    token = f"{side.lower()}-token"
    return Day0DiurnalNowcastContext(
        probability_witness_identity="nowcast-witness",
        probability_authority=authority,
        q_source=q_source,
        city_name="Manila",
        city_timezone="Asia/Manila",
        settlement_unit="C",
        metric=metric,
        running_extreme=32.0 if metric == "high" else 26.0,
        carrier_future_extremes_c=(),
        candidate_bindings=(
            Day0DiurnalNowcastCandidateBinding(
                bin_id="bin-nowcast",
                condition_id="condition-nowcast",
                side=side,
                token_id=token,
                bin_label=(
                    "Will the highest temperature in Manila be between 32-33°C on July 2?"
                    if metric == "high"
                    else "Will the lowest temperature in Manila be between 25-26°C on July 2?"
                ),
            ),
        ),
    )


def _nowcast_candidate(*, side: str, curve: ExecutableCostCurve, mode: str = "TAKER_LIMIT"):
    return SimpleNamespace(
        action="BUY",
        probability_witness_identity="nowcast-witness",
        bin_id="bin-nowcast",
        condition_id="condition-nowcast",
        side=side,
        token_id=f"{side.lower()}-token",
        execution_mode=mode,
        economic_cost_curve=curve,
    )


@pytest.mark.parametrize("metric,boundary", (("high", 33.2), ("low", 25.2)))
def test_global_nowcast_context_binds_real_topology_candidates_to_witness_bins(metric, boundary):
    candidate = MarketTopologyCandidate(
        city="Manila",
        target_date="2026-07-02",
        metric=metric,
        condition_id="condition-nowcast",
        yes_token_id="yes-token",
        no_token_id="no-token",
        bin=Bin(low=32.0, high=32.0, unit="C", label=FLOOR_RANGE_BIN),
    )
    family = EventBoundCandidateFamily(
        family_id="Manila|2026-07-02|high",
        event_id="event-nowcast",
        event_type="DAY0_EXTREME_UPDATED",
        city="Manila",
        target_date="2026-07-02",
        metric=metric,
        condition_ids=("condition-nowcast",),
        yes_token_ids=("yes-token",),
        no_token_ids=("no-token",),
        bins=(candidate.bin,),
        candidates=(candidate,),
        causal_snapshot_id="causal-nowcast",
        market_topology_source="test",
        binding_hash="binding-nowcast",
    )
    prepared = bridge.PreparedGlobalFamily(
        decision_id="prepared-nowcast",
        probability_witness=SimpleNamespace(
            witness_identity="nowcast-witness",
            bindings=(
                OutcomeTokenBinding(
                    bin_id="witness-bin",
                    condition_id="condition-nowcast",
                    yes_token_id="yes-token",
                    no_token_id="no-token",
                ),
            ),
        ),
        candidate_seeds=(),
    )

    payload = _action_payload()
    payload.update(
        {
            "probability_authority": "day0_remaining_day_global_probability_v1",
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_probability_boundary_native": boundary,
        }
    )
    bound = era._bind_day0_diurnal_nowcast_context(
        prepared,
        payload,
        family,
        event_type="DAY0_EXTREME_UPDATED",
    )

    context = bound.day0_diurnal_nowcast_context
    assert context.probability_witness_identity == "nowcast-witness"
    assert context.running_extreme == boundary
    assert {(row.bin_id, row.side, row.token_id) for row in context.candidate_bindings} == {
        ("witness-bin", "YES", "yes-token"),
        ("witness-bin", "NO", "no-token"),
    }
    nested_replacement = {
        **_action_payload(),
        "day0_probability_authority": {
            "probability_authority": "replacement_current_global_probability_v1",
            "_edli_q_source": "replacement_0_1",
        },
    }
    assert era._bind_day0_diurnal_nowcast_context(
        prepared,
        nested_replacement,
        family,
        event_type="DAY0_EXTREME_UPDATED",
    ) is prepared


@pytest.mark.parametrize(
    ("metric", "side", "mode", "levels", "fee", "expected"),
    (
        ("high", "YES", "TAKER_LIMIT", ("0.49",), "0.10", "DAY0_DIURNAL_NOWCAST_VETO"),
        ("high", "NO", "MAKER_REST", ("0.50",), "0", "DAY0_DIURNAL_NOWCAST_VETO"),
        ("low", "YES", "TAKER_LIMIT", ("0.40", "0.80"), "0", None),
        ("low", "NO", "MAKER_REST", ("0.49",), "0.10", "DAY0_DIURNAL_NOWCAST_VETO"),
    ),
)
def test_global_nowcast_uses_current_proposal_cheapest_fee_inclusive_cost(
    monkeypatch, metric, side, mode, levels, fee, expected,
):
    seen = []
    monkeypatch.setattr(
        era,
        "_day0_diurnal_nowcast_verdict",
        lambda **kwargs: seen.append(kwargs["decision_time"]) or SimpleNamespace(q_held=0.5),
    )
    curve = _curve(token=f"{side.lower()}-token", side=side, levels=levels, fee=fee)

    reason = era.day0_diurnal_nowcast_candidate_rejection_reason(
        _nowcast_context(metric=metric, side=side),
        _nowcast_candidate(side=side, curve=curve, mode=mode),
        decision_time=DECISION_TIME,
    )

    assert reason == expected
    assert seen == [DECISION_TIME]


def test_global_nowcast_context_is_inert_for_mismatch_missing_or_replacement(monkeypatch):
    monkeypatch.setattr(
        era,
        "_day0_diurnal_nowcast_verdict",
        lambda **_kwargs: SimpleNamespace(q_held=0.5),
    )
    curve = _curve(token="yes-token", side="YES", levels=("0.90",), fee="0")
    candidate = _nowcast_candidate(side="YES", curve=curve)

    assert era.day0_diurnal_nowcast_candidate_rejection_reason(
        None, candidate, decision_time=DECISION_TIME,
    ) is None
    assert era.day0_diurnal_nowcast_candidate_rejection_reason(
        _nowcast_context(),
        SimpleNamespace(**{**candidate.__dict__, "token_id": "other-token"}),
        decision_time=DECISION_TIME,
    ) is None
    assert era.day0_diurnal_nowcast_candidate_rejection_reason(
        _nowcast_context(
            q_source="replacement_0_1",
            authority="replacement_current_global_probability_v1",
        ),
        candidate,
        decision_time=DECISION_TIME,
    ) is None


def test_global_nowcast_recomputes_the_local_time_cell_and_has_no_cross_cut_latch(
    monkeypatch,
):
    nowcast = DiurnalResidualNowcast(
        {
            "schema_version": SCHEMA_VERSION,
            "fit_date": FIT_DATE,
            "peak_hours": {"Manila": 13.0},
            "trough_hours": {"Manila": 3.0},
            "unit": {"Manila": "C"},
            "pooled": {
                "high|3": _counts(j0=900, j1=100),
                "high|2": _counts(j0=100, j1=900),
            },
            "gap": {},
            "city": {},
        }
    )
    monkeypatch.setattr(
        "src.calibration.day0_diurnal_residual.load_day0_diurnal_residual_nowcast",
        lambda **_kw: nowcast,
    )
    context = _nowcast_context()
    context = replace(
        context,
        candidate_bindings=(
            replace(context.candidate_bindings[0], bin_label=FLOOR_POINT_BIN),
        ),
    )
    expensive = _nowcast_candidate(
        side="YES",
        curve=_curve(token="yes-token", side="YES", levels=("0.50",), fee="0"),
    )
    cheap = _nowcast_candidate(
        side="YES",
        curve=_curve(token="yes-token", side="YES", levels=("0.05",), fee="0"),
    )
    first_cut = datetime(2026, 7, 2, 2, 20, tzinfo=timezone.utc)
    next_cut = datetime(2026, 7, 2, 2, 40, tzinfo=timezone.utc)

    assert era.day0_diurnal_nowcast_candidate_rejection_reason(
        context, expensive, decision_time=first_cut,
    ) is None
    assert era.day0_diurnal_nowcast_candidate_rejection_reason(
        context, expensive, decision_time=next_cut,
    ) == "DAY0_DIURNAL_NOWCAST_VETO"
    assert era.day0_diurnal_nowcast_candidate_rejection_reason(
        context, cheap, decision_time=next_cut,
    ) is None


@pytest.mark.parametrize("metric,observed,boundary", (("high", 32.0, 33.2), ("low", 26.0, 25.2)))
@pytest.mark.parametrize("direction", ("buy_yes", "buy_no"))
@pytest.mark.parametrize("unit", ("C", "F"))
def test_nowcast_final_uses_current_probability_boundary_over_stale_actionable(
    monkeypatch, metric, observed, boundary, direction, unit,
):
    seen = []
    model = SimpleNamespace(
        fitted_unit=lambda city: unit,
        held_probability=lambda **kwargs: seen.append(kwargs) or SimpleNamespace(q_held=0.5),
    )
    monkeypatch.setattr(
        "src.calibration.day0_diurnal_residual.load_day0_diurnal_residual_nowcast",
        lambda **kwargs: model,
    )
    key = "high_so_far" if metric == "high" else "low_so_far"
    event = {key: observed, "rounded_value": observed,
             "_edli_day0_probability_boundary_native": boundary}
    actionable = {key: observed - 1, "direction": direction,
                  "bin_label": f"32°{unit}"}
    result = era._day0_diurnal_nowcast_verdict(
        actionable_payload=actionable, event_payload=event, metric=metric,
        city=SimpleNamespace(name="Manila", timezone="Asia/Manila", settlement_unit=unit),
        decision_time=DECISION_TIME,
    )
    assert result is not None
    assert seen[0]["running_extreme"] == boundary
    assert seen[0]["direction"] == direction
    assert era._day0_nowcast_extreme_native(event, metric) == boundary


@pytest.mark.parametrize("metric,observed", (("high", 32.2), ("low", 25.8)))
@pytest.mark.parametrize("boundary", (None, "invalid", float("nan"), float("inf")))
def test_nowcast_without_finite_current_boundary_preserves_legacy(metric, observed, boundary):
    key = "high_so_far" if metric == "high" else "low_so_far"
    payload = {key: observed, "rounded_value": round(observed),
               "_edli_day0_probability_boundary_native": boundary}
    assert era._day0_nowcast_extreme_native(payload, metric) == observed
    assert era._day0_nowcast_extreme_native(
        payload, metric, actionable_payload={key: observed + 0.1},
    ) == observed + 0.1


@pytest.mark.parametrize("metric,observed,boundary", (("high", 32.2, 31.0), ("low", 25.8, 27.0)))
def test_nowcast_rejects_regressing_physical_boundary_like_probability(metric, observed, boundary):
    key = "high_so_far" if metric == "high" else "low_so_far"
    payload = {key: observed, "rounded_value": round(observed),
               "_edli_day0_probability_boundary_native": boundary}
    assert era._day0_nowcast_extreme_native(payload, metric) == era._day0_probability_boundary_native(payload, metric)
