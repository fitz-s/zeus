# Created: 2026-09-10
# Last reused or audited: 2026-09-10
# Authority basis: hourly capital gains improvement loop — read-only cash proof.
"""Offline contract tests for finalized fill cash reader."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

import src.state.fill_cash_reader as reader
from src.state.schema.venue_fill_cash_facts_schema import ensure_table


CUTOFF = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
WALLET = "0xwallet"
ORDER = "order-1"
TOKEN = "token-yes"


def _hash_json(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _setup() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT, venue_order_id TEXT
        );
        CREATE TABLE venue_submission_envelopes (
            envelope_id TEXT, chain_id INTEGER, funder_address TEXT,
            selected_outcome_token_id TEXT, side TEXT,
            canonical_pre_sign_payload_hash TEXT, raw_request_hash TEXT,
            signed_order_blob BLOB, signed_order_hash TEXT, order_id TEXT,
            captured_at TEXT
        );
        """
    )
    pre = (
        "pre", 137, WALLET, TOKEN, "BUY", "c" * 64, "r" * 64,
        None, None, None, "2026-09-10 10:00:00",
    )
    signed = (
        "signed", 137, WALLET, TOKEN, "BUY", "c" * 64, "r" * 64,
        b"signed-order", hashlib.sha256(b"signed-order").hexdigest(), ORDER,
        "2026-09-10 10:00:00",
    )
    conn.execute("INSERT INTO venue_submission_envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?)", pre)
    conn.execute("INSERT INTO venue_submission_envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?)", signed)
    conn.execute("INSERT INTO venue_commands VALUES (?,?)", ("cmd-1", ORDER))
    return conn


def _proof(
    *,
    tx_hash: str = "0xtx1",
    observed_at: str = "2026-09-10T11:00:00Z",
    decoded: dict | None = None,
    finalized_number: str = "0x70",
    receipt_marker: str = "receipt-1",
) -> dict:
    decoded = decoded or {
        "status": "PROVEN", "reason": "decoded",
        "events": [{
            "log_index": 1, "order_hash": ORDER, "token_id": TOKEN,
            "side": "BUY", "maker": WALLET, "shares_atoms": 1_000_000,
            "principal_atoms": 350_000, "fee_atoms": 7_000,
        }],
        "collateral_delta_atoms": -357_000,
        "collateral": "0xcollateral", "decimals": 6,
    }
    body = {
        "revision": "polygon_finalized_fill_cash_v1", "chain_id": 137,
        "tx_hash": tx_hash, "wallet": WALLET,
        "receipt": {"marker": receipt_marker, "status": "0x1"},
        "rpc_chain_id": 137,
        "header": {"number": "0x64", "hash": "0x" + "11" * 32},
        "finalized_header": {"number": finalized_number, "hash": "0x" + "22" * 32},
        "header_after": {"number": "0x64", "hash": "0x" + "11" * 32},
        "collateral_by_exchange": {"exchange": {"address": "0xcollateral", "decimals": 6}},
        "decoded": decoded,
    }
    return {
        "chain_id": 137, "tx_hash": tx_hash, "wallet": WALLET,
        "status": decoded["status"], "reason": decoded["reason"],
        "block_number": 100, "block_hash": "0x" + "11" * 32,
        "finalized_number": int(finalized_number, 16), "finalized_hash": "0x" + "22" * 32,
        "observed_at": observed_at, "proof_hash": _hash_json(body),
        "proof_json": json.dumps(body, sort_keys=True, separators=(",", ":")),
    }


def _read(conn, fills=None, *, cutoff=CUTOFF):
    return reader.read_command_fill_cash(
        conn,
        command={
            "command_id": "cmd-1", "venue_order_id": ORDER,
            "token_id": TOKEN, "envelope_id": "pre", "order_side": "BUY",
        },
        fills=fills or [{"tx_hash": "0xtx1", "filled_size": "1.000000", "venue_order_id": ORDER}],
        cutoff=cutoff,
    )


def _install_decoder(monkeypatch, proofs):
    decoded_by_tx = {
        proof["tx_hash"]: json.loads(proof["proof_json"])["decoded"]
        for proof in proofs
    }

    def decode(**kwargs):
        return decoded_by_tx[kwargs["tx_hash"]]

    monkeypatch.setattr(reader, "_decode_fill_cash_proof", decode)


def test_missing_table_is_unknown_without_pseudo_zero():
    conn = sqlite3.connect(":memory:")
    result = reader.read_command_fill_cash(
        conn,
        command={"command_id": "cmd-1", "venue_order_id": ORDER, "token_id": TOKEN,
                 "envelope_id": "pre", "order_side": "BUY"},
        fills=[{"tx_hash": "0xtx1", "filled_size": "1", "venue_order_id": ORDER}],
        cutoff=CUTOFF,
    )
    assert result == {"status": "UNKNOWN", "reason": "CHAIN_CASH_TABLE_MISSING"}


def test_one_to_many_local_children_conserve_against_one_chain_event(monkeypatch):
    conn = _setup()
    proof = _proof()
    conn.execute(
        "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (137, "0xtx1", WALLET, proof["status"], proof["reason"], proof["block_number"],
         proof["block_hash"], proof["finalized_number"], proof["finalized_hash"],
         proof["observed_at"], proof["proof_hash"], proof["proof_json"]),
    )
    _install_decoder(monkeypatch, [proof])
    result = _read(conn, [
        {"tx_hash": "0xtx1", "filled_size": "0.400000", "venue_order_id": ORDER},
        {"tx_hash": "0xtx1", "filled_size": "0.600000", "venue_order_id": ORDER},
    ])
    assert result["status"] == "PROVEN"
    assert result["shares_atoms"] == 1_000_000
    assert result["principal_atoms"] == 350_000
    assert result["fee_atoms"] == 7_000
    assert result["collateral_delta_atoms"] == -357_000
    assert result["decimals"] == 6
    assert result["collateral"] == "0xcollateral"


def test_sell_collateral_delta_uses_principal_minus_fee(monkeypatch):
    conn = _setup()
    conn.execute("UPDATE venue_submission_envelopes SET side='SELL'")
    decoded = {
        "status": "PROVEN", "reason": "decoded",
        "events": [{
            "log_index": 1, "order_hash": ORDER, "token_id": TOKEN,
            "side": "SELL", "maker": WALLET, "shares_atoms": 1_000_000,
            "principal_atoms": 350_000, "fee_atoms": 7_000,
        }],
        "collateral_delta_atoms": 343_000,
        "collateral": "0xcollateral", "decimals": 6,
    }
    proof = _proof(decoded=decoded)
    conn.execute(
        "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (137, "0xtx1", WALLET, proof["status"], proof["reason"], proof["block_number"],
         proof["block_hash"], proof["finalized_number"], proof["finalized_hash"],
         proof["observed_at"], proof["proof_hash"], proof["proof_json"]),
    )
    _install_decoder(monkeypatch, [proof])
    result = reader.read_command_fill_cash(
        conn,
        command={"command_id": "cmd-1", "venue_order_id": ORDER,
                 "token_id": TOKEN, "envelope_id": "pre", "side": "SELL"},
        fills=[{"tx_hash": "0xtx1", "filled_size": "1.000000", "venue_order_id": ORDER}],
        cutoff=CUTOFF,
    )
    assert result["status"] == "PROVEN"
    assert result["collateral_delta_atoms"] == 343_000


def test_same_wallet_foreign_order_is_conserved_but_cannot_pollute_target_fee(monkeypatch):
    conn = _setup()
    decoded = {
        "status": "PROVEN", "reason": "decoded",
        "events": [
            {"log_index": 1, "order_hash": ORDER, "token_id": TOKEN,
             "side": "BUY", "maker": WALLET, "shares_atoms": 1_000_000,
             "principal_atoms": 350_000, "fee_atoms": 7_000},
            {"log_index": 2, "order_hash": "order-foreign", "token_id": "token-other",
             "side": "SELL", "maker": WALLET, "shares_atoms": 2_000_000,
             "principal_atoms": 100_000, "fee_atoms": 99_000},
        ],
        "collateral_delta_atoms": -356_000,
        "collateral": "0xcollateral", "decimals": 6,
    }
    proof = _proof(decoded=decoded)
    conn.execute(
        "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (137, "0xtx1", WALLET, proof["status"], proof["reason"], proof["block_number"],
         proof["block_hash"], proof["finalized_number"], proof["finalized_hash"],
         proof["observed_at"], proof["proof_hash"], proof["proof_json"]),
    )
    _install_decoder(monkeypatch, [proof])
    result = _read(conn)
    assert result["status"] == "PROVEN"
    assert result["shares_atoms"] == 1_000_000
    assert result["principal_atoms"] == 350_000
    assert result["fee_atoms"] == 7_000


def test_duplicate_chain_proof_does_not_double_count_and_finality_upgrade_is_allowed(monkeypatch):
    conn = _setup()
    first = _proof(finalized_number="0x70")
    second = _proof(finalized_number="0x71", observed_at="2026-09-10T11:30:00Z")
    for proof in (first, second):
        conn.execute(
            "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
            (137, "0xtx1", WALLET, proof["status"], proof["reason"], proof["block_number"],
             proof["block_hash"], proof["finalized_number"], proof["finalized_hash"],
             proof["observed_at"], proof["proof_hash"], proof["proof_json"]),
        )
    _install_decoder(monkeypatch, [first, second])
    result = _read(conn)
    assert result["status"] == "PROVEN"
    assert result["proof_hashes"] == [first["proof_hash"]]
    assert result["available_at"] == "2026-09-10T11:00:00+00:00"


@pytest.mark.parametrize("bad_field", ["token_id", "side", "maker", "order_hash"])
def test_event_identity_mismatch_is_unknown(monkeypatch, bad_field):
    conn = _setup()
    decoded = _proof()
    body = json.loads(decoded["proof_json"])
    body["decoded"]["events"][0][bad_field] = "wrong"
    decoded = _proof(decoded=body["decoded"])
    conn.execute(
        "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (137, "0xtx1", WALLET, decoded["status"], decoded["reason"], decoded["block_number"],
         decoded["block_hash"], decoded["finalized_number"], decoded["finalized_hash"],
         decoded["observed_at"], decoded["proof_hash"], decoded["proof_json"]),
    )
    _install_decoder(monkeypatch, [decoded])
    assert _read(conn)["status"] == "UNKNOWN"


def test_proof_after_cutoff_and_invalid_decimal_are_unknown(monkeypatch):
    conn = _setup()
    late = _proof(observed_at="2026-09-10T12:00:00Z")
    conn.execute(
        "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (137, "0xtx1", WALLET, late["status"], late["reason"], late["block_number"],
         late["block_hash"], late["finalized_number"], late["finalized_hash"],
         late["observed_at"], late["proof_hash"], late["proof_json"]),
    )
    _install_decoder(monkeypatch, [late])
    assert _read(conn)["status"] == "UNKNOWN"
    assert _read(conn, [{"tx_hash": "0xtx1", "filled_size": "1.0000001", "venue_order_id": ORDER}])["status"] == "UNKNOWN"


def test_conflicting_proven_receipts_are_unknown(monkeypatch):
    conn = _setup()
    first = _proof(receipt_marker="receipt-1")
    second = _proof(receipt_marker="receipt-2")
    for proof in (first, second):
        conn.execute(
            "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
            (137, "0xtx1", WALLET, proof["status"], proof["reason"], proof["block_number"],
             proof["block_hash"], proof["finalized_number"], proof["finalized_hash"],
             proof["observed_at"], proof["proof_hash"], proof["proof_json"]),
        )
    _install_decoder(monkeypatch, [first, second])
    assert _read(conn)["status"] == "UNKNOWN"


def test_decoder_mismatch_and_signed_hash_collision_are_unknown(monkeypatch):
    conn = _setup()
    proof = _proof()
    conn.execute(
        "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (137, "0xtx1", WALLET, proof["status"], proof["reason"], proof["block_number"],
         proof["block_hash"], proof["finalized_number"], proof["finalized_hash"],
         proof["observed_at"], proof["proof_hash"], proof["proof_json"]),
    )
    _install_decoder(monkeypatch, [proof])
    monkeypatch.setattr(reader, "_decode_fill_cash_proof", lambda **kwargs: {"status": "UNKNOWN"})
    assert _read(conn)["status"] == "UNKNOWN"
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("signed-2", 137, WALLET, TOKEN, "BUY", "c" * 64, "r" * 64,
         b"different", hashlib.sha256(b"different").hexdigest(), ORDER,
         "2026-09-10 10:00:00"),
    )
    monkeypatch.setattr(reader, "_decode_fill_cash_proof", lambda **kwargs: json.loads(proof["proof_json"])["decoded"])
    assert _read(conn)["reason"] == "SIGNED_ENVELOPE_CONFLICT"


def test_future_signed_identity_does_not_change_historical_cutoff(monkeypatch):
    conn = _setup()
    proof = _proof()
    conn.execute(
        "INSERT INTO venue_fill_cash_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (137, "0xtx1", WALLET, proof["status"], proof["reason"], proof["block_number"],
         proof["block_hash"], proof["finalized_number"], proof["finalized_hash"],
         proof["observed_at"], proof["proof_hash"], proof["proof_json"]),
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("signed-future", 137, WALLET, TOKEN, "BUY", "c" * 64, "r" * 64,
         b"future", hashlib.sha256(b"future").hexdigest(), ORDER,
         "2026-09-10 13:00:00"),
    )
    _install_decoder(monkeypatch, [proof])
    assert _read(conn)["status"] == "PROVEN"


def test_future_pre_sign_envelope_is_not_historical_identity():
    conn = _setup()
    conn.execute("UPDATE venue_submission_envelopes SET captured_at='2026-09-10 13:00:00' WHERE envelope_id='pre'")
    assert _read(conn)["reason"] == "PRE_SIGN_ENVELOPE_PIT_UNBOUND"


def test_invalid_schema_is_rejected_without_sql():
    conn = _setup()
    with pytest.raises(ValueError, match="unsupported cash fact schema"):
        reader.read_command_fill_cash(
            conn, command={}, fills=[], cutoff=CUTOFF, schema="main;DROP TABLE venue_commands"
        )
