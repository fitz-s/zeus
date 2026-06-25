#!/usr/bin/env python3
# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: docs/evidence/live_order_pathology/2026-06-21_escalation_cross_fix.md
#   (GAP 4 escalation-cross re-rest race) + 2026-06-21_captured_ev_deciding_analysis.md.
"""Read-only audit: which live families would now CROSS vs HOLD/REST under the
GAP-4 escalation-cross re-rest fix.

WHY
---
The fix changes `_family_rest_state` (src/engine/event_reactor_adapter.py) so an
armed escalation (a prior cancelled-unfilled aged rest) is no longer masked by
a just-posted serial re-rest. Because this changes when the engine places TAKER
crosses (real capital), the coordinator must be able to audit, BEFORE any
arming, exactly which live families flip from HOLD to the armed-cross lane — and
confirm the cross population matches the real admissible no-fill population.

WHAT IT DOES (no behaviour, pure read)
--------------------------------------
For every distinct family token set with a recent ENTRY command in zeus_trades:
  1. Open zeus_trades.db READ-ONLY (mode=ro). No write connection, INV-37 clean
     (this audit reads a single store; it does not write or ATTACH-write).
  2. Call the REAL `_family_rest_state` (imported, not re-implemented — so the
     audit can never drift from the live decision logic) at `decision_time=now`.
  3. Classify each family by the returned (unexpired_family_rest, escalated):
       - WOULD_CROSS  : escalated=True AND unexpired_rest=False
                        -> reaches TAKER_ESCALATED_AFTER_REST (line ~571) IF the
                        FRESH book is FIX-B admissible (ask+fee <= q_lcb). This
                        audit does NOT re-price the fresh book; it reports the
                        families that now reach the cross LANE (the lane is still
                        gated by FIX-B at proof and again at submit).
       - WOULD_HOLD   : unexpired_rest=True -> HOLD_REST_IN_PROGRESS (line ~561).
       - ARMED_NO_OPEN: escalated=True AND unexpired_rest=False with NO open
                        re-rest (the plain armed cross, already worked pre-fix).
       - NEUTRAL      : neither flag (first rest / nothing pending).

The DELTA the fix introduces is the WOULD_CROSS families that have an OPEN
post-escalation re-rest — pre-fix those returned (True, True) -> HOLD; post-fix
they return (False, True) -> the armed cross lane. The script flags them as
``flipped_by_fix=True`` by also computing the PRE-FIX classification (the simple
"any open row -> unexpired_rest" rule) for contrast.

USAGE
-----
Run against the PRIMARY checkout's live DB (operator directive: paths follow
main):

    ZEUS_PRIMARY_ROOT=/Users/leofitz/zeus \
      /Users/leofitz/zeus/.venv/bin/python3 scripts/audit_escalation_cross_rerest.py

Optional: ``--hours N`` (default 24) bounds the ENTRY-command lookback;
``--json`` prints machine-readable rows.

SAFETY: read-only (mode=ro URI). It NEVER submits, cancels, or writes. It is a
pure observation of what the fixed decision logic WOULD classify right now.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from src.engine.event_reactor_adapter import _family_rest_state
from src.state.db_paths import primary_trade_db_path

UTC = timezone.utc


def _open_ro(path: str) -> sqlite3.Connection:
    """Open zeus_trades.db strictly read-only (URI mode=ro)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _distinct_family_token_groups(
    conn: sqlite3.Connection, *, hours: int
) -> list[tuple[str, ...]]:
    """Group recent ENTRY-command tokens into family token sets.

    Real-chain families are keyed by market_id (the condition): all ENTRY
    commands on the same market_id form one family token set (yes+no tokens).
    """
    cutoff = (
        datetime.now(UTC).timestamp() - hours * 3600.0
    )
    cutoff_iso = datetime.fromtimestamp(cutoff, UTC).isoformat()
    rows = conn.execute(
        """
        SELECT DISTINCT market_id, token_id
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           AND created_at >= ?
           AND token_id IS NOT NULL AND token_id != ''
        """,
        (cutoff_iso,),
    ).fetchall()
    by_market: dict[str, set[str]] = {}
    for row in rows:
        market = str(row["market_id"] or "")
        token = str(row["token_id"] or "")
        if not token:
            continue
        by_market.setdefault(market, set()).add(token)
    return [tuple(sorted(tokens)) for tokens in by_market.values() if tokens]


def _prefix_classification(
    conn: sqlite3.Connection, family, *, decision_time: datetime
) -> tuple[bool, bool]:
    """The PRE-FIX behaviour: ANY open row -> unexpired_rest (no re-rest exemption).

    Re-derives the simple rule for contrast only (so the audit shows which
    families FLIP). Mirrors the unfixed loop: open fact or acked-no-fact -> True.
    """
    token_ids = {
        str(tid)
        for cand in getattr(family, "candidates", ())
        for tid in (getattr(cand, "yes_token_id", None), getattr(cand, "no_token_id", None))
        if tid
    }
    if not token_ids:
        return (False, False)
    # Reuse the FIXED function for `escalated` (identical between pre/post), then
    # recompute the PRE-FIX unexpired_rest with the naive "any open row" rule.
    _, escalated = _family_rest_state(conn, family=family, decision_time=decision_time)
    placeholders = ",".join("?" for _ in token_ids)
    open_fact_states = ("LIVE", "RESTING", "PARTIALLY_MATCHED")
    nonterminal = ("SUBMITTING", "POSTING", "POST_ACKED", "ACKED", "PARTIAL")
    cutoff_iso = (
        datetime.fromtimestamp(decision_time.timestamp() - 24 * 3600.0, UTC).isoformat()
    )
    rows = conn.execute(
        f"""
        WITH latest_facts AS (
            SELECT venue_order_id, state,
                   ROW_NUMBER() OVER (
                       PARTITION BY venue_order_id ORDER BY local_sequence DESC
                   ) AS rn
            FROM venue_order_facts
        )
        SELECT vc.state AS command_state, lf.state AS fact_state
        FROM venue_commands vc
        LEFT JOIN latest_facts lf
               ON lf.venue_order_id = vc.venue_order_id AND lf.rn = 1
        WHERE vc.intent_kind = 'ENTRY'
          AND vc.token_id IN ({placeholders})
          AND vc.created_at >= ?
        """,
        (*sorted(token_ids), cutoff_iso),
    ).fetchall()
    unexpired = False
    for row in rows:
        fs = str(row["fact_state"] or "")
        cs = str(row["command_state"] or "")
        if fs in open_fact_states or (not fs and cs in nonterminal):
            unexpired = True
            break
    return (unexpired, escalated)


def _classify(unexpired: bool, escalated: bool) -> str:
    if unexpired:
        return "WOULD_HOLD"
    if escalated:
        return "WOULD_CROSS"
    return "NEUTRAL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24, help="ENTRY-command lookback window")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    db_path = str(primary_trade_db_path())
    now = datetime.now(UTC)
    conn = _open_ro(db_path)
    try:
        groups = _distinct_family_token_groups(conn, hours=args.hours)
        results = []
        for tokens in groups:
            # Build a minimal family shape matching what _family_rest_state reads
            # (candidates[*].yes_token_id / no_token_id). We do not know the
            # yes/no split here, so expose every token on BOTH fields — the
            # function unions them into the token-id set regardless.
            candidates = tuple(
                SimpleNamespace(yes_token_id=t, no_token_id=t) for t in tokens
            )
            family = SimpleNamespace(candidates=candidates)
            post_unexpired, post_escalated = _family_rest_state(
                conn, family=family, decision_time=now
            )
            pre_unexpired, pre_escalated = _prefix_classification(
                conn, family, decision_time=now
            )
            post_cls = _classify(post_unexpired, post_escalated)
            pre_cls = _classify(pre_unexpired, pre_escalated)
            flipped = pre_cls == "WOULD_HOLD" and post_cls == "WOULD_CROSS"
            results.append(
                {
                    "tokens": list(tokens),
                    "pre_fix": pre_cls,
                    "post_fix": post_cls,
                    "post_unexpired_rest": post_unexpired,
                    "post_escalated": post_escalated,
                    "flipped_by_fix": flipped,
                }
            )
    finally:
        conn.close()

    flipped = [r for r in results if r["flipped_by_fix"]]
    would_cross = [r for r in results if r["post_fix"] == "WOULD_CROSS"]
    would_hold = [r for r in results if r["post_fix"] == "WOULD_HOLD"]

    if args.json:
        print(json.dumps({"now": now.isoformat(), "results": results}, indent=2))
    else:
        print(f"escalation-cross re-rest audit (read-only) @ {now.isoformat()}")
        print(f"  DB: {db_path}")
        print(f"  families scanned (last {args.hours}h ENTRY cmds): {len(results)}")
        print(f"  WOULD_CROSS (reach armed-cross lane, still FIX-B gated): {len(would_cross)}")
        print(f"  WOULD_HOLD  (single-flight HOLD): {len(would_hold)}")
        print(f"  FLIPPED by fix (HOLD -> armed cross via re-rest exemption): {len(flipped)}")
        for r in flipped:
            print(f"    FLIP  tokens={r['tokens']}  pre={r['pre_fix']} post={r['post_fix']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
