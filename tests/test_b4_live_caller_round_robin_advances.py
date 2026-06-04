# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 B4 fix (adversarial-verify finding #3). The B4 round-robin
#   derives cycle_index from the emit `source` via int(source.split('-')[-1]) — it
#   needs a "cycle-N" suffix. But the LIVE caller (src/main.py redecision block) passed
#   source = f"edli_redecision:{now.isoformat()}", an ISO timestamp. split('-')[-1] on
#   "edli_redecision:2026-06-03T..." is "03T20:53:50.123456" -> int() ValueError ->
#   cycle_index = 0 EVERY cycle -> the round-robin window is always [0, LIMIT) -> cities
#   21..N stay DARK even with the flag ON. The existing B4 tests hand-passed source=
#   "cycle-N" and so never exercised the broken live source. This test exercises the
#   LIVE caller's source generation and proves consecutive cycles ADVANCE the window.
"""B4 live-caller round-robin relationship test.

RELATIONSHIP under test: the LIVE redecision caller (main._edli_event_reactor_cycle)
PRODUCES the emit `source`; the B4 round-robin (forecast_snapshot_ready) CONSUMES it to
derive the cycle index. The invariant across that boundary: consecutive live cycles
must produce sources that parse to MONOTONICALLY ADVANCING cycle indices, so the round-
robin window slides and every city is covered within ceil(N/LIMIT) cycles — NOT a window
frozen at [0, LIMIT) because the source could not be parsed.

Written RED-first against the pre-fix live caller (ISO-timestamp source -> always 0).
"""
from __future__ import annotations

import math

import pytest

import src.main as main
from src.events.triggers.forecast_snapshot_ready import CoverageFairnessRequest


def _cycle_index_from_source(source: str) -> int:
    """Replicate the EXACT round-robin parse in scan_committed_snapshots."""
    try:
        return int(source.split("-")[-1])
    except (ValueError, IndexError):
        return 0


def test_live_redecision_source_parses_to_advancing_cycle_index(monkeypatch):
    """The live caller's source for N consecutive cycles must parse (via the round-robin's
    own int(source.split('-')[-1]) rule) to DISTINCT, monotonically advancing indices.
    Pre-fix the source was an ISO timestamp -> every cycle parsed to 0 (frozen window)."""
    # Reset the monotonic counter so the test is deterministic.
    main._reset_edli_redecision_cycle_index()

    sources = [main._edli_next_redecision_source() for _ in range(5)]
    indices = [_cycle_index_from_source(s) for s in sources]

    # Monotonically advancing, distinct — the frozen-at-0 bug makes these all 0.
    assert indices == [0, 1, 2, 3, 4], (
        f"live redecision sources did not advance the round-robin: sources={sources}, "
        f"parsed indices={indices}"
    )


def test_live_caller_source_covers_all_cities_within_ceil_n_over_limit(monkeypatch):
    """End-to-end coverage on the LIVE caller path: feed the round-robin the SOURCE the
    live caller actually generates for each of ceil(54/20)=3 cycles and assert every one
    of 54 cities lands in some cycle's window. Pre-fix (ISO source) all 3 cycles map to
    index 0 -> the same first-20 cities -> 34 cities never selected."""
    main._reset_edli_redecision_cycle_index()

    cities = [f"City{i:02d}" for i in range(1, 55)]  # 54 cities
    limit = 20
    cycles_required = math.ceil(len(cities) / limit)  # 3
    assert cycles_required == 3

    # The round-robin orders unique keys by insertion; mimic one row per city.
    candidate_rows = [
        {"city": c, "target_local_date": "2026-06-04", "temperature_metric": "high",
         "snapshot_id": i, "readiness_status": "LIVE_ELIGIBLE"}
        for i, c in enumerate(cities, start=1)
    ]

    seen: set[str] = set()
    for _ in range(cycles_required):
        source = main._edli_next_redecision_source()  # the LIVE caller's source
        cycle_index = _cycle_index_from_source(source)
        req = CoverageFairnessRequest(limit=limit, cycle_index=cycle_index)
        for row in req.select_rows(candidate_rows):
            seen.add(str(row["city"]))

    missing = set(cities) - seen
    assert not missing, (
        f"B4 live-caller coverage FAILED: {len(missing)}/54 cities never selected within "
        f"{cycles_required} cycles. The live source must advance the round-robin window. "
        f"Missing(sample): {sorted(missing)[:8]}"
    )


def test_live_redecision_source_is_distinct_per_cycle_for_idempotency(monkeypatch):
    """The source must ALSO stay distinct per cycle so the re-emitted FSR-equivalent does
    not dedup to the consumed FSR (the original reason an ISO timestamp was used). A
    monotonic cycle-N is distinct per cycle, preserving that property."""
    main._reset_edli_redecision_cycle_index()
    sources = [main._edli_next_redecision_source() for _ in range(10)]
    assert len(set(sources)) == 10  # all distinct
    assert all(s.startswith("cycle-") for s in sources)  # parseable form
