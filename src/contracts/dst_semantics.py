# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Canonical home for DST-gap detection (`_is_missing_local_hour`).
#          Extracted from src.signal.diurnal per G10 helper-extraction (con-nyx
#          MAJOR #1) so the ingest lane (scripts/ingest/*) can call this helper
#          without transitively pulling in src.signal — the trading-engine
#          surface fenced off by tests/test_ingest_isolation.py.
# Reuse: This module is in `src.contracts.*` (allowed for both ingest and
#        engine lanes). When adding new DST-related helpers, prefer this
#        module over src.signal.* unless the helper is signal-specific.
# Authority basis: docs/operations/task_2026-04-26_g10_helper_extraction/plan.md
#   §2 + parent docs/operations/task_2026-04-26_live_readiness_completion/plan.md
#   K3.G10 + con-nyx G10-scaffold APPROVE_WITH_CONDITIONS MAJOR #1.
"""DST-semantic helpers (timezone-gap detection).

Canonical home for timezone-aware DST helpers. Extracted from
src.signal.diurnal so that the ingest lane (scripts/ingest/*) can call
DST helpers without transitively importing src.signal.

src.signal.diurnal.* re-exports `_is_missing_local_hour` from this
module for back-compat — existing callers continue to work unchanged.
NEW callers should import directly from src.contracts.dst_semantics.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _is_missing_local_hour(local_dt: datetime, tz: ZoneInfo) -> bool:
    """True if the wall-clock hour does not exist in the given timezone (spring-forward gap).

    Example: London 2025-03-30 01:30 does not exist because clocks jumped 01:00 -> 02:00.
    """
    # Take the naive local datetime; try to localize in the tz; round-trip through UTC.
    # If the round-trip shifts the hour or the date, the original hour was in a DST gap.
    if local_dt.tzinfo is not None:
        local_naive = local_dt.replace(tzinfo=None)
    else:
        local_naive = local_dt
    # Localize with fold=0 (the "earlier" option) — in a gap, this produces a post-gap instant
    localized = local_naive.replace(tzinfo=tz)
    # Round-trip through UTC
    utc = localized.astimezone(ZoneInfo("UTC"))
    back = utc.astimezone(tz)
    # If the hour or date changed, the original wall-clock hour does not exist
    return back.hour != local_naive.hour or back.date() != local_naive.date()
