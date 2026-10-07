# Created: 2026-04-27 (BATCH C of 2026-04-27 harness debate executor work)
# Last reused/audited: 2026-10-07
# Authority basis: docs/operations/task_2026-04-27_harness_debate/round2_verdict.md
#   §1.1 #4 + §4.1 #4 + opponent §3.1 (relationship test for type-encoded HK
#   HKO antibody). Per Fitz "test relationships, not just functions" — these
#   tests verify the cross-module invariant survives, not just the function
#   arithmetic.

"""Relationship tests for SettlementRoundingPolicy + settle_market type encoding.

Three load-bearing relationship tests verify the cross-module invariant that a
wrong (city, policy) pair raises TypeError BEFORE any rounding happens. The
arithmetic correctness of WMO_HalfUp / HKO_Truncation themselves is incidental;
the load-bearing assertion is the type guard at the settle_market boundary.

Test count = 3 (per BATCH C dispatch baseline arithmetic 73 + 3 = 76).
"""
from __future__ import annotations

from decimal import Decimal
import json

import pytest

from src.contracts.settlement_semantics import (
    HKO_Truncation,
    WMO_HalfUp,
    settle_market,
)


@pytest.mark.parametrize("unit,rule", [("C", "oracle_truncate"), ("F", "wmo_half_up")])
def test_frozen_settlement_semantics_does_not_read_current_city(monkeypatch, unit, rule):
    from src.contracts.settlement_semantics import SettlementSemantics
    payload = dict(resolution_source="frozen_station", measurement_unit=unit,
        precision=1., rounding_rule=rule, finalization_time="12:00:00Z")
    monkeypatch.setattr(SettlementSemantics, "for_city", classmethod(
        lambda *_: (_ for _ in ()).throw(AssertionError("current city drift"))))
    restored = SettlementSemantics.from_frozen_payload(payload)
    assert restored.measurement_unit == unit and restored.rounding_rule == rule
    assert restored.round_single(28.7) == (28. if rule == "oracle_truncate" else 29.)


@pytest.mark.parametrize("field,value", [("precision", float("nan")), ("precision", 0.),
    ("precision", True), ("rounding_rule", "unknown"), ("measurement_unit", "K"),
    ("finalization_time", "25:00:00Z"), ("resolution_source", ""), ("extra", 1)])
def test_frozen_settlement_semantics_refuses_invalid_fields(field, value):
    from src.contracts.settlement_semantics import SettlementSemantics
    payload = dict(resolution_source="frozen_station", measurement_unit="C",
        precision=1., rounding_rule="wmo_half_up", finalization_time="12:00:00Z")
    payload[field] = value
    with pytest.raises(ValueError): SettlementSemantics.from_frozen_payload(payload)


@pytest.mark.parametrize("step", [1., 5./9.])
@pytest.mark.parametrize("rule", ["wmo_half_up", "oracle_truncate", "floor", "ceil"])
def test_preimage_axis_quantization_preserves_negative_half_boundary_neighbors(step, rule):
    import numpy as np
    from src.contracts.settlement_semantics import quantize_preimage_axis
    thresholds = np.array([-1.5, -.5, .5, 1.5])*step
    values = np.concatenate([np.nextafter(thresholds, -np.inf), thresholds,
                             np.nextafter(thresholds, np.inf)])
    scaled = values*(1./step)
    rounded = (np.floor(scaled+.5) if rule == "wmo_half_up" else
               np.ceil(scaled) if rule == "ceil" else np.floor(scaled))
    expected = rounded/(1./step)
    np.testing.assert_array_equal(quantize_preimage_axis(values,
        rounding_rule=rule, half_step=step/2.), expected)


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("step", [1., 5./9.])
@pytest.mark.parametrize("rule", ["wmo_half_up", "oracle_truncate", "ceil"])
def test_preimage_zero_sigma_point_and_bootstrap_use_same_extreme_axis(metric, step, rule):
    import numpy as np
    from types import SimpleNamespace
    from src.data import replacement_forecast_materializer as mat
    bins = [SimpleNamespace(bin_id="negative", lower_c=None, upper_c=-step),
        SimpleNamespace(bin_id="zero", lower_c=0., upper_c=0.),
        SimpleNamespace(bin_id="positive", lower_c=step, upper_c=None)]
    mu, obs = .2*step, (.7 if metric == "high" else -.7)*step
    extreme = max(mu, obs) if metric == "high" else min(mu, obs)
    scaled = extreme*(1./step)
    atom = (np.floor(scaled+.5) if rule == "wmo_half_up" else
            np.ceil(scaled) if rule == "ceil" else np.floor(scaled))/(1./step)
    expected = {b.bin_id: float((b.lower_c is None or atom >= b.lower_c)
        and (b.upper_c is None or atom <= b.upper_c)) for b in bins}
    point, capped, uniform = mat._build_scaled_normal_uniform_q(mu=mu, sigma_pred=0.,
        k=1., uniform_w=0., floor_steps=0., bins=bins, half_step=step/2.,
        rounding_rule=rule, day0_obs_extreme_c=obs, metric=metric,
        settlement_step_c=step, settlement_sigma_floor_c=None, city_unit="C")
    assert point == expected and capped == [] and uniform is False
    lower, upper, samples = mat._build_fused_q_bounds(mu_star=mu, center_sigma_c=0.,
        predictive_sigma_c=0., bins=bins, half_step=step/2., rounding_rule=rule,
        q_point=point, n_draws=8, return_samples=True,
        day0_observed_extreme_c=obs, day0_metric=metric)
    assert lower == upper == point
    assert samples == {key: [value]*8 for key, value in point.items()}


@pytest.mark.parametrize("values,step,rule", [([float("nan")], .5, "wmo_half_up"),
    ([float("inf")], .5, "wmo_half_up"), ([0.], 0., "wmo_half_up"),
    ([0.], -.5, "wmo_half_up"), ([0.], float("inf"), "wmo_half_up"),
    ([0.], True, "wmo_half_up"), ([0.], .5, "unknown")])
def test_preimage_axis_invalid_values_step_or_rule_refuse(values, step, rule):
    from src.contracts.settlement_semantics import quantize_preimage_axis
    with pytest.raises(ValueError):
        quantize_preimage_axis(values, rounding_rule=rule, half_step=step)


@pytest.mark.parametrize("metric", ["high", "low"])
def test_hko_selection_keeps_original_metric_provenance_without_publication_authority(metric):
    from src.config import City
    from src.data.settlement_observation_selection import observation_selection

    city = City(name="Hong Kong", lat=22.3, lon=114.2, timezone="Asia/Hong_Kong",
                settlement_unit="C", cluster="HK", wu_station="HKO",
                country_code="HK", settlement_source_type="hko")
    original = json.dumps({"source_entity": {
        "entity_sha256": ("a" if metric == "high" else "b") * 64,
        "entity_bytes_b64": "e30=", "source_issued_at_utc": None,
        "capture_received_at_utc": "2026-09-29T00:00:00Z",
        "first_publication": True,
    }})
    row = {metric + "_provenance_metadata": original,
           "station_id": "HKO", "fetched_at": "2026-09-29T00:00:00Z",
           ("low" if metric == "high" else "high") + "_provenance_metadata": "twin"}
    _, selected = observation_selection(None, city, "2026-09-27", "hko_daily_api",
                                         row=row, metric=metric)
    assert selected["provenance_metadata"] == original
    assert selected["source_entity"] == json.loads(original)["source_entity"]
    assert selected["temperature_metric"] == metric
    assert selected["source_grade"] == "UNKNOWN"
    from src.contracts.settlement_semantics import SettlementSemantics
    sem = SettlementSemantics.for_city(city)
    assert sem.precision == 1.0
    assert sem.assert_settlement_value(32.7) == 32.0


def test_hko_policy_required_for_hong_kong():
    """RELATIONSHIP: HK city + WMO policy → TypeError (wrong rounding for HK).

    Antibody for the YAML caution row in fatal_misreads.yaml:hong_kong_hko_explicit_caution_path.
    Type-encoded so the wrong combination is unconstructable, not merely
    documented (per Fitz Constraint #1).
    """
    with pytest.raises(TypeError, match=r"Hong Kong.*require.*HKO_Truncation"):
        settle_market("Hong Kong", Decimal("28.7"), WMO_HalfUp())


def test_hko_policy_invalid_for_non_hong_kong():
    """RELATIONSHIP: non-HK city + HKO policy → TypeError.

    HKO truncation is the wrong rounding semantics for any non-HK market;
    using it on (e.g.) New York would systematically produce 1°F-low
    settlement values vs the WU integer °F oracle.
    """
    with pytest.raises(TypeError, match=r"HKO_Truncation.*Hong Kong only"):
        settle_market("New York", Decimal("74.5"), HKO_Truncation())


def test_invalid_policy_type_rejected():
    """RELATIONSHIP: non-policy object → TypeError before any rounding happens.

    Defends the type contract at the settle_market boundary: only objects
    inheriting from SettlementRoundingPolicy may decide a settlement value;
    duck-typed substitutes are rejected.
    """
    class FakePolicy:  # NOT a SettlementRoundingPolicy subclass.
        def round_to_settlement(self, x: Decimal) -> int:
            return int(x)

    with pytest.raises(TypeError, match=r"requires a SettlementRoundingPolicy"):
        settle_market("New York", Decimal("74.5"), FakePolicy())  # type: ignore[arg-type]


# SIDECAR-3 (2026-04-28): C4 negative-half regression tests. Critic batch_C_review
# caught silent divergence between WMO_HalfUp (originally Decimal ROUND_HALF_UP =
# half-away-from-zero, -3.5→-4) and legacy round_wmo_half_up_value (np.floor(x+0.5) =
# asymmetric toward +∞, -3.5→-3). Legacy is the documented choice (file docstring
# settlement_semantics.py:19 + docs/reference/modules/contracts.md:89). DB has
# 11 negative settled values (-7..-1); raw forecast Monte Carlo can produce -X.5
# in NYC/Chicago winter — silent drift would have shifted settlement by 1°C on
# negative-half boundary cases. Three regression tests pin the legacy semantic.

def test_wmo_half_up_negative_half_rounds_toward_positive_infinity():
    """C4 regression: -3.5 → -3 (asymmetric half-up matches legacy + WMO 306)."""
    policy = WMO_HalfUp()
    assert policy.round_to_settlement(Decimal("-3.5")) == -3
    assert policy.round_to_settlement(Decimal("-0.5")) == 0
    assert policy.round_to_settlement(Decimal("-100.5")) == -100


def test_wmo_half_up_positive_half_rounds_up_unchanged():
    """Positive half-values unaffected by C4 fix; both semantics agree at +X.5."""
    policy = WMO_HalfUp()
    assert policy.round_to_settlement(Decimal("3.5")) == 4
    assert policy.round_to_settlement(Decimal("100.5")) == 101


def test_wmo_half_up_matches_legacy_round_wmo_half_up_value():
    """C4 regression: ABC must match legacy round_wmo_half_up_value byte-for-byte."""
    from src.contracts.settlement_semantics import round_wmo_half_up_value
    policy = WMO_HalfUp()
    test_cases = [3.5, -3.5, 0.5, -0.5, 28.5, -28.5, -100.5, 28.7, -28.7]
    for x in test_cases:
        legacy = int(round_wmo_half_up_value(x))
        new = policy.round_to_settlement(Decimal(str(x)))
        assert legacy == new, f"divergence at {x}: legacy={legacy} new={new}"


# INV-X — for_city() routing antibody (ultrareview25_remediation 2026-05-01 P0-5)
def test_settlement_semantics_construction_routes_through_for_city():
    """RELATIONSHIP antibody: production code constructs SettlementSemantics
    ONLY through the `for_city()` factory (or the per-unit
    `default_wu_fahrenheit`/`default_wu_celsius` helpers it composes).

    This is the SOCIAL gate that makes the wrong-rounding-for-wrong-city
    failure mode structurally impossible WITHOUT requiring the type-encoded
    `settle_market()` migration. The factory dispatches:
      - settlement_source_type == 'hko'  → rounding_rule='oracle_truncate'
      - everything else                  → rounding_rule='wmo_half_up'
    so a caller cannot accidentally apply WMO half-up to Hong Kong (or
    oracle_truncate to anywhere else) without bypassing the factory.

    The 2026-05-01 review (docs/operations/repo_review_2026-05-01/SYNTHESIS.md
    P0-5 reclassification) found that production has zero direct
    `SettlementSemantics(...)` constructor calls outside settlement_semantics.py
    itself. This test pins that discipline so a future agent cannot
    silently introduce a direct construction with arbitrary rounding_rule —
    the test fails immediately and the maintainer has to either (a) route the
    new call through `for_city()`, or (b) extend the factory to handle the
    new dispatch case.

    settle_market() / SettlementRoundingPolicy / WMO_HalfUp / HKO_Truncation
    remain as the FUTURE TYPE-ENCODED migration target (Tier 3 P8 per the
    settlement_semantics.py:194 author note). Until that migration lands,
    the SOCIAL gate enforced by this test is what holds the line.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    semantics_file = repo_root / "src/contracts/settlement_semantics.py"

    # Pattern: a line that constructs SettlementSemantics with parens, e.g.
    # `SettlementSemantics(`, `cls(`, `= SettlementSemantics(`. We allow lines
    # inside settlement_semantics.py itself (factory + classmethods) and
    # rule out anywhere else under src/.
    construct_re = re.compile(r"\bSettlementSemantics\s*\(")

    offending = []
    for path in src_dir.rglob("*.py"):
        if path == semantics_file:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            # Skip imports and type annotations — they reference the class but
            # don't construct an instance.
            if stripped.startswith(("import ", "from ", "#")):
                continue
            if "->" in line and "SettlementSemantics" in line.split("->")[1]:
                continue
            if construct_re.search(line):
                offending.append(f"{path.relative_to(repo_root)}:{lineno}: {stripped}")

    assert not offending, (
        "INV-X violation: direct SettlementSemantics(...) construction "
        "detected outside src/contracts/settlement_semantics.py. Route the "
        "construction through `SettlementSemantics.for_city(city)` instead "
        "so the city↔rounding_rule dispatch contract holds. If a new "
        "settlement source family is being added, extend `for_city()` (and "
        "this test's docstring), not the call site. Offending lines:\n  "
        + "\n  ".join(offending)
    )

    # Also assert no `rounding_rule="..."` kwarg-with-string-literal appears
    # outside the canonical module — that's the dispatch-bypass shape (a
    # caller hard-coding a rounding rule rather than going through
    # for_city()). Plain attribute reads like `r = sem.rounding_rule` are
    # legit and not flagged.
    rule_re = re.compile(r"\brounding_rule\s*=\s*['\"]")
    rule_offenders = []
    for path in src_dir.rglob("*.py"):
        if path == semantics_file:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if rule_re.search(line):
                rule_offenders.append(
                    f"{path.relative_to(repo_root)}:{lineno}: {line.strip()}"
                )

    assert not rule_offenders, (
        "INV-X violation: bare `rounding_rule='...'` literal outside "
        "src/contracts/settlement_semantics.py. The string-dispatch "
        "rounding_rule must only appear inside the canonical module so the "
        "for_city() factory is the single source of dispatch. Offending "
        "lines:\n  " + "\n  ".join(rule_offenders)
    )
