# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md
#                  slice A4 (authority-strict learning consumers + structural
#                  anchor for the rescue_events_v2 learning contract)
"""Slice A4 relationship + future-proofing antibody tests.

PR #19 workbook F8: legacy position metric fallback returns
`("high", "UNVERIFIED", "position_missing_metric:<repr>")` when the
position has no temperature_metric. Strict downstream learning consumers
must filter on `authority = 'VERIFIED'` so quarantine placeholders /
stale JSON reconstructions / legacy rows do not silently become training
evidence.

A4 audit (2026-04-26): there are currently NO src/ or scripts/ readers
of `rescue_events_v2` at the SELECT seam — only writers via
`log_rescue_event`. The F8 risk is therefore future-proofing: any future
consumer that adds a SELECT-side read in a learning context must
respect the authority-VERIFIED contract.

This test pins that contract structurally:

1. `LEARNING_AUTHORITY_REQUIRED = "VERIFIED"` constant lives in
   src/state/chain_reconciliation.py and is the canonical anchor.
2. The `resolve_rescue_authority` invariant: positions with
   missing/invalid temperature_metric MUST be tagged UNVERIFIED with a
   concrete authority_source. Positions with valid metric MUST be
   tagged VERIFIED.
3. Repo-wide source scan: every SELECT-side read of `rescue_events_v2`
   must carry an authority filter. Today the count is 0 (trivially
   passes); future readers must conform.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# -----------------------------------------------------------------------------
# Authority constant pin
# -----------------------------------------------------------------------------


def test_learning_authority_required_constant_exists_and_is_verified():
    """The structural anchor for the rescue_events_v2 learning contract."""
    from src.state.chain_reconciliation import LEARNING_AUTHORITY_REQUIRED
    assert LEARNING_AUTHORITY_REQUIRED == "VERIFIED", (
        "Slice A4 contract: rescue_events_v2 learning consumers must filter "
        "on authority='VERIFIED'. Changing this constant silently breaks the "
        "antibody set in tests/test_authority_strict_learning.py."
    )


# -----------------------------------------------------------------------------
# resolve_rescue_authority invariants (pin authority-tag policy)
# -----------------------------------------------------------------------------


def test_resolve_rescue_authority_high_position_returns_verified():
    from src.state.chain_reconciliation import resolve_rescue_authority

    position = SimpleNamespace(temperature_metric="high")
    metric, authority, source = resolve_rescue_authority(position)
    assert metric == "high"
    assert authority == "VERIFIED"
    assert source == "position_materialized"


def test_resolve_rescue_authority_low_position_returns_verified():
    from src.state.chain_reconciliation import resolve_rescue_authority

    position = SimpleNamespace(temperature_metric="low")
    metric, authority, source = resolve_rescue_authority(position)
    assert metric == "low"
    assert authority == "VERIFIED"
    assert source == "position_materialized"


@pytest.mark.parametrize(
    "raw_metric",
    [None, "", "  ", "garbage", "HIGH", "Low", 0, 42],
)
def test_resolve_rescue_authority_invalid_metric_returns_unverified(raw_metric):
    """Anything not exactly 'high' or 'low' must tag UNVERIFIED with provenance.

    Pre-A4 docstring promise made structural: a future consumer that filters
    `authority='VERIFIED'` will reject every one of these inputs as expected.
    """
    from src.state.chain_reconciliation import resolve_rescue_authority

    position = SimpleNamespace(temperature_metric=raw_metric)
    metric, authority, source = resolve_rescue_authority(position)
    assert metric == "high", (
        "SD-1 default direction is HIGH for missing metric (preserved)."
    )
    assert authority == "UNVERIFIED", (
        f"raw_metric={raw_metric!r} must produce UNVERIFIED authority tag; "
        "downstream learning consumers depend on this to avoid mis-classifying "
        "stale/placeholder rows as training evidence."
    )
    assert source.startswith("position_missing_metric:"), (
        "authority_source must carry concrete provenance for forensic filters."
    )


def test_resolve_rescue_authority_position_missing_attribute():
    """A bare object with no temperature_metric attribute → UNVERIFIED."""
    from src.state.chain_reconciliation import resolve_rescue_authority

    position = SimpleNamespace()  # no temperature_metric attr
    metric, authority, source = resolve_rescue_authority(position)
    assert metric == "high"
    assert authority == "UNVERIFIED"
    assert source.startswith("position_missing_metric:")


# -----------------------------------------------------------------------------
# Source scanner: future learning consumers must filter authority
# -----------------------------------------------------------------------------


def _list_python_sources() -> list[Path]:
    roots = [PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"]
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        out.extend(p for p in root.rglob("*.py") if p.is_file())
    return out


def test_no_rescue_events_v2_select_lacks_authority_filter():
    """Repo-wide antibody: every SELECT-side rescue_events_v2 read must carry
    a `WHERE ... authority` clause (= "VERIFIED" or routed through
    LEARNING_AUTHORITY_REQUIRED).

    Today the SELECT count is 0 — only writers exist via log_rescue_event.
    This test trivially passes today and pins the contract going forward:
    any future consumer that adds a SELECT must include the authority filter
    or this test fails with a useful diagnostic.

    The test is intentionally strict (any rescue_events_v2 SELECT must
    filter authority, not just learning-flagged ones). If a legitimate
    non-learning read is added later, route it through a wrapper that
    documents the exemption rather than weakening this antibody.
    """
    # Require SELECT...FROM rescue_events_v2 SQL form. Excludes Python
    # `from src.state.db import ... rescue_events_v2` comment matches.
    select_pattern = re.compile(
        r"(?is)\bSELECT\b[^;]{0,500}\bFROM\s+rescue_events_v2\b[^;]{0,500}"
    )
    offenders: list[tuple[Path, str]] = []
    for src_file in _list_python_sources():
        text = src_file.read_text(encoding="utf-8", errors="replace")
        for match in select_pattern.finditer(text):
            snippet = match.group(0)
            # Exclude clearly write-only contexts where the regex caught a
            # comment or a string referencing rescue_events_v2 in an INSERT
            # template. We only flag SELECT-side reads.
            if not re.search(r"\bSELECT\b", snippet, re.IGNORECASE):
                continue
            # Strip trailing INSERT continuations: SELECT-from-rescue_events_v2
            # used as a subquery for INSERT INTO calibration_pairs etc.
            if not re.search(r"\bauthority\b", snippet, re.IGNORECASE):
                offenders.append((src_file, snippet[:200]))
    assert not offenders, (
        "Slice A4 antibody violation: every SELECT-side read of "
        "rescue_events_v2 must filter on authority (use "
        "LEARNING_AUTHORITY_REQUIRED from src.state.chain_reconciliation). "
        f"Found {len(offenders)} unfiltered read(s):\n"
        + "\n".join(f"  {p}: {s!r}" for p, s in offenders)
    )


def test_source_scanner_actually_finds_violations():
    """Self-test for the scanner's regex: a synthetic SELECT without authority
    filter must be detected. Guards against the scanner silently regressing
    into a no-op.
    """
    sample_bad = """
    rows = conn.execute('''
        SELECT trade_id, temperature_metric
        FROM rescue_events_v2
        WHERE city = ?
    ''', (city,)).fetchall()
    """
    sample_good = """
    rows = conn.execute('''
        SELECT trade_id, temperature_metric
        FROM rescue_events_v2
        WHERE city = ? AND authority = 'VERIFIED'
    ''', (city,)).fetchall()
    """
    pat = re.compile(
        r"(?is)\bSELECT\b[^;]{0,500}\bFROM\s+rescue_events_v2\b[^;]{0,500}"
    )
    bad_match = pat.search(sample_bad)
    good_match = pat.search(sample_good)
    assert bad_match is not None
    assert good_match is not None
    assert not re.search(r"\bauthority\b", bad_match.group(0), re.IGNORECASE)
    assert re.search(r"\bauthority\b", good_match.group(0), re.IGNORECASE)
