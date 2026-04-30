# Created: 2026-04-07
# Last reused/audited: 2026-04-30
# Authority basis: first-principles ZEUS_MODE cleanup 2026-04-30; live smoke remains explicit operator path.
"""P12 Live Smoke Test — minimal $0.01 order to verify CLOB connectivity.

This script:
1. Initializes the live CLOB client (keychain auth)
2. Fetches wallet balance
3. Fetches orderbook for a real weather market
4. Places a BUY order at $0.01 (price=0.01) — virtually unfillable
5. Checks order status
6. Cancels the order
7. Logs everything

Run: python scripts/live_smoke_test.py

This does NOT use the daemon or CycleRunner. It tests the raw CLOB API path only.
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("live_smoke_test")

# Houston Apr 7 "72-73°F" NO token — active weather market
# Price ~0.78, our buy at 0.01 will never fill
TEST_TOKEN_ID = "38349917473995914299741095272614481668910955733521660984613127061753857599029"
TEST_PRICE = 0.01  # Virtually unfillable — far below market
TEST_SIZE = 5.0    # Minimum order size on this market; still only $0.05 at price 0.01

results = {}


def step(name, fn):
    logger.info(">>> STEP: %s", name)
    try:
        result = fn()
        results[name] = {"status": "OK", "result": result}
        logger.info("    OK: %s", result)
        return result
    except Exception as e:
        results[name] = {"status": "FAIL", "error": str(e), "type": type(e).__name__}
        logger.error("    FAIL: %s: %s", type(e).__name__, e)
        return None


def main():
    from src.data.polymarket_client import PolymarketClient

    # Step 1: Initialize live client
    clob = step("init_live_client", lambda: PolymarketClient() or "initialized")

    if clob is None:
        logger.error("Cannot initialize live client. Aborting.")
        return dump_results()

    clob = PolymarketClient()

    # Step 2: Fetch balance
    step("get_balance", lambda: f"${clob.get_balance():.6f}")

    # Step 3: Fetch orderbook
    def fetch_book():
        book = clob.get_orderbook(TEST_TOKEN_ID)
        bids = book.get("bids", [])[:3]
        asks = book.get("asks", [])[:3]
        return {
            "best_bid": bids[0]["price"] if bids else None,
            "best_ask": asks[0]["price"] if asks else None,
            "bid_depth": len(book.get("bids", [])),
            "ask_depth": len(book.get("asks", [])),
        }
    step("fetch_orderbook", fetch_book)

    # Step 4a: V2 preflight — INV-25: must verify endpoint identity before any placement.
    # This is the operator-bypass path; the executor handles this in runtime code.
    # If preflight fails, abort before attempting to place an order.
    from src.data.polymarket_client import V2PreflightError
    try:
        clob.v2_preflight()
        results["v2_preflight"] = {"status": "OK", "result": "endpoint reachable"}
        logger.info("    OK: v2_preflight passed")
    except V2PreflightError as exc:
        results["v2_preflight"] = {"status": "FAIL", "error": str(exc), "type": "V2PreflightError"}
        logger.error("    FAIL: v2_preflight: %s", exc)
        logger.error("Cannot place order — V2 preflight failed (INV-25). Aborting.")
        return dump_results()

    # Step 4b: Place minimal order
    order_result = step("place_order", lambda: clob.place_limit_order(
        token_id=TEST_TOKEN_ID,
        price=TEST_PRICE,
        size=TEST_SIZE,
        side="BUY",
    ))

    order_id = None
    if order_result and isinstance(order_result, dict):
        order_id = order_result.get("orderID") or order_result.get("order_id") or order_result.get("id")
        logger.info("    Order ID: %s", order_id)

    # Step 5: Check order status (if we got an order ID)
    if order_id:
        time.sleep(2)  # Wait for exchange to process
        step("check_order_status", lambda: clob._clob_client.get_order(order_id))
    else:
        results["check_order_status"] = {"status": "SKIP", "reason": "no order_id"}
        # Try to get open orders instead
        step("get_open_orders", lambda: clob._clob_client.get_orders())

    # Step 6: Cancel order
    if order_id:
        step("cancel_order", lambda: clob.cancel_order(order_id))
    else:
        results["cancel_order"] = {"status": "SKIP", "reason": "no order_id to cancel"}

    # Step 7: Verify cancellation
    if order_id:
        time.sleep(1)
        step("verify_cancelled", lambda: clob._clob_client.get_order(order_id))

    # Step 8: Test heartbeat via the current Level-2 API helper.
    def test_heartbeat():
        resp = clob._clob_client.post_heartbeat("zeus-live-smoke-test")
        return {
            "status_code": getattr(resp, "status_code", None),
            "body": getattr(resp, "text", str(resp))[:200],
        }
    step("test_heartbeat", test_heartbeat)

    return dump_results()


def dump_results():
    logger.info("\n" + "=" * 60)
    logger.info("SMOKE TEST RESULTS")
    logger.info("=" * 60)
    for name, r in results.items():
        status = r["status"]
        detail = r.get("result", r.get("error", r.get("reason", "")))
        if isinstance(detail, dict):
            detail = json.dumps(detail)[:120]
        logger.info("  %-25s %s  %s", name, status, detail)

    # Write to file
    out_path = Path(__file__).parent.parent / "state" / "live_smoke_test_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("\nResults written to %s", out_path)

    all_ok = all(r["status"] in ("OK", "SKIP") for r in results.values())
    logger.info("\nOVERALL: %s", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
