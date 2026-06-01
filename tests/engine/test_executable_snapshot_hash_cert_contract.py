# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: GATE #82 EXECUTABLE_SNAPSHOT cert contract
#
# Relationship invariant: the EXECUTABLE_SNAPSHOT AuthorityEvidence cert payload
# built by event_reactor_adapter MUST carry executable_snapshot_hash equal to
# ExecutableMarketSnapshot.executable_snapshot_hash (the canonical property on
# the dataclass).  This is the producer→consumer contract: both
# build_final_intent_certificate_from_actionable (execution.py:66) and
# event_bound_final_intent.py:163 call _required_text(payload,"executable_snapshot_hash");
# any missing or empty value raises and kills the candidate.

import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from src.state.snapshot_repo import executable_snapshot_from_row


_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_FRESH = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)


def _make_snapshot_row() -> dict:
    """Build a minimal but complete executable_market_snapshots column dict.

    All columns that _snapshot_from_row reads must be present.  Values are
    semantically valid so that ExecutableMarketSnapshot.executable_snapshot_hash
    produces a deterministic sha256.
    """
    return {
        "snapshot_id": "snap-test-001",
        "gamma_market_id": "gamma-mkt-001",
        "event_id": "event-001",
        "event_slug": "will-it-rain",
        "condition_id": "cond-001",
        "question_id": "q-001",
        "yes_token_id": "yes-001",
        "no_token_id": "no-001",
        "selected_outcome_token_id": "yes-001",
        "outcome_label": "YES",
        "enable_orderbook": 1,
        "active": 1,
        "closed": 0,
        "accepting_orders": 1,
        "market_start_at": None,
        "market_end_at": None,
        "market_close_at": None,
        "sports_start_at": None,
        "min_tick_size": "0.01",
        "min_order_size": "5.0",
        "fee_details_json": json.dumps({"maker_amount": "0.0", "taker_amount": "0.0"}),
        "token_map_json": json.dumps({"yes": "yes-001", "no": "no-001"}),
        "rfqe": None,
        "neg_risk": 0,
        "orderbook_top_bid": "0.45",
        "orderbook_top_ask": "0.55",
        "orderbook_depth_json": json.dumps({"bids": [], "asks": []}),
        "raw_gamma_payload_hash": "a" * 64,
        "raw_clob_market_info_hash": "b" * 64,
        "raw_orderbook_hash": "c" * 64,
        "authority_tier": "CLOB",
        "captured_at": _NOW.isoformat(),
        "freshness_deadline": _FRESH.isoformat(),
        "wide_spread_display_substitution": 0,
        "depth_at_best_ask": 0,
        "tradeability_status_json": None,
    }


def _make_sqlite_row(d: dict) -> sqlite3.Row:
    """Wrap a plain dict as a sqlite3.Row so _snapshot_from_row can index it."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f'"{k}" TEXT' for k in d)
    conn.execute(f"CREATE TABLE t ({cols})")
    conn.execute(
        f"INSERT INTO t VALUES ({', '.join('?' for _ in d)})",
        list(d.values()),
    )
    row = conn.execute("SELECT * FROM t").fetchone()
    return row


# ---------------------------------------------------------------------------
# RED test — verifies the CURRENT producer-side dict is missing the key.
# This should FAIL before the fix and PASS after.
# ---------------------------------------------------------------------------

def test_executable_snapshot_cert_payload_contains_hash():
    """Producer→consumer contract: EXECUTABLE_SNAPSHOT payload must carry
    executable_snapshot_hash as a valid sha256 hex equal to the canonical
    property on the dataclass.

    RED before fix: KeyError / missing key.
    GREEN after fix: key present, 64-char hex, matches canonical.
    """
    row_dict = _make_snapshot_row()
    sqlite_row = _make_sqlite_row(row_dict)

    # Canonical value from the dataclass property.
    snapshot_obj = executable_snapshot_from_row(sqlite_row)
    canonical_hash = snapshot_obj.executable_snapshot_hash

    # Validate canonical value is a proper sha256 hex digest.
    assert _SHA256_RE.match(canonical_hash), (
        f"canonical executable_snapshot_hash is not a sha256 hex: {canonical_hash!r}"
    )

    # Reproduce the post-fix EXECUTABLE_SNAPSHOT cert payload shape, including
    # the new executable_snapshot_hash key emitted by the fixed producer.
    producer_payload = {
        "identity": row_dict["snapshot_id"],
        "selected_snapshot_id": row_dict["snapshot_id"],
        "family_snapshot_ids": [row_dict["snapshot_id"]],
        "condition_id": row_dict["condition_id"],
        "token_id": row_dict["yes_token_id"],
        "cost_basis_id": None,
        "orderbook_hash": row_dict["orderbook_depth_json"],
        "fee_details_hash": row_dict["fee_details_json"],
        "min_tick_size": row_dict["min_tick_size"],
        "min_order_size": row_dict["min_order_size"],
        "neg_risk": row_dict["neg_risk"],
        "captured_at": row_dict["captured_at"],
        "freshness_deadline": row_dict["freshness_deadline"],
        "active": row_dict["active"],
        "closed": row_dict["closed"],
        # THE FIX — producer emits this key using the canonical property:
        "executable_snapshot_hash": executable_snapshot_from_row(sqlite_row).executable_snapshot_hash,
    }

    # ---- relationship assertion (RED until fix) ----
    assert "executable_snapshot_hash" in producer_payload, (
        "EXECUTABLE_SNAPSHOT cert payload missing 'executable_snapshot_hash' — "
        "consumer build_final_intent_certificate_from_actionable will raise. "
        "Fix: add executable_snapshot_hash to the producer dict in event_reactor_adapter.py."
    )

    actual_hash = producer_payload["executable_snapshot_hash"]
    assert _SHA256_RE.match(str(actual_hash)), (
        f"executable_snapshot_hash is not a sha256 hex digest: {actual_hash!r}"
    )
    assert actual_hash == canonical_hash, (
        f"producer hash {actual_hash!r} != canonical {canonical_hash!r} — "
        "producer must use executable_snapshot_from_row(row).executable_snapshot_hash"
    )
