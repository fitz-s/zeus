# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: resolver-graded Day0 observation model (external review
#   2026-09-24, design decision item 6).
"""Resolver-graded Day0 terminal residual: label law, hierarchy, operator, switch."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from scipy.stats import beta as beta_dist

import src.calibration.day0_resolver_terminal_residual as terminal
from src.calibration.day0_resolver_terminal_residual import (
    Day0ResolverTerminalInput,
    ReportRendering,
    ResolverTerminalArtifact,
    TerminalLabel,
    contract_settlement_value,
    failure_category,
    fit_resolver_terminal_residual,
    running_extreme,
    station_day_labels,
    terminal_margin,
    to_contract_unit,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.day0_hourly_vectors import (
    DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
    build_day0_remaining_probability_carrier,
)
from src.forecast.day0_terminal_distribution import (
    compose_resolver_terminal_distribution,
)

UTC = timezone.utc
C_SEM = SettlementSemantics.default_wu_celsius("RJTT")
F_SEM = SettlementSemantics.default_wu_fahrenheit("KATL")
HKO_SEM = SettlementSemantics.for_city(SimpleNamespace(settlement_source_type="hko"))
CUTOFF = datetime(2026, 9, 20, tzinfo=UTC)


def _input(levels, g_levels=None, **kwargs) -> Day0ResolverTerminalInput:
    empty = (0, 0, 0, 0)
    return Day0ResolverTerminalInput(
        artifact_hash="test",
        fit_cutoff_utc=CUTOFF.isoformat(),
        cell=("noaa_wrh", "metar_tenth", "C", "high", "h12_18", "gap_lt1", "RJTT"),
        levels=tuple(levels),
        g_levels=tuple(g_levels or (empty,) * 4),
        **kwargs,
    )


def _label(*, station="RJTT", metric="high", observed=30.0, settled=30.0, day=1, phase="h12_18", unit="C"):
    return TerminalLabel(
        city="Tokyo",
        station=station,
        target_date=f"2026-09-{day:02d}",
        metric=metric,
        resolver_product="noaa_wrh",
        channel_class="metar_tenth",
        unit=unit,
        phase=phase,
        gap="gap_lt1",
        observed_settlement=observed,
        settled=settled,
        checkpoint_utc=datetime(2026, 9, day, 6, tzinfo=UTC),
        available_at_utc=datetime(2026, 9, day + 1, 6, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Label law
# ---------------------------------------------------------------------------


def test_awc_ogimet_agreement_with_resolver_below_running_high_is_one_failure():
    """Two renderings of one report are one observation; the resolver grades it."""

    report = datetime(2026, 9, 10, 5, 50, tzinfo=UTC)
    renderings = [
        ReportRendering(report, report + timedelta(minutes=2), 31.4),  # AWC
        ReportRendering(report, report + timedelta(hours=11), 31.4),  # Ogimet mirror
        ReportRendering(report - timedelta(hours=1), report - timedelta(minutes=58), 29.8),
    ]
    day_start = datetime(2026, 9, 9, 15, tzinfo=UTC)

    def running(at):
        return running_extreme(renderings, at=at, day_start_utc=day_start, metric="high")

    labels = station_day_labels(
        city="Tokyo",
        station="RJTT",
        target_date=date(2026, 9, 10),
        timezone_name="Asia/Tokyo",
        metric="high",
        resolver_product="noaa_wrh",
        channel_class="metar_tenth",
        semantics=C_SEM,
        settled=30.0,  # resolver's product settles below the running high (31)
        settled_available_at=datetime(2026, 9, 11, 5, tzinfo=UTC),
        running_extreme_at=running,
        members_at=lambda _at: None,
    )
    # One label per phase with evidence; each is ONE failure, never two successes.
    graded = [label for label in labels if label.observed_settlement == 31.0]
    assert graded, labels
    assert all(label.margin == -1.0 and not label.nonviolation for label in graded)
    artifact = fit_resolver_terminal_residual(
        graded[-1:],
        fit_cutoff_utc=CUTOFF,
        station_channel={"RJTT": "metar_tenth"},
    )
    leaf = artifact["nodes"]["L3|noaa_wrh|metar_tenth|C|high|" + graded[-1].phase + "|gap_missing|RJTT"]
    assert (leaf["n"], leaf["f"], leaf["g"]) == (1, 1, [1, 0, 0, 0])


def test_running_extreme_uses_only_renderings_possessed_at_checkpoint():
    report = datetime(2026, 9, 10, 5, 50, tzinfo=UTC)
    renderings = [ReportRendering(report, report + timedelta(hours=11), 31.4)]
    start = datetime(2026, 9, 9, 15, tzinfo=UTC)
    assert running_extreme(renderings, at=report + timedelta(hours=1), day_start_utc=start, metric="high") is None
    assert running_extreme(renderings, at=report + timedelta(hours=12), day_start_utc=start, metric="high") == 31.4


def test_missing_settlement_is_censored_not_a_success():
    labels = station_day_labels(
        city="Tokyo",
        station="RJTT",
        target_date=date(2026, 9, 10),
        timezone_name="Asia/Tokyo",
        metric="high",
        resolver_product="noaa_wrh",
        channel_class="metar_tenth",
        semantics=C_SEM,
        settled=None,
        settled_available_at=None,
        running_extreme_at=lambda _at: 30.0,
        members_at=lambda _at: None,
    )
    assert labels == []


def test_one_checkpoint_per_station_day_phase_is_the_last_eligible_hour():
    labels = station_day_labels(
        city="Tokyo",
        station="RJTT",
        target_date=date(2026, 9, 10),
        timezone_name="Asia/Tokyo",
        metric="high",
        resolver_product="noaa_wrh",
        channel_class="metar_tenth",
        semantics=C_SEM,
        settled=31.0,
        settled_available_at=datetime(2026, 9, 11, tzinfo=UTC),
        running_extreme_at=lambda at: 30.0 if at.astimezone(ZoneInfo("Asia/Tokyo")).hour < 15 else None,
        members_at=lambda _at: None,
    )
    local_hours = [label.checkpoint_utc.astimezone(ZoneInfo("Asia/Tokyo")).hour for label in labels]
    assert [label.phase for label in labels] == ["h00_12", "h12_18"]
    assert local_hours == [11, 14]


def test_labels_after_fit_cutoff_are_excluded():
    late = _label(settled=29.0)
    artifact = fit_resolver_terminal_residual(
        [late],
        fit_cutoff_utc=late.available_at_utc,
        station_channel={"RJTT": "metar_tenth"},
    )
    assert artifact["nodes"] == {}


def test_high_and_low_failure_support():
    assert terminal_margin(settled=29.0, observed_settlement=31.0, metric="high") == -2.0
    assert terminal_margin(settled=12.0, observed_settlement=10.0, metric="low") == -2.0
    assert terminal_margin(settled=33.0, observed_settlement=31.0, metric="high") == 2.0
    assert failure_category(-1.0) == 0
    assert failure_category(-3.0) == 2
    assert failure_category(-9.0) == 3  # overflow
    assert failure_category(0.0) is None
    grid = ((None, 26.0), *((float(v), float(v)) for v in range(27, 34)), (34.0, None))
    template = np.ones(len(grid))
    for metric, expected_support in (("high", {27.0, 28.0, 29.0}), ("low", {31.0, 32.0, 33.0})):
        q = compose_resolver_terminal_distribution(
            template=template,
            template_bins=grid,
            observed=30.0,
            metric=metric,
            nonviolation_probability=0.0,
            failure_magnitude=[0.5, 0.3, 0.2, 0.0],
            bins=grid,
        )
        support = {grid[i][0] for i, value in enumerate(q) if value > 0.0}
        assert support == expected_support
        assert q.sum() == pytest.approx(1.0)
    # The overflow category lands only on bins that contain such values.
    q = compose_resolver_terminal_distribution(
        template=template,
        template_bins=grid,
        observed=30.0,
        metric="high",
        nonviolation_probability=0.0,
        failure_magnitude=[0.0, 0.0, 0.0, 1.0],
        bins=grid,
    )
    assert q[0] == pytest.approx(1.0)  # "26 or below" holds every k >= 4


def test_celsius_to_fahrenheit_conversion_uses_contract_rounding():
    # 26.1 C = 78.98 F -> WMO half-up 79 F; 25.8 C = 78.44 F -> 78 F.
    assert to_contract_unit(26.1, "F") == pytest.approx(78.98)
    assert contract_settlement_value(to_contract_unit(26.1, "F"), F_SEM) == 79.0
    assert contract_settlement_value(to_contract_unit(25.8, "F"), F_SEM) == 78.0
    assert contract_settlement_value(to_contract_unit(25.0, "F"), F_SEM) == 77.0
    with pytest.raises(ValueError):
        to_contract_unit(25.0, "K")


def test_negative_rounding_boundaries_are_wmo_half_up_not_python_round():
    assert contract_settlement_value(-2.5, C_SEM) == -2.0  # Python round(-2.5) == -2 but -3.5 differs
    assert contract_settlement_value(-3.5, C_SEM) == -3.0
    assert round(-3.5) == -4  # the rule Python would have applied
    assert contract_settlement_value(-0.5, C_SEM) == 0.0
    assert contract_settlement_value(-0.51, C_SEM) == -1.0
    # LOW at a negative boundary: A = R(-3.5) = -3; settling at -4 is a violation.
    assert terminal_margin(settled=-4.0, observed_settlement=contract_settlement_value(-3.5, C_SEM), metric="low") == 1.0
    assert terminal_margin(settled=-2.0, observed_settlement=contract_settlement_value(-3.5, C_SEM), metric="low") == -1.0


def test_hko_floor_semantics():
    assert HKO_SEM.rounding_rule == "oracle_truncate"
    assert contract_settlement_value(30.9, HKO_SEM) == 30.0
    assert contract_settlement_value(31.0, HKO_SEM) == 31.0
    assert contract_settlement_value(-0.2, HKO_SEM) == -1.0
    # HKO running max 30.9 settles floor(30.9) = 30: non-violation, not a failure.
    assert terminal_margin(
        settled=30.0, observed_settlement=contract_settlement_value(30.9, HKO_SEM), metric="high"
    ) == 0.0


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def test_sparse_station_inherits_parent_with_nonzero_uncertainty():
    labels = [_label(station="RJTT", day=d % 28 + 1, settled=29.0 if d < 10 else 31.0) for d in range(400)]
    artifact = ResolverTerminalArtifact.from_payload(
        fit_resolver_terminal_residual(labels, fit_cutoff_utc=CUTOFF, station_channel={"RJTT": "metar_tenth"})
    )
    parent = artifact.input_for(("noaa_wrh", "metar_tenth", "C", "high", "h12_18", "gap_lt1", "RJTT"))
    sparse = artifact.input_for(("noaa_wrh", "metar_tenth", "C", "high", "h12_18", "gap_lt1", "ZZZZ"))
    assert sparse.levels[-1][1] == 0  # no station history
    assert sparse.nonviolation_probability == pytest.approx(1.0 - sparse.failure_means()[2])
    assert sparse.nonviolation_probability != pytest.approx(0.5)
    assert parent.failure_means()[2] == pytest.approx(sparse.failure_means()[2])
    s_draws, _ = sparse.draw(np.random.default_rng(0), 4000)
    assert float(np.std(s_draws)) > 0.001
    assert 0.9 < float(np.mean(s_draws)) < 1.0


def test_zero_failure_history_carries_binomial_uncertainty():
    """299 clean observations bound the failure rate below 1% at 95%, not at 0."""
    inp = _input(
        levels=(
            ("L0|high", 299, 0, 1.0),
            ("L1|x", 299, 0, 1e6),
            ("L2|x", 299, 0, 1e6),
            ("L3|x", 299, 0, 1e6),
        )
    )
    upper = beta_dist.ppf(0.95, 1.0, 1.0 + 299)
    assert upper == pytest.approx(0.01, abs=2e-4)
    s_draws, _ = inp.draw(np.random.default_rng(1), 20000)
    failure = 1.0 - s_draws
    assert inp.nonviolation_probability < 1.0
    assert float(np.quantile(failure, 0.95)) == pytest.approx(upper, rel=0.1)
    assert float(np.quantile(failure, 0.05)) > 0.0


def test_fitted_zero_failure_cell_does_not_serve_certainty():
    labels = [_label(day=d % 28 + 1, settled=31.0) for d in range(300)]
    artifact = ResolverTerminalArtifact.from_payload(
        fit_resolver_terminal_residual(labels, fit_cutoff_utc=CUTOFF, station_channel={"RJTT": "metar_tenth"})
    )
    inp = artifact.input_for(("noaa_wrh", "metar_tenth", "C", "high", "h12_18", "gap_lt1", "RJTT"))
    s_draws, _ = inp.draw(np.random.default_rng(2), 20000)
    assert 0.99 < inp.nonviolation_probability < 1.0
    assert float(np.quantile(1.0 - s_draws, 0.95)) > 0.002


def test_artifact_hash_and_input_payload_round_trip():
    labels = [_label(day=d % 28 + 1, settled=29.0 if d % 17 == 0 else 31.0) for d in range(120)]
    payload = fit_resolver_terminal_residual(labels, fit_cutoff_utc=CUTOFF, station_channel={"RJTT": "metar_tenth"})
    artifact = ResolverTerminalArtifact.from_payload(payload)
    inp = artifact.input_for(("noaa_wrh", "metar_tenth", "C", "high", "h12_18", "gap_lt1", "RJTT"))
    assert Day0ResolverTerminalInput.from_payload(inp.to_payload()) == inp
    tampered = dict(payload)
    tampered["kappa"] = {"high": {"L1": 1.0, "L2": 1.0, "L3": 1.0}}
    with pytest.raises(ValueError):
        ResolverTerminalArtifact.from_payload(tampered)


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------


def test_nonviolation_equals_s_exactly_including_the_counterexample():
    grid = ((None, 29.0), (30.0, 30.0), (31.0, None))
    template = [0.2, 0.5, 0.3]  # P(X >= A) = 0.8
    old_mixture = 0.97 * 1.0 + 0.03 * 0.8
    assert old_mixture == pytest.approx(0.994)
    q = compose_resolver_terminal_distribution(
        template=template,
        template_bins=grid,
        observed=30.0,
        metric="high",
        nonviolation_probability=0.97,
        failure_magnitude=[1.0, 0.0, 0.0, 0.0],
        bins=grid,
    )
    assert q[1:].sum() == pytest.approx(0.97, abs=1e-15)
    assert q.sum() == pytest.approx(1.0, abs=1e-15)
    # Q+ keeps the observed-bin atom: 0.97 * 0.5 / 0.8.
    assert q[1] == pytest.approx(0.97 * 0.5 / 0.8)


@pytest.mark.parametrize("metric,boundary,bins,sem,unit", [
    ("high", 30.2, ((None, 28.0), (29.0, 29.0), (30.0, 30.0), (31.0, 31.0), (32.0, None)), C_SEM, "C"),
    ("low", 80.6, ((None, 77.0), (78.0, 79.0), (80.0, 81.0), (82.0, 83.0), (84.0, None)), F_SEM, "F"),
])
def test_carrier_nonviolation_mass_equals_s_in_point_and_every_row(metric, boundary, bins, sem, unit):
    inp = _input(
        levels=(("L0|x", 100, 3, 1.0), ("L1|x", 50, 2, 40.0), ("L2|x", 20, 1, 40.0), ("L3|x", 5, 0, 40.0)),
        g_levels=((2, 1, 0, 0), (1, 1, 0, 0), (1, 0, 0, 0), (0, 0, 0, 0)),
    )
    carrier = build_day0_remaining_probability_carrier(
        future_extremes_c=(boundary - 1.2, boundary + 0.4, boundary + 1.3),
        boundary_scenarios=((boundary, 1.0),),
        metric=metric,
        path_error_sigma_c=0.7,
        instrument_sigma_c=0.3,
        bin_bounds_c=bins,
        n_point=100,
        n_samples=500,
        identity_inputs={"city": "X", "unit": unit, "station_id": "XXXX"},
        settlement_semantics=sem,
        resolver_terminal=inp,
    )
    assert carrier["operator"] == DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER
    observed = contract_settlement_value(boundary, sem)
    q = np.asarray(carrier["q"])
    rows = np.asarray(carrier["samples"])
    # The observed value sits strictly inside one bin, so terminal non-violation
    # is checked with a unit-grid recomposition of the same template.
    assert q.sum() == pytest.approx(1.0)
    assert np.allclose(rows.sum(axis=1), 1.0)
    grid = ((None, observed - 5.0), *((float(v), float(v)) for v in range(int(observed) - 4, int(observed) + 5)), (observed + 5.0, None))
    fine = build_day0_remaining_probability_carrier(
        future_extremes_c=(boundary - 1.2, boundary + 0.4, boundary + 1.3),
        boundary_scenarios=((boundary, 1.0),),
        metric=metric,
        path_error_sigma_c=0.7,
        instrument_sigma_c=0.3,
        bin_bounds_c=grid,
        n_point=100,
        n_samples=2,
        identity_inputs={"city": "X", "unit": unit, "station_id": "XXXX"},
        settlement_semantics=sem,
        resolver_terminal=inp,
    )
    fine_q = np.asarray(fine["q"])
    side = [
        (low is not None and low >= observed) if metric == "high" else (high is not None and high <= observed)
        for low, high in grid
    ]
    assert fine_q[np.asarray(side)].sum() == pytest.approx(inp.nonviolation_probability, abs=1e-12)


def test_missing_failure_magnitude_changes_only_violation_side_claims():
    """G- enters only claims whose payoff depends on the violation side."""
    grid = ((None, 27.0), (28.0, 28.0), (29.0, 29.0), (30.0, 30.0), (31.0, None))
    template = [0.1, 0.1, 0.1, 0.4, 0.3]
    common = dict(template=template, template_bins=grid, observed=30.0, metric="high",
                  nonviolation_probability=0.9, bins=grid)
    a = compose_resolver_terminal_distribution(failure_magnitude=[1.0, 0.0, 0.0, 0.0], **common)
    b = compose_resolver_terminal_distribution(failure_magnitude=[0.2, 0.2, 0.3, 0.3], **common)
    # Non-violation claims (30, 31+) and the aggregate violation claim are invariant.
    assert np.allclose(a[3:], b[3:])
    assert a[:3].sum() == pytest.approx(b[:3].sum())
    # Claims that split the violation side are the only ones that move.
    assert not np.isclose(a[2], b[2])


def test_no_double_application_after_composition(monkeypatch):
    """Adapter masks and samplers leave a resolver-composed q untouched."""
    import src.engine.event_reactor_adapter as era

    family = SimpleNamespace(
        candidates=[
            SimpleNamespace(bin=SimpleNamespace(low=None, high=29.0)),
            SimpleNamespace(bin=SimpleNamespace(low=30.0, high=30.0)),
            SimpleNamespace(bin=SimpleNamespace(low=31.0, high=None)),
        ]
    )
    q = np.asarray([0.03, 0.6, 0.37])
    rows = np.tile(q, (4, 1))
    payload = {
        "rounded_value": 30.0,
        "metric": "high",
        "settlement_source": "noaa_wrh_rjtt",  # absorbing finality
        "_edli_day0_probability_operator": DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
        "_edli_day0_remaining_probability_samples": rows.tolist(),
    }
    assert era._day0_resolver_terminal_carrier(payload)
    assert np.array_equal(era._apply_day0_mask_to_probability_vector(payload=payload, family=family, vector=q), q)
    sampler = era._Day0CarrierRowSampler.from_payload(payload)
    analysis = SimpleNamespace(_rng=np.random.default_rng(0))
    assert np.array_equal(sampler.sample_matrix(analysis, 3, 99), rows[:3])
    # The legacy absorbing mask still applies to non-resolver payloads.
    legacy = dict(payload, _edli_day0_probability_operator="extreme_observed_then_noisy_future_v1")
    masked = era._apply_day0_mask_to_probability_vector(payload=legacy, family=family, vector=q)
    assert masked[0] == 0.0


def test_builder_rejects_survival_mixture_scenarios_with_resolver_input():
    inp = _input(levels=(("L0|x", 10, 0, 1.0), ("L1|x", 0, 0, 5.0), ("L2|x", 0, 0, 5.0), ("L3|x", 0, 0, 5.0)))
    with pytest.raises(ValueError, match="BOUNDARY_SCENARIO_INVALID"):
        build_day0_remaining_probability_carrier(
            future_extremes_c=(30.0,),
            boundary_scenarios=((30.2, 0.9), (None, 0.1)),
            metric="high",
            path_error_sigma_c=0.5,
            instrument_sigma_c=0.3,
            bin_bounds_c=((None, 29.0), (30.0, None)),
            n_point=10,
            n_samples=5,
            identity_inputs={"city": "X", "unit": "C", "station_id": "XXXX"},
            settlement_semantics=C_SEM,
            resolver_terminal=inp,
        )


# ---------------------------------------------------------------------------
# Switch
# ---------------------------------------------------------------------------

GOLDEN_OFF = {
    # sha256 of the full carrier (q, samples, identity, operator) captured on
    # origin/live 14244e742 (clean git-archive export) with identical inputs,
    # without and with the live remaining-center shift.
    "v2_c": "86a5673239f842d1a38ce3a37bbde45db28ce3000b4f11f881e753326a8f679c",
    "v3_c": "f355dc7b5c8cce13e0c51da710e565f77dbf9b4ae094c4d859d11d75696ac71f",
    "v2_f_low": "04df8b314132985199dc15fc1527e9a02fbc71e52e18d6b6c339715e3604dcd5",
    "v2_c_shift": "4677e20eda86a0a58611d4fca2be2a2a8a4e66858d5cf52b8d80201fed434781",
    "v3_c_shift": "c87db963004cc0df3622a5047994bd67bdf0f462f34c0118132a7054fa0f069e",
    "v2_f_low_shift": "054d6fccc9109c0af74b09779c4a6aed9ed141d096c8d3a08d4216a8e2e2620d",
}
BINS_C = ((None, 28.0), (29.0, 29.0), (30.0, 30.0), (31.0, 31.0), (32.0, 32.0), (33.0, None))
BINS_F = ((None, 77.0), (78.0, 79.0), (80.0, 81.0), (82.0, 83.0), (84.0, None))
GOLDEN_CASES = {
    "v2_c": dict(future_extremes_c=(29.4, 30.8, 31.6), boundary_scenarios=((30.2, 0.93), (None, 0.07)),
                 metric="high", path_error_sigma_c=0.6, instrument_sigma_c=0.28, bin_bounds_c=BINS_C,
                 identity_inputs={"city": "Tokyo", "unit": "C", "station_id": "RJTT"}, settlement_semantics=C_SEM),
    "v3_c": dict(future_extremes_c=(29.4, 30.8, 31.6), final_extreme_centers_c=(31.0,),
                 boundary_scenarios=((30.2, 0.93), (None, 0.07)), metric="high", path_error_sigma_c=0.6,
                 instrument_sigma_c=0.28, bin_bounds_c=BINS_C,
                 identity_inputs={"city": "Tokyo", "unit": "C", "station_id": "RJTT"}, settlement_semantics=C_SEM),
    "v2_f_low": dict(future_extremes_c=(79.1, 80.4, 81.9), boundary_scenarios=((80.6, 0.5), (None, 0.5)),
                     metric="low", path_error_sigma_c=1.1, instrument_sigma_c=0.5, bin_bounds_c=BINS_F,
                     identity_inputs={"city": "Atlanta", "unit": "F", "station_id": "KATL"}, settlement_semantics=F_SEM),
}
GOLDEN_CASES["v2_c_shift"] = dict(GOLDEN_CASES["v2_c"], remaining_center_bias_native=0.4)
GOLDEN_CASES["v3_c_shift"] = dict(GOLDEN_CASES["v3_c"], remaining_center_bias_native=0.5)
GOLDEN_CASES["v2_f_low_shift"] = dict(GOLDEN_CASES["v2_f_low"], remaining_center_bias_native=0.72)


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_switch_off_carrier_is_byte_identical_to_live(name):
    carrier = build_day0_remaining_probability_carrier(n_point=10000, n_samples=500, **GOLDEN_CASES[name])
    blob = json.dumps(carrier, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(blob).hexdigest() == GOLDEN_OFF[name]


def test_switch_off_resolver_input_is_none_and_revision_is_unchanged(monkeypatch, tmp_path):
    from src.events import day0_authority

    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: False)
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}")
    assert terminal.resolve_day0_resolver_terminal_input(
        city=SimpleNamespace(), target_date="2026-09-20", metric="high", source="aviationweather_metar",
        decision_time=CUTOFF, boundary_native=30.0, members_native=(30.0,), settlement_semantics=C_SEM,
        artifact_file=artifact,
    ) is None
    assert day0_authority.DAY0_PROBABILITY_SEMANTICS_REVISION == "day0_remaining_center_bias_v20"
    assert day0_authority.DAY0_PROBABILITY_SEMANTICS_REVISION_SURVIVAL == "day0_remaining_center_bias_v20"
    assert day0_authority.DAY0_PROBABILITY_SEMANTICS_REVISION_RESOLVER == (
        "day0_resolver_terminal_composition_v21"
    )


def test_switch_off_coverage_sql_excludes_resolver_rows(monkeypatch):
    from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql

    kwargs = dict(posterior_columns=("q_lcb_json", "q_ucb_json", "provenance_json"), decision_time=CUTOFF)
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: False)
    off = tradeable_grade_coverage_sql(**kwargs)
    assert DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER not in off
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: True)
    assert DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER in tradeable_grade_coverage_sql(**kwargs)


def test_switch_on_without_valid_artifact_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: True)
    terminal.reset_cache()
    with pytest.raises(ValueError, match="ARTIFACT_UNAVAILABLE"):
        terminal.resolve_day0_resolver_terminal_input(
            city=SimpleNamespace(), target_date="2026-09-20", metric="high", source="aviationweather_metar",
            decision_time=CUTOFF, boundary_native=30.0, members_native=(30.0,), settlement_semantics=C_SEM,
            artifact_file=tmp_path / "absent.json",
        )


def test_switch_on_resolves_causal_artifact_cell(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: True)
    terminal.reset_cache()
    labels = [_label(day=d % 18 + 1, settled=29.0 if d % 20 == 0 else 31.0) for d in range(200)]
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(fit_resolver_terminal_residual(
        labels, fit_cutoff_utc=CUTOFF, station_channel={"RJTT": "metar_tenth"}
    )))
    city = SimpleNamespace(
        wu_station="RJTT", timezone="Asia/Tokyo", settlement_source_type="noaa",
        settlement_source_type_effective_date=None, previous_settlement_source_type=None,
    )
    decision = datetime(2026, 9, 21, 5, tzinfo=UTC)  # 14:00 Tokyo -> h12_18
    common = dict(city=city, target_date="2026-09-21", metric="high", source="aviationweather_metar",
                  boundary_native=30.2, members_native=(30.1, 30.4), settlement_semantics=C_SEM,
                  artifact_file=path)
    resolved = terminal.resolve_day0_resolver_terminal_input(decision_time=decision, **common)
    assert resolved.cell == ("noaa_wrh", "metar_tenth", "C", "high", "h12_18", "gap_lt1", "RJTT")
    assert resolved.levels[-1][1] > 0
    with pytest.raises(ValueError, match="NOT_CAUSAL_OR_STALE"):
        terminal.resolve_day0_resolver_terminal_input(decision_time=CUTOFF - timedelta(seconds=1), **common)


def _istanbul_materializer_carrier(monkeypatch):
    """Run the real materializer carrier seam on the Istanbul fixture shape."""
    from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
    from src.data.replacement_forecast_materializer import (
        ReplacementForecastMaterializeRequest,
        _day0_noaa_future_vector_members,
        _day0_noaa_preliminary_carrier,
    )
    import src.data.day0_hourly_vectors as hourly
    from src.data.day0_hourly_vectors import Day0HourlyVector

    target = date(2026, 8, 24)
    times = tuple(f"{target.isoformat()}T{hour:02d}:00" for hour in range(24))
    vectors = tuple(
        Day0HourlyVector(
            model=model, city="Istanbul", target_date=target.isoformat(),
            timezone_name="Europe/Istanbul", captured_at="2026-08-24T08:30:00+00:00",
            times=times,
            temps_c=tuple(20.0 + hour * (0.2 if model == "ecmwf_ifs" else 0.25) for hour in range(24)),
        )
        for model in ("ecmwf_ifs", "icon_global")
    )
    monkeypatch.setattr(hourly, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs", "icon_global"])
    monkeypatch.setattr(hourly, "read_freshest_day0_hourly_vectors", lambda **_kwargs: list(vectors))
    likelihood = {
        "semantics": "same_station_preliminary_report_survival_likelihood_jeffreys_prior_only_v1",
        "cutoff": "2026-08-24T09:30:00+00:00", "successes": [], "failures": [],
        "unconfirmed_awc_ids": [], "alpha": 0.5, "beta": 0.5,
        "evidence_basis": "no_confirmed_same_station_transitions", "station_id": "LTFM",
        "source_channel_pair": {"awc": "aviationweather_metar", "ogimet": "ogimet_metar_ltfm"},
    }
    likelihood["identity_hash"] = hashlib.sha256(
        json.dumps(likelihood, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "src.data.day0_observation_reader.same_station_preliminary_report_survival_likelihood",
        lambda *_args, **_kwargs: {**likelihood, "boundary_survival_probability": 0.95},
    )
    local_tz = ZoneInfo("Europe/Istanbul")
    anchor = OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Europe/Istanbul", target_local_date=target, high_c=25.0, low_c=18.0,
        sample_count=1, contributing_local_times=(datetime(2026, 8, 24, 0, tzinfo=local_tz),),
        contributing_valid_times_utc=(datetime(2026, 8, 23, 21, tzinfo=UTC),),
        source_cycle_time=datetime(2026, 8, 24, 6, tzinfo=UTC),
    )
    request = ReplacementForecastMaterializeRequest(
        city="Istanbul", city_id="Istanbul", city_timezone="Europe/Istanbul", target_date=target,
        temperature_metric="high", baseline_source_run_id="b0-istanbul",
        baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at="2026-08-24T08:00:00+00:00", openmeteo_anchor=anchor,
        openmeteo_source_run_id="om-istanbul", openmeteo_source_available_at="2026-08-24T08:10:00+00:00",
        bins=(
            SimpleNamespace(lower_c=None, upper_c=29.0),
            SimpleNamespace(lower_c=30.0, upper_c=30.0),
            SimpleNamespace(lower_c=31.0, upper_c=None),
        ),
        source_cycle_time="2026-08-24T06:00:00+00:00", computed_at="2026-08-24T09:30:00+00:00",
        day0_observed_extreme_c=30.0, day0_observed_extreme_source="ogimet_metar_ltfm",
        day0_observed_extreme_observation_time="2026-08-24T09:00:00+00:00",
        day0_observed_extreme_sample_count=24, day0_observed_extreme_unit="C",
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE observation_prints (
            id INTEGER PRIMARY KEY, city TEXT, station_id TEXT, source_channel TEXT,
            publish_ts_utc TEXT, value_native REAL, unit TEXT, fetched_at_utc TEXT, raw_report TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO observation_prints VALUES (?,?,?,?,?,?,?,?,?)",
        (1, "Istanbul", "LTFM", "ogimet_metar_ltfm", "2026-08-24T06:20:00+00:00", 26.0, "C",
         "2026-08-24T06:25:00+00:00", None),
    )
    future, path_sigma, _cutoff = _day0_noaa_future_vector_members(conn, request, metric="high")
    carrier, _likelihood = _day0_noaa_preliminary_carrier(
        conn, request, metric="high", future_members_c=future, bins=request.bins,
        path_error_sigma_c=path_sigma,
    )
    conn.close()
    return carrier


def test_switch_off_materializer_carrier_is_byte_identical(monkeypatch):
    """OFF: the live materializer seam never loads the artifact and keeps the V2 carrier."""
    loads = []
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: False)
    monkeypatch.setattr(terminal, "load_resolver_terminal_artifact", lambda *a, **k: loads.append(1))
    off = _istanbul_materializer_carrier(monkeypatch)
    assert loads == []
    assert off["operator"] == "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2"
    assert "resolver_terminal_input" not in off
    # Pin: identical inputs reproduce the identical carrier (content identity + q).
    again = _istanbul_materializer_carrier(monkeypatch)
    assert again == off


def test_switch_on_materializer_carrier_composes_resolver_terminal(monkeypatch):
    inp = _input(
        levels=(("L0|high", 1000, 2, 1.0), ("L1|x", 400, 1, 60.0), ("L2|x", 90, 0, 60.0), ("L3|x", 30, 0, 60.0)),
        g_levels=((2, 0, 0, 0), (1, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)),
    )
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: True)
    monkeypatch.setattr(terminal, "resolve_day0_resolver_terminal_input", lambda **_k: inp)
    on = _istanbul_materializer_carrier(monkeypatch)
    assert on["operator"] == DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER
    assert Day0ResolverTerminalInput.from_payload(on["resolver_terminal_input"]) == inp
    # Observed 30 C sits in the "30" bin; non-violation = bins 30 and 31+.
    assert sum(on["q"][1:]) == pytest.approx(inp.nonviolation_probability, abs=1e-12)


def test_switch_on_adapter_rebuild_then_strict_replay_hko_low(monkeypatch):
    """Adapter seam: ON rebuild composes resolver-graded q; strict replay reproduces it;
    OFF refuses to replay a resolver carrier under the survival revision."""
    import src.engine.event_reactor_adapter as era
    from src.config import runtime_cities_by_name
    from src.types.temperature import TemperatureDelta

    city = runtime_cities_by_name()["Hong Kong"]
    semantics = SettlementSemantics.for_city(city)
    future, final_centers = (25.4, 25.8, 28.4, 26.0), (24.0,)
    decision_time = datetime(2026, 9, 3, 4, 58, 45, tzinfo=UTC)
    likelihood = {
        "semantics": "hko_provisional_monotonic_survival_beta_jeffreys_v1",
        "lookback_start": "2026-08-04", "lookback_end": "2026-09-03", "transition_count": 30,
        "retraction_count": 0, "median_update_seconds": 600.0, "projected_remaining_updates": 5,
    }
    likelihood["identity_hash"] = hashlib.sha256(
        json.dumps(likelihood, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    likelihood["boundary_survival_probability"] = 0.97
    inp = _input(
        levels=(("L0|low", 400, 2, 1.0), ("L1|x", 200, 0, 90.0), ("L2|x", 60, 0, 90.0), ("L3|x", 60, 0, 90.0)),
        g_levels=((2, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)),
    )
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: True)
    monkeypatch.setattr(terminal, "resolve_day0_resolver_terminal_input", lambda **_k: inp)
    monkeypatch.setattr(
        "src.signal.ensemble_signal.sigma_instrument_for_city", lambda _city: TemperatureDelta(0.0, "C")
    )
    monkeypatch.setattr(era, "_day0_extra_member_sigma_native", lambda **_k: 0.4)
    payload = {
        "city": "Hong Kong", "target_date": "2026-09-03", "metric": "low", "rounded_value": 25.0,
        "low_so_far": 25.9, "settlement_source": "hko_hourly_accumulator",
        "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
        "_edli_day0_probability_boundary_native": 25.9,
        "_edli_day0_provisional_boundary_survival_probability": 0.97,
        "_edli_day0_provisional_revision_likelihood": likelihood,
        "_edli_day0_redecision_authority_scope": "held_exposure_current_bundle_day0_only_v1",
        "_edli_day0_remaining_vector_witness": {
            key: {"ecmwf_ifs": "x"} if key.endswith("_by_model") or key.endswith("_by_model_utc") else ["ecmwf_ifs"]
            for key in (
                "expected_models", "actual_models", "capture_times_by_model_utc",
                "provider_source_cycle_time_by_model_utc", "provider_source_available_at_by_model_utc",
                "source_run_id_by_model", "provider_run_id_by_model", "request_hash_by_model",
            )
        } | {"vector_id": "hko-vector"},
    }
    family = SimpleNamespace(
        city="Hong Kong", target_date="2026-09-03", metric="low",
        candidates=[
            SimpleNamespace(bin=SimpleNamespace(low=None, high=23)),
            SimpleNamespace(bin=SimpleNamespace(low=24, high=24)),
            SimpleNamespace(bin=SimpleNamespace(low=25, high=25)),
            SimpleNamespace(bin=SimpleNamespace(low=26, high=None)),
        ],
    )
    era._rebuild_decision_time_day0_carrier(
        payload=payload, family=family, unit="C", decision_time=decision_time,
        future_extremes_c=future, final_extreme_centers_c=final_centers,
        authority_kind="held_current_remaining_path", entry_authority=False,
    )
    assert payload["_edli_day0_probability_operator"] == DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER
    q = np.asarray(payload["_edli_day0_remaining_carrier_q"])
    # A = floor(25.9) = 25 under HKO; LOW non-violation = bins at or below 25.
    assert q[:3].sum() == pytest.approx(inp.nonviolation_probability, abs=1e-12)
    from src.types.market import Bin

    bins = [Bin(None, 23, "C", "23C or below"), Bin(24, 24, "C", "24C"), Bin(25, 25, "C", "25C"),
            Bin(26, None, "C", "26C or above")]
    replay = era._day0_remaining_p_raw_vector(
        np.sort(np.asarray((*future, *final_centers))), city=city, settlement_semantics=semantics,
        bins=bins, payload=payload, extra_member_sigma=0.0, decision_time=decision_time,
    )
    assert replay.tolist() == payload["_edli_day0_remaining_carrier_q"]
    sampler = era._Day0CarrierRowSampler.from_payload(payload)
    assert sampler.rows.shape == (500, 4)
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: False)
    with pytest.raises(ValueError, match="SWITCH_OFF"):
        era._day0_remaining_p_raw_vector(
            np.sort(np.asarray((*future, *final_centers))), city=city, settlement_semantics=semantics,
            bins=bins, payload=payload, extra_member_sigma=0.0, decision_time=decision_time,
        )


def test_resolver_carrier_replays_byte_identically_from_persisted_input():
    """The adapter's strict replay rebuilds from the persisted typed input only."""
    inp = _input(
        levels=(("L0|x", 300, 4, 1.0), ("L1|x", 120, 2, 80.0), ("L2|x", 40, 1, 80.0), ("L3|x", 9, 0, 80.0)),
        g_levels=((3, 1, 0, 0), (2, 0, 0, 0), (1, 0, 0, 0), (0, 0, 0, 0)),
    )
    kwargs = dict(
        future_extremes_c=(29.1, 30.6, 31.2),
        final_extreme_centers_c=(31.0,),
        boundary_scenarios=((30.2, 1.0),),
        metric="high",
        path_error_sigma_c=0.5,
        instrument_sigma_c=0.28,
        bin_bounds_c=BINS_C,
        n_point=100,
        n_samples=500,
        identity_inputs={"city": "Tokyo", "unit": "C", "station_id": "RJTT"},
        settlement_semantics=C_SEM,
    )
    built = build_day0_remaining_probability_carrier(resolver_terminal=inp, **kwargs)
    replayed = build_day0_remaining_probability_carrier(
        operator=built["operator"],
        resolver_terminal=Day0ResolverTerminalInput.from_payload(
            json.loads(json.dumps(built["resolver_terminal_input"]))
        ),
        **kwargs,
    )
    assert replayed == built
    with pytest.raises(ValueError, match="OPERATOR_INPUT_MISMATCH"):
        build_day0_remaining_probability_carrier(operator=built["operator"], **kwargs)


def test_resolver_composition_shifts_template_before_censoring_at_observed():
    """The live center shift moves the remaining members first; the censor at A and
    the terminal composition come after, so non-violation mass is still exactly s."""
    inp = _input(
        levels=(("L0|x", 300, 4, 1.0), ("L1|x", 120, 2, 80.0), ("L2|x", 40, 1, 80.0), ("L3|x", 9, 0, 80.0)),
        g_levels=((3, 1, 0, 0), (2, 0, 0, 0), (1, 0, 0, 0), (0, 0, 0, 0)),
    )
    grid = ((None, 25.0), *((float(v), float(v)) for v in range(26, 36)), (36.0, None))
    common = dict(
        future_extremes_c=(29.1, 30.6, 31.2), final_extreme_centers_c=(31.0,), metric="high",
        path_error_sigma_c=0.5, instrument_sigma_c=0.28, bin_bounds_c=grid, n_point=100, n_samples=500,
        identity_inputs={"city": "Tokyo", "unit": "C", "station_id": "RJTT"}, settlement_semantics=C_SEM,
    )
    shift = 0.6
    shifted = build_day0_remaining_probability_carrier(
        boundary_scenarios=((30.2, 1.0),), resolver_terminal=inp, remaining_center_bias_native=shift, **common
    )
    manual = build_day0_remaining_probability_carrier(
        boundary_scenarios=((30.2, 1.0),),
        resolver_terminal=inp,
        **{**common, "future_extremes_c": tuple(v + shift for v in common["future_extremes_c"])},
    )
    unshifted = build_day0_remaining_probability_carrier(
        boundary_scenarios=((30.2, 1.0),), resolver_terminal=inp, **common
    )
    assert shifted["q"] == manual["q"]  # shift enters on the members, before censoring
    assert shifted["q"] != unshifted["q"]
    assert shifted["content_identity"] != unshifted["content_identity"]
    observed_index = [low for low, _ in grid].index(30.0)
    assert sum(shifted["q"][observed_index:]) == pytest.approx(inp.nonviolation_probability, abs=1e-12)
    # The boundary and final-extreme centers are never shifted: a shift that would
    # put members above A cannot move mass below A.
    assert sum(shifted["q"][:observed_index]) == pytest.approx(1.0 - inp.nonviolation_probability, abs=1e-12)


def test_switch_on_replay_corpus_rebuild_composes_shift_then_resolver(monkeypatch):
    """The replay corpus re-scores through the same adapter rebuild: shift, censor, compose."""
    from tests.calibration import test_probability_replay_corpus as corpus_fixture
    import src.calibration.day0_remaining_bias as bias_mod
    import src.engine.event_reactor_adapter as era

    inp = _input(
        levels=(("L0|low", 500, 3, 1.0), ("L1|x", 200, 1, 90.0), ("L2|x", 60, 0, 90.0), ("L3|x", 20, 0, 90.0)),
        g_levels=((3, 0, 0, 0), (1, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)),
    )
    shifts = []

    def fake_bias(**kwargs):
        shifts.append(kwargs)
        return bias_mod.Day0RemainingBias(-0.3, bias_mod.APPLIED, "test-artifact")

    monkeypatch.setattr(bias_mod, "day0_remaining_bias", fake_bias)
    monkeypatch.setattr("src.config.day0_resolver_terminal_residual_enabled", lambda: True)
    monkeypatch.setattr(terminal, "resolve_day0_resolver_terminal_input", lambda **_k: inp)
    captured = {}
    original = era._rebuild_decision_time_day0_carrier

    def spy(**kwargs):
        original(**kwargs)
        captured.update(kwargs["payload"])

    monkeypatch.setattr(era, "_rebuild_decision_time_day0_carrier", spy)
    observation = corpus_fixture._observation()
    q, reason, _ = corpus_fixture._replay_state(observation)
    assert reason is None and shifts
    assert captured["_edli_day0_probability_operator"] == DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER
    assert captured["_edli_day0_remaining_center_bias_c"] == -0.3
    assert Day0ResolverTerminalInput.from_payload(captured["_edli_day0_resolver_terminal_input"]) == inp
    carrier_q = captured["_edli_day0_remaining_carrier_q"]
    # LOW, A = R(9.0) = 9: non-violation = bins <= 9 ("8 or below", "9").
    assert carrier_q[0] + carrier_q[1] == pytest.approx(inp.nonviolation_probability, abs=1e-12)
    # No impossible-bin mask after the composition: held q is the carrier's own bin mass.
    assert q == pytest.approx(carrier_q[corpus_fixture.SELECTED], abs=1e-12)


def test_precision_class_at_cutoff_ignores_reports_possessed_after_it():
    """The fitter's tenth/whole stratum uses only AWC reports published AND possessed
    before the walk-forward cutoff; later rows cannot flip the class of earlier labels."""
    from scripts import fit_day0_resolver_terminal_residual as fit

    before = [
        fit.TenthEvidence(CUTOFF - timedelta(hours=h), CUTOFF - timedelta(hours=h) + timedelta(minutes=2), False)
        for h in range(1, 5)
    ]
    # Published before the cutoff but possessed only after it (late fetch), and
    # rows published after the cutoff: both carry T-groups and would flip RJTT.
    late_fetch = [
        fit.TenthEvidence(CUTOFF - timedelta(hours=h), CUTOFF + timedelta(minutes=1), True)
        for h in range(1, 9)
    ]
    after = [
        fit.TenthEvidence(CUTOFF + timedelta(hours=h), CUTOFF + timedelta(hours=h), True)
        for h in range(1, 20)
    ]
    # Possessed before the cutoff but stamped with a publication clock after it
    # (a skewed provider clock): the publish bound must exclude these on its own.
    skewed_publish = [
        fit.TenthEvidence(CUTOFF + timedelta(minutes=h), CUTOFF - timedelta(minutes=1), True)
        for h in range(1, 9)
    ]
    evidence = {"RJTT": before + late_fetch + after + skewed_publish}
    assert fit.channel_classes_at(evidence, CUTOFF) == {"RJTT": "metar_whole"}
    assert fit.channel_classes_at(evidence, CUTOFF + timedelta(days=1)) == {"RJTT": "metar_tenth"}
    assert fit.channel_classes_at({"RJTT": after}, CUTOFF) == {}

    label = _label(station="RJTT", settled=29.0)
    assert label.available_at_utc < CUTOFF
    fitted = fit.fit_at([label], evidence, CUTOFF)
    assert fitted["station_channel"] == {"RJTT": "metar_whole"}
    assert any(key.startswith("L1|noaa_wrh|metar_whole|") for key in fitted["nodes"])
    assert not any("metar_tenth" in key for key in fitted["nodes"])


def test_fitter_reads_canonical_settlement_outcomes_not_legacy_settlements(tmp_path):
    """Labels come from forecasts.settlement_outcomes (canonical), never the
    legacy_archived settlements shell; unit is settlement_unit and a label is
    known at max(settled_at, recorded_at)."""
    from scripts import fit_day0_resolver_terminal_residual as fit
    from src.state.schema.v2_schema import _create_settlement_outcomes

    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    _create_settlement_outcomes(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settlements (
            city TEXT, target_date TEXT, temperature_metric TEXT, settlement_value REAL,
            unit TEXT, settlement_source TEXT, settled_at TEXT, authority TEXT)"""
    )
    wrh = "https://www.weather.gov/wrh/timeseries?site=RJTT"
    conn.execute(
        "INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?)",
        ("Tokyo", "2026-09-10", "high", 99.0, "C", wrh, "2026-09-11T05:00:00+00:00", "VERIFIED"),
    )
    rows = [
        # canonical, learning-final
        ("Tokyo", "2026-09-10", "high", "30C", 31.0, wrh, "2026-09-11T05:00:00+00:00", "VERIFIED",
         "2026-09-11 06:00:00", "C", "VENUE_RESOLVED"),
        # not learning-final: censored
        ("Tokyo", "2026-09-11", "high", "30C", 30.0, wrh, "2026-09-12T05:00:00+00:00", "VERIFIED",
         "2026-09-12T06:00:00+00:00", "C", "UNRESOLVED"),
        # disputed authority: censored
        ("Tokyo", "2026-09-12", "high", "30C", 30.0, wrh, "2026-09-13T05:00:00+00:00", "DISPUTED",
         "2026-09-13T06:00:00+00:00", "C", "VENUE_RESOLVED"),
    ]
    conn.executemany(
        """INSERT INTO settlement_outcomes (city, target_date, temperature_metric, winning_bin,
               settlement_value, settlement_source, settled_at, authority, recorded_at,
               settlement_unit, resolution_state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()

    labels = fit.read_settlements(str(db), "2026-09-01")
    assert labels == [
        {
            "city": "Tokyo",
            "target_date": "2026-09-10",
            "metric": "high",
            "value": 31.0,
            "unit": "C",
            "resolver": "noaa_wrh",
            "available_at": datetime(2026, 9, 11, 6, tzinfo=UTC),
        }
    ]
