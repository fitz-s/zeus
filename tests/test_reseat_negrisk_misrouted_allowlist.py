# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR-208 5th-iter Karachi fix; _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS allowlist expansion
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody tests — REDEEM_NEGRISK_MISROUTED added to autonomous retry allowlist.
# Reuse: Run when modifying reseat_stub_deferred_rows_for_autonomous_retry(),
#         _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS, or the antibody → reseat → submitter chain.
"""Antibody tests: REDEEM_NEGRISK_MISROUTED in _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS.

Root cause (Karachi c8c220f5, 5th iteration): PR-207 antibody correctly resets
misrouted negRisk redeems to REDEEM_OPERATOR_REQUIRED with errorCode=REDEEM_NEGRISK_MISROUTED,
but reseat_stub_deferred_rows_for_autonomous_retry only allowed REDEEM_DEFERRED_TO_R1
in _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS — so Karachi sat parked in OPERATOR_REQUIRED forever
and the submitter never retried via NegRiskAdapter.

Fix (PR-208): add REDEEM_NEGRISK_MISROUTED to _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS.

Antibody contracts:
  T1: OPERATOR_REQUIRED + REDEEM_NEGRISK_MISROUTED → promoted to RETRYING, event appended.
  T2: OPERATOR_REQUIRED + REDEEM_NEGRISK_MISROUTED + autonomous_enabled=False → not promoted.
  T3: OPERATOR_REQUIRED + errorCode NOT in allowlist (REDEEM_VENUE_REJECTED) → not promoted.
  T4 (sed-flip): remove REDEEM_NEGRISK_MISROUTED from allowlist → T1 fails.
  T5 (end-to-end): seed Karachi-pattern row → call reseat → state advances to REDEEM_RETRYING.

Sed-break meta-verify: removing "REDEEM_NEGRISK_MISROUTED" from
_AUTONOMOUS_RETRY_ERRORCODES_ALWAYS causes T1 and T5 to fail (promoted=0).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.execution.settlement_commands import (
    SettlementState,
    _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS,
    reseat_stub_deferred_rows_for_autonomous_retry,
)
from src.state.db import init_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


def _insert_operator_required(
    conn: sqlite3.Connection, command_id: str, error_code: str | None
) -> None:
    error_payload = json.dumps({"errorCode": error_code}) if error_code else None
    conn.execute(
        """
        INSERT INTO settlement_commands
          (command_id, state, condition_id, market_id, payout_asset, requested_at, error_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            SettlementState.REDEEM_OPERATOR_REQUIRED.value,
            "0xnegrisk_cond_karachi",
            "0xmarket_karachi",
            "USDC",
            "2026-05-19T00:00:00Z",
            error_payload,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# T1 — primary contract: NEGRISK_MISROUTED is promoted when autonomous ON
# ---------------------------------------------------------------------------

def test_t1_negrisk_misrouted_promoted_to_retrying(conn, monkeypatch):
    """T1: OPERATOR_REQUIRED + REDEEM_NEGRISK_MISROUTED → promoted to RETRYING,
    event row appended with prior_errorcode=REDEEM_NEGRISK_MISROUTED."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    _insert_operator_required(conn, "karachi-t1", "REDEEM_NEGRISK_MISROUTED")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)
    conn.commit()

    assert promoted == 1, f"expected 1 promoted, got {promoted}"
    row = conn.execute(
        "SELECT state, terminal_at FROM settlement_commands WHERE command_id = ?",
        ("karachi-t1",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_RETRYING.value, (
        f"T1 FAIL: state={row['state']!r}, expected REDEEM_RETRYING"
    )
    assert row["terminal_at"] is None, "REDEEM_RETRYING must not be terminal"

    events = conn.execute(
        "SELECT event_type, payload_json FROM settlement_command_events WHERE command_id = ?",
        ("karachi-t1",),
    ).fetchall()
    assert len(events) == 1, f"expected 1 event, got {len(events)}"
    assert events[0]["event_type"] == SettlementState.REDEEM_RETRYING.value
    payload = json.loads(events[0]["payload_json"])
    assert payload.get("prior_errorcode") == "REDEEM_NEGRISK_MISROUTED", (
        f"event payload must carry prior_errorcode=REDEEM_NEGRISK_MISROUTED, got {payload!r}"
    )


# ---------------------------------------------------------------------------
# T2 — env gate: autonomous_enabled=False blocks promotion
# ---------------------------------------------------------------------------

def test_t2_negrisk_misrouted_not_promoted_when_autonomous_disabled(conn, monkeypatch):
    """T2: REDEEM_NEGRISK_MISROUTED row is NOT promoted when ZEUS_AUTONOMOUS_REDEEM_ENABLED
    is unset — env gate must be respected regardless of errorCode."""
    monkeypatch.delenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", raising=False)
    _insert_operator_required(conn, "karachi-t2", "REDEEM_NEGRISK_MISROUTED")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 0, f"T2 FAIL: expected 0 promoted, got {promoted}"
    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("karachi-t2",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value


# ---------------------------------------------------------------------------
# T3 — closed allowlist: non-allowlist errorCodes not promoted
# ---------------------------------------------------------------------------

def test_t3_non_allowlist_errorcode_not_promoted(conn, monkeypatch):
    """T3: REDEEM_VENUE_REJECTED is NOT in the allowlist — must stay in
    OPERATOR_REQUIRED even when autonomous is enabled."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    _insert_operator_required(conn, "karachi-t3", "REDEEM_VENUE_REJECTED")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 0, f"T3 FAIL: expected 0 promoted, got {promoted}"
    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("karachi-t3",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value


# ---------------------------------------------------------------------------
# T4 — sed-flip: removing NEGRISK_MISROUTED from allowlist kills T1
# ---------------------------------------------------------------------------

def test_t4_allowlist_gates_negrisk_misrouted_promotion():
    """T4 (sed-flip antibody): REDEEM_NEGRISK_MISROUTED MUST appear in
    _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS. This test is the direct membership
    assertion — it fails loudly (not silently) the moment the entry is removed.
    T1 and T5 also fail loudly on removal (they assert promoted == 1), so T4
    provides an explicit, self-documenting signal naming the exact entry at risk.

    To verify the sed-flip catches a regression: remove 'REDEEM_NEGRISK_MISROUTED'
    from _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS and this test should turn RED."""
    assert "REDEEM_NEGRISK_MISROUTED" in _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS, (
        "T4 FAIL: REDEEM_NEGRISK_MISROUTED missing from _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS. "
        "PR-208 allowlist expansion was reverted or not applied. "
        "Karachi rows will park in OPERATOR_REQUIRED forever."
    )


# ---------------------------------------------------------------------------
# T5 — end-to-end: seed Karachi-pattern row → reseat → RETRYING
# ---------------------------------------------------------------------------

def test_t5_end_to_end_karachi_pattern_advances_to_retrying(conn, monkeypatch):
    """T5 (end-to-end): seed a row matching the Karachi pattern
    (state=REDEEM_OPERATOR_REQUIRED, errorCode=REDEEM_NEGRISK_MISROUTED),
    call reseat_stub_deferred_rows_for_autonomous_retry, assert state
    advances to REDEEM_RETRYING. Adapter is not invoked — this tests
    the reseat → submitter hand-off seam, not the adapter broadcast."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    # Seed the exact error payload the PR-207 antibody writes
    error_payload = json.dumps({
        "errorCode": "REDEEM_NEGRISK_MISROUTED",
        "errorMessage": "tx routed to POLYGON_CTF_ADDRESS for negRisk market; reset for NegRiskAdapter retry",
        "detected_by": "reconcile_pending_redeems",
    })
    conn.execute(
        """
        INSERT INTO settlement_commands
          (command_id, state, condition_id, market_id, payout_asset, requested_at,
           error_payload, tx_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            "karachi-t5",
            SettlementState.REDEEM_OPERATOR_REQUIRED.value,
            "0xkarachi_negrisk_condition_id_c8c220f5",
            "0xkarachi_market",
            "USDC",
            "2026-05-19T06:00:00Z",
            error_payload,
        ),
    )
    conn.commit()

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)
    conn.commit()

    assert promoted == 1, f"T5 FAIL: expected 1 promoted, got {promoted}"
    row = conn.execute(
        "SELECT state, terminal_at FROM settlement_commands WHERE command_id = ?",
        ("karachi-t5",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_RETRYING.value, (
        f"T5 FAIL: Karachi row still stuck in {row['state']!r}; "
        "submitter will never retry via NegRiskAdapter"
    )
    assert row["terminal_at"] is None

    # Confirm event row exists so forensics queries find the reseat
    events = conn.execute(
        "SELECT payload_json FROM settlement_command_events WHERE command_id = ?",
        ("karachi-t5",),
    ).fetchall()
    assert len(events) >= 1
    payload = json.loads(events[-1]["payload_json"])
    assert payload.get("prior_errorcode") == "REDEEM_NEGRISK_MISROUTED"


# ---------------------------------------------------------------------------
# T6 — REDEEM_NEGRISK_FACT_MISSING also in autonomous retry allowlist (PR #212
# completion). The Gamma fallback helper _fetch_neg_risk_from_gamma_for_submitter
# resolves FACT_MISSING on the next submit attempt; the retry must therefore
# be allowed without operator intervention.
# ---------------------------------------------------------------------------


def test_t6_negrisk_fact_missing_promoted_to_retrying(conn, monkeypatch):
    """T6 (PR #212 completion): an OPERATOR_REQUIRED row with errorCode=
    REDEEM_NEGRISK_FACT_MISSING must be promoted to RETRYING — the submitter
    will then call _fetch_neg_risk_from_gamma_for_submitter() to source the
    missing fact from canonical Gamma authority and complete the redeem.
    Sed-flip: remove REDEEM_NEGRISK_FACT_MISSING from
    _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS → T6 → RED.
    """
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    error_payload = json.dumps({
        "errorCode": "REDEEM_NEGRISK_FACT_MISSING",
        "condition_id": "0xkarachi_negrisk_condition_id_c8c220f5",
        "errorMessage": "no snapshot row in world.executable_market_snapshots",
    })
    conn.execute(
        """
        INSERT INTO settlement_commands
          (command_id, state, condition_id, market_id, payout_asset, requested_at,
           error_payload, tx_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            "karachi-t6",
            SettlementState.REDEEM_OPERATOR_REQUIRED.value,
            "0xkarachi_negrisk_condition_id_c8c220f5",
            "0xkarachi_market",
            "USDC",
            "2026-05-19T06:00:00Z",
            error_payload,
        ),
    )
    conn.commit()

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)
    conn.commit()

    assert promoted == 1, (
        f"T6 FAIL: expected 1 promoted (FACT_MISSING now auto-retryable via "
        f"PR #212 Gamma fallback), got {promoted}. _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS "
        f"must include REDEEM_NEGRISK_FACT_MISSING."
    )
    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("karachi-t6",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_RETRYING.value, (
        f"T6 FAIL: Karachi-class FACT_MISSING row still stuck in {row['state']!r}; "
        "PR #212 Gamma fallback will never run because submitter only processes RETRYING rows"
    )


def test_t7_allowlist_contains_both_misrouted_and_fact_missing():
    """T7: explicit membership contract — _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS
    must contain both auto-recoverable error codes. Sed-flip: drop either key →
    RED. Drift guard: if the allowlist is rewritten, both must be preserved."""
    from src.execution.settlement_commands import _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS

    required = {"REDEEM_NEGRISK_MISROUTED", "REDEEM_NEGRISK_FACT_MISSING"}
    missing = required - set(_AUTONOMOUS_RETRY_ERRORCODES_ALWAYS)
    assert not missing, (
        f"T7 FAIL: _AUTONOMOUS_RETRY_ERRORCODES_ALWAYS missing {sorted(missing)}; "
        f"these are the structurally auto-recoverable error codes (PR #209 + #212). "
        f"Removing either silently re-introduces the Karachi-class latch."
    )
