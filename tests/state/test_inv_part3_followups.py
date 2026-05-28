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


# ---------- Part-5 Finding 1: durable chain timestamps round-trip via loader ----------

def _projection_row(**overrides) -> dict:
    row = {
        "position_id": "p-1", "trade_id": "p-1", "phase": "active", "state": "entered",
        "market_id": "m", "city": "London", "cluster": "eu", "target_date": "2026-06-01",
        "bin_label": "b", "direction": "buy_yes", "unit": "C", "size_usd": 1.0, "shares": 1.0,
        "cost_basis_usd": 1.0, "entry_price": 0.5, "p_posterior": 0.5,
        "decision_snapshot_id": "snap", "entry_method": "limit", "strategy_key": "center_buy",
        "chain_state": "synced", "token_id": "tok", "condition_id": "cond",
        "order_id": "o", "order_status": "filled", "updated_at": "2026-06-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def test_loader_maps_chain_seen_at_to_chain_verified_at() -> None:
    from src.state.portfolio import _position_from_projection_row
    pos = _position_from_projection_row(
        _projection_row(chain_seen_at="2026-06-01T12:00:00+00:00"), current_mode="live"
    )
    assert pos.chain_verified_at == "2026-06-01T12:00:00+00:00", (
        "chain_seen_at must map to runtime chain_verified_at, else classify_chain_state "
        "mis-reads a restarted chain-synced position as CHAIN_UNKNOWN"
    )


def test_loader_maps_chain_absence_at_to_runtime_field() -> None:
    from src.state.portfolio import _position_from_projection_row
    pos = _position_from_projection_row(
        _projection_row(chain_absence_at="2026-06-01T18:00:00+00:00"), current_mode="live"
    )
    assert pos.last_chain_absence_observed_at == "2026-06-01T18:00:00+00:00"


def test_loader_prefers_legacy_chain_verified_at_when_present() -> None:
    from src.state.portfolio import _position_from_projection_row
    pos = _position_from_projection_row(
        _projection_row(
            chain_verified_at="2026-06-01T09:00:00+00:00",
            chain_seen_at="2026-06-01T12:00:00+00:00",
        ),
        current_mode="live",
    )
    assert pos.chain_verified_at == "2026-06-01T09:00:00+00:00"


# ---------- Part-5 Finding 2: venue builder payload == projection authority ----------

def test_venue_observed_payload_matches_projection_even_with_wrong_attr() -> None:
    import json
    from src.engine.lifecycle_events import build_venue_position_observed_canonical_write

    class _P:
        def __init__(self):
            for k, v in dict(
                trade_id="t", market_id="m", city="London", cluster="eu",
                target_date="2026-06-01", bin_label="b", direction="buy_yes", unit="C",
                size_usd=1.0, shares=1.0, cost_basis_usd=1.0, entry_price=0.5, p_posterior=0.5,
                decision_snapshot_id="s", entry_method="limit", strategy_key="center_buy",
                strategy="center_buy", chain_state="synced", token_id="tok", condition_id="c",
                order_id="o", order_status="filled", state="entered", exit_state="",
                entered_at="2026-06-01T10:00:00+00:00", env="test",
                fill_authority="venue_confirmed_full",  # deliberately WRONG for this builder
            ).items():
                setattr(self, k, v)

    events, projection = build_venue_position_observed_canonical_write(_P(), venue_observed_at="2026-06-01T10:00:00+00:00", sequence_no=1)
    payload = json.loads(events[0]["payload_json"])
    assert payload["fill_authority"] == "venue_position_observed"
    assert payload["recovery_authority"] == "balance_only"
    assert projection["fill_authority"] == payload["fill_authority"]
    assert projection["recovery_authority"] == payload["recovery_authority"]


# ---------- Part-5 Finding 3: no exposure gate reads entry_fill_verified ----------

def test_no_exposure_module_branches_on_entry_fill_verified() -> None:
    """entry_fill_verified is a fill-verification signal, not an exposure gate.
    Exposure/risk/exit decisions must use has_tradable_exposure / fill_authority
    so balance-only rescued positions (entry_fill_verified=False, but real
    on-chain exposure) stay managed. Ban conditional branches on the bool in the
    exposure-side modules (construction `entry_fill_verified=...` is allowed)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    exposure_modules = [
        root / "src" / "riskguard" / "riskguard.py",
        root / "src" / "execution" / "exit_lifecycle.py",
    ]
    # Gate patterns: `if ... entry_fill_verified`, `and/or entry_fill_verified`,
    # `not entry_fill_verified`, `if pos.entry_fill_verified:`.
    gate_re = re.compile(r"\b(if|elif|and|or|not|while|assert)\b[^\n=]*entry_fill_verified")
    for mod in exposure_modules:
        if not mod.exists():
            continue
        for ln in mod.read_text().splitlines():
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            assert not gate_re.search(ln), (
                f"{mod.name}: exposure decision branches on entry_fill_verified "
                f"(use has_tradable_exposure / fill_authority): {stripped!r}"
            )
