# Created: prior to 2026-04-26
# Last reused/audited: 2026-05-13
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
#                  + 2026-05-13 collateral_ledger singleton conn lifecycle remediation
"""Polymarket CLOB API client. Spec §6.4.

Limit orders ONLY. Auth via macOS Keychain.
All numeric fields from API are STRINGS — always float() before use.
"""

import json
import logging
import os
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import httpx

from src.contracts.executable_market_snapshot_v2 import (
    MarketSnapshotMismatchError,
    canonicalize_fee_details,
    fee_rate_fraction_from_details,
)

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"


# ---------------------------------------------------------------------------
# INV-24 / NC-16 runtime call-stack guard for place_limit_order
# ---------------------------------------------------------------------------
# Two-ring enforcement (per repo_review_2026-05-01 SYNTHESIS K-A):
#   Ring 1 (lint-time): semgrep `zeus-place-limit-order-gateway-only` in
#                       architecture/ast_rules/semgrep_zeus.yml
#   Ring 2 (runtime, this file): refuse calls whose immediate non-self caller
#                                frame is not under one of the allowed paths
#                                below.
# Test contexts are auto-allowed via PYTEST_CURRENT_TEST. Operator emergency
# override: INV24_CALLSTACK_GUARD_SKIP=1 (audit-logged).

_INV24_REPO_ROOT = Path(__file__).resolve().parents[2]
_INV24_ALLOWED_CALLER_ABS_PATHS = frozenset(
    str(_INV24_REPO_ROOT / rel)
    for rel in (
        # Authoritative gateway. The executor seam: every order placement
        # flows through executor.execute_intent / _live_order.
        "src/execution/executor.py",
        # Self-references (recursion or wrapper paths inside this module).
        "src/data/polymarket_client.py",
        # Operator-only smoke harness; calls v2_preflight() itself per INV-25.
        "scripts/live_smoke_test.py",
    )
)
_INV24_OVERRIDE_LOG = _INV24_REPO_ROOT / ".claude" / "logs" / "inv24-overrides.log"


def _enforce_inv24_caller_allowlist() -> None:
    """Refuse `place_limit_order` calls from any caller outside the documented
    gateway allowlist. Raises RuntimeError on bypass.

    Matches the semgrep allowlist at architecture/ast_rules/semgrep_zeus.yml
    rule `zeus-place-limit-order-gateway-only`. Extending one without the
    other creates lint-vs-runtime drift. If you genuinely need a new caller,
    update BOTH this allowlist AND the semgrep rule AND
    architecture/negative_constraints.yaml NC-16.

    Test contexts: pytest sets PYTEST_CURRENT_TEST automatically; we honor
    that as a blanket allow so existing tests do not need a manual env flag.
    The semgrep rule already excludes tests/** at lint time.

    Override (operator emergency): INV24_CALLSTACK_GUARD_SKIP=1; audit-logged
    to .claude/logs/inv24-overrides.log so post-hoc forensics is possible.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    if os.environ.get("INV24_CALLSTACK_GUARD_SKIP") == "1":
        try:
            _INV24_OVERRIDE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _INV24_OVERRIDE_LOG.open("a") as fh:
                fh.write(
                    f"{datetime.now(timezone.utc).isoformat()}\t"
                    f"pid={os.getpid()}\t"
                    f"cwd={os.getcwd()}\n"
                )
        except OSError:
            pass  # Override is the operator's escape hatch; do not block.
        logger.warning(
            "INV-24 runtime guard SKIPPED via INV24_CALLSTACK_GUARD_SKIP=1; "
            "logged to %s",
            _INV24_OVERRIDE_LOG,
        )
        return

    self_file = str(Path(__file__).resolve())
    # Frame 0 = this guard; walk up until we leave polymarket_client.py.
    frame = sys._getframe(1)
    while frame is not None:
        caller_file = str(Path(frame.f_code.co_filename).resolve())
        if caller_file != self_file:
            break
        frame = frame.f_back

    if frame is None:
        raise RuntimeError(
            "INV-24 violation: place_limit_order called with no external "
            "caller frame. This should be impossible; investigate the call "
            "chain."
        )

    if caller_file not in _INV24_ALLOWED_CALLER_ABS_PATHS:
        # Render allowlist as repo-relative for the error message.
        allowed_rel = sorted(
            str(Path(p).relative_to(_INV24_REPO_ROOT))
            for p in _INV24_ALLOWED_CALLER_ABS_PATHS
        )
        try:
            caller_rel = str(Path(caller_file).relative_to(_INV24_REPO_ROOT))
        except ValueError:
            caller_rel = caller_file
        raise RuntimeError(
            f"INV-24 violation: place_limit_order called from {caller_rel}, "
            f"which is not in the gateway allowlist. Allowed callers: "
            f"{allowed_rel}. See architecture/invariants.yaml INV-24 + "
            f"architecture/negative_constraints.yaml NC-16. To extend the "
            f"allowlist, update BOTH _INV24_ALLOWED_CALLER_ABS_PATHS in "
            f"src/data/polymarket_client.py AND the semgrep rule "
            f"`zeus-place-limit-order-gateway-only` AND NC-16. Do not edit "
            f"only one — they must stay in sync."
        )


class V2PreflightError(RuntimeError):
    """Raised when the V2 endpoint preflight check fails (INV-25).

    A V2PreflightError means the CLOB endpoint is unreachable or returned an
    unexpected response. Callers (executor._live_order) must treat this as a
    hard rejection — no place_limit_order call may proceed in the same cycle.
    """


def _import_keychain_resolver():
    """Import bin.keychain_resolver from the OpenClaw root, on demand.

    Replaces a prior `subprocess.run(["python3", "-c", f"...{root!r}..."])`
    code-string pattern (ultrareview25_remediation P1-7 +
    repo_review_2026-05-01 SYNTHESIS K-E security finding §11). The
    subprocess pattern was safe in practice (`repr()` neutralised the only
    user-influenceable interpolation, the openclaw_root env var) but
    fragile: any change to the keychain_resolver API forced a string-edit
    at a distance, errors crossed a process boundary as opaque stderr,
    and the eval-on-strings shape gave reviewers the wrong threat model.
    In-process import gives proper Python tracebacks, no subprocess
    overhead, and a normal threat model.
    """
    openclaw_root = os.environ.get(
        "OPENCLAW_HOME", os.path.expanduser("~/.openclaw")
    )
    if openclaw_root not in sys.path:
        sys.path.insert(0, openclaw_root)
    # Module name on disk: bin/keychain_resolver.py
    from bin.keychain_resolver import read_keychain  # type: ignore[import-not-found]
    return read_keychain


def _resolve_credentials() -> dict:
    """Resolve Polymarket credentials from macOS Keychain.

    Returns {'private_key', 'funder_address'}.

    L2 API creds (key/secret/passphrase) are intentionally NOT read here:
    they are deterministically derivable from the L1 signer via
    `ClobClient.create_or_derive_api_key()` inside the v2 adapter. Storing a
    second copy in Keychain creates a drift hazard — observed 2026-05-01,
    where the keychain copy was stale and caused PolyException(401 Invalid
    api key) for the entire trading boot. The adapter now derives at
    construction time so api_creds always match the active signer.
    """
    try:
        read_keychain = _import_keychain_resolver()
        creds = {
            "private_key": read_keychain("openclaw-metamask-private-key"),
            "funder_address": read_keychain("openclaw-polymarket-funder-address"),
        }
        if not creds["private_key"] or not creds["funder_address"]:
            raise RuntimeError(
                "Missing private_key or funder_address from Keychain"
            )
        return creds
    except Exception as e:
        raise RuntimeError(f"Cannot resolve Polymarket credentials: {e}") from e


class PolymarketClient:
    """CLOB client for order placement and orderbook queries."""

    def __init__(self):
        self._clob_client = None
        self._v2_adapter = None
        self._pending_submission_envelope = None

    def _ensure_client(self):
        """Deprecated compatibility alias for the V2 adapter boundary."""
        warnings.warn(
            "PolymarketClient._ensure_client() is deprecated; live venue I/O routes "
            "through src.venue.polymarket_v2_adapter.PolymarketV2Adapter.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._ensure_v2_adapter()

    def _ensure_v2_adapter(self):
        """Lazy init: connect live CLOB I/O through the strict V2 adapter."""
        adapter = getattr(self, "_v2_adapter", None)
        if adapter is not None:
            return adapter

        from src.venue.polymarket_v2_adapter import DEFAULT_V2_HOST, PolymarketV2Adapter

        creds = _resolve_credentials()
        adapter = PolymarketV2Adapter(
            host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
            funder_address=creds["funder_address"],
            signer_key=creds["private_key"],
            chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
            api_creds=creds.get("api_creds"),
        )
        self._v2_adapter = adapter
        logger.info("Polymarket CLOB V2 adapter initialized (live mode)")
        return adapter

    def v2_preflight(self) -> None:
        """Verify V2 endpoint reachability before any order placement (INV-25).

        Calls self._clob_client.get_ok() — a V2-only SDK health-check method.
        Any failure (network error, unexpected response, AttributeError if the
        SDK does not expose get_ok) raises V2PreflightError.

        This is a reachability-only check today. Full V2 endpoint-identity
        verification (signature challenge, API version header assertion) requires
        operator-confirmed endpoint signature and is deferred to a follow-up slice
        per decisions.md §O3-b.

        INV-25: When this method raises, _live_order must return a rejected
        OrderResult without calling place_limit_order.
        """
        legacy_client = getattr(self, "_clob_client", None)
        if legacy_client is not None and getattr(self, "_v2_adapter", None) is None:
            warnings.warn(
                "Injected legacy CLOB client preflight is deprecated and retained "
                "only for compatibility tests; live preflight uses PolymarketV2Adapter.",
                DeprecationWarning,
                stacklevel=2,
            )
            if not hasattr(legacy_client, "get_ok"):
                raise V2PreflightError(
                    "SDK lacks get_ok preflight method; preflight cannot verify endpoint identity. "
                    "Use py-clob-client-v2 through PolymarketV2Adapter to satisfy INV-25."
                )
            try:
                legacy_client.get_ok()
            except Exception as exc:
                raise V2PreflightError(f"V2 endpoint preflight failed: {exc!r}") from exc
            return

        result = self._ensure_v2_adapter().preflight()
        if not result.ok:
            raise V2PreflightError(
                f"{result.error_code or 'V2_PREFLIGHT_FAILED'}: {result.message}"
            )

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch orderbook for a token. Public endpoint, no auth.

        Returns: {"bids": [{"price": float, "size": float}...],
                  "asks": [{"price": float, "size": float}...]}
        """
        data = self.get_orderbook_snapshot(token_id)

        # Normalize: API returns string numerics
        for side in ("bids", "asks"):
            if side in data:
                for entry in data[side]:
                    entry["price"] = float(entry["price"])
                    entry["size"] = float(entry["size"])

        return data

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        """Fetch raw CLOB orderbook facts for executable snapshot capture."""

        resp = httpx.get(f"{CLOB_BASE}/book", params={"token_id": token_id}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"CLOB orderbook response for {token_id} is not an object")
        return data

    def get_clob_market_info(self, condition_id: str) -> dict:
        """Fetch raw CLOB market facts for executable snapshot capture."""

        adapter = getattr(self, "_v2_adapter", None)
        if adapter is not None:
            getter = getattr(adapter, "get_clob_market_info", None)
            if callable(getter):
                info = getter(condition_id)
                raw = getattr(info, "raw", info)
                if isinstance(raw, dict):
                    return dict(raw)

        legacy_client = getattr(self, "_clob_client", None)
        if legacy_client is not None:
            getter = getattr(legacy_client, "get_market", None)
            if callable(getter):
                raw = getter(condition_id)
                if isinstance(raw, dict):
                    return dict(raw)
                if raw is not None and hasattr(raw, "__dict__"):
                    return dict(raw.__dict__)

        resp = httpx.get(f"{CLOB_BASE}/markets/{condition_id}", timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"CLOB market response for {condition_id} is not an object")
        return data

    def get_best_bid_ask(self, token_id: str) -> tuple[float, float, float, float]:
        """Get best bid/ask with sizes for VWMP calculation.

        Returns: (best_bid, best_ask, bid_size, ask_size)
        """
        from src.contracts.exceptions import EmptyOrderbookError
        from src.data.market_scanner import _top_book_level_decimal

        book = self.get_orderbook(token_id)
        try:
            best_bid, bid_size = _top_book_level_decimal(book, "bids")
            best_ask, ask_size = _top_book_level_decimal(book, "asks")
        except Exception as exc:
            raise EmptyOrderbookError(f"No executable top book for {token_id}: {exc}") from exc
        if best_bid >= best_ask:
            raise EmptyOrderbookError(f"Crossed orderbook for {token_id}")

        return float(best_bid), float(best_ask), float(bid_size), float(ask_size)

    def get_fee_rate(self, token_id: str) -> float:
        """Fetch the token-specific Polymarket taker fee as a fraction."""

        return fee_rate_fraction_from_details(self.get_fee_rate_details(token_id))

    def get_fee_rate_details(self, token_id: str) -> dict[str, Any]:
        """Fetch token-specific fee metadata with explicit fraction/bps units."""

        resp = httpx.get(f"{CLOB_BASE}/fee-rate", params={"token_id": token_id}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        schedule = data.get("feeSchedule") if isinstance(data, dict) else None
        if not isinstance(schedule, dict):
            schedule = data if isinstance(data, dict) else {}
        try:
            return canonicalize_fee_details(
                schedule,
                source="clob_fee_rate",
                token_id=token_id,
            )
        except MarketSnapshotMismatchError as exc:
            raise RuntimeError(f"Fee-rate response missing base_fee/feeRate for {token_id}: {exc}") from exc

    def bind_submission_envelope(self, envelope: Any) -> None:
        """Bind the next limit-order submit to executable snapshot provenance."""

        self._pending_submission_envelope = envelope

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> Optional[dict]:
        """Place a limit order. Spec §6.4: limit orders ONLY.

        Args:
            token_id: YES or NO token ID
            price: limit price [0.01, 0.99]
            size: number of shares
            side: "BUY" or "SELL"
            order_type: concrete CLOB limit-order type ("GTC", "FOK", "FAK", ...)

        Returns: order result dict or None on failure
        """
        # INV-24 / NC-16 runtime guard. See module-level
        # _enforce_inv24_caller_allowlist for the full contract; raises
        # RuntimeError on bypass attempt. Two-ring with semgrep
        # `zeus-place-limit-order-gateway-only` (lint-time).
        _enforce_inv24_caller_allowlist()

        if side not in {"BUY", "SELL"}:
            raise ValueError(f"place_limit_order requires side='BUY' or 'SELL', got {side!r}")

        pending_envelope = getattr(self, "_pending_submission_envelope", None)
        if pending_envelope is not None:
            self._pending_submission_envelope = None
            live_bound_error = _submission_envelope_live_bound_error(pending_envelope)
            if live_bound_error:
                rejected_envelope = _submission_envelope_with_error(
                    pending_envelope,
                    error_code="BOUND_ENVELOPE_NOT_LIVE_AUTHORITY",
                    error_message=live_bound_error,
                )
                return {
                    "success": False,
                    "status": "rejected",
                    "errorCode": "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY",
                    "errorMessage": live_bound_error,
                    "_venue_submission_envelope": _submission_envelope_to_dict(
                        rejected_envelope
                    ),
                }
            mismatch = _submission_envelope_mismatch(
                pending_envelope,
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=order_type,
            )
            if mismatch:
                return {
                    "success": False,
                    "status": "rejected",
                    "errorCode": "BOUND_ENVELOPE_MISMATCH",
                    "errorMessage": mismatch,
                    "_venue_submission_envelope": _submission_envelope_to_dict(
                        pending_envelope
                    ),
                }
            try:
                adapter = self._ensure_v2_adapter()
            except Exception as exc:
                rejected_envelope = pending_envelope.with_updates(
                    error_code="V2_PREFLIGHT_EXCEPTION",
                    error_message=str(exc),
                )
                return {
                    "success": False,
                    "status": "rejected",
                    "errorCode": rejected_envelope.error_code,
                    "errorMessage": rejected_envelope.error_message,
                    "_venue_submission_envelope": rejected_envelope.to_dict(),
                }
            submit = adapter.submit(pending_envelope)
            result = _legacy_order_result_from_submit(submit)
            logger.info(
                "V2 bound-envelope submit result: %s %s @ %.3f x %.1f → %s",
                side,
                token_id[:12],
                price,
                size,
                result.get("status"),
            )
            return result

        warnings.warn(
            "PolymarketClient.place_limit_order() is a compatibility wrapper; "
            "live placement requires a bound VenueSubmissionEnvelope.",
            DeprecationWarning,
            stacklevel=2,
        )
        return {
            "success": False,
            "status": "rejected",
            "errorCode": "BOUND_ENVELOPE_REQUIRED",
            "errorMessage": "live placement requires bind_submission_envelope() before place_limit_order()",
        }

    def get_order(self, order_id: str) -> Optional[dict]:
        """Fetch a single order by venue order ID. Returns None if not found.

        Wraps SDK's get_order. Normalizes response to at least
        {"orderID": str, "status": str} so the recovery loop is stable
        against SDK response shape changes.

        Returns None when the venue returns 404 or similar "not found" signal.
        Other exceptions (network error, auth failure) propagate — the
        recovery loop catches and logs them so a single bad lookup does not
        kill the loop.
        """
        try:
            state = self._ensure_v2_adapter().get_order(order_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        except Exception as exc:
            # Some SDK versions raise a plain exception on 404; treat any
            # message containing "not found" (case-insensitive) as None.
            if "not found" in str(exc).lower() or "404" in str(exc):
                return None
            raise

        result = dict(state.raw)

        # Guarantee the two load-bearing keys downstream code reads.
        if "orderID" not in result:
            result.setdefault("orderID", state.order_id or result.get("id") or result.get("order_id") or order_id)
        if "status" not in result:
            result.setdefault("status", state.status or result.get("state") or result.get("order_status") or "UNKNOWN")

        return result

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel a pending order."""
        from src.control.cutover_guard import CutoverPending, gate_for_intent
        from src.execution.command_bus import IntentKind

        decision = gate_for_intent(IntentKind.CANCEL)
        if not decision.allow_cancel:
            raise CutoverPending(decision.block_reason or decision.state.value)
        result = self._ensure_v2_adapter().cancel(order_id)
        payload = {
            "orderID": result.order_id,
            "status": result.status,
            "errorCode": result.error_code,
            "errorMessage": result.error_message,
            "raw_response_json": result.raw_response_json,
        }
        logger.info("Order cancel result: %s → %s", order_id, result.status)
        return payload

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Fetch a live order's latest exchange status."""
        try:
            result = self.get_order(order_id)
            if result is None:
                return {"status": "NOT_FOUND"}
            logger.info("Order status: %s → %s", order_id, result.get("status"))
            return result
        except Exception as exc:
            logger.warning("Order status fetch failed for %s: %s", order_id, exc)
            return {"status": "FETCH_ERROR", "reason": str(exc)}

    def get_open_orders(self) -> list[dict]:
        """Return all currently open exchange orders for the funded wallet."""
        legacy_client = getattr(self, "_clob_client", None)
        if legacy_client is not None and getattr(self, "_v2_adapter", None) is None:
            warnings.warn(
                "Injected legacy CLOB client get_open_orders is deprecated and retained "
                "only for compatibility tests; live order queries use PolymarketV2Adapter.",
                DeprecationWarning,
                stacklevel=2,
            )
            return list(legacy_client.get_orders())
        states = self._ensure_v2_adapter().get_open_orders()
        result = []
        for state in states:
            raw = dict(state.raw)
            raw.setdefault("orderID", state.order_id)
            raw.setdefault("status", state.status)
            result.append(raw)
        return result

    def get_positions_from_api(self) -> Optional[list[dict]]:
        """Fetch authoritative live positions from Polymarket's data API."""
        creds = _resolve_credentials()
        address = creds.get("funder_address", "")
        if not address:
            raise RuntimeError("Missing funder_address for position fetch")

        resp = httpx.get(
            f"{DATA_API_BASE}/positions",
            params={"user": address, "sizeThreshold": "0.01"},
            timeout=15.0,
        )
        resp.raise_for_status()
        raw = resp.json()
        if isinstance(raw, dict):
            raw = raw.get("data", []) or []

        positions: list[dict] = []
        for item in raw:
            token_id = item.get("asset", "") or item.get("token_id", "")
            if not token_id:
                continue
            try:
                size = float(item.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if size < 0.01:
                continue

            try:
                avg_price = float(item.get("avgPrice", 0) or item.get("avg_price", 0) or 0)
                initial_value = float(item.get("initialValue", 0) or 0)
                current_value = float(item.get("currentValue", 0) or 0)
                cash_pnl = float(item.get("cashPnl", 0) or 0)
                cur_price = float(item.get("curPrice", 0) or 0)
            except (TypeError, ValueError) as e:
                logger.warning("Quarantining token %s due to malformed metrics: %s", token_id, e)
                continue

            positions.append({
                "token_id": token_id,
                "condition_id": item.get("conditionId", "") or item.get("condition_id", ""),
                "size": round(size, 4),
                "avg_price": round(avg_price, 6),
                "cost": round(initial_value, 4) if initial_value > 0 else round(size * avg_price, 4),
                "side": item.get("outcome", "") or item.get("side", ""),
                "current_value": round(current_value, 4),
                "cash_pnl": round(cash_pnl, 4),
                "cur_price": round(cur_price, 6),
                "redeemable": bool(item.get("redeemable", False)),
                "title": item.get("title", ""),
                "end_date": item.get("endDate", ""),
            })
        return positions

    def get_balance(self) -> float:
        """Get pUSD balance through the Z4 CollateralLedger.

        Connection discipline (2026-05-10 leak fix): get_trade_connection_with_world()
        ATTACHes zeus-world.db; conn must be closed after use. Prior to this fix
        conn was never closed, producing +2 zeus-world.db fds per bankroll_provider
        tick (one per riskguard tick = 60s accumulation rate).
        """
        warnings.warn(
            "PolymarketClient.get_balance() is a compatibility wrapper; "
            "live balance queries route through CollateralLedger.",
            DeprecationWarning,
            stacklevel=2,
        )
        # 2026-05-13 remediation: this compat path MUST NOT publish to the
        # global ledger slot. Prior to this fix, `configure_global_ledger(ledger)`
        # ran here with a `ledger` holding a transient `conn` that the
        # `finally` block closes immediately, leaving the singleton
        # unusable (`sqlite3.ProgrammingError: Cannot operate on a closed
        # database`). Global-singleton lifecycle is owned by daemon startup
        # in `src/main.py::_startup_wallet_check`; the compat wrapper now
        # only computes the snapshot for legacy callers.
        from src.state.collateral_ledger import CollateralLedger
        from src.state.db import get_trade_connection_with_world

        conn = get_trade_connection_with_world()
        try:
            ledger = CollateralLedger(conn)
            snapshot = ledger.refresh(self._ensure_v2_adapter())
            conn.commit()
            return snapshot.pusd_balance_micro / 1_000_000
        finally:
            conn.close()

    def redeem(self, condition_id: str) -> Optional[dict]:
        """Redeem winning shares for USDC after settlement.

        Not urgent (USDC stays claimable indefinitely) but without it,
        winning capital sits on-chain instead of being available for new trades.
        """
        warnings.warn(
            "PolymarketClient.redeem() is a compatibility wrapper; "
            "redeem attempts route through PolymarketV2Adapter when supported.",
            DeprecationWarning,
            stacklevel=2,
        )
        from src.state.collateral_ledger import require_pusd_redemption_allowed

        require_pusd_redemption_allowed()
        logger.warning(
            "Redeem deferred for condition %s: R1 settlement command ledger is not implemented",
            condition_id,
        )
        return {
            "success": False,
            "errorCode": "REDEEM_DEFERRED_TO_R1",
            "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
            "condition_id": condition_id,
        }


def _submission_envelope_mismatch(
    envelope: Any,
    *,
    token_id: str,
    price: float,
    size: float,
    side: str,
    order_type: str,
) -> str:
    if str(getattr(envelope, "selected_outcome_token_id", "")) != str(token_id):
        return "bound envelope token_id does not match submit token_id"
    if str(getattr(envelope, "side", "")) != str(side):
        return "bound envelope side does not match submit side"
    if str(getattr(envelope, "order_type", "")) != str(order_type):
        return "bound envelope order_type does not match submit order_type"
    if Decimal(str(getattr(envelope, "price", ""))) != Decimal(str(price)):
        return "bound envelope price does not match submit price"
    if Decimal(str(getattr(envelope, "size", ""))) != Decimal(str(size)):
        return "bound envelope size does not match submit size"
    return ""


def _submission_envelope_live_bound_error(envelope: Any) -> str:
    validator = getattr(envelope, "assert_live_submit_bound", None)
    if not callable(validator):
        return "bound envelope does not expose live-submit validation"
    try:
        validator()
    except Exception as exc:
        return str(exc)
    return ""


def _submission_envelope_with_error(
    envelope: Any,
    *,
    error_code: str,
    error_message: str,
) -> Any:
    updater = getattr(envelope, "with_updates", None)
    if callable(updater):
        return updater(error_code=error_code, error_message=error_message)
    return envelope


def _submission_envelope_to_dict(envelope: Any) -> dict:
    to_dict = getattr(envelope, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(envelope, dict):
        return dict(envelope)
    return {"repr": repr(envelope)}


def _legacy_order_result_from_submit(submit: Any) -> dict:
    envelope = submit.envelope
    payload = {
        "success": submit.status == "accepted",
        "status": submit.status,
        "errorCode": submit.error_code,
        "errorMessage": submit.error_message,
        "_venue_submission_envelope": envelope.to_dict(),
    }
    if envelope.order_id:
        payload.update(
            {
                "orderID": envelope.order_id,
                "orderId": envelope.order_id,
                "id": envelope.order_id,
            }
        )
    return payload
