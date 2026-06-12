# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: docs/evidence/settlement_guard/2026-06-11_serve_freshest_available_plan.md
#   — OPERATOR LAW (stated 3x, last 2026-06-11 "没有新的就用老的"): the freshest AVAILABLE
#   tradeable row serves; staleness brands age, never turns a scope dark.
"""RELATIONSHIP tests: staleness gates brand, never block.

Cross-module invariant (bundle reader -> staleness policy -> served provenance):
  An expired readiness or an over-bound source cycle on the FRESHEST tradeable row must
  not block the read; the bundle serves with `staleness_violations` in provenance.
  Conversely the violations list is ABSENT when the row is within bounds — the brand is
  precise, not ambient.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data.replacement_forecast_cycle_policy import (
    replacement_source_cycle_max_age_hours,
)

UTC = timezone.utc


def test_staleness_gates_brand_instead_of_block_source_level() -> None:
    """Source pin: the two former hard-BLOCK returns are gone; the brand machinery and
    serve-with-violations path exist. (Full DB-fixture round-trip lives in the reader's
    own suite; this antibody pins the structural conversion so a future edit cannot
    silently reintroduce the dark-scope category.)"""
    source = open("src/data/replacement_forecast_bundle_reader.py").read()
    assert source.count('"BLOCKED", "REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_EXPIRED"') == 0, (
        "the hard readiness-expiry block is dead law (operator: 没有新的就用老的) — "
        "expiry must brand provenance, never return BLOCKED"
    )
    assert "staleness_violations" in source
    assert "REPLACEMENT_0_1_LIVE_AUTHORITY_CYCLE_AGE_EXCEEDS_BOUND" in source


def test_bound_constant_still_single_authority() -> None:
    """The staleness BOUND itself stays alive as the pursuit trigger — branding did not
    delete the derived constant (downloads/polls/re-seeds key off it)."""
    bound = replacement_source_cycle_max_age_hours()
    assert bound >= 24.0  # 2 x 12h live cycle + measured lag; derivation pinned elsewhere


def test_brand_message_carries_age_and_cycle() -> None:
    """The cycle-age violation string is parseable: source_cycle + age_hours present."""
    source = open("src/data/replacement_forecast_bundle_reader.py").read()
    assert "source_cycle={_source_cycle_utc.isoformat()}" in source
    assert "age_hours={_age_hours:.1f}" in source
