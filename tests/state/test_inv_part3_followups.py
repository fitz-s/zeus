# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Part-4 audit (PR #352) Finding 1 hardening (generated upsert
#   update set) + Copilot #350 finding (ChainOnlyFact 48h review escalation fold).
"""Antibody invariants: generated upsert update set + chain-only review fold.

1. upsert_position_current's ON CONFLICT update set is generated from
   CANONICAL_POSITION_CURRENT_COLUMNS, so EVERY mutable canonical column is
   refreshed on conflict — no column-drift can silently omit one (the root
   cause of the D0b authority-stale bug).

2. check_quarantine_timeouts escalates ChainOnlyFacts past the 48h review
   window (the timeout consumer the README references) and skips RESOLVED facts.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.contracts.position_truth import ChainOnlyFact, ChainOnlyReviewState
from src.state.chain_reconciliation import check_quarantine_timeouts
from src.state.portfolio import PortfolioState
from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, upsert_position_current

_NUMERIC = {"size_usd", "shares", "cost_basis_usd", "entry_price", "p_posterior", "chain_shares"}


def _fresh_pc(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "pc.db"))
    cols = ", ".join(
        f"{c} REAL" if c in _NUMERIC else f"{c} TEXT"
        for c in CANONICAL_POSITION_CURRENT_COLUMNS
    )
    conn.execute(f"CREATE TABLE position_current ({cols}, PRIMARY KEY(position_id))")
    return conn


def _projection(value_for) -> dict:
    out = {}
    for c in CANONICAL_POSITION_CURRENT_COLUMNS:
        if c in _NUMERIC:
            out[c] = value_for
        else:
            out[c] = f"v{value_for}_{c}"
    out["position_id"] = "p-1"          # stable conflict key
    out["phase"] = "active"             # keep out of F109 condition_id guard? active needs condition_id
    out["token_id"] = "tok-1"
    out["condition_id"] = "cond-1"      # open-phase requires non-empty condition_id
    return out


def test_upsert_conflict_updates_every_mutable_canonical_column(tmp_path) -> None:
    conn = _fresh_pc(tmp_path)
    upsert_position_current(conn, _projection(1))
    upsert_position_current(conn, _projection(2))  # same position_id -> UPDATE path
    row = conn.execute(
        f"SELECT {', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)} FROM position_current WHERE position_id='p-1'"
    ).fetchone()
    got = dict(zip(CANONICAL_POSITION_CURRENT_COLUMNS, row))
    for c in CANONICAL_POSITION_CURRENT_COLUMNS:
        if c == "position_id":
            continue
        if c in ("phase", "token_id", "condition_id"):
            continue  # pinned constant across both writes
        expected = 2 if c in _NUMERIC else f"v2_{c}"
        assert got[c] == expected, f"column {c} not refreshed on conflict: got {got[c]!r}"


# ---------- F3: ChainOnlyFact 48h review escalation fold ----------

def _fact(token: str, *, age_hours: float, resolved: bool = False) -> ChainOnlyFact:
    seen = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    state = ChainOnlyReviewState.RESOLVED if resolved else ChainOnlyReviewState.UNRESOLVED
    return ChainOnlyFact(
        token_id=token, condition_id=f"cond-{token}", size=5.0, avg_price=0.4,
        cost_basis=2.0, first_seen_at=seen, last_seen_at=seen, review_state=state,
    )


def test_chain_only_fact_past_48h_logs_escalation(caplog) -> None:
    portfolio = PortfolioState(positions=[])
    portfolio.chain_only_facts.append(_fact("old", age_hours=60.0))
    with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
        check_quarantine_timeouts(portfolio)
    assert any("CHAIN_ONLY_REVIEW EXPIRED" in r.message and "old" in r.message for r in caplog.records)


def test_chain_only_fact_within_window_not_escalated(caplog) -> None:
    portfolio = PortfolioState(positions=[])
    portfolio.chain_only_facts.append(_fact("fresh", age_hours=1.0))
    with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
        check_quarantine_timeouts(portfolio)
    assert not any("CHAIN_ONLY_REVIEW EXPIRED" in r.message for r in caplog.records)


def test_resolved_chain_only_fact_skipped(caplog) -> None:
    portfolio = PortfolioState(positions=[])
    portfolio.chain_only_facts.append(_fact("done", age_hours=99.0, resolved=True))
    with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
        check_quarantine_timeouts(portfolio)
    assert not any("CHAIN_ONLY_REVIEW EXPIRED" in r.message for r in caplog.records)
