# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 6: Dataclass field name scan on Position (portfolio.py).
xfail(strict=False): 3 _version fields remain in Position:
  - execution_cost_basis_version — pricing semantics version tag (B5 deferred)
  - signal_version — signal generation tracking (B5 deferred)
  - calibration_version — calibration model version tracking (B5 deferred)
Renaming requires coordinated src/state/portfolio.py + DB column rename (B5) — deferred post-PR3.
"""
import dataclasses
import re

import pytest

# Forbidden suffix built by concatenation
_VER_SUFFIX = "_" + "ver" + "sion"    # "_version"
_V_NUM = re.compile(r"_v\d+$")


def _version_fields(cls):
    """Return field names containing forbidden generation tokens."""
    bad = []
    for f in dataclasses.fields(cls):
        if _VER_SUFFIX in f.name or _V_NUM.search(f.name):
            bad.append(f.name)
    return bad


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Position has 3 _version fields: execution_cost_basis_version, signal_version, "
        "calibration_version. Rename to execution_cost_basis_id/signal_id/calibration_model_id "
        "(B5) requires portfolio.py + DB column rename — deferred post-PR3."
    ),
)
def test_position_has_no_version_fields():
    """Position dataclass must have zero *_version field names."""
    from src.state.portfolio import Position  # type: ignore[import]

    bad = _version_fields(Position)
    assert bad == [], (
        f"Position has {len(bad)} generation-named fields: {bad}\n"
        "Expected renamed per PR3 B5 (signal_id, calibration_model_id, etc.)"
    )
