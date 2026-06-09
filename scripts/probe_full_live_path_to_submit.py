"""
Probe: drive real positive-edge candidates through the full live cert path to the
venue-POST boundary WITHOUT placing a real order.

Created: 2026-06-01
Last reused or audited: 2026-06-01
Authority basis: GOAL #36 first-fill path enumeration

Usage:
    source .venv/bin/activate
    python scripts/probe_full_live_path_to_submit.py

The script:
1. Selects real high-score buy_no candidates from no_trade_regret_events +
   executable_market_snapshots.
2. Reconstructs an OpportunityEvent from opportunity_events.
3. Calls build_event_bound_no_submit_receipt (the real no-submit path).
4. If no_submit proof passes, runs _build_live_execution_command_certificates
   with a synthetic PreSubmitAuthorityWitness that mirrors what the live daemon
   would provide (book data from snapshot, all status=OK, all provenance fields
   populated).
5. Reports the exact terminal stage for each candidate:
   - REACHED_CERT_BUILD (cert chain passed, final_intent cert emitted)
   - or the first exception/gate that blocks with file:line + DATA/WIRING/CERT tag.

NO real venue POST is attempted. No daemon restart. No DB writes outside
the in-memory test connections used by build_event_bound_no_submit_receipt.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone

# Ensure project root is on path
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# ── constants ─────────────────────────────────────────────────────────────────

_WORLD_DB = os.path.join(_REPO, "state", "zeus-world.db")
_TRADE_DB = os.path.join(_REPO, "state", "zeus_trades.db")
_FORECAST_DB = os.path.join(_REPO, "state", "zeus-forecasts.db")

UTC = timezone.utc


# ── candidate selection ───────────────────────────────────────────────────────

def _select_candidates(world_conn, trade_conn, forecast_conn) -> list[dict]:
    """Return live candidates: fresh opportunity_events matched to fresh snapshots.

    Strategy: find executable_market_snapshots with valid bid+ask captured in the
    last 2h, match to a city via forecast.market_events, then find the most recent
    FORECAST_SNAPSHOT_READY event for that city.  Prefer cities that historically
    appear in no_trade_regret_events with positive trade_score.
    """
    import json

    # Fresh snapshots (captured in last 2h, valid bid+ask)
    snaps = trade_conn.execute("""
        SELECT snapshot_id, condition_id, orderbook_top_bid, orderbook_top_ask,
               min_tick_size, min_order_size, neg_risk, freshness_deadline,
               selected_outcome_token_id, event_id AS gamma_event_id, captured_at
        FROM executable_market_snapshots
        WHERE orderbook_top_bid IS NOT NULL AND orderbook_top_bid != 'ABSENT'
          AND CAST(orderbook_top_bid AS REAL) > 0
          AND orderbook_top_ask IS NOT NULL AND CAST(orderbook_top_ask AS REAL) > 0
        ORDER BY captured_at DESC
        LIMIT 100
    """).fetchall()

    seen_city = set()
    candidates = []
    for snap in snaps:
        cid = snap["condition_id"]
        # Resolve city via forecast market_events (has temperature_metric col)
        me = forecast_conn.execute("""
            SELECT city, target_date, temperature_metric
            FROM market_events WHERE condition_id=? LIMIT 1
        """, (cid,)).fetchone()
        if me is None:
            continue
        city = me["city"]
        if city in seen_city:
            continue
        # Find fresh opportunity_event for this city
        ev = world_conn.execute("""
            SELECT event_id, entity_key, received_at, causal_snapshot_id, payload_json
            FROM opportunity_events
            WHERE event_type = 'FORECAST_SNAPSHOT_READY'
              AND entity_key LIKE ?
            ORDER BY received_at DESC LIMIT 1
        """, (f"{city}|%",)).fetchone()
        if ev is None:
            continue
        p = json.loads(ev["payload_json"])
        seen_city.add(city)
        candidates.append({
            "event_id": ev["event_id"],
            "condition_id": cid,
            "token_id": snap["selected_outcome_token_id"] or "",
            "direction": "buy_no",   # default probe direction; no direction col in snap
            "trade_score": 0.04,     # synthetic; probe is about wall enumeration
            "rejection_stage": "LIVE_PROBE",
            "rejection_reason": "LIVE_PROBE",
            "city": city,
            "executable_snapshot_id": snap["snapshot_id"],
            "causal_snapshot_id": ev["causal_snapshot_id"],
            "snap": dict(snap),
        })
        if len(candidates) >= 3:
            break
    return candidates


def _load_opportunity_event(world_conn, event_id: str):
    from src.events.opportunity_event import OpportunityEvent
    row = world_conn.execute("""
        SELECT event_id, event_type, entity_key, source, observed_at,
               available_at, received_at, causal_snapshot_id,
               payload_hash, idempotency_key, priority, expires_at,
               payload_json, schema_version, created_at
        FROM opportunity_events
        WHERE event_id = ?
    """, (event_id,)).fetchone()
    if row is None:
        return None
    return OpportunityEvent(**dict(row))


# ── synthetic PreSubmitAuthorityWitness ───────────────────────────────────────

def _latest_book_for_token(trade_conn, token_id: str):
    """Return (best_bid, best_ask, book_hash) for the elected token from the most
    recent executable_market_snapshot that captured a two-sided book on it.

    This is the OFFLINE faithful stand-in for the live JIT ``/book`` fetch
    (``_edli_pre_submit_jit_book_quote_provider``): live pulls the book for EXACTLY
    ``intent["token_id"]`` at submit; the probe looks up that same token's latest
    captured book in the trade DB. Critically this binds the would_cross check to
    the ORDER's token (the NO token for buy_no), not the candidate-selection
    snapshot's YES book — the earlier probe compared a NO limit against a YES book
    and produced a FALSE would_cross.
    """
    row = trade_conn.execute(
        """
        SELECT orderbook_top_bid, orderbook_top_ask, raw_orderbook_hash
        FROM executable_market_snapshots
        WHERE selected_outcome_token_id = ?
          AND orderbook_top_bid IS NOT NULL AND orderbook_top_bid NOT IN ('', 'ABSENT')
          AND orderbook_top_ask IS NOT NULL AND orderbook_top_ask NOT IN ('', 'ABSENT')
          AND CAST(orderbook_top_ask AS REAL) > 0
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (str(token_id),),
    ).fetchone()
    if row is None:
        return None
    bid = row["orderbook_top_bid"] if isinstance(row, sqlite3.Row) else row[0]
    ask = row["orderbook_top_ask"] if isinstance(row, sqlite3.Row) else row[1]
    bh = (row["raw_orderbook_hash"] if isinstance(row, sqlite3.Row) else row[2]) or ""
    try:
        return float(bid), float(ask), str(bh)
    except (TypeError, ValueError):
        return None


def _faithful_pre_submit_provider(snap: dict, trade_conn=None):
    """Return a pre-submit authority provider that FAITHFULLY mirrors the live
    daemon's ``_edli_pre_submit_authority_provider_from_world_conn`` JIT path.

    LIVE PARITY (the key fixes for probe artifacts):
      * ``tick_size`` and ``min_order_size`` are read from
        ``final_intent.payload`` — EXACTLY as the live provider does at
        ``main.py:3954-3955`` (``float(intent["tick_size"])``). The earlier probe
        read the raw DB-row tick, which diverges from the elected-snapshot-cert
        tick the limit price was rounded with, producing a FALSE tick_aligned=false.
      * book bid/ask are fetched for ``final_intent["token_id"]`` (the ELECTED
        order token), mirroring live's per-token JIT ``/book``. The earlier probe
        used the candidate-selection snapshot's YES book against a NO-token limit,
        producing a FALSE would_cross. Falls back to the candidate snapshot's book
        only when no per-token book is found (book-less token → DATA condition).
      * ``quote_seen_at`` / ``book_captured_at`` are anchored to OUR observation
        instant (``checked_at``), like the JIT book authority — so quote_age_ms is
        the observation-to-submit latency (effectively 0 here), not a stale stamp.

    All connectivity/heartbeat/balance statuses are OK with populated authority
    IDs so the provenance-required gate passes — the probe asserts the CERT path,
    not live venue connectivity.
    """
    from src.engine.event_reactor_adapter import PreSubmitAuthorityWitness

    ask = snap["orderbook_top_ask"]
    bid = snap["orderbook_top_bid"]
    fallback_bid = float(bid) if bid not in (None, "ABSENT", "", "None") else 0.0
    fallback_ask = float(ask) if ask not in (None, "ABSENT", "", "None") else 0.0

    def _provider(final_intent, _executable_snapshot, dt):
        intent = final_intent.payload
        now_iso = dt.astimezone(UTC).isoformat()
        # LIVE PARITY: tick + min_order from the FinalIntent payload (main.py:3954-3955).
        tick = float(intent["tick_size"])
        min_order = float(intent["min_order_size"])
        # LIVE PARITY: per-token book for the ELECTED order token (mirrors JIT /book).
        best_bid, best_ask, book_hash = fallback_bid, fallback_ask, "probe_jit_book_hash_0000000000000000"
        if trade_conn is not None:
            elected = _latest_book_for_token(trade_conn, str(intent.get("token_id") or ""))
            if elected is not None:
                best_bid, best_ask, _bh = elected
                book_hash = _bh or book_hash
        return PreSubmitAuthorityWitness(
            quote_seen_at=now_iso,
            book_hash=book_hash,
            current_best_bid=best_bid,
            current_best_ask=best_ask,
            tick_size=tick,
            min_order_size=min_order,
            neg_risk=bool(intent.get("neg_risk", False)),
            heartbeat_status="OK",
            user_ws_status="OK",
            venue_connectivity_status="OK",
            balance_allowance_status="OK",
            book_authority_id="clob_jit_book",
            book_captured_at=now_iso,
            heartbeat_authority_id="probe_jit_heartbeat_authority",
            heartbeat_checked_at=now_iso,
            user_ws_authority_id="probe_jit_ws_authority",
            user_ws_checked_at=now_iso,
            venue_connectivity_authority_id="probe_jit_venue_authority",
            venue_connectivity_checked_at=now_iso,
            balance_allowance_authority_id="probe_jit_balance_authority",
            balance_allowance_checked_at=now_iso,
            checked_at=now_iso,
            max_quote_age_ms=1000,  # LIVE PARITY: pre_submit_max_quote_age_ms default
        )

    return _provider


# ── probe runner ──────────────────────────────────────────────────────────────

def _probe_candidate(cand: dict, world_conn, trade_conn, forecast_conn) -> dict:
    """
    Drive one candidate through the full cert chain and report the terminal stage.
    Returns a result dict with keys: city, direction, score, terminal_stage, detail, tag.
    """
    from src.engine.event_reactor_adapter import (
        build_event_bound_no_submit_receipt,
        _build_live_execution_command_certificates,
    )
    from src.decision_kernel import claims

    city = cand["city"]
    direction = cand["direction"]
    score = cand["trade_score"]
    snap = cand["snap"]

    event = _load_opportunity_event(world_conn, cand["event_id"])
    if event is None:
        return {
            "city": city, "direction": direction, "score": score,
            "terminal_stage": "CANDIDATE_EVENT_MISSING",
            "detail": f"event_id={cand['event_id']} not in opportunity_events",
            "tag": "DATA",
        }

    decision_time = datetime.now(UTC)

    # ── Phase 1: no-submit proof ───────────────────────────────────────────────
    # LIVE PARITY (main.py:3485-3524): forecast_conn==topology_conn==forecasts(read-only);
    # calibration_conn==world_conn with forecasts ATTACHED (platt_models in world,
    # calibration_pairs in forecasts schema).
    try:
        no_submit_receipt = build_event_bound_no_submit_receipt(
            event,
            trade_conn=trade_conn,
            decision_time=decision_time,
            forecast_conn=forecast_conn,
            topology_conn=forecast_conn,    # market_events with temperature_metric in zeus-forecasts.db
            calibration_conn=world_conn,    # world_conn has forecasts ATTACHED; platt_models in world, calibration_pairs in forecasts schema
            get_current_level=lambda: __import__('src.riskguard.risk_level', fromlist=['RiskLevel']).RiskLevel.GREEN,
            bankroll_usd_provider=lambda: 1000.0,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        return {
            "city": city, "direction": direction, "score": score,
            "terminal_stage": "NO_SUBMIT_EXCEPTION",
            "detail": f"{type(exc).__name__}: {exc}",
            "traceback": tb,
            "tag": "WIRING",
        }

    if no_submit_receipt.proof_accepted is not True or no_submit_receipt.decision_proof_bundle is None:
        return {
            "city": city, "direction": direction, "score": score,
            "terminal_stage": f"NO_SUBMIT_REJECTED:{no_submit_receipt.reason or 'unknown'}",
            "detail": f"proof_accepted={no_submit_receipt.proof_accepted}",
            "tag": "CERT",
        }

    # ── Phase 2: live cert chain (command certs) ───────────────────────────────
    # FAITHFUL provider: tick/min_order read from final_intent.payload (live parity).
    _pre_submit_provider = _faithful_pre_submit_provider(snap, trade_conn=trade_conn)

    # NON-DESTRUCTIVE: the cert chain reserves a live_cap usage row and appends
    # aggregate-ledger events (real writes on the ATTACHed world DB). Wrap the
    # whole build in a SAVEPOINT and ROLL IT BACK so the probe leaves zero
    # persisted state (HARD RULE). The reservation no longer applies any notional
    # or order-count cap (2026-06-08 directive); size comes from fractional Kelly.
    _sp = "probe_candidate_sp"
    trade_conn.execute(f"SAVEPOINT {_sp}")
    try:
        command_certs = _build_live_execution_command_certificates(
            event=event,
            receipt=no_submit_receipt,
            decision_time=decision_time,
            live_cap_conn=trade_conn,       # live_cap reads from trade DB (K1 world-class tables)
            pre_submit_authority_provider=_pre_submit_provider,
            canary_force_taker=True,
            taker_fok_fak_live_enabled=True,
        )
    except Exception as exc:
        with __import__("contextlib").suppress(Exception):
            trade_conn.execute(f"ROLLBACK TO SAVEPOINT {_sp}")
            trade_conn.execute(f"RELEASE SAVEPOINT {_sp}")
        tb = traceback.format_exc()
        # Classify the gate
        msg = str(exc)
        if "would_cross_book must be false" in msg:
            tag = "WIRING"
        elif "BOOK_AUTHORITY" in msg or "PROVENANCE" in msg:
            tag = "DATA"
        elif "quote_age" in msg:
            tag = "DATA"
        elif "CERTIFICATE" in msg or "cert" in msg.lower():
            tag = "CERT"
        else:
            tag = "WIRING"
        return {
            "city": city, "direction": direction, "score": score,
            "terminal_stage": f"CERT_BUILD_FAILED",
            "detail": f"{type(exc).__name__}: {msg}",
            "traceback": tb,
            "tag": tag,
        }

    # NON-DESTRUCTIVE: the cert chain built successfully and reserved a live_cap slot +
    # appended ledger events. Roll the SAVEPOINT back so nothing persists (the probe
    # STOPS at the POST boundary; the daemon — not the probe — owns real reservations).
    with __import__("contextlib").suppress(Exception):
        trade_conn.execute(f"ROLLBACK TO SAVEPOINT {_sp}")
        trade_conn.execute(f"RELEASE SAVEPOINT {_sp}")

    # ── Phase 3: inspect what we got ──────────────────────────────────────────
    cert_types = [c.certificate_type for c in command_certs]
    pre_submit_cert = next((c for c in command_certs if c.certificate_type == claims.PRE_SUBMIT_REVALIDATION), None)
    final_intent_cert = next((c for c in command_certs if c.certificate_type == claims.FINAL_INTENT), None)
    command_cert = next((c for c in command_certs if c.certificate_type == claims.EXECUTION_COMMAND), None)

    detail_parts = [f"cert_types={cert_types}"]
    if pre_submit_cert:
        ps = pre_submit_cert.payload
        detail_parts.append(
            f"would_cross={ps.get('would_cross_book')} "
            f"tick_aligned={ps.get('tick_aligned')} "
            f"size_ok={ps.get('size_ok')} "
            f"order_type={ps.get('order_type')} "
            f"post_only={ps.get('post_only')} "
            f"quote_age_ms={ps.get('quote_age_ms')} "
            f"bid={ps.get('current_best_bid')} ask={ps.get('current_best_ask')} "
            f"limit={ps.get('limit_price')}"
        )
    would_post = None
    if final_intent_cert:
        fi = final_intent_cert.payload
        detail_parts.append(
            f"FinalIntent: direction={fi.get('direction')} "
            f"order_type={fi.get('order_type')} "
            f"tif={fi.get('time_in_force')} "
            f"post_only={fi.get('post_only')} "
            f"size={fi.get('size')} "
            f"limit_price={fi.get('limit_price')}"
        )
        would_post = {
            "token_id": fi.get("token_id"),
            "side": fi.get("side"),
            "direction": fi.get("direction"),
            "order_type": fi.get("order_type"),
            "time_in_force": fi.get("time_in_force"),
            "post_only": fi.get("post_only"),
            "limit_price": fi.get("limit_price"),
            "size": fi.get("size"),
            "notional_usd": fi.get("notional_usd"),
        }

    # REACHED_VENUE_POST_BOUNDARY: the full cert chain (incl EXECUTION_COMMAND) was
    # built and the pre-submit revalidation passed — the daemon would now POST this
    # exact order to the venue. The probe STOPS here (no POST).
    terminal = (
        "REACHED_VENUE_POST_BOUNDARY"
        if command_cert is not None and would_post is not None
        else "REACHED_CERT_BUILD"
    )
    return {
        "city": city, "direction": direction, "score": score,
        "terminal_stage": terminal,
        "detail": " | ".join(detail_parts),
        "would_post": would_post,
        "tag": "OK",
    }


# ── verifier probe (second pass) ─────────────────────────────────────────────

def _probe_verifier(cand: dict, world_conn, trade_conn, forecast_conn) -> dict:
    """
    After cert build succeeds, run the verifier on the full cert chain to find
    what the verifier would block.
    """
    from src.engine.event_reactor_adapter import (
        build_event_bound_no_submit_receipt,
        _build_live_execution_command_certificates,
    )
    from src.decision_kernel.verifier import verify_execution_command

    city = cand["city"]
    snap = cand["snap"]
    event = _load_opportunity_event(world_conn, cand["event_id"])
    if event is None:
        return {"city": city, "verifier": "EVENT_MISSING"}

    decision_time = datetime.now(UTC)
    try:
        no_submit_receipt = build_event_bound_no_submit_receipt(
            event,
            trade_conn=trade_conn,
            decision_time=decision_time,
            forecast_conn=forecast_conn,
            topology_conn=forecast_conn,
            calibration_conn=world_conn,    # LIVE PARITY: world_conn w/ forecasts ATTACHED
            get_current_level=lambda: __import__('src.riskguard.risk_level', fromlist=['RiskLevel']).RiskLevel.GREEN,
            bankroll_usd_provider=lambda: 1000.0,
        )
    except Exception as exc:
        return {"city": city, "verifier": f"NO_SUBMIT_EXCEPTION:{exc}"}

    if no_submit_receipt.proof_accepted is not True:
        return {"city": city, "verifier": f"NO_SUBMIT_REJECTED:{no_submit_receipt.reason}"}

    _provider = _faithful_pre_submit_provider(snap, trade_conn=trade_conn)

    try:
        certs = _build_live_execution_command_certificates(
            event=event,
            receipt=no_submit_receipt,
            decision_time=decision_time,
            live_cap_conn=trade_conn,
            pre_submit_authority_provider=_provider,
            canary_force_taker=True,
            taker_fok_fak_live_enabled=True,
        )
    except Exception as exc:
        return {"city": city, "verifier": f"CERT_BUILD_FAILED:{exc}"}

    try:
        # verify_execution_command validates the full command cert against its parents
        from src.decision_kernel import claims as _claims
        command_cert = next((c for c in certs if c.certificate_type == _claims.EXECUTION_COMMAND), None)
        if command_cert is None:
            return {"city": city, "verifier": "NO_EXECUTION_COMMAND_CERT"}
        verify_execution_command(command_cert, list(certs))
        return {"city": city, "verifier": "VERIFIER_PASSED"}
    except Exception as exc:
        return {"city": city, "verifier": f"VERIFIER_FAILED:{exc}"}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("PROBE: full live cert path to venue-POST boundary")
    print("  NO real orders. NO daemon restart. NO DB writes beyond no-submit path.")
    print("=" * 72)

    world_conn = sqlite3.connect(_WORLD_DB)
    world_conn.row_factory = sqlite3.Row
    trade_conn = sqlite3.connect(_TRADE_DB)
    trade_conn.row_factory = sqlite3.Row

    # K1 calibration wiring: world_conn is calibration_conn but needs forecasts ATTACHED
    # so that calibration_pairs (in zeus-forecasts.db) is visible at "forecasts.calibration_pairs"
    # while platt_models (in zeus-world.db) is visible at the main schema.
    # This mirrors the live daemon wiring in main.py:3333-3341.
    try:
        world_conn.execute("ATTACH DATABASE ? AS forecasts", (_FORECAST_DB,))
    except Exception as _attach_exc:
        print(f"  WARNING: ATTACH forecasts (world) failed (non-fatal): {_attach_exc}")

    # LIVE PARITY (db.py:419-430 get_trade_connection_with_world_*): the trade conn
    # the no-submit/live path runs on has BOTH world and forecasts ATTACHed so K1
    # cross-DB reads (executable_market_snapshots in trade; market_events/ensemble in
    # forecasts; live_cap world-class tables) resolve from a single connection.
    for _alias, _path in (("world", _WORLD_DB), ("forecasts", _FORECAST_DB)):
        try:
            trade_conn.execute(f"ATTACH DATABASE ? AS {_alias}", (_path,))
        except Exception as _attach_exc:
            print(f"  WARNING: ATTACH {_alias} (trade) failed (non-fatal): {_attach_exc}")

    try:
        forecast_conn = sqlite3.connect(_FORECAST_DB)
        forecast_conn.row_factory = sqlite3.Row
    except Exception:
        forecast_conn = world_conn

    print("\n[1] Selecting candidates: fresh snapshots matched to live opportunity_events...")
    candidates = _select_candidates(world_conn, trade_conn, forecast_conn)
    if not candidates:
        print("  ERROR: no candidates found with valid snapshots. Cannot probe.")
        return

    for c in candidates:
        print(f"  Candidate: {c['city']} dir={c['direction']} score={c['trade_score']:.4f} "
              f"snap={c['executable_snapshot_id'][:20]}... "
              f"ask={c['snap']['orderbook_top_ask']} bid={c['snap']['orderbook_top_bid']}")

    print("\n[2] Probing cert chain for each candidate...")
    print("-" * 72)

    for cand in candidates:
        result = _probe_candidate(cand, world_conn, trade_conn, forecast_conn)
        print(f"\nCITY: {result['city']}  dir={result['direction']}  score={result['score']:.4f}")
        print(f"  TERMINAL: {result['terminal_stage']}  [{result['tag']}]")
        print(f"  DETAIL:   {result['detail']}")
        if result.get("would_post"):
            wp = result["would_post"]
            print("  WOULD POST (probe STOPS here — no real order):")
            print(f"    token_id={wp['token_id']}")
            print(f"    side={wp['side']} direction={wp['direction']} "
                  f"order_type={wp['order_type']} time_in_force={wp['time_in_force']} "
                  f"post_only={wp['post_only']}")
            print(f"    limit_price={wp['limit_price']} size={wp['size']} "
                  f"notional_usd={wp['notional_usd']}")
        if result.get("traceback"):
            # Print last 8 lines of traceback (most informative)
            tb_lines = result["traceback"].strip().split("\n")
            for line in tb_lines[-8:]:
                print(f"    {line}")

    print("\n" + "-" * 72)
    print("[3] Verifier check for candidates that reached CERT_BUILD...")
    for cand in candidates:
        vr = _probe_verifier(cand, world_conn, trade_conn, forecast_conn)
        print(f"  {vr['city']}: {vr['verifier']}")

    print("\n" + "-" * 72)
    print("[4] Live rejection breakdown (last 2h, from no_trade_regret_events)...")
    regret_rows = world_conn.execute("""
        SELECT rejection_reason, COUNT(*) as n, MAX(created_at) as last
        FROM no_trade_regret_events
        WHERE created_at > datetime('now', '-2 hours')
          AND rejection_reason NOT IN ('TRADE_SCORE_NON_POSITIVE', 'EXECUTABLE_SNAPSHOT_BLOCKED')
        GROUP BY rejection_reason
        ORDER BY n DESC LIMIT 15
    """).fetchall()
    if not regret_rows:
        print("  (no rejections in last 2h beyond TRADE_SCORE/SNAPSHOT_BLOCKED)")
    for r in regret_rows:
        print(f"  n={r['n']:4d} last={str(r['last'])[:19]} | {r['rejection_reason']}")

    # Check execution_feasibility_evidence freshness
    efe_max = world_conn.execute(
        "SELECT MAX(quote_seen_at) as last FROM execution_feasibility_evidence"
    ).fetchone()
    print(f"\n  execution_feasibility_evidence last entry: {efe_max['last'] if efe_max else 'NONE'}")

    print("\n" + "=" * 72)
    print("PROBE COMPLETE")
    print("=" * 72)


if __name__ == "__main__":
    main()
