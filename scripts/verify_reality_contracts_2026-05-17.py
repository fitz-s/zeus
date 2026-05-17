"""Live verifier for reality_contracts YAML files.

# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_reference_authority_docs_phase/PLAN.md Part B
# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: live verify reality_contracts YAML against Polymarket CLOB + Open-Meteo; renew last_verified on PASS
# Reuse: re-run with --dry-run before --apply; check VERIFIER_REPORT.md for prior UNMAPPED contracts

Reads config/reality_contracts/*.yaml, calls live APIs (read-only),
updates last_verified on PASS, flags failures in VERIFIER_REPORT.md.

Usage:
  python scripts/verify_reality_contracts_2026-05-17.py --dry-run
  python scripts/verify_reality_contracts_2026-05-17.py --apply

Constraints:
  - Read-only API calls only (no writes to Polymarket)
  - 1 req/sec rate limit between calls
  - 60 sec per-call timeout
  - --dry-run previews which contracts would be touched without writing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "config" / "reality_contracts"
REPORT_PATH = REPO_ROOT / "docs" / "operations" / "task_2026-05-17_reference_authority_docs_phase" / "VERIFIER_REPORT.md"
LOG_PATH = REPO_ROOT / "state" / "verifier_log_2026-05-17.txt"

REQUEST_TIMEOUT = 60      # seconds per call
RATE_LIMIT_DELAY = 1.0    # seconds between API calls

# Open-Meteo sample call — no token_id needed
OPENMETEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=40.71&longitude=-74.01"
    "&hourly=temperature_2m&forecast_days=1&timezone=UTC"
)

# Polymarket CLOB health / public endpoint — no token_id needed
CLOB_FEE_SCHEDULE_URL = "https://clob.polymarket.com/fee-schedules"
CLOB_SAMPLING_TOKEN = None   # no known active token; CLOB contracts need operator token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def fetch_json(url: str, label: str) -> tuple[bool, Any, str]:
    """Fetch URL, return (ok, data_or_none, message)."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "zeus-reality-verifier/2026-05-17"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return True, data, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, None, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, None, f"URLError: {e.reason}"
    except Exception as e:
        return False, None, f"Error: {e}"


def fetch_ws_reachable(url: str) -> tuple[bool, str]:
    """Verify WebSocket endpoint via real WS handshake + subscribe frame."""
    import asyncio

    async def _try_ws() -> tuple[bool, str]:
        try:
            import websockets  # type: ignore[import-untyped]
            subscribe_payload = json.dumps({"type": "market", "assets_ids": []})
            async with websockets.connect(url, open_timeout=5, close_timeout=2) as ws:
                await ws.send(subscribe_payload)
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    # No response frame within 5 s — handshake itself succeeded
                    pass
            return True, f"WS handshake succeeded for {url}"
        except ImportError:
            # Fallback: raw HTTP/1.1 Upgrade handshake via urllib + socket
            import socket, ssl, base64, os
            m = re.match(r"wss?://([^/:]+)(?::(\d+))?(/.*)?", url)
            if not m:
                return False, f"Cannot parse host from {url}"
            host = m.group(1)
            port = int(m.group(2) or 443)
            path = m.group(3) or "/"
            nonce = base64.b64encode(os.urandom(16)).decode()
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {nonce}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            try:
                raw_sock = socket.create_connection((host, port), timeout=10)
                ctx = ssl.create_default_context()
                tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
                tls_sock.sendall(request.encode())
                resp = tls_sock.recv(4096).decode("utf-8", errors="replace")
                tls_sock.close()
                if "101" in resp.split("\r\n", 1)[0]:
                    return True, f"HTTP 101 Switching Protocols for {url}"
                return False, f"No 101 response — got: {resp.splitlines()[0]}"
            except Exception as e:
                return False, f"Upgrade handshake failed for {url}: {e}"
        except Exception as e:
            return False, f"WS handshake failed for {url}: {e}"

    try:
        return asyncio.run(_try_ws())
    except Exception as e:
        return False, f"WS check error: {e}"


# ---------------------------------------------------------------------------
# Per-contract verifiers
# ---------------------------------------------------------------------------

def verify_noaa_time_scale(contract: dict[str, Any]) -> tuple[bool, str]:
    """Verify Open-Meteo still returns UTC timestamps with proper suffix."""
    ok, data, msg = fetch_json(OPENMETEO_URL, "NOAA_TIME_SCALE")
    if not ok:
        return False, f"Open-Meteo unreachable: {msg}"
    data = data or {}
    tz = data.get("timezone", "MISSING")
    tz_ok = tz in ("UTC", "GMT")
    if not tz_ok:
        return False, f"Open-Meteo timezone={tz!r} — expected UTC or GMT"
    # Check time string suffixes (first 10 samples)
    hourly = data.get("hourly", {})
    time_list = hourly.get("time", [])[:10]
    bad_times = [t for t in time_list if not (t.endswith("Z") or t.endswith("+00:00"))]
    if bad_times:
        return False, (
            f"timezone OK (={tz!r}) but suffix MISSING on {len(bad_times)} sample(s): "
            f"{bad_times[:3]!r}"
        )
    suffix_status = "suffix OK" if time_list else "no time samples to check"
    return True, f"Open-Meteo timezone={tz!r} — timezone OK + {suffix_status}"


def verify_websocket_required(contract: dict[str, Any]) -> tuple[bool, str]:
    """Verify WS endpoint is reachable via TCP."""
    current = contract.get("current_value") or {}
    endpoint = current.get("endpoint", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
    ok, msg = fetch_ws_reachable(endpoint)
    return ok, msg


def verify_unmapped(contract: dict[str, Any], reason: str) -> tuple[bool | None, str]:
    """Mark contract as requiring operator verification."""
    return None, f"UNMAPPED_NEEDS_OPERATOR: {reason}"


# ---------------------------------------------------------------------------
# Contract routing table
# ---------------------------------------------------------------------------

def route_contract(contract: dict[str, Any]) -> tuple[bool | None, str]:
    """Route a contract to its verifier. Returns (ok, message) where ok=None means unmapped."""
    cid = contract.get("contract_id", "")

    if cid == "NOAA_TIME_SCALE":
        return verify_noaa_time_scale(contract)

    if cid == "WEBSOCKET_REQUIRED":
        return verify_websocket_required(contract)

    if cid.startswith("SETTLEMENT_SOURCE_"):
        return verify_unmapped(contract, "requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page")

    if cid == "GAMMA_CLOB_PRICE_CONSISTENCY":
        return verify_unmapped(contract, "requires active token_id for current weather market — operator must compare Gamma vs CLOB prices for an active market")

    if cid in ("FEE_RATE_WEATHER", "MAKER_REBATE_RATE"):
        return verify_unmapped(contract, "requires active token_id for GET /fee-rate — operator must query per-market feeSchedule with live token")

    if cid in ("TICK_SIZE_STANDARD", "MIN_ORDER_SIZE_SHARES"):
        return verify_unmapped(contract, "requires active token_id for GET /book — operator must query per-market tick_size/min_order_size with live token")

    if cid == "RATE_LIMIT_BEHAVIOR":
        return verify_unmapped(contract, "rate limit verification requires observing 429 responses under load or checking py-clob-client changelog — not automatable read-only")

    if cid == "RESOLUTION_TIMELINE":
        return verify_unmapped(contract, "resolution timeline verification requires manual review of docs.polymarket.com/trading/resolution and recent market resolution timestamps")

    return verify_unmapped(contract, f"no verifier defined for {cid!r}")


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------

def load_contracts(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def dump_contracts(path: Path, contracts: list[dict[str, Any]]) -> None:
    """Write updated contracts back preserving structure."""
    lines_orig = path.read_text(encoding="utf-8").splitlines(keepends=True)
    # Rewrite only last_verified lines for PASS contracts
    # Strategy: line-by-line replacement to preserve comments and formatting
    # For each contract with a new last_verified, find its last_verified line and replace
    # Build a mapping: contract_id -> new_last_verified
    updates: dict[str, str] = {}
    for c in contracts:
        if "_new_last_verified" in c:
            updates[c["contract_id"]] = c["_new_last_verified"]

    if not updates:
        return

    # Walk lines, track current contract_id context, replace last_verified in context
    result = []
    current_cid: str | None = None
    for line in lines_orig:
        # Detect contract_id line
        m = re.match(r"^- contract_id:\s*(\S+)", line)
        if m:
            current_cid = m.group(1)
        # Detect and replace last_verified line within correct contract
        if current_cid in updates:
            lv_m = re.match(r'^(\s*last_verified:\s*)"([^"]*)"', line)
            if lv_m:
                line = f'{lv_m.group(1)}"{updates[current_cid]}"\n'
                del updates[current_cid]  # applied
                current_cid = None
        result.append(line)

    path.write_text("".join(result), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify reality_contracts YAML files against live APIs")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview which contracts would be touched; no writes")
    mode.add_argument("--apply", action="store_true", help="Run verifications, update last_verified on PASS, write report")
    args = parser.parse_args()

    dry_run = args.dry_run
    ts_now = now_iso()

    yaml_files = sorted(CONTRACTS_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"[ERROR] No YAML files found in {CONTRACTS_DIR}")
        return 1

    all_results: list[dict[str, Any]] = []

    first_call = True
    for yaml_path in yaml_files:
        contracts = load_contracts(yaml_path)
        file_results: list[dict[str, Any]] = []
        modified = False

        for contract in contracts:
            cid = contract.get("contract_id", "<unknown>")

            if dry_run:
                ok, msg = route_contract(contract)
                status = "UNMAPPED" if ok is None else ("WOULD_PASS" if ok else "WOULD_FAIL")
                print(f"[DRY-RUN] {yaml_path.name}::{cid} -> {status}: {msg}")
                file_results.append({"contract_id": cid, "status": status, "message": msg, "file": yaml_path.name})
                continue

            # Apply mode: rate-limit then call
            if not first_call:
                time.sleep(RATE_LIMIT_DELAY)
            first_call = False

            ok, msg = route_contract(contract)

            if ok is None:
                status = "UNMAPPED"
                print(f"[UNMAPPED] {yaml_path.name}::{cid}: {msg}")
            elif ok:
                status = "PASS"
                contract["_new_last_verified"] = ts_now
                modified = True
                print(f"[PASS]    {yaml_path.name}::{cid}: {msg}")
            else:
                status = "FAIL"
                print(f"[FAIL]    {yaml_path.name}::{cid}: {msg}")

            file_results.append({
                "contract_id": cid,
                "status": status,
                "message": msg,
                "file": yaml_path.name,
                "old_last_verified": contract.get("last_verified"),
                "new_last_verified": ts_now if status == "PASS" else None,
            })

        if not dry_run and modified:
            dump_contracts(yaml_path, contracts)
            print(f"[WRITE]   Updated last_verified in {yaml_path.name}")

        all_results.extend(file_results)

    # Summarize
    passes = [r for r in all_results if r["status"] in ("PASS", "WOULD_PASS")]
    fails = [r for r in all_results if r["status"] in ("FAIL", "WOULD_FAIL")]
    unmapped = [r for r in all_results if r["status"] == "UNMAPPED"]

    print()
    print(f"Summary: {len(passes)} PASS, {len(fails)} FAIL, {len(unmapped)} UNMAPPED_NEEDS_OPERATOR")

    if dry_run:
        return 0

    # Write report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Reality Contract Verifier Report\n\n",
        f"Generated: {ts_now}\n\n",
        f"**Summary**: {len(passes)} PASS | {len(fails)} FAIL | {len(unmapped)} UNMAPPED_NEEDS_OPERATOR\n\n",
    ]

    if passes:
        lines.append("## PASS (last_verified updated)\n\n")
        for r in passes:
            lines.append(f"- `{r['file']}::{r['contract_id']}`: {r['message']}\n")
            lines.append(f"  - old: `{r.get('old_last_verified')}` → new: `{r.get('new_last_verified')}`\n")
        lines.append("\n")

    if fails:
        lines.append("## FAIL (last_verified NOT updated — operator must re-verify)\n\n")
        for r in fails:
            lines.append(f"- `{r['file']}::{r['contract_id']}`: {r['message']}\n")
        lines.append("\n")

    if unmapped:
        lines.append("## UNMAPPED_NEEDS_OPERATOR\n\n")
        lines.append("These contracts cannot be verified by automated read-only API calls.\n")
        lines.append("Operator must re-verify manually and update `last_verified` in the YAML.\n\n")
        for r in unmapped:
            lines.append(f"- `{r['file']}::{r['contract_id']}`: {r['message']}\n")
        lines.append("\n")

    REPORT_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"\n[REPORT]  Written to {REPORT_PATH.relative_to(REPO_ROOT)}")

    # Also write structured log
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps({"generated": ts_now, "results": all_results}, indent=2))

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
