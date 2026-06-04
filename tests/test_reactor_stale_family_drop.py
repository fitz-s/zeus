# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: reactor-wedge root (this session). The reactor's
#   _refresh_pending_family_snapshots Gamma-refreshed weeks-stale pending families
#   (May-24..) within a bounded 16-slot/60s budget; every fetch whiffed, the cycle
#   overran ("max running instances reached"), fresh families never captured -> 0
#   receipts. Staleness predicate: a family whose target_date < today is settled/
#   closed and MUST NOT consume the refresh budget. Same K-decision as #180/#182.
"""RED->GREEN: _drop_stale_families excludes past-target families."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.main import _drop_stale_families


def test_drops_past_target_families_keeps_today_and_future():
    fams = [
        ("Seoul", "2026-05-24", "high"),    # stale (weeks past)
        ("Tokyo", "2026-06-03", "high"),    # stale (yesterday)
        ("Chicago", "2026-06-04", "high"),  # today — keep
        ("Paris", "2026-06-05", "low"),     # future — keep (the tradeable one)
    ]
    kept, dropped = _drop_stale_families(fams, today="2026-06-04")
    kept_dates = {f[1] for f in kept}
    assert kept_dates == {"2026-06-04", "2026-06-05"}, f"kept wrong set: {kept_dates}"
    assert dropped == 2
    assert ("Seoul", "2026-05-24", "high") not in kept
    assert ("Paris", "2026-06-05", "low") in kept


def test_all_stale_returns_empty():
    fams = [("A", "2026-05-01", "high"), ("B", "2026-05-31", "low")]
    kept, dropped = _drop_stale_families(fams, today="2026-06-04")
    assert kept == [] and dropped == 2


def test_handles_missing_target_date_as_stale():
    fams = [("A", "", "high"), ("B", "2026-06-05", "high")]
    kept, dropped = _drop_stale_families(fams, today="2026-06-04")
    assert [f[0] for f in kept] == ["B"] and dropped == 1
