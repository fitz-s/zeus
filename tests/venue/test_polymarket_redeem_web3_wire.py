# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/PR_I5_WEB3_WIRE.md (PR-I.5.c)
"""Antibody tests for the PolymarketV2Adapter.redeem web3 wire.

PR-I.5.c (2026-05-18) wires `redeemPositions` calldata + sign + broadcast via
eth_abi + eth_account + the adapter's existing JSON-RPC caller. This module
proves the wire end-to-end without ever broadcasting on chain (RPC is mocked).

Test surface:
1. Kill switch default OFF → stub returned bytes-for-bytes; no RPC contact.
2. Kill switch ON, missing index_sets → REDEEM_INDEX_SETS_MISSING; no broadcast.
3. Kill switch ON, full wire happy path → broadcast hex equals the signed raw
   transaction; calldata starts with 0x01b7037c; ABI-decoded args match inputs.
4. eth_estimateGas reverts → REDEEM_GAS_ESTIMATE_REVERTED; no broadcast.
5. Idempotency invariant: same (condition_id, index_sets) → identical calldata
   bytes (modulo nonce/gasPrice). This is what makes CTF idempotency safe at
   the application layer: re-broadcasting is the same call, not a new one.
6. Selector pin (meta-verify antibody): keccak("redeemPositions(...)")[:4] is
   the constant the adapter uses; sed-break the constant and this test fails.

Meta-verify per feedback_antibody_recursion_metaverify_essential:
    Plan: flip CTF_REDEEM_POSITIONS_SELECTOR from "0x01b7037c" to "0x00000000"
    and re-run `test_selector_pin_matches_keccak` — it MUST fail with a
    selector mismatch assertion (not import error, not unrelated failure).
    Outcome recorded in PR description.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.venue.polymarket_v2_adapter import (
    AUTONOMOUS_REDEEM_ENABLED_ENV,
    CTF_REDEEM_POSITIONS_SELECTOR,
    POLYGON_CTF_ADDRESS,
    POLYGON_PUSD_ADDRESS,
    PolymarketV2Adapter,
    _build_redeem_calldata,
)


_TEST_PRIVATE_KEY = "0x" + "11" * 32  # deterministic test key
# _TEST_FUNDER must be the EOA derived from _TEST_PRIVATE_KEY so the
# fail-closed signer/funder-match preflight in redeem() passes.
from eth_account import Account as _Account  # noqa: E402
_TEST_FUNDER = _Account.from_key(_TEST_PRIVATE_KEY).address  # 0x19E7E376...
_TEST_MISMATCHED_FUNDER = "0x" + "22" * 20  # deliberately != _TEST_FUNDER
_TEST_RPC_URL = "https://polygon-test.invalid"
_TEST_CONDITION_ID = "0x" + "ab" * 32


def _make_adapter(rpc_call, funder_address=None):
    """Build a real PolymarketV2Adapter with rpc_call injected.

    No SDK client is constructed (factory replaced with a sentinel); only the
    web3 sign+broadcast path is exercised.  funder_address defaults to
    _TEST_FUNDER (EOA matching _TEST_PRIVATE_KEY) so the signer/funder-match
    preflight passes in all tests except the explicit mismatch test.
    """
    return PolymarketV2Adapter(
        funder_address=funder_address if funder_address is not None else _TEST_FUNDER,
        signer_key=_TEST_PRIVATE_KEY,
        chain_id=137,
        polygon_rpc_url=_TEST_RPC_URL,
        rpc_call=rpc_call,
        q1_egress_evidence_path=None,
        client_factory=lambda **kwargs: MagicMock(name="ClobClient"),
    )


def test_kill_switch_off_returns_stub_verbatim(monkeypatch):
    """Default OFF: adapter returns REDEEM_DEFERRED_TO_R1 stub; no RPC contact.

    This is the Karachi-safety guarantee. settlement_commands.py:426 keys on
    this exact errorCode to route to REDEEM_OPERATOR_REQUIRED.
    """
    monkeypatch.delenv(AUTONOMOUS_REDEEM_ENABLED_ENV, raising=False)
    rpc_call = MagicMock(side_effect=AssertionError("RPC must not be touched when kill switch OFF"))
    adapter = _make_adapter(rpc_call)

    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"
    assert result["condition_id"] == _TEST_CONDITION_ID
    rpc_call.assert_not_called()


def test_kill_switch_on_without_index_sets_returns_typed_error(monkeypatch):
    """Autonomous ON but index_sets unknown → typed errorCode, no broadcast."""
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")
    rpc_call = MagicMock(side_effect=AssertionError("RPC must not be touched without index_sets"))
    adapter = _make_adapter(rpc_call)

    for missing in (None, [], ()):
        result = adapter.redeem(_TEST_CONDITION_ID, index_sets=missing)
        assert result["success"] is False
        assert result["errorCode"] == "REDEEM_INDEX_SETS_MISSING"
    rpc_call.assert_not_called()


def test_full_wire_happy_path_signed_raw_tx_broadcast(monkeypatch):
    """End-to-end: kill switch ON, valid inputs → eth_sendRawTransaction called
    with the signed raw tx hex. Verify calldata structure and args round-trip
    via ABI-decode.
    """
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "true")

    rpc_calls: list[tuple[str, list]] = []

    def _fake_rpc_call(rpc_url, method, params):
        rpc_calls.append((method, list(params)))
        if method == "eth_getTransactionCount":
            return "0x7"  # nonce=7
        if method == "eth_gasPrice":
            return "0x6fc23ac00"  # 30 gwei
        if method == "eth_estimateGas":
            return "0x30d40"  # 200_000
        if method == "eth_sendRawTransaction":
            return "0x" + "ab" * 32  # tx hash
        raise AssertionError(f"unexpected RPC method: {method}")

    adapter = _make_adapter(_fake_rpc_call)
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is True
    assert result["tx_hash"] == "0x" + "ab" * 32
    assert result["nonce"] == 7
    assert result["index_sets"] == [2]
    # gas_limit = 200000 * 1.2 = 240000
    assert result["gas_limit"] == 240_000

    # RPC call order is load-bearing: nonce, gasPrice, estimateGas, send.
    methods = [m for m, _ in rpc_calls]
    assert methods == [
        "eth_getTransactionCount",
        "eth_gasPrice",
        "eth_estimateGas",
        "eth_sendRawTransaction",
    ]

    # eth_getTransactionCount uses 'pending' so an unconfirmed prior tx does
    # not collide with this nonce.
    assert rpc_calls[0][1] == [_TEST_FUNDER, "pending"]

    # The estimateGas 'to' must match POLYGON_CTF_ADDRESS exactly.
    estimate_params = rpc_calls[2][1][0]
    assert estimate_params["to"] == POLYGON_CTF_ADDRESS
    assert estimate_params["from"] == _TEST_FUNDER
    assert estimate_params["data"].startswith(CTF_REDEEM_POSITIONS_SELECTOR)

    # The broadcast payload is the signed raw transaction hex.
    raw_hex = rpc_calls[3][1][0]
    assert raw_hex.startswith("0x")
    assert len(raw_hex) > 100  # signed tx is not trivial

    # ABI-decode the calldata args (skip 4-byte selector) and prove the
    # condition_id + index_sets survived the wire intact.
    from eth_abi import decode as _abi_decode

    args_hex = estimate_params["data"][len(CTF_REDEEM_POSITIONS_SELECTOR):]
    args_bytes = bytes.fromhex(args_hex)
    decoded = _abi_decode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        args_bytes,
    )
    decoded_collateral, decoded_parent, decoded_condition, decoded_index_sets = decoded
    assert decoded_collateral.lower() == POLYGON_PUSD_ADDRESS.lower()
    assert decoded_parent == b"\x00" * 32
    assert "0x" + decoded_condition.hex() == _TEST_CONDITION_ID
    assert list(decoded_index_sets) == [2]


def test_eth_estimate_gas_revert_returns_review_required_errorcode(monkeypatch):
    """If eth_estimateGas reverts (already-redeemed, wrong index_sets, no
    balance), surface REDEEM_GAS_ESTIMATE_REVERTED — NOT REDEEM_RETRYING.
    Re-broadcasting won't change on-chain truth; operator must review.
    """
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    sent_payloads: list[str] = []
    from src.venue.polymarket_v2_adapter import V2AdapterError

    def _fake_rpc_call(rpc_url, method, params):
        if method == "eth_getTransactionCount":
            return "0x0"
        if method == "eth_gasPrice":
            return "0x1"
        if method == "eth_estimateGas":
            raise V2AdapterError("polygon rpc error: execution reverted")
        if method == "eth_sendRawTransaction":
            sent_payloads.append(params[0])
            return "0x" + "cd" * 32
        raise AssertionError(method)

    adapter = _make_adapter(_fake_rpc_call)
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_GAS_ESTIMATE_REVERTED"
    # CRITICAL: no broadcast happened — gas-revert short-circuits before sign+send.
    assert sent_payloads == []


def test_calldata_idempotent_for_same_inputs(monkeypatch):
    """Idempotency invariant at the calldata layer.

    CTF redeemPositions is spec-idempotent on chain (second call burns gas but
    does not double-pay), but application-layer idempotency depends on the
    state machine: REDEEM_SUBMITTED transitions BEFORE the adapter call, so
    only REDEEM_RETRYING rows re-broadcast. This test pins the calldata
    structure so a retry produces the same arguments — only nonce/gasPrice
    vary by chain state.
    """
    cd1 = _build_redeem_calldata(_TEST_CONDITION_ID, [2])
    cd2 = _build_redeem_calldata(_TEST_CONDITION_ID, [2])
    assert cd1 == cd2

    # Different condition_id MUST produce different calldata bytes.
    other_condition = "0x" + "cd" * 32
    cd_other = _build_redeem_calldata(other_condition, [2])
    assert cd_other != cd1

    # Different index_sets MUST produce different calldata bytes.
    cd_no_outcome = _build_redeem_calldata(_TEST_CONDITION_ID, [1])
    assert cd_no_outcome != cd1


def test_selector_pin_matches_keccak():
    """Meta-verify antibody: CTF_REDEEM_POSITIONS_SELECTOR is the canonical
    keccak256("redeemPositions(address,bytes32,bytes32,uint256[])")[:4].

    Sed-break/restore protocol: change the constant in
    src/venue/polymarket_v2_adapter.py from "0x01b7037c" to "0x00000000" and
    this test MUST fail with the selector mismatch assertion below (not an
    import error, not unrelated). Restore after verifying.
    """
    from eth_utils import keccak

    expected = (
        "0x"
        + keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])").hex()[:8]
    )
    assert CTF_REDEEM_POSITIONS_SELECTOR == expected, (
        f"CTF_REDEEM_POSITIONS_SELECTOR={CTF_REDEEM_POSITIONS_SELECTOR!r} "
        f"does not match keccak256 of redeemPositions signature ({expected!r})"
    )


def test_calldata_rejects_malformed_inputs():
    """Defensive contract on the calldata builder."""
    with pytest.raises(ValueError):
        _build_redeem_calldata("not_hex", [2])
    with pytest.raises(ValueError):
        _build_redeem_calldata("0x1234", [2])  # wrong length
    with pytest.raises(ValueError):
        _build_redeem_calldata(_TEST_CONDITION_ID, [])
    with pytest.raises(ValueError):
        _build_redeem_calldata(_TEST_CONDITION_ID, [0])  # uint256 must be > 0


def test_polygon_ctf_address_is_valid_checksum():
    """Antibody: POLYGON_CTF_ADDRESS is a valid EIP-55 checksummed Ethereum
    address.  A typo in the constant would produce a mis-checksummed or
    malformed address and silently broadcast to the wrong contract.

    PR description claimed this antibody exists; added here per bot comment
    #3256766577.
    """
    from eth_utils import to_checksum_address

    # to_checksum_address raises ValueError on invalid addresses and returns
    # the canonical checksummed form on valid ones.  Assert the constant
    # round-trips identically so case errors are also caught.
    assert to_checksum_address(POLYGON_CTF_ADDRESS) == POLYGON_CTF_ADDRESS, (
        f"POLYGON_CTF_ADDRESS {POLYGON_CTF_ADDRESS!r} is not EIP-55 checksum-valid"
    )


def test_kill_switch_off_errorMessage_unchanged():
    """Byte-for-byte audit: kill-switch OFF errorMessage must be identical to
    the pre-PR legacy stub so the audited fallback path is preserved exactly.
    """
    from unittest.mock import MagicMock

    adapter = _make_adapter(MagicMock())
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"
    assert result["errorMessage"] == (
        "R1 settlement command ledger must own pUSD redemption side effects"
    ), f"errorMessage changed from legacy value: {result['errorMessage']!r}"


def test_kill_switch_recognizes_truthy_variants(monkeypatch):
    """Accept the standard truthy strings; reject everything else."""
    rpc_call = MagicMock(side_effect=AssertionError("RPC must not be touched in dry-run"))
    adapter = _make_adapter(rpc_call)

    for falsy in ("", "0", "false", "no", "off", " "):
        monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, falsy)
        result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])
        assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1", (
            f"value {falsy!r} must disable autonomous mode"
        )

    # Truthy values must enable the autonomous path (which fails next-stage at
    # the RPC mock — that's expected; we only need to verify the kill switch
    # did NOT short-circuit to stub).
    captured: list[str] = []

    def _fake_rpc(rpc_url, method, params):
        captured.append(method)
        # short-circuit by raising — we only want to prove RPC was attempted
        raise RuntimeError("intercepted at first RPC call")

    adapter2 = _make_adapter(_fake_rpc)
    for truthy in ("1", "true", "True", "yes", "on"):
        captured.clear()
        monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, truthy)
        result = adapter2.redeem(_TEST_CONDITION_ID, index_sets=[2])
        assert result["errorCode"] != "REDEEM_DEFERRED_TO_R1", (
            f"value {truthy!r} must enable autonomous mode (got {result})"
        )
        assert captured, f"value {truthy!r} should have reached RPC"


def test_signer_funder_mismatch_fails_closed(monkeypatch):
    """Fail-closed guard: signature_type != 2 + EOA != funder → REDEEM_SIGNER_FUNDER_MISMATCH.

    With signature_type=2 a mismatch now enters the Safe wrap path (tested
    separately).  This test covers signature_type=0 (plain EOA path) where
    a mismatch remains a hard fail with no RPC contact.
    """
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")
    rpc_call = MagicMock(side_effect=AssertionError("RPC must not be called on mismatch"))
    adapter = PolymarketV2Adapter(
        funder_address=_TEST_MISMATCHED_FUNDER,
        signer_key=_TEST_PRIVATE_KEY,
        chain_id=137,
        polygon_rpc_url=_TEST_RPC_URL,
        rpc_call=rpc_call,
        q1_egress_evidence_path=None,
        client_factory=lambda **kwargs: MagicMock(name="ClobClient"),
        signature_type=0,
    )

    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_SIGNER_FUNDER_MISMATCH", (
        f"expected REDEEM_SIGNER_FUNDER_MISMATCH, got {result}"
    )
    rpc_call.assert_not_called()


def test_wrong_chain_id_fails_closed(monkeypatch):
    """Fail-closed guard: autonomous redeem must refuse to broadcast on any
    chain other than Polygon mainnet (137).
    """
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")
    rpc_call = MagicMock(side_effect=AssertionError("RPC must not be called on wrong chain"))
    adapter = PolymarketV2Adapter(
        funder_address=_TEST_FUNDER,
        signer_key=_TEST_PRIVATE_KEY,
        chain_id=1,  # Ethereum mainnet — wrong
        polygon_rpc_url=_TEST_RPC_URL,
        rpc_call=rpc_call,
        q1_egress_evidence_path=None,
        client_factory=lambda **kwargs: MagicMock(name="ClobClient"),
    )

    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_WRONG_CHAIN"
    rpc_call.assert_not_called()


def test_settlement_commands_parses_winning_index_set_and_passes_kw(monkeypatch, tmp_path):
    """Integration: submit_redeem → adapter.redeem must pass index_sets parsed
    from the JSON-encoded winning_index_set column.
    """
    import sqlite3
    from datetime import datetime, timezone

    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        SettlementState,
        init_settlement_command_schema,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: type("CutoverDecision", (), {
            "allow_redemption": True,
            "block_reason": None,
            "state": "LIVE_ENABLED",
        })(),
    )

    db_path = tmp_path / "settlement.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_settlement_command_schema(conn)

    command_id = request_redeem(
        _TEST_CONDITION_ID,
        "pUSD",
        market_id="market-test",
        pusd_amount_micro=1_000_000,
        token_amounts={"yes-token": "1"},
        conn=conn,
        requested_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        winning_index_set='["2"]',
    )

    captured_kwargs: dict = {}

    class _CapturingAdapter:
        def redeem(self, condition_id, *, index_sets=None, **_ignored):
            captured_kwargs["condition_id"] = condition_id
            captured_kwargs["index_sets"] = index_sets
            return {"success": True, "tx_hash": "0xtest"}

    submit_redeem(command_id, _CapturingAdapter(), object(), conn=conn)

    assert captured_kwargs["condition_id"] == _TEST_CONDITION_ID
    assert captured_kwargs["index_sets"] == [2]
    conn.close()


def test_submit_redeem_routes_index_sets_missing_to_operator_required(monkeypatch, tmp_path):
    """Integration: submit_redeem must route REDEEM_INDEX_SETS_MISSING to
    REDEEM_OPERATOR_REQUIRED (non-terminal), not REDEEM_FAILED (terminal).

    Missing winning-bin data is a harvester input gap, not a chain failure.
    The row must remain repairable via operator CLI.
    """
    import sqlite3
    from datetime import datetime, timezone

    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        SettlementState,
        init_settlement_command_schema,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: type("CutoverDecision", (), {
            "allow_redemption": True,
            "block_reason": None,
            "state": "LIVE_ENABLED",
        })(),
    )

    db_path = tmp_path / "settlement.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_settlement_command_schema(conn)

    # Request with winning_index_set=None so adapter receives index_sets=None.
    command_id = request_redeem(
        _TEST_CONDITION_ID,
        "pUSD",
        market_id="market-test",
        pusd_amount_micro=1_000_000,
        token_amounts={"yes-token": "1"},
        conn=conn,
        requested_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        winning_index_set=None,
    )

    class _MissingIndexSetsAdapter:
        def redeem(self, condition_id, *, index_sets=None, **_ignored):
            return {
                "success": False,
                "errorCode": "REDEEM_INDEX_SETS_MISSING",
                "errorMessage": "no index_sets",
                "condition_id": condition_id,
            }

    result = submit_redeem(command_id, _MissingIndexSetsAdapter(), object(), conn=conn)

    assert result.state == SettlementState.REDEEM_OPERATOR_REQUIRED, (
        f"REDEEM_INDEX_SETS_MISSING must route to REDEEM_OPERATOR_REQUIRED, got {result.state}"
    )
    conn.close()


def test_winning_index_set_json_non_list_rejected(monkeypatch, tmp_path):
    """Defensive parsing: a JSON-encoded string or object must not iterate
    characters/keys and produce silently wrong index_sets.  The parse must
    fail-closed to parsed_index_sets=None so the adapter returns
    REDEEM_INDEX_SETS_MISSING rather than broadcasting wrong calldata.
    """
    import sqlite3
    from datetime import datetime, timezone

    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        SettlementState,
        init_settlement_command_schema,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: type("CutoverDecision", (), {
            "allow_redemption": True,
            "block_reason": None,
            "state": "LIVE_ENABLED",
        })(),
    )

    db_path = tmp_path / "settlement.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_settlement_command_schema(conn)

    # Malformed: JSON string "2" — iterating chars would yield ["2"], an
    # accidental match for [2], so this is a subtle correctness failure.
    command_id = request_redeem(
        _TEST_CONDITION_ID,
        "pUSD",
        market_id="market-test",
        pusd_amount_micro=1_000_000,
        token_amounts={"yes-token": "1"},
        conn=conn,
        requested_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        winning_index_set='"2"',  # JSON-encoded bare string, not array
    )

    captured_index_sets: list = []

    class _RecordingAdapter:
        def redeem(self, condition_id, *, index_sets=None, **_ignored):
            captured_index_sets.append(index_sets)
            return {
                "success": False,
                "errorCode": "REDEEM_INDEX_SETS_MISSING",
                "errorMessage": "index_sets was None after parse failure",
                "condition_id": condition_id,
            }

    submit_redeem(command_id, _RecordingAdapter(), object(), conn=conn)

    assert captured_index_sets == [None], (
        f"malformed JSON string must produce index_sets=None, got {captured_index_sets}"
    )
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Safe v1.3.0 execTransaction antibody tests (Option A, 2026-05-19)
# Authority: /tmp/SAFE_REDEEM_AUTOMATION_DESIGN.md §Antibody tests
# Meta-verify protocol: test_safe_tx_hash_pinned_against_reference includes a
# monkeypatch.setattr sed-break of SAFE_TX_TYPEHASH to confirm the assertion
# actually catches a wrong typehash (not a vacuous pass).
# ─────────────────────────────────────────────────────────────────────────────

# Known-good reference vector.  Parameters:
#   safe=0xaaaa...aa, chain=137, to=0xbbbb...bb, value=0, data=b'', op=0, nonce=0
#   All zero gas fields.  Computed offline by build_safe_tx_hash itself (see
#   the computation at repo root scripts/compute_safe_tx_hash_vector.py).
_REF_SAFE_ADDRESS  = "0x" + "aa" * 20
_REF_CHAIN_ID      = 137
_REF_TO            = "0x" + "bb" * 20
_REF_SAFE_TX_HASH  = "0x7cabb3e5b9d5fd12a38408cb16feefd041cb8cb8cfeb4dd465397af08e0f2ec6"


def test_safe_tx_hash_pinned_against_reference(monkeypatch):
    """build_safe_tx_hash must reproduce the reference vector exactly.

    Meta-verify (sed-break): monkeypatch SAFE_TX_TYPEHASH to all-zeros and
    confirm the assertion fails with a hash mismatch (antibody catches the break).
    """
    import src.venue.safe_exec as _safe_exec
    from src.venue.safe_exec import build_safe_tx_hash

    result = build_safe_tx_hash(
        safe_address=_REF_SAFE_ADDRESS,
        chain_id=_REF_CHAIN_ID,
        to=_REF_TO,
        value=0,
        data=b"",
        operation=0,
        nonce=0,
    )
    assert "0x" + result.hex() == _REF_SAFE_TX_HASH, (
        f"SafeTxHash mismatch: got 0x{result.hex()}, expected {_REF_SAFE_TX_HASH}"
    )

    # ── Meta-verify: break SAFE_TX_TYPEHASH → hash must differ ───────────────
    broken_typehash = bytes(32)  # all-zeros
    monkeypatch.setattr(_safe_exec, "SAFE_TX_TYPEHASH", broken_typehash)
    broken_result = build_safe_tx_hash(
        safe_address=_REF_SAFE_ADDRESS,
        chain_id=_REF_CHAIN_ID,
        to=_REF_TO,
        value=0,
        data=b"",
        operation=0,
        nonce=0,
    )
    assert "0x" + broken_result.hex() != _REF_SAFE_TX_HASH, (
        "ANTIBODY META-VERIFY FAILED: zeroed SAFE_TX_TYPEHASH still produces "
        "the reference hash — the assertion does not catch a typehash mutation"
    )

    # ── Meta-verify: break DOMAIN_SEPARATOR_TYPEHASH → hash must differ ──────
    monkeypatch.setattr(_safe_exec, "SAFE_TX_TYPEHASH", _safe_exec.SAFE_TX_TYPEHASH)  # restore
    broken_domain_typehash = bytes(32)  # all-zeros
    monkeypatch.setattr(_safe_exec, "DOMAIN_SEPARATOR_TYPEHASH", broken_domain_typehash)
    broken_domain_result = build_safe_tx_hash(
        safe_address=_REF_SAFE_ADDRESS,
        chain_id=_REF_CHAIN_ID,
        to=_REF_TO,
        value=0,
        data=b"",
        operation=0,
        nonce=0,
    )
    assert "0x" + broken_domain_result.hex() != _REF_SAFE_TX_HASH, (
        "ANTIBODY META-VERIFY FAILED: zeroed DOMAIN_SEPARATOR_TYPEHASH still "
        "produces the reference hash — the domain separator mutation is not detected"
    )


def test_safe_branch_engaged_only_when_signature_type_2_and_eoa_differs(monkeypatch):
    """Routing logic: three cases.

    1. sig_type=0 + mismatch → REDEEM_SIGNER_FUNDER_MISMATCH (no Safe wrap)
    2. sig_type=2 + EOA matches funder → Safe wrap NOT engaged (passes through)
    3. sig_type=2 + mismatch → _redeem_via_safe is entered (VERSION call made)
    """
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    # Case 1: sig_type=0, mismatch → hard fail, no RPC
    rpc_no_call = MagicMock(side_effect=AssertionError("RPC must not be called"))
    adapter0 = PolymarketV2Adapter(
        funder_address=_TEST_MISMATCHED_FUNDER,
        signer_key=_TEST_PRIVATE_KEY,
        chain_id=137,
        polygon_rpc_url=_TEST_RPC_URL,
        rpc_call=rpc_no_call,
        q1_egress_evidence_path=None,
        client_factory=lambda **kwargs: MagicMock(name="ClobClient"),
        signature_type=0,
    )
    r0 = adapter0.redeem(_TEST_CONDITION_ID, index_sets=[2])
    assert r0["errorCode"] == "REDEEM_SIGNER_FUNDER_MISMATCH", r0
    rpc_no_call.assert_not_called()

    # Case 2: sig_type=2, EOA == funder → not a mismatch; standard path continues
    # (will hit REDEEM_RPC_PRECHECK_FAILED because rpc_call raises, but NOT Safe wrap)
    rpc_fail = MagicMock(side_effect=RuntimeError("rpc stub"))
    adapter2_match = _make_adapter(rpc_fail, funder_address=_TEST_FUNDER)
    r2_match = adapter2_match.redeem(_TEST_CONDITION_ID, index_sets=[2])
    # Standard path: should reach RPC (nonce fetch) before failing, not Safe wrap
    assert r2_match["errorCode"] != "REDEEM_SIGNER_FUNDER_MISMATCH", r2_match
    assert r2_match["errorCode"] != "REDEEM_SAFE_VERSION_UNSUPPORTED", r2_match

    # Case 3: sig_type=2, mismatch → Safe wrap engaged; VERSION call is first RPC call
    rpc_version_fail = MagicMock(side_effect=RuntimeError("version rpc stub"))
    adapter2_mismatch = PolymarketV2Adapter(
        funder_address=_TEST_MISMATCHED_FUNDER,
        signer_key=_TEST_PRIVATE_KEY,
        chain_id=137,
        polygon_rpc_url=_TEST_RPC_URL,
        rpc_call=rpc_version_fail,
        q1_egress_evidence_path=None,
        client_factory=lambda **kwargs: MagicMock(name="ClobClient"),
        signature_type=2,
    )
    r2_mismatch = adapter2_mismatch.redeem(_TEST_CONDITION_ID, index_sets=[2])
    # Safe wrap engaged; VERSION() RPC failed → REDEEM_RPC_PRECHECK_FAILED
    # (not REDEEM_SAFE_VERSION_UNSUPPORTED which is reserved for a decoded
    # version string that doesn't match 1.3.0 — see SEV-3 fix).
    assert r2_mismatch["errorCode"] == "REDEEM_RPC_PRECHECK_FAILED", r2_mismatch
    rpc_version_fail.assert_called_once()


def _make_safe_adapter(rpc_call_fn):
    """Build adapter with sig_type=2 and mismatched funder (Safe deployment)."""
    return PolymarketV2Adapter(
        funder_address=_TEST_MISMATCHED_FUNDER,
        signer_key=_TEST_PRIVATE_KEY,
        chain_id=137,
        polygon_rpc_url=_TEST_RPC_URL,
        rpc_call=rpc_call_fn,
        q1_egress_evidence_path=None,
        client_factory=lambda **kwargs: MagicMock(name="ClobClient"),
        signature_type=2,
    )


def _abi_encode_string(s: str) -> str:
    """ABI-encode a string for eth_call return simulation."""
    import eth_abi
    return "0x" + eth_abi.encode(["string"], [s]).hex()


def _abi_encode_address_array(addrs: list) -> str:
    import eth_abi
    return "0x" + eth_abi.encode(["address[]"], [addrs]).hex()


def _hex32(n: int) -> str:
    return "0x" + n.to_bytes(32, "big").hex()


def test_safe_version_mismatch_fails_closed(monkeypatch):
    """Mock VERSION()='1.4.1' → REDEEM_SAFE_VERSION_UNSUPPORTED; no broadcast."""
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    rpc = MagicMock(return_value=_abi_encode_string("1.4.1"))
    adapter = _make_safe_adapter(rpc)
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_SAFE_VERSION_UNSUPPORTED", result
    # Only VERSION() call should have been made; no broadcast
    assert rpc.call_count == 1


def test_safe_owner_not_in_getowners_fails_closed(monkeypatch):
    """Mock getOwners() missing signer → REDEEM_SAFE_OWNER_MISMATCH."""
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    # Sequence: VERSION ok, getOwners returns list without our signer
    other_addr = "0x" + "33" * 20
    responses = [
        _abi_encode_string("1.3.0"),
        _abi_encode_address_array([other_addr]),
    ]
    rpc = MagicMock(side_effect=responses)
    adapter = _make_safe_adapter(rpc)
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_SAFE_OWNER_MISMATCH", result
    assert rpc.call_count == 2


def test_eoa_matic_below_floor_fails_closed(monkeypatch):
    """eth_getBalance < 0.05 MATIC → REDEEM_EOA_MATIC_INSUFFICIENT."""
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    signer_eoa = _Account.from_key(_TEST_PRIVATE_KEY).address
    # VERSION ok, getOwners includes signer, nonce=0, balance=0.01 MATIC
    responses = [
        _abi_encode_string("1.3.0"),
        _abi_encode_address_array([signer_eoa]),
        _hex32(0),            # nonce
        hex(10_000_000_000_000_000),  # 0.01 MATIC — below 0.05 floor
    ]
    rpc = MagicMock(side_effect=responses)
    adapter = _make_safe_adapter(rpc)
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_EOA_MATIC_INSUFFICIENT", result


def test_dry_run_signs_but_skips_broadcast(monkeypatch):
    """DRY_RUN=1: raw tx built+signed, no eth_sendRawTransaction, errorCode=REDEEM_DRY_RUN_LOGGED."""
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_DRY_RUN", "1")

    signer_eoa = _Account.from_key(_TEST_PRIVATE_KEY).address
    # Responses for: VERSION, getOwners, nonce, eth_getBalance,
    # eth_getTransactionCount, eth_gasPrice, eth_estimateGas
    responses = [
        _abi_encode_string("1.3.0"),
        _abi_encode_address_array([signer_eoa]),
        _hex32(0),                          # safe nonce
        hex(100_000_000_000_000_000),        # 0.1 MATIC — above floor
        hex(5),                              # EOA tx count
        hex(30_000_000_000),                 # gasPrice 30 gwei
        hex(200_000),                        # estimateGas
    ]
    rpc = MagicMock(side_effect=responses)
    adapter = _make_safe_adapter(rpc)
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_DRY_RUN_LOGGED", result
    # codereview-may19 P0-2: raw_tx_hex was redacted to a SHA-256 fingerprint
    # to prevent broadcast-by-observer. Operators correlate via fingerprint,
    # not the raw bytes.
    assert "raw_tx_hex" not in result, (
        "Safe dry-run return must NOT include raw_tx_hex (P0-2 redaction)."
    )
    assert "dry_run_fingerprint" in result, (
        "Safe dry-run return MUST include dry_run_fingerprint (P0-2 contract)."
    )
    assert isinstance(result["dry_run_fingerprint"], str)
    assert len(result["dry_run_fingerprint"]) == 16, (
        "dry_run_fingerprint must be first-16-hex-chars of SHA-256(raw_hex)."
    )
    # Confirm eth_sendRawTransaction was never called
    for call_args in rpc.call_args_list:
        method = call_args[0][1] if len(call_args[0]) >= 2 else ""
        assert method != "eth_sendRawTransaction", (
            "DRY_RUN must not call eth_sendRawTransaction"
        )


def test_exec_transaction_calldata_selector_is_0x6a761202():
    """Wire pin: EXEC_TX_SELECTOR must equal keccak256('execTransaction(...)')[:4]."""
    from eth_utils import keccak
    from src.venue.safe_exec import EXEC_TX_SELECTOR, build_exec_transaction_calldata

    # Verify constant
    expected_selector = keccak(
        b"execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)"
    )[:4]
    assert EXEC_TX_SELECTOR == expected_selector, (
        f"EXEC_TX_SELECTOR mismatch: {EXEC_TX_SELECTOR.hex()} != {expected_selector.hex()}"
    )

    # Verify calldata starts with selector
    calldata = build_exec_transaction_calldata(
        to="0x" + "bb" * 20,
        value=0,
        data=b"",
        operation=0,
        signatures=b"\x00" * 65,
    )
    assert calldata.startswith("0x" + EXEC_TX_SELECTOR.hex()), (
        f"calldata selector mismatch: {calldata[:10]}"
    )


def test_operator_review_errorcodes_set_equals_adapter_enumerated_codes(monkeypatch):
    """Drift antibody: _OPERATOR_REVIEW_ERRORCODES must contain the 4 new Safe codes.

    For each new code, verifies it routes to REDEEM_OPERATOR_REQUIRED via
    submit_redeem (integration: state machine routing, not just set membership).
    """
    import sqlite3
    from datetime import datetime, timezone

    from src.contracts.fx_classification import FXClassification
    from src.execution.settlement_commands import (
        init_settlement_command_schema,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: type("D", (), {"allow_redemption": True, "block_reason": None, "state": "LIVE_ENABLED"})(),
    )

    SAFE_CODES = {
        "REDEEM_SAFE_VERSION_UNSUPPORTED",
        "REDEEM_SAFE_OWNER_MISMATCH",
        "REDEEM_EOA_MATIC_INSUFFICIENT",
        "REDEEM_DRY_RUN_LOGGED",
    }

    for code in SAFE_CODES:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_settlement_command_schema(conn)

        command_id = request_redeem(
            _TEST_CONDITION_ID,
            "pUSD",
            market_id="market-drift-test",
            pusd_amount_micro=1_000_000,
            token_amounts={"yes-token": "1"},
            conn=conn,
            requested_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            winning_index_set='[2]',
        )

        _code = code  # capture for closure

        class _SafeCodeAdapter:
            def redeem(self, cid, *, index_sets=None, **_ignored):
                return {"success": False, "errorCode": _code, "errorMessage": "test", "condition_id": cid}

        submit_redeem(command_id, _SafeCodeAdapter(), object(), conn=conn)

        row = conn.execute(
            "SELECT state FROM settlement_commands WHERE command_id=?", (command_id,)
        ).fetchone()
        assert row["state"] == "REDEEM_OPERATOR_REQUIRED", (
            f"errorCode {code!r} should route to REDEEM_OPERATOR_REQUIRED, got {row['state']!r}"
        )
        conn.close()


def test_safe_branch_aborts_when_chain_id_not_polygon(monkeypatch):
    """SEV-1 antibody: chain_id guard fires BEFORE Safe branch routing.

    With signature_type=2 and a mismatched funder (Safe deployment scenario),
    a non-137 chain_id must return REDEEM_WRONG_CHAIN without making any RPC
    calls.  Prior to the fix the chain guard sat below the Safe branch dispatch,
    so a wrong-chain adapter could enter _redeem_via_safe and begin preflights.
    """
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    rpc_no_call = MagicMock(side_effect=AssertionError("RPC must not be called on wrong chain"))
    adapter = PolymarketV2Adapter(
        funder_address=_TEST_MISMATCHED_FUNDER,
        signer_key=_TEST_PRIVATE_KEY,
        chain_id=1,  # Ethereum mainnet — wrong
        polygon_rpc_url=_TEST_RPC_URL,
        rpc_call=rpc_no_call,
        q1_egress_evidence_path=None,
        client_factory=lambda **kwargs: MagicMock(name="ClobClient"),
        signature_type=2,  # Safe deployment — would enter _redeem_via_safe pre-fix
    )

    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_WRONG_CHAIN", result
    rpc_no_call.assert_not_called()


def test_rpc_timeout_distinguished_from_version_mismatch(monkeypatch):
    """SEV-3 antibody: RPC failure on VERSION() must return REDEEM_RPC_PRECHECK_FAILED,
    not REDEEM_SAFE_VERSION_UNSUPPORTED.

    A network blip or RPC node error on the VERSION() eth_call must not quarantine
    a healthy Safe as having an unsupported version.
    """
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    # Simulate RPC failure (timeout, network error, etc.)
    rpc = MagicMock(side_effect=RuntimeError("connection timeout"))
    adapter = _make_safe_adapter(rpc)
    result = adapter.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_RPC_PRECHECK_FAILED", (
        f"RPC failure must map to REDEEM_RPC_PRECHECK_FAILED, not "
        f"REDEEM_SAFE_VERSION_UNSUPPORTED; got {result['errorCode']!r}"
    )

    # Semantic mismatch (decoded version != 1.3.0) must still produce the
    # semantic code — the RPC itself succeeds but the version is wrong.
    rpc2 = MagicMock(return_value=_abi_encode_string("1.4.1"))
    adapter2 = _make_safe_adapter(rpc2)
    result2 = adapter2.redeem(_TEST_CONDITION_ID, index_sets=[2])

    assert result2["success"] is False
    assert result2["errorCode"] == "REDEEM_SAFE_VERSION_UNSUPPORTED", (
        f"Semantic version mismatch must map to REDEEM_SAFE_VERSION_UNSUPPORTED; "
        f"got {result2['errorCode']!r}"
    )


def test_safe_sign_against_pinned_eth_account_version(monkeypatch):
    """SEV-2 antibody: sign_safe_tx must use the public unsafe_sign_hash API and
    produce a valid 65-byte signature for a known input.

    eth_account==0.13.7 is pinned in requirements.txt.  This test catches any
    API rename that would cause a silent AttributeError at signing time.
    """
    from src.venue.safe_exec import sign_safe_tx

    test_hash = bytes.fromhex("7cabb3e5b9d5fd12a38408cb16feefd041cb8cb8cfeb4dd465397af08e0f2ec6")
    sig = sign_safe_tx(test_hash, _TEST_PRIVATE_KEY)

    assert isinstance(sig, bytes), f"Expected bytes, got {type(sig)}"
    assert len(sig) == 65, f"Expected 65-byte signature, got {len(sig)}"
    # v byte must be 27 or 28 (Safe EOA ECDSA convention)
    assert sig[64] in (27, 28), f"Expected v in (27, 28), got {sig[64]}"
