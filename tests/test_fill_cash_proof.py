# Created: 2026-09-10
# Last reused/audited: 2026-09-10
# Authority basis: finalized fill cash proof implementation contract

from __future__ import annotations

from copy import deepcopy

import pytest
from eth_utils import keccak

from src.venue.fill_cash_proof import (
    POLYGON_EXCHANGE_V2_ADDRESS,
    POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS,
    _FEE_CHARGED,
    _ORDER_FILLED,
    _TRANSFER,
    decode_fill_cash_proof,
)


TX = "0x" + "11" * 32
BLOCK_HASH = "0x" + "22" * 32
FINALIZED_HASH = "0x" + "33" * 32
WALLET = "0x" + "aa" * 20
COUNTERPARTY = "0x" + "bb" * 20
COLLATERAL = "0x" + "cc" * 20
FEE_RECEIVER = "0x" + "dd" * 20
EXCHANGE = POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS.lower()


def _word(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _address_word(address: str) -> str:
    return "0x" + "0" * 24 + address[2:]


def _data(*values: int) -> str:
    return "0x" + "".join(_word(value)[2:] for value in values)


def _log(index: int, address: str, topics: list[str], data: str) -> dict:
    return {
        "logIndex": hex(index),
        "removed": False,
        "address": address,
        "transactionHash": TX,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x10",
        "topics": topics,
        "data": data,
    }


def _order(
    index: int = 0,
    *,
    maker: str = WALLET,
    taker: str = EXCHANGE,
    side: int = 0,
    token_id: int = 123,
    maker_amount: int = 2_730_000,
    taker_amount: int = 9_100_000,
    fee: int = 95_550,
    emitter: str = EXCHANGE,
) -> dict:
    return _log(
        index,
        emitter,
        [_word(int(_ORDER_FILLED, 16)), _word(index + 1), _address_word(maker), _address_word(taker)],
        _data(side, token_id, maker_amount, taker_amount, fee, 0, 0),
    )


def _fee(index: int = 1, *, amount: int = 95_550, receiver: str = FEE_RECEIVER) -> dict:
    return _log(index, EXCHANGE, [_word(int(_FEE_CHARGED, 16)), _address_word(receiver)], _data(amount))


def _transfer(index: int, sender: str, receiver: str, amount: int, *, token: str = COLLATERAL) -> dict:
    return _log(index, token, [_word(int(_TRANSFER, 16)), _address_word(sender), _address_word(receiver)], _data(amount))


def _valid_receipt(logs: list[dict]) -> tuple[dict, dict, dict, dict]:
    receipt = {
        "transactionHash": TX,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x10",
        "status": "0x1",
        "logs": logs,
    }
    header = {"hash": BLOCK_HASH, "number": "0x10"}
    finalized = {"hash": FINALIZED_HASH, "number": "0x11"}
    after = deepcopy(header)
    return receipt, header, finalized, after


def _decode(logs: list[dict], **changes):
    receipt, header, finalized, after = _valid_receipt(logs)
    receipt.update(changes.pop("receipt", {}))
    return decode_fill_cash_proof(
        chain_id=changes.pop("chain_id", 137),
        tx_hash=changes.pop("tx_hash", TX),
        wallet=changes.pop("wallet", WALLET),
        receipt=receipt,
        header=changes.pop("header", header),
        finalized_header=changes.pop("finalized_header", finalized),
        collateral_by_exchange=changes.pop(
            "collateral_by_exchange", {EXCHANGE: {"address": COLLATERAL, "decimals": 6}}
        ),
        header_after=changes.pop("header_after", after),
    )


def _buy_logs() -> list[dict]:
    return [
        _order(),
        _fee(),
        _transfer(2, WALLET, COUNTERPARTY, 2_730_000),
        _transfer(3, WALLET, FEE_RECEIVER, 95_550),
    ]


def test_buy_direct_counterparty_cash_and_exact_event_shape() -> None:
    result = _decode(_buy_logs())
    assert result == {
        "status": "PROVEN",
        "reason": "ok",
        "events": [
            {
                "log_index": 0,
                "exchange": EXCHANGE,
                "order_hash": _word(1),
                "maker": WALLET,
                "token_id": "123",
                "side": "BUY",
                "shares_atoms": 9_100_000,
                "principal_atoms": 2_730_000,
                "fee_atoms": 95_550,
            }
        ],
        "collateral_delta_atoms": -2_825_550,
        "collateral": COLLATERAL,
        "decimals": 6,
    }


def test_sell_cash_formula_and_zero_fee_without_fee_event() -> None:
    logs = [
        _order(side=1, maker_amount=9_100_000, taker_amount=2_730_000, fee=0),
        _transfer(2, COUNTERPARTY, WALLET, 2_730_000),
    ]
    result = _decode(logs)
    assert result["status"] == "PROVEN"
    assert result["events"][0]["side"] == "SELL"
    assert result["events"][0]["shares_atoms"] == 9_100_000
    assert result["collateral_delta_atoms"] == 2_730_000


def test_opponent_taker_event_is_not_double_counted() -> None:
    logs = _buy_logs()
    logs.insert(1, _order(index=4, maker=COUNTERPARTY, taker=WALLET, fee=0))
    result = _decode(logs)
    assert result["status"] == "PROVEN"
    assert len(result["events"]) == 1
    assert result["collateral_delta_atoms"] == -2_825_550

    opponent_only = _decode([_order(maker=COUNTERPARTY, taker=WALLET, fee=0)])
    assert opponent_only["status"] == "UNKNOWN"
    assert "wallet_orderfilled_missing" in opponent_only["reason"]


def test_multiple_wallet_maker_events_aggregate_once_each() -> None:
    logs = [
        _order(index=0, maker_amount=100, taker_amount=200, fee=3),
        _order(index=1, maker_amount=50, taker_amount=80, fee=0),
        _fee(index=2, amount=3),
        _transfer(3, WALLET, COUNTERPARTY, 100),
        _transfer(4, WALLET, COUNTERPARTY, 50),
        _transfer(5, WALLET, FEE_RECEIVER, 3),
    ]
    result = _decode(logs)
    assert result["status"] == "PROVEN"
    assert [event["log_index"] for event in result["events"]] == [0, 1]
    assert result["collateral_delta_atoms"] == -153


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"chain_id": 1}, "wrong_chain"),
        ({"receipt": {"status": "0x0"}}, "receipt_status_not_1"),
        ({"finalized_header": {"hash": FINALIZED_HASH, "number": "0xf"}}, "receipt_not_finalized"),
        ({"header_after": {"hash": "0x" + "44" * 32, "number": "0x10"}}, "canonical_header_after_mismatch"),
        ({"header": {"hash": "0x" + "44" * 32, "number": "0x10"}}, "canonical_header_mismatch"),
    ],
)
def test_chain_finality_and_reorg_gates_fail_closed(changes: dict, reason: str) -> None:
    result = _decode(_buy_logs(), **changes)
    assert result["status"] == "UNKNOWN"
    assert reason in result["reason"]
    assert result["collateral_delta_atoms"] is None


def test_chain_and_receipt_status_require_native_int_or_strict_hex() -> None:
    assert "wrong_chain" in _decode(_buy_logs(), chain_id=137.0)["reason"]
    assert "receipt_status_not_1" in _decode(_buy_logs(), receipt={"status": True})["reason"]

    same_height = {"hash": "0x" + "44" * 32, "number": "0x10"}
    result = _decode(_buy_logs(), finalized_header=same_height)
    assert result["status"] == "UNKNOWN"
    assert "finalized_header_hash_mismatch_same_height" in result["reason"]


def test_wrong_emitter_and_malformed_abi_are_unknown() -> None:
    wrong_emitter = _order(emitter="0x" + "ee" * 20)
    result = _decode([wrong_emitter] + _buy_logs()[1:])
    assert result["status"] == "UNKNOWN"
    assert "wallet_orderfilled_foreign_exchange" in result["reason"]

    malformed = _buy_logs()
    malformed[0]["topics"][2] = "0x" + "01" + "0" * 62
    result = _decode(malformed)
    assert result["status"] == "UNKNOWN"
    assert "orderfilled_address_padding_invalid" in result["reason"]


def test_duplicate_removed_and_identity_logs_are_unknown() -> None:
    duplicate = _buy_logs() + [deepcopy(_buy_logs()[0])]
    assert "duplicate_log_index" in _decode(duplicate)["reason"]

    removed = _buy_logs()
    removed[0]["removed"] = True
    assert "log_identity_invalid" in _decode(removed)["reason"]

    identity = _buy_logs()
    identity[0]["transactionHash"] = "0x" + "44" * 32
    assert "log_identity_invalid" in _decode(identity)["reason"]


def test_uint8_side_and_hex_width_are_strict() -> None:
    bad_side = _buy_logs()
    bad_side[0]["data"] = _data(2, 123, 2_730_000, 9_100_000, 95_550, 0, 0)
    assert "orderfilled_side_invalid" in _decode(bad_side)["reason"]

    bad_width = _buy_logs()
    bad_width[0]["topics"][3] = "0x" + "1" + "0" * 63
    assert "orderfilled_address_padding_invalid" in _decode(bad_width)["reason"]


def test_extra_wallet_cash_and_fee_mismatch_are_unknown() -> None:
    extra = _buy_logs() + [_transfer(4, COUNTERPARTY, WALLET, 1)]
    result = _decode(extra)
    assert result["status"] == "UNKNOWN"
    assert "wallet_cash_conservation_mismatch" in result["reason"]

    malformed_transfer = _buy_logs()
    malformed_transfer[2]["topics"] = malformed_transfer[2]["topics"][:2]
    result = _decode(malformed_transfer)
    assert result["status"] == "UNKNOWN"
    assert "transfer_abi_invalid" in result["reason"]

    mismatch = _buy_logs()
    mismatch[1] = _fee(amount=95_551)
    result = _decode(mismatch)
    assert result["status"] == "UNKNOWN"
    assert "fee_conservation_mismatch" in result["reason"]


def test_wallet_cash_uses_only_the_own_event_collateral() -> None:
    other_collateral = "0x" + "ee" * 20
    logs = [
        _order(),
        _fee(),
        _transfer(2, WALLET, COUNTERPARTY, 2_730_000, token=other_collateral),
        _transfer(3, WALLET, FEE_RECEIVER, 95_550),
    ]
    result = _decode(
        logs,
        collateral_by_exchange={
            EXCHANGE: {"address": COLLATERAL, "decimals": 6},
            POLYGON_EXCHANGE_V2_ADDRESS.lower(): {"address": other_collateral, "decimals": 6},
        },
    )
    assert result["status"] == "UNKNOWN"
    assert "wallet_cash_conservation_mismatch" in result["reason"]


def test_fill_amount_and_token_bounds_reject_non_fill_events() -> None:
    # Replace the order's token word while retaining the complete cash proof.
    logs = _buy_logs()
    logs[0]["data"] = _data(0, 0, 2_730_000, 9_100_000, 95_550, 0, 0)
    token_zero = _decode(logs)
    assert token_zero["status"] == "UNKNOWN"
    assert "orderfilled_token_id_invalid" in token_zero["reason"]

    zero_shares = _buy_logs()
    zero_shares[0]["data"] = _data(0, 123, 2_730_000, 0, 95_550, 0, 0)
    result = _decode(zero_shares)
    assert result["status"] == "UNKNOWN"
    assert "orderfilled_zero_shares" in result["reason"]

    negative_sell = _decode(
        [
            _order(side=1, maker_amount=9_100_000, taker_amount=10, fee=11),
            _fee(amount=11),
            _transfer(2, COUNTERPARTY, WALLET, 10),
            _transfer(3, WALLET, FEE_RECEIVER, 11),
        ]
    )
    assert negative_sell["status"] == "UNKNOWN"
    assert "negative_sell_net" in negative_sell["reason"]


def test_event_topics_match_official_keccak_signatures() -> None:
    signatures = {
        _ORDER_FILLED: "OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)",
        _FEE_CHARGED: "FeeCharged(address,uint256)",
        _TRANSFER: "Transfer(address,address,uint256)",
    }
    for topic, signature in signatures.items():
        assert "0x" + topic == "0x" + keccak(text=signature).hex()


def test_collateral_identity_and_decimals_are_not_guessed() -> None:
    result = _decode(_buy_logs(), collateral_by_exchange={EXCHANGE: {"address": COLLATERAL, "decimals": 18}})
    assert result["status"] == "UNKNOWN"
    assert "unsupported_collateral_decimals" in result["reason"]

    result = _decode(_buy_logs(), collateral_by_exchange={EXCHANGE: {"address": "0x" + "ef" * 20, "decimals": 6}})
    assert result["status"] == "UNKNOWN"
    assert "wallet_cash_conservation_mismatch" in result["reason"]


def test_fee_receiver_aggregate_allows_one_transfer_for_many_fee_rows() -> None:
    logs = [
        _order(index=0, maker_amount=100, taker_amount=200, fee=3),
        _order(index=1, maker_amount=50, taker_amount=80, fee=4),
        _fee(index=2, amount=3),
        _fee(index=3, amount=4),
        _transfer(4, WALLET, COUNTERPARTY, 100),
        _transfer(5, WALLET, COUNTERPARTY, 50),
        _transfer(6, WALLET, FEE_RECEIVER, 7),
    ]
    result = _decode(logs)
    assert result["status"] == "PROVEN"
    assert result["collateral_delta_atoms"] == -157
