# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: U1 antibody — pin HKO floor-truncate semantic at the
#          SettlementSemantics dispatch boundary. Locks the cross-module
#          relationship: HK city + decimal °C input → floor() not wmo_half_up().
#          Catches any future refactor that "standardizes" rounding rules and
#          silently drops the HKO exception (would re-poison: 5/14 vs 14/14
#          match rate per known_gaps.md:141-148).
# Reuse: Covers src/contracts/settlement_semantics.py SettlementSemantics.for_city()
#        HK branch + the np.floor dispatch at the rounding-rule layer. Workbook
#        U1 entry was a triple-deliverable (code + AGENTS.md + antibody);
#        code half absorbed by predecessor commit d99273a; AGENTS.md half
#        absorbed by removal of WMO-universal claim; this slice ships the
#        antibody half.
# Authority basis: docs/operations/task_2026-04-26_u1_hk_floor_antibody/plan.md
#   §4 antibody design + parent
#   docs/operations/task_2026-04-26_live_readiness_completion/plan.md K4.U1.
"""U1 antibody — HK SettlementSemantics floor-truncate pin.

Why this exists even though production code is already correct:
- HKO floor decision (oracle_truncate alias) is a CRITICAL invariant — switching
  back to wmo_half_up would silently re-poison HKO settlement (per known_gaps:
  5/14 vs 14/14 match rate against PM oracle).
- No dedicated test pins the dispatch at SettlementSemantics.for_city(hk).
- A future "let's standardize all rounding to wmo_half_up" refactor could drop
  the HKO exception unnoticed. This antibody fires on that.

Why oracle_truncate IS floor (not a separate rounding rule):
- src/contracts/settlement_semantics.py:71-79 dispatches
  `rounding_rule in ("floor", "oracle_truncate")` to `np.floor(scaled)`.
- The named alias (oracle_truncate) carries justification: "UMA voters treat
  decimal °C as truncated: 28.7 hasn't reached 29, so it's 28."
- Test 7 pins the alias contract — if a future split makes oracle_truncate
  diverge from floor, fires.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import City
from src.contracts.settlement_semantics import SettlementSemantics


def _hk_city() -> City:
    """Hong Kong configuration: HKO source, °C unit, no WU station.

    Mirrors `config/cities.json` Hong Kong entry. wu_station="" because
    City dataclass requires str; HK has no WU station.
    """
    return City(
        name="Hong Kong",
        lat=22.303611,
        lon=114.171944,
        timezone="Asia/Hong_Kong",
        settlement_unit="C",
        cluster="Hong Kong",
        wu_station="",
        settlement_source_type="hko",
    )


def _wu_celsius_city() -> City:
    """Control: a non-HK °C city that uses WU + wmo_half_up.

    Taipei is a real Zeus °C city configured via wu_icao path.
    Used in test 6 to confirm HK exception is HK-only, not all-°C.
    """
    return City(
        name="Taipei",
        lat=25.0696,
        lon=121.5520,
        timezone="Asia/Taipei",
        settlement_unit="C",
        cluster="Taipei",
        wu_station="RCSS",
        settlement_source_type="wu_icao",
    )


# ---------------------------------------------------------------------------
# Dispatch pins (1-3): SettlementSemantics.for_city(hk) returns the right shape
# ---------------------------------------------------------------------------


def test_hk_city_dispatches_to_oracle_truncate():
    """HK city → SettlementSemantics with rounding_rule='oracle_truncate'.

    Pin the dispatch decision. A future refactor that branches on city.unit
    instead of city.settlement_source_type, or that drops the HK case entirely,
    fires here.
    """
    sem = SettlementSemantics.for_city(_hk_city())
    assert sem.rounding_rule == "oracle_truncate", (
        f"HK city must dispatch to oracle_truncate (= floor for UMA-truncation "
        f"semantic). Got {sem.rounding_rule!r}. If switching back to wmo_half_up, "
        f"expect HKO settlement match rate to drop from 14/14 to 5/14 per "
        f"known_gaps.md:141-148."
    )


def test_hk_city_dispatches_to_celsius_unit():
    """HK city → measurement_unit='C'."""
    sem = SettlementSemantics.for_city(_hk_city())
    assert sem.measurement_unit == "C"


def test_hk_resolution_source_is_hko_hq():
    """HK city → resolution_source='HKO_HQ' (label distinct from WU_<station>)."""
    sem = SettlementSemantics.for_city(_hk_city())
    assert sem.resolution_source == "HKO_HQ", (
        f"HKO source label must remain 'HKO_HQ' (distinct from WU_*). "
        f"Got {sem.resolution_source!r}."
    )


# ---------------------------------------------------------------------------
# Numeric behavior (4-5): floor not wmo_half_up
# ---------------------------------------------------------------------------


def test_hk_floor_truncates_decimal_celsius():
    """27.8°C → 27 (floor), NOT 28 (wmo_half_up).

    The exact failure mode known_gaps.md:141-148 documents:
    - PM HK contract: 'temperature range that contains the highest temperature'
    - HKO Daily Extract: 27.8°C
    - PM bin assignment: floor containment → 27 ≤ 27.8 < 28 → '27°C' bin
    - WMO half-up would give floor(27.8+0.5)=28 → '28°C' bin (WRONG)
    """
    sem = SettlementSemantics.for_city(_hk_city())
    assert sem.round_single(27.8) == 27.0, (
        f"HK floor must truncate 27.8 → 27. Got {sem.round_single(27.8)}. "
        f"WMO half-up would give 28; this test catches that regression."
    )


def test_hk_floor_truncates_full_known_gaps_cases():
    """Each of the 3 documented HKO mismatch days produces floor() not wmo_half_up().

    Per known_gaps.md:141-148: '03-18, 03-24, 03-29' were the 3/3 HKO-period
    mismatches that switching to floor fixed (with 0 regressions across 16
    total HK PM markets, all 11 existing matches preserved).

    Representative decimal values that would round differently under the two
    rules:
      28.7°C → floor=28, wmo_half_up=29
      24.5°C → floor=24, wmo_half_up=25
      19.4°C → floor=19, wmo_half_up=19 (control — same outcome)
    """
    sem = SettlementSemantics.for_city(_hk_city())
    cases = [
        (28.7, 28.0),  # floor different from wmo_half_up
        (24.5, 24.0),  # floor different from wmo_half_up
        (19.4, 19.0),  # floor agrees with wmo_half_up (control)
        (15.0, 15.0),  # exact integer (no rounding ambiguity)
    ]
    for raw, expected_floor in cases:
        got = sem.round_single(raw)
        assert got == expected_floor, (
            f"HK floor of {raw} should be {expected_floor}, got {got}. "
            f"Known-gaps mismatch class would re-emerge."
        )


# ---------------------------------------------------------------------------
# Negative pin (6): HK exception does NOT generalize to all °C cities
# ---------------------------------------------------------------------------


def test_wu_celsius_city_still_uses_wmo_half_up():
    """Control — Taipei (wu_icao + °C) still uses wmo_half_up.

    Negative pin: the HK exception is HK-specific (settlement_source_type='hko'),
    NOT a unit-based override. A future refactor that thinks 'HK uses floor
    because it's °C → all °C cities should use floor' would silently break
    Taipei settlement. This test fires.
    """
    sem = SettlementSemantics.for_city(_wu_celsius_city())
    assert sem.rounding_rule == "wmo_half_up", (
        f"WU °C city (Taipei) must still use wmo_half_up. Got {sem.rounding_rule!r}. "
        f"If this fails, the HK exception was over-generalized."
    )
    # And empirically: 27.8 rounds UP to 28 under WMO (vs HK floor → 27).
    assert sem.round_single(27.8) == 28.0


# ---------------------------------------------------------------------------
# Alias-contract pin (7): oracle_truncate ≡ floor
# ---------------------------------------------------------------------------


def test_oracle_truncate_semantically_equivalent_to_floor():
    """oracle_truncate and floor rules produce identical output for same input.

    The alias contract: oracle_truncate is a NAMED floor with HKO/UMA
    justification. A future split where oracle_truncate diverges from floor
    (e.g., adds an offset, switches dispatch) would fire here.
    """
    floor_sem = SettlementSemantics(
        resolution_source="test_floor",
        measurement_unit="C",
        precision=1.0,
        rounding_rule="floor",
        finalization_time="12:00:00Z",
    )
    oracle_sem = SettlementSemantics(
        resolution_source="test_oracle",
        measurement_unit="C",
        precision=1.0,
        rounding_rule="oracle_truncate",
        finalization_time="12:00:00Z",
    )
    samples = np.array([27.8, 28.7, 24.5, 19.4, 15.0, -3.2, 0.0, 99.999])
    floor_out = floor_sem.round_values(samples)
    oracle_out = oracle_sem.round_values(samples)
    np.testing.assert_array_equal(
        floor_out, oracle_out,
        err_msg="oracle_truncate diverged from floor — alias contract broken."
    )
