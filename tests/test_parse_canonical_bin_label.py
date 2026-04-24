# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: S2.4 data-readiness hardening tail
# (docs/operations/task_2026-04-23_midstream_remediation/); closure-banner
# item 6 / rule 15 (NH-E1) — strict parser for canonical bin labels so
# silent trailing-garbage misparses become structurally impossible.

"""Antibody for strict canonical bin label parsing (NH-E1).

Pre-S2.4, `_parse_temp_range` used `re.search` on unanchored patterns.
A near-canonical label with trailing garbage (e.g. "17°Cfoo") silently
matched as point bin 17.0, leaking garbage into settlement authority.

Post-S2.4, `_parse_canonical_bin_label` uses `re.fullmatch` so the
ENTIRE input must match one of 4 canonical shapes. Everything else
returns None.

This test file pins:
1. Round-trip: every `_canonical_bin_label` output parses back cleanly.
2. Rejection: garbage-suffix, garbage-prefix, unicode shoulders,
   float degrees, empty/non-string inputs ALL return None.
3. Non-regression: the tolerant `_parse_temp_range` still accepts
   market questions containing extra words (the two parsers have
   distinct responsibilities).
"""

from __future__ import annotations

import pytest

from src.data.market_scanner import _parse_canonical_bin_label, _parse_temp_range
from src.execution.harvester import _canonical_bin_label


# ---------------------------------------------------------------------------
# Round-trip: every canonical label emitted by _canonical_bin_label parses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lo,hi,unit,expected_lo,expected_hi", [
    # point bins
    (17.0, 17.0, "C", 17.0, 17.0),
    (75.0, 75.0, "F", 75.0, 75.0),
    (-5.0, -5.0, "C", -5.0, -5.0),
    (0.0, 0.0, "F", 0.0, 0.0),
    # finite bounded ranges
    (15.0, 16.0, "C", 15.0, 16.0),
    (74.0, 76.0, "F", 74.0, 76.0),
    (-10.0, -5.0, "C", -10.0, -5.0),
    # left-shoulder (... or below)
    (None, 15.0, "C", None, 15.0),
    (None, 50.0, "F", None, 50.0),
    # right-shoulder (... or higher)
    (21.0, None, "C", 21.0, None),
    (90.0, None, "F", 90.0, None),
])
def test_every_canonical_label_round_trips(lo, hi, unit, expected_lo, expected_hi):
    label = _canonical_bin_label(lo, hi, unit)
    assert label is not None, f"_canonical_bin_label returned None for ({lo}, {hi}, {unit})"
    parsed = _parse_canonical_bin_label(label)
    assert parsed is not None, f"strict parser REJECTED a canonical label: {label!r}"
    assert parsed == (expected_lo, expected_hi), (
        f"round-trip mismatch for {label!r}: expected ({expected_lo}, {expected_hi}), got {parsed}"
    )


# ---------------------------------------------------------------------------
# Rejection: trailing garbage, leading garbage, near-canonical but wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_input", [
    "17°Cfoo",           # trailing garbage
    "foo17°C",           # leading garbage
    "17°C ",             # trailing whitespace
    " 17°C",             # leading whitespace
    "17°C\n",            # trailing newline
    "17.5°C",            # float degree (canonical always emits int)
    "17°K",              # wrong unit letter
    "17°",               # missing unit
    "°C",                # missing value
    "17",                # missing unit + degree
    "",                  # empty string
    "Will the temp be 17°C?",  # full market question (should go to _parse_temp_range)
    "17°C or below, actually",  # near-canonical with suffix
    "maybe 17°C",        # prefix word
])
def test_strict_parser_rejects_garbage(bad_input):
    """Every trailing/leading garbage shape returns None under fullmatch."""
    assert _parse_canonical_bin_label(bad_input) is None, (
        f"strict parser WRONGLY accepted: {bad_input!r}"
    )


def test_strict_parser_rejects_unicode_shoulders():
    """Unicode ≥ / ≤ do NOT match canonical text-form — critic-opus C1
    finding from P-E pre-review: silent misparse as point bin."""
    assert _parse_canonical_bin_label("≥21°C") is None
    assert _parse_canonical_bin_label("≤15°C") is None
    assert _parse_canonical_bin_label("≥75°F") is None
    assert _parse_canonical_bin_label("≤32°F") is None


def test_strict_parser_rejects_non_string_input():
    """Non-string inputs must not crash and must not accept."""
    assert _parse_canonical_bin_label(None) is None
    assert _parse_canonical_bin_label(17) is None
    assert _parse_canonical_bin_label(17.5) is None
    assert _parse_canonical_bin_label(["17°C"]) is None
    assert _parse_canonical_bin_label({"label": "17°C"}) is None


# ---------------------------------------------------------------------------
# Non-regression: tolerant _parse_temp_range is unchanged for market questions
# ---------------------------------------------------------------------------


def test_tolerant_parser_still_accepts_market_questions():
    """The strict parser is ADDITIVE — the tolerant _parse_temp_range keeps
    parsing free-form market questions as before (non-regression)."""
    lo, hi = _parse_temp_range("Will the high in NYC be 17°C on April 15?")
    assert (lo, hi) == (17.0, 17.0)

    lo, hi = _parse_temp_range("75-76°F temperature?")
    assert (lo, hi) == (75.0, 76.0)

    lo, hi = _parse_temp_range("Will it be 21°C or higher tomorrow?")
    assert (lo, hi) == (21.0, None)

    lo, hi = _parse_temp_range("Will it be 15°C or below?")
    assert (lo, hi) == (None, 15.0)


def test_tolerant_and_strict_parsers_are_orthogonal():
    """For NON-canonical inputs that the tolerant parser accepts, the strict
    parser correctly rejects — proving they are orthogonal code paths with
    distinct responsibilities."""
    q = "Will the temperature be 17°C on Tuesday?"
    # tolerant accepts (matches the "X°C on" Gamma question pattern)
    assert _parse_temp_range(q) == (17.0, 17.0)
    # strict rejects (full string doesn't match any canonical shape)
    assert _parse_canonical_bin_label(q) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
