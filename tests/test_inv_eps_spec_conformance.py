# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.2 INV-eps-spec-conformance,
#   topology packet "phase0-pr4-decision-group-id"
"""R-4.2: INV-eps-spec-conformance — CI antibody for P_CLAMP_LOW drift.

LIVE TESTS (not xfail): These tests verify the CURRENT documented deviation,
not a future target. They must pass today and catch future silent drift.

Contract:
    P_CLAMP_LOW is 0.01 by deliberate operator decision (2026-05-19).
    This is a documented deviation from zeus_math_spec.md §14.9 (spec: 1e-6).
    If P_CLAMP_LOW ever changes, one of these tests must fail immediately,
    forcing the developer to:
      1. Update zeus_math_spec.md §14.9 to match.
      2. Schedule a full calibration refit (91M rows).
      3. Update EXPECTED_P_CLAMP_LOW in this test.

    This is INV-eps-spec-conformance: the antibody against silent drift.

Test plan:
    T1: P_CLAMP_LOW == 0.01 exactly (drift detector).
    T2: P_CLAMP_HIGH == 0.99 exactly (paired drift detector).
    T3: logit_safe clamping behaviour at boundary: logit_safe(0.005) == logit_safe(0.01).
    T4: logit_safe does NOT clamp at 0.05 (not below P_CLAMP_LOW).
    T5: Deviation from spec is documented — zeus_math_spec.md §14.9 exists and
        contains a reference to 0.01 OR to the documented deviation note.
        (File existence check; does not require parsing the spec.)
"""

import os

import numpy as np
import pytest

from src.calibration.platt import P_CLAMP_HIGH, P_CLAMP_LOW, logit_safe

# Documented operator-approved deviation value (2026-05-19).
# UPDATE THIS if P_CLAMP_LOW is intentionally changed (with spec + refit).
EXPECTED_P_CLAMP_LOW = 0.01
EXPECTED_P_CLAMP_HIGH = 0.99


def test_p_clamp_low_matches_documented_value():
    """P_CLAMP_LOW must equal the operator-approved documented deviation."""
    assert P_CLAMP_LOW == EXPECTED_P_CLAMP_LOW, (
        f"INV-eps-spec-conformance VIOLATED: P_CLAMP_LOW={P_CLAMP_LOW!r} "
        f"differs from documented value {EXPECTED_P_CLAMP_LOW!r}. "
        f"If intentional: update zeus_math_spec.md §14.9, schedule refit, "
        f"and update EXPECTED_P_CLAMP_LOW in this test."
    )


def test_p_clamp_high_matches_documented_value():
    """P_CLAMP_HIGH must equal the operator-approved documented value."""
    assert P_CLAMP_HIGH == EXPECTED_P_CLAMP_HIGH, (
        f"INV-eps-spec-conformance VIOLATED: P_CLAMP_HIGH={P_CLAMP_HIGH!r} "
        f"differs from documented value {EXPECTED_P_CLAMP_HIGH!r}."
    )


def test_logit_safe_clamps_below_p_clamp_low():
    """logit_safe(p) for p < P_CLAMP_LOW must equal logit_safe(P_CLAMP_LOW)."""
    below = 0.005  # below the 0.01 clamp
    result_below = logit_safe(below)
    result_at_clamp = logit_safe(P_CLAMP_LOW)
    assert result_below == pytest.approx(result_at_clamp), (
        f"logit_safe({below}) = {result_below} != logit_safe({P_CLAMP_LOW}) = {result_at_clamp}"
    )


def test_logit_safe_does_not_clamp_above_p_clamp_low():
    """logit_safe(p) for p > P_CLAMP_LOW must NOT equal logit_safe(P_CLAMP_LOW)."""
    above = 0.05
    result_above = logit_safe(above)
    result_at_clamp = logit_safe(P_CLAMP_LOW)
    assert result_above != pytest.approx(result_at_clamp), (
        f"logit_safe({above}) unexpectedly equals logit_safe({P_CLAMP_LOW})"
    )


def test_zeus_math_spec_section_14_9_exists():
    """zeus_math_spec.md must exist (deviation documentation anchor)."""
    spec_path = os.path.join(
        os.path.dirname(__file__), "..", "docs", "reference", "zeus_math_spec.md"
    )
    assert os.path.isfile(spec_path), (
        f"zeus_math_spec.md not found at {spec_path}. "
        f"The §14.9 deviation documentation anchor is missing."
    )
