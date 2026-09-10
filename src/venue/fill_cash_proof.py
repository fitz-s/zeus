"""Pure, fail-closed decoder for finalized Polymarket fill cash evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any


# Keep these local so this proof decoder never imports the live SDK adapter.
POLYGON_EXCHANGE_V2_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"
POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"

_EXCHANGES = frozenset(
    {
        POLYGON_EXCHANGE_V2_ADDRESS.lower(),
        POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS.lower(),
    }
)
_ORDER_FILLED = "d543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
_FEE_CHARGED = "55bb3cade9d43b798a4fe5ffdd05024b2d7870df53920673bfc7e68047cd0ab1"
_TRANSFER = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _unknown(
    reasons: list[str],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "UNKNOWN",
        "reason": ";".join(dict.fromkeys(reasons)) or "proof_incomplete",
        "events": events,
        "collateral_delta_atoms": None,
        "collateral": None,
        "decimals": None,
    }


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _hex(value: Any, width: int) -> str | None:
    if not isinstance(value, str) or not value.startswith("0x"):
        return None
    body = value[2:]
    if len(body) != width * 2:
        return None
    if any(c not in "0123456789abcdefABCDEF" for c in body):
        return None
    return "0x" + body.lower()


def _word(value: Any) -> str | None:
    return _hex(value, 32)


def _address(value: Any) -> str | None:
    return _hex(value, 20)


def _address_word(value: Any) -> str | None:
    word = _word(value)
    if word is None or word[2:26] != "0" * 24:
        return None
    return "0x" + word[-40:]


def _quantity(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str) or not value.startswith("0x"):
        return None
    body = value[2:]
    if not body or any(c not in "0123456789abcdefABCDEF" for c in body):
        return None
    if len(body) > 1 and body[0] == "0":
        return None
    return int(body, 16)


def _topic_signature(topics: Any) -> str | None:
    if not isinstance(topics, list) or not topics:
        return None
    return _word(topics[0])


def _uint_word(value: Any) -> int | None:
    word = _word(value)
    return int(word[2:], 16) if word is not None else None


def _data_words(data: Any, count: int) -> list[str] | None:
    raw = _hex(data, count * 32)
    if raw is None:
        return None
    body = raw[2:]
    if len(body) != count * 64:
        return None
    return ["0x" + body[i : i + 64] for i in range(0, len(body), 64)]


def _event_base(log: Mapping[str, Any], receipt: Mapping[str, Any], tx_hash: str, block_hash: str, block_number: int) -> tuple[int, str] | None:
    index = _quantity(log.get("logIndex"))
    if index is None:
        return None
    if log.get("removed") is not False:
        return None
    log_tx = _hex(log.get("transactionHash"), 32)
    log_block_hash = _hex(log.get("blockHash"), 32)
    log_block = _quantity(log.get("blockNumber"))
    if log_tx != tx_hash or log_block_hash != block_hash or log_block != block_number:
        return None
    emitter = _address(log.get("address"))
    if emitter is None:
        return None
    topics = log.get("topics")
    if not isinstance(topics, list) or not topics or any(_word(topic) is None for topic in topics):
        return None
    return index, emitter


def decode_fill_cash_proof(
    *,
    chain_id: int,
    tx_hash: str,
    wallet: str,
    receipt: dict,
    header: dict,
    finalized_header: dict,
    collateral_by_exchange: dict[str, dict[str, Any]],
    header_after: dict,
) -> dict[str, Any]:
    """Decode one receipt into a finalized, wallet-scoped cash proof.

    The function only interprets supplied values.  It performs no RPC, DB, SDK,
    filesystem, signing, or persistence operation.
    """

    reasons: list[str] = []
    events: list[dict[str, Any]] = []
    if type(chain_id) is not int or chain_id != 137:
        reasons.append("wrong_chain")

    tx = _hex(tx_hash, 32)
    wallet_address = _address(wallet)
    if tx is None:
        reasons.append("tx_hash_invalid")
    if wallet_address is None:
        reasons.append("wallet_invalid")

    if not _is_mapping(receipt):
        return _unknown(reasons + ["receipt_invalid"], events)
    receipt_tx = _hex(receipt.get("transactionHash"), 32)
    receipt_block_hash = _hex(receipt.get("blockHash"), 32)
    receipt_block_number = _quantity(receipt.get("blockNumber"))
    if tx is None or receipt_tx != tx:
        reasons.append("receipt_tx_identity_mismatch")
    if receipt_block_hash is None or receipt_block_number is None:
        reasons.append("receipt_block_identity_invalid")
    status = receipt.get("status")
    if not ((type(status) is int and status == 1) or (type(status) is str and status == "0x1")):
        reasons.append("receipt_status_not_1")

    def header_identity(value: Any) -> tuple[str | None, int | None]:
        if not _is_mapping(value):
            return None, None
        return _hex(value.get("hash"), 32), _quantity(value.get("number"))

    canonical_hash, canonical_number = header_identity(header)
    after_hash, after_number = header_identity(header_after)
    finalized_hash, finalized_number = header_identity(finalized_header)
    if receipt_block_hash is None or receipt_block_number is None:
        reasons.append("receipt_block_identity_missing")
    else:
        if (canonical_hash, canonical_number) != (receipt_block_hash, receipt_block_number):
            reasons.append("canonical_header_mismatch")
        if (after_hash, after_number) != (receipt_block_hash, receipt_block_number):
            reasons.append("canonical_header_after_mismatch")
        if finalized_hash is None or finalized_number is None:
            reasons.append("finalized_header_invalid")
        elif finalized_number < receipt_block_number:
            reasons.append("receipt_not_finalized")
        elif finalized_number == receipt_block_number and finalized_hash != receipt_block_hash:
            reasons.append("finalized_header_hash_mismatch_same_height")
    if finalized_hash is None or finalized_number is None:
        reasons.append("finalized_header_invalid")

    if not isinstance(receipt.get("logs"), list):
        return _unknown(reasons + ["receipt_logs_invalid"], events)

    # Validate every log identity before interpreting any event. A receipt with
    # a missing/removed/replayed log cannot become a partial cash proof.
    log_rows: list[tuple[int, str, Mapping[str, Any], str]] = []
    seen_indexes: set[int] = set()
    if tx is not None and receipt_block_hash is not None and receipt_block_number is not None:
        for log in receipt["logs"]:
            if not _is_mapping(log):
                reasons.append("log_invalid")
                continue
            base = _event_base(log, receipt, tx, receipt_block_hash, receipt_block_number)
            if base is None:
                reasons.append("log_identity_invalid")
                continue
            index, emitter = base
            if index in seen_indexes:
                reasons.append("duplicate_log_index")
            seen_indexes.add(index)
            signature = _topic_signature(log.get("topics"))
            if signature is None:
                reasons.append("log_topics_invalid")
                continue
            log_rows.append((index, emitter, log, signature[2:]))

    orders_by_exchange: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fees_by_exchange: dict[str, list[dict[str, Any]]] = defaultdict(list)
    transfers: list[dict[str, Any]] = []
    collateral_by_exchange_normalized: dict[str, tuple[str, int]] = {}

    if isinstance(collateral_by_exchange, Mapping):
        for raw_exchange, value in collateral_by_exchange.items():
            exchange = _address(raw_exchange)
            if exchange is None or not _is_mapping(value):
                continue
            collateral = _address(value.get("address"))
            decimals = value.get("decimals")
            if collateral is not None and isinstance(decimals, int) and not isinstance(decimals, bool):
                collateral_by_exchange_normalized[exchange] = (collateral, decimals)
    else:
        reasons.append("collateral_mapping_invalid")

    for index, emitter, log, signature in log_rows:
        topics = log["topics"]
        if signature == _ORDER_FILLED:
            words = _data_words(log.get("data"), 7)
            if len(topics) != 4 or words is None:
                reasons.append("orderfilled_abi_invalid")
                continue
            order_hash = _word(topics[1])
            maker = _address_word(topics[2])
            taker = _address_word(topics[3])
            side_word = words[0]
            side_value = _uint_word(side_word)
            if order_hash is None or maker is None or taker is None:
                reasons.append("orderfilled_address_padding_invalid")
                continue
            if side_word[2:64] != "0" * 62 or side_value not in (0, 1):
                reasons.append("orderfilled_side_invalid")
                continue
            values = [_uint_word(word) for word in words[:5]]
            if any(value is None for value in values):
                reasons.append("orderfilled_uint_invalid")
                continue
            side, token_id, maker_amount, taker_amount, fee = values
            if token_id == 0:
                reasons.append("orderfilled_token_id_invalid")
            if (taker_amount if side == 0 else maker_amount) <= 0:
                reasons.append("orderfilled_zero_shares")
            if (maker_amount if side == 0 else taker_amount) <= 0:
                reasons.append("orderfilled_zero_principal")
            if side == 1 and taker_amount < fee:
                reasons.append("negative_sell_net")
            row = {
                "log_index": index,
                "exchange": emitter,
                "order_hash": order_hash,
                "maker": maker,
                "taker": taker,
                "side": side,
                "token_id": token_id,
                "maker_amount": maker_amount,
                "taker_amount": taker_amount,
                "fee": fee,
            }
            if emitter in _EXCHANGES:
                orders_by_exchange[emitter].append(row)
            if wallet_address is not None and maker == wallet_address:
                if emitter not in _EXCHANGES:
                    reasons.append("wallet_orderfilled_foreign_exchange")
                else:
                    collateral_info = collateral_by_exchange_normalized.get(emitter)
                    if collateral_info is None:
                        reasons.append("historical_collateral_missing")
                    elif collateral_info[1] != 6:
                        reasons.append("unsupported_collateral_decimals")
                    if side == 0:
                        shares_atoms, principal_atoms = taker_amount, maker_amount
                        side_name = "BUY"
                    else:
                        shares_atoms, principal_atoms = maker_amount, taker_amount
                        side_name = "SELL"
                    events.append(
                        {
                            "log_index": index,
                            "exchange": emitter,
                            "order_hash": order_hash,
                            "maker": maker,
                            "token_id": str(token_id),
                            "side": side_name,
                            "shares_atoms": shares_atoms,
                            "principal_atoms": principal_atoms,
                            "fee_atoms": fee,
                        }
                    )
        elif signature == _FEE_CHARGED:
            amount_words = _data_words(log.get("data"), 1)
            if emitter not in _EXCHANGES:
                continue
            if len(topics) != 2 or amount_words is None:
                reasons.append("feecharged_abi_invalid")
                continue
            receiver = _address_word(topics[1])
            amount = _uint_word(amount_words[0])
            if receiver is None or amount is None:
                reasons.append("feecharged_abi_invalid")
                continue
            fees_by_exchange[emitter].append(
                {"log_index": index, "exchange": emitter, "receiver": receiver, "amount": amount}
            )
        elif signature == _TRANSFER:
            if len(topics) != 3:
                known_collateral = any(
                    emitter == collateral
                    for collateral, _decimals in collateral_by_exchange_normalized.values()
                )
                if any(
                    _address_word(raw_value) == wallet_address
                    for raw_value in topics[1:]
                    if isinstance(raw_value, str)
                ) or known_collateral:
                    reasons.append("transfer_abi_invalid")
                continue
            amount_words = _data_words(log.get("data"), 1)
            sender = _address_word(topics[1])
            receiver = _address_word(topics[2])
            amount = _uint_word(amount_words[0]) if amount_words else None
            if sender is None or receiver is None or amount is None:
                reasons.append("transfer_abi_invalid")
                continue
            for exchange, (collateral, _decimals) in collateral_by_exchange_normalized.items():
                if emitter == collateral:
                    transfers.append(
                        {
                            "log_index": index,
                            "collateral": collateral,
                            "from": sender,
                            "to": receiver,
                            "amount": amount,
                            "exchange": exchange,
                        }
                    )
                    break

    relevant_exchanges = set(orders_by_exchange) | set(fees_by_exchange)
    collateral_info: dict[str, tuple[str, int]] = {}
    for exchange in relevant_exchanges:
        info = collateral_by_exchange_normalized.get(exchange)
        if info is None:
            reasons.append("historical_collateral_missing")
            continue
        collateral, decimals = info
        if decimals != 6:
            reasons.append("unsupported_collateral_decimals")
        collateral_info[exchange] = info

        order_fee_total = sum(row["fee"] for row in orders_by_exchange.get(exchange, []))
        fee_rows = fees_by_exchange.get(exchange, [])
        charged_total = sum(row["amount"] for row in fee_rows)
        if not fee_rows:
            if order_fee_total != 0:
                reasons.append("feecharged_missing")
        elif charged_total != order_fee_total:
            reasons.append("fee_conservation_mismatch")
        if fee_rows:
            for receiver in {row["receiver"] for row in fee_rows}:
                expected = sum(row["amount"] for row in fee_rows if row["receiver"] == receiver)
                incoming = sum(
                    row["amount"]
                    for row in transfers
                    if row["collateral"] == collateral and row["to"] == receiver
                )
                if incoming != expected:
                    reasons.append("fee_receiver_transfer_mismatch")

    event_collaterals = {
        collateral_info[event["exchange"]][0]
        for event in events
        if event["exchange"] in collateral_info
    }
    event_exchanges = {event["exchange"] for event in events}
    if len(event_exchanges) > 1:
        reasons.append("wallet_events_multiple_exchange")
    if len(event_collaterals) > 1:
        reasons.append("wallet_events_multiple_collateral")
    if not events:
        reasons.append("wallet_orderfilled_missing")

    expected_delta = sum(
        -event["principal_atoms"] - event["fee_atoms"]
        if event["side"] == "BUY"
        else event["principal_atoms"] - event["fee_atoms"]
        for event in events
    )
    cash_collateral = next(iter(event_collaterals), None) if len(event_collaterals) == 1 else None
    transfer_delta = 0
    if wallet_address is not None:
        for row in transfers:
            if row["collateral"] != cash_collateral:
                continue
            if row["from"] == wallet_address:
                transfer_delta -= row["amount"]
            if row["to"] == wallet_address:
                transfer_delta += row["amount"]
    if transfer_delta != expected_delta:
        reasons.append("wallet_cash_conservation_mismatch")

    if reasons:
        return _unknown(reasons, events)
    if len(event_collaterals) == 1:
        collateral = next(iter(event_collaterals))
        decimals = 6
    else:
        collateral = None
        decimals = None
    return {
        "status": "PROVEN",
        "reason": "ok",
        "events": events,
        "collateral_delta_atoms": transfer_delta,
        "collateral": collateral,
        "decimals": decimals,
    }
