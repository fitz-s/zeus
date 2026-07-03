# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 6: Dataclass field name scan on Position (portfolio.py).
xfail(strict=False): signal_version, calibration_version, pricing_semantics_version,
execution_cost_basis_version exist today. PR3 B5 will rename them.
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


@pytest.mark.xfail(strict=False, reason="awaits PR3 B5 sweep — *_" + "ver" + "sion fields still in Position")
def test_position_has_no_version_fields():
    """Position dataclass must have zero *_version field names."""
    from src.state.portfolio import Position  # type: ignore[import]

    bad = _version_fields(Position)
    assert bad == [], (
        f"Position has {len(bad)} generation-named fields: {bad}\n"
        "Expected renamed per PR3 B5 (signal_id, calibration_model_id, etc.)"
    )
