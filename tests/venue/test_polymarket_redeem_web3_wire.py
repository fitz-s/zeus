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
    """Fail-closed guard: if signer EOA != funder_address, redeem() must return
    REDEEM_SIGNER_FUNDER_MISMATCH without touching the RPC.

    This is the structural antibody for the Safe/proxy deployment scenario where
    Zeus's funder is a POLY_GNOSIS_SAFE but the signer_key is an EOA.
    """
    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")
    rpc_call = MagicMock(side_effect=AssertionError("RPC must not be called on mismatch"))
    adapter = _make_adapter(rpc_call, funder_address=_TEST_MISMATCHED_FUNDER)

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
        def redeem(self, condition_id, *, index_sets=None):
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
        def redeem(self, condition_id, *, index_sets=None):
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
        def redeem(self, condition_id, *, index_sets=None):
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
