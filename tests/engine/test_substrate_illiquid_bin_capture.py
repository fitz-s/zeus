# Created: 2026-05-30
# Last reused/audited: 2026-05-30
# Authority basis: EDLI FDR full-family identity proof (event_reactor_adapter L481-499);
#                   operator design "市场捕捉了不会消失; freshness 针对价格不针对市场"
#                   (capture market IDENTITY for every active MECE bin; price freshness separate).
"""Relationship test (cross-module invariant) — illiquid MECE tail-bin capture.

Modules at the seam:
  A = src.data.market_scanner.capture_executable_market_snapshot  (PRODUCES snapshots)
  B = the EDLI FDR full-family identity proof in
      src.engine.event_reactor_adapter.build_event_bound_no_submit_receipt
      (CONSUMES snapshot presence: line ~490
       ``set(family_condition_ids) - set(snapshot_token_maps)`` must be empty).

Invariant under test (the live halt, 2026-05-30):
  Weather families are MECE temperature partitions. Near-zero-probability TAIL bins
  are active Gamma markets (active=1, closed=0) with NO asks (no liquidity). Before
  this change capture aborted on the missing ask side, so those bins never produced
  a snapshot row, the family was never fully covered, and the FDR identity gate
  ALWAYS returned ``FDR_FULL_FAMILY_PROOF_MISSING`` → zero candidates, ever.

  Cross-module property:
    - A family of N condition_ids where K are liquid (have asks) and N-K are no-ask:
      substrate capture (tolerate_missing_book=True) writes N identity rows
      (illiquid ones with orderbook_top_ask=None, tradeability NON-executable).
    - The FDR identity subset (B) is then complete: no missing siblings.
    - A no-ask snapshot is NEVER tradeable: assert_snapshot_executable rejects it,
      and on the order/strict path capture still aborts (tolerate_missing_book=False).

Sed-break antibody: reverting the tolerate-missing-asks change (forcing capture to
use the STRICT ask reader on the substrate path) makes the illiquid bin un-captured,
which re-opens FDR_FULL_FAMILY_PROOF_MISSING. The dedicated
``test_strict_path_still_aborts_on_missing_ask`` proves the test would go RED.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.contracts.executable_market_snapshot import (
    MarketNotTradableError,
    MarketSnapshotMismatchError,
    assert_snapshot_executable,
)
from src.data.market_scanner import (
    ExecutableSnapshotCaptureError,
    capture_executable_market_snapshot,
)
from src.engine.event_reactor_adapter import (
    _latest_snapshot_rows_for_event_family,
    _snapshot_token_maps_by_condition,
)
from src.state.db import init_schema
from src.state.snapshot_repo import get_snapshot

NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
HASH = "a" * 64


class _FakeClob:
    """CLOB surface for capture; ``has_asks=False`` simulates an illiquid tail bin."""

    def __init__(self, *, condition_id: str, yes_token: str, no_token: str, has_asks: bool) -> None:
        book: dict = {
            "asset_id": yes_token,
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": False,
            "bids": [{"price": "0.01", "size": "100"}],
        }
        if has_asks:
            book["asks"] = [{"price": "0.40", "size": "100"}]
        else:
            book["asks"] = []  # active market, no liquidity on the ask side
        self.orderbook = book
        self.market_info = {
            "condition_id": condition_id,
            "tokens": [{"token_id": yes_token}, {"token_id": no_token}],
            "accepting_orders": True,
            "archived": False,
            "enable_order_book": True,
            "feesEnabled": True,
        }

    def get_clob_market_info(self, condition_id: str) -> dict:
        return self.market_info

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        return self.orderbook

    def get_fee_rate(self, token_id: str) -> float:
        return 0.0


def _market(*, condition_id: str, yes_token: str, no_token: str, question_id: str) -> dict:
    return {
        "event_id": "event-mece",
        "slug": "chicago-temperature-high",
        "outcomes": [
            {
                "title": f"bin-{condition_id}",
                "token_id": yes_token,
                "no_token_id": no_token,
                "market_id": condition_id,
                "condition_id": condition_id,
                "question_id": question_id,
                "gamma_market_id": f"gamma-{condition_id}",
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "executable": True,
                "neg_risk": False,
                "market_end_at": (NOW + timedelta(days=1)).isoformat(),
                "token_map_raw": {"YES": yes_token, "NO": no_token},
                "raw_gamma_payload_hash": HASH,
                "gamma_market_raw": {
                    "id": f"gamma-{condition_id}",
                    "conditionId": condition_id,
                    "questionID": question_id,
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "negRisk": False,
                    "clobTokenIds": [yes_token, no_token],
                },
            }
        ],
    }


def _decision(*, yes_token: str, no_token: str, condition_id: str):
    return SimpleNamespace(
        tokens={"market_id": condition_id, "token_id": yes_token, "no_token_id": no_token},
        edge=SimpleNamespace(direction="buy_yes"),
    )


def _capture(conn, *, condition_id: str, has_asks: bool, tolerate: bool) -> None:
    yes_token = f"yes-{condition_id}"
    no_token = f"no-{condition_id}"
    capture_executable_market_snapshot(
        conn,
        market=_market(
            condition_id=condition_id,
            yes_token=yes_token,
            no_token=no_token,
            question_id=f"q-{condition_id}",
        ),
        decision=_decision(yes_token=yes_token, no_token=no_token, condition_id=condition_id),
        clob=_FakeClob(
            condition_id=condition_id,
            yes_token=yes_token,
            no_token=no_token,
            has_asks=has_asks,
        ),
        captured_at=NOW,
        scan_authority="VERIFIED",
        execution_side="BUY",
        tolerate_missing_book=tolerate,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


# --- Module-A behavior: substrate capture writes identity for every active bin ---


def test_strict_path_still_aborts_on_missing_ask(conn):
    """Order/strict path (tolerate_missing_book=False default) MUST abort on no asks.

    This is the sed-break sentinel: it pins that WITHOUT the tolerate flag the
    no-ask bin is un-capturable — exactly the pre-change behavior that starved the
    FDR family proof. The relationship test below depends on tolerate=True flipping
    this from raise to a persisted (non-tradeable) identity row.
    """
    with pytest.raises(ExecutableSnapshotCaptureError, match="missing asks"):
        _capture(conn, condition_id="condition-tail", has_asks=False, tolerate=False)


def test_substrate_path_captures_illiquid_bin_as_nontradeable_identity(conn):
    """tolerate_missing_book=True persists a no-ask bin with identity + NULL ask."""
    _capture(conn, condition_id="condition-tail", has_asks=False, tolerate=True)

    row = conn.execute(
        "SELECT * FROM executable_market_snapshots WHERE condition_id = ?",
        ("condition-tail",),
    ).fetchone()
    assert row is not None, "illiquid bin must be captured for identity"
    # The NOT-NULL ask column uses the pre-existing ABSENT sentinel for a
    # one-sided book; the reader maps it back to None (no schema change needed).
    assert row["orderbook_top_ask"] == "ABSENT", "no-ask bin stores ABSENT sentinel"
    assert row["yes_token_id"] == "yes-condition-tail"
    assert row["no_token_id"] == "no-condition-tail"

    snap = get_snapshot(conn, row["snapshot_id"])
    assert snap is not None
    assert snap.orderbook_top_ask is None
    assert snap.tradeability_status is not None
    assert snap.tradeability_status.executable_allowed is False
    assert snap.tradeability_status.reason == "clob_no_ask_illiquid"


# --- Cross-module relationship: A's illiquid rows complete B's FDR identity proof ---


def test_illiquid_bins_complete_fdr_family_identity_proof(conn):
    """Relationship invariant: N-bin family with K liquid + (N-K) no-ask bins.

    After substrate capture, the FDR identity subset
    (``set(family_condition_ids) - set(snapshot_token_maps)``) is EMPTY — the exact
    line-490 check in build_event_bound_no_submit_receipt that returns
    FDR_FULL_FAMILY_PROOF_MISSING when non-empty.
    """
    liquid = ["condition-1", "condition-2"]
    illiquid = ["condition-3", "condition-4"]
    family_condition_ids = tuple(liquid + illiquid)

    for cid in liquid:
        _capture(conn, condition_id=cid, has_asks=True, tolerate=True)
    for cid in illiquid:
        _capture(conn, condition_id=cid, has_asks=False, tolerate=True)

    event = SimpleNamespace(event_id="evt", causal_snapshot_id="csid")
    family_rows = _latest_snapshot_rows_for_event_family(
        conn, event, condition_ids=family_condition_ids, require_fresh=False
    )
    token_maps = _snapshot_token_maps_by_condition(family_rows)

    missing = sorted(set(family_condition_ids) - set(token_maps))
    assert missing == [], (
        "every MECE bin (liquid and illiquid) must contribute identity so the FDR "
        f"full-family proof is complete; missing={missing}"
    )
    # Identity present for illiquid bins too.
    for cid in illiquid:
        assert cid in token_maps
        assert token_maps[cid]["yes_token_id"] == f"yes-{cid}"
        assert token_maps[cid]["no_token_id"] == f"no-{cid}"


def test_illiquid_bin_is_never_tradeable_at_submission(conn):
    """An illiquid (no-ask) snapshot must be rejected by the submit contract."""
    _capture(conn, condition_id="condition-tail", has_asks=False, tolerate=True)
    row = conn.execute(
        "SELECT snapshot_id FROM executable_market_snapshots WHERE condition_id = ?",
        ("condition-tail",),
    ).fetchone()
    snap = get_snapshot(conn, row["snapshot_id"])

    # Tradeability gate blocks first (executable_allowed=False).
    with pytest.raises(MarketNotTradableError, match="clob_no_ask_illiquid"):
        assert_snapshot_executable(
            snap,
            token_id="yes-condition-tail",
            side="BUY",
            price=Decimal("0.40"),
            size=Decimal("5"),
            now=NOW,
        )


def test_liquid_bin_remains_tradeable(conn):
    """Control: a liquid bin captured on the same substrate path stays tradeable."""
    _capture(conn, condition_id="condition-1", has_asks=True, tolerate=True)
    row = conn.execute(
        "SELECT snapshot_id FROM executable_market_snapshots WHERE condition_id = ?",
        ("condition-1",),
    ).fetchone()
    snap = get_snapshot(conn, row["snapshot_id"])
    assert snap.orderbook_top_ask == Decimal("0.40")
    assert snap.tradeability_status.executable_allowed is True
    # Passes the tradeability + ask-side gates (token/selection mismatch is a
    # different axis; here we only assert it is NOT blocked as illiquid).
    try:
        assert_snapshot_executable(
            snap,
            token_id="yes-condition-1",
            side="BUY",
            price=Decimal("0.40"),
            size=Decimal("5"),
            now=NOW,
        )
    except MarketNotTradableError:
        pytest.fail("liquid bin must not be blocked as non-tradeable")
    except MarketSnapshotMismatchError:
        pass  # tolerated: selection/label axis, not the illiquidity axis under test


def test_synthetic_clob_substrate_identity_is_nontradeable_even_with_liquidity(conn, monkeypatch):
    monkeypatch.setenv("ZEUS_PENDING_SUBSTRATE_SYNTHETIC_CLOB_MARKET_INFO", "true")

    _capture(conn, condition_id="condition-1", has_asks=True, tolerate=True)

    row = conn.execute(
        "SELECT snapshot_id FROM executable_market_snapshots WHERE condition_id = ?",
        ("condition-1",),
    ).fetchone()
    snap = get_snapshot(conn, row["snapshot_id"])
    assert snap.orderbook_top_ask == Decimal("0.40")
    assert snap.tradeability_status.executable_allowed is False
    assert snap.tradeability_status.reason == "synthetic_clob_market_info_substrate_only"


def test_default_substrate_fee_identity_is_nontradeable_when_explicitly_enabled(conn, monkeypatch):
    monkeypatch.setenv("ZEUS_PENDING_SUBSTRATE_DEFAULT_FEE_DETAILS", "true")

    _capture(conn, condition_id="condition-1", has_asks=True, tolerate=True)

    row = conn.execute(
        "SELECT snapshot_id, fee_details_json FROM executable_market_snapshots WHERE condition_id = ?",
        ("condition-1",),
    ).fetchone()
    snap = get_snapshot(conn, row["snapshot_id"])
    assert snap.orderbook_top_ask == Decimal("0.40")
    assert snap.tradeability_status.executable_allowed is False
    assert snap.tradeability_status.reason == "default_substrate_fee_details_not_final_intent_authority"
    assert "submit_boundary_revalidates_fee" in row["fee_details_json"]
