#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: Wave-2 item 3 flip verification (operator "需要测试的现在就测试好") —
#   does flipping CANONICAL_EXIT_PATH / HOLD_VALUE_EXIT_COSTS / exit_bias_family_unify_enabled
#   change exit decisions on REAL historical positions, and in which direction?
#
# READ-ONLY. Opens state/zeus_trades.db via file:...?mode=ro&immutable=1 URI. SELECT-only.
# Registered in src/state/db_writer_lock.SQLITE_CONNECT_ALLOWLIST as
# "scripts/replay_exit_path_comparison.py" (read_only_ro_uri). Writes a markdown
# evidence report only; never mutates any DB.
#
# METHOD (no live mutation, uses the REAL contract functions, not a re-implementation):
#   The three flags split into TWO categories by what they gate:
#
#   (A) CANONICAL_EXIT_PATH  — gated ONLY at src/execution/harvester.py:2525, choosing
#       mark_settled() vs compute_settlement_close() at SETTLEMENT close. mark_settled is a
#       thin wrapper that CALLS compute_settlement_close (exit_lifecycle.py:2529) + one
#       logger.info. Identical settlement_price in, identical realized P&L + identical state
#       transition out. It is a lifecycle-routing/event-emission refactor, NOT an exit DECISION.
#       => zero P&L / zero decision divergence by construction. Asserted below, not replayed.
#
#   (B) HOLD_VALUE_EXIT_COSTS — gated at the EV gate inside Position._buy_yes_exit /
#       _buy_no_exit (src/state/portfolio.py). It changes ONLY the hold_value.net_value the
#       EV gate compares against shares*best_bid:
#         legacy:    net_value = shares * prob                       (HoldValue.compute, fee=0,time=0)
#         canonical: net_value = shares*prob - fee - time - crowding (HoldValue.compute_with_exit_costs)
#       The hold/sell test is  `shares*best_bid <= net_value` -> HOLD (False=continue to EXIT).
#       Because canonical net_value <= legacy net_value ALWAYS (costs >= 0), the canonical gate
#       can only flip a HOLD into a sell-permit, never the reverse. This is the structural
#       invariant. We REPLAY it per stored monitor refresh using the actual HoldValue contract
#       functions with the flag's two branches, to count REAL divergences and size their P&L.
#
#   (C) exit_bias_family_unify_enabled — gated at evaluator.py:3417 / monitor_refresh.py:636.
#       It changes how the monitor PROBABILITY is computed UPSTREAM (subtracts a per-city bias
#       shift from member_extrema before p_raw, then identity-Platt). The stored
#       last_monitor_prob is the ALREADY-COMPUTED posterior; the raw member_extrema and bias
#       rows needed to recompute the shifted prob are NOT in the MONITOR_REFRESHED payload.
#       => NOT replayable from stored decision inputs. Reported as a DATA-AVAILABILITY GAP with
#       analytic direction, not fabricated.
#
# Replay corpus: position_events.event_type='MONITOR_REFRESHED' payloads carry per-cycle
#   last_monitor_prob, last_monitor_best_bid, last_monitor_market_price, *_is_fresh, direction.
#   We keep refreshes with a fresh prob and a finite nonzero best_bid (the only ones on which
#   the live EV gate would actually run; stale/no-bid refreshes return INCOMPLETE_EXIT_CONTEXT
#   on both legs identically).
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.contracts.hold_value import HoldValue  # noqa: E402
from src.config import exit_fee_rate, exit_daily_hurdle_rate  # noqa: E402

TRADES_DB = ROOT / "state" / "zeus_trades.db"
OUT_MD = ROOT / "docs" / "evidence" / "exit_path_replay" / "2026-06-12_canonical_vs_legacy.md"


def _ro_conn(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _hours_to_settlement(occurred_at: str, target_date: str) -> float | None:
    """Settlement resolves at end of the local settlement day. We approximate the
    settlement instant as target_date 23:59:59Z (the live exit path uses a city-local
    end-of-day; UTC end-of-day is a conservative-enough proxy for the SIGN of the
    time-cost term, which is tiny: bid*(h/24)*1e-4). Returns None if either ts unparseable.
    """
    t = _parse_ts(occurred_at)
    if t is None or not target_date:
        return None
    try:
        d = datetime.fromisoformat(f"{target_date}T23:59:59+00:00")
    except Exception:
        return None
    h = (d - t).total_seconds() / 3600.0
    return h


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x)) and float(x) > 0.0


def _ev_gate_decision(shares: float, prob: float, best_bid: float,
                      hours_to_settlement: float | None, *, cost_aware: bool):
    """Return (hold: bool, net_value, sell_value) for ONE leg of the EV gate, using the
    REAL HoldValue contract. Mirrors src/state/portfolio.py _buy_*_exit EV-gate seam exactly:
      hold iff  shares*best_bid <= hold_value.net_value
    cost_aware=False -> legacy (flag OFF); cost_aware=True -> canonical (flag ON).
    correlation_crowding defaults to 0.0 (live config exit.correlation_crowding_rate=0.0).
    """
    if cost_aware:
        hv = HoldValue.compute_with_exit_costs(
            shares=shares,
            current_p_posterior=prob,
            best_bid=best_bid,
            hours_to_settlement=hours_to_settlement,
            fee_rate=exit_fee_rate(),
            daily_hurdle_rate=exit_daily_hurdle_rate(),
            correlation_crowding=0.0,
        )
    else:
        hv = HoldValue.compute(
            gross_value=shares * prob,
            fee_cost=0.0,
            time_cost=0.0,
        )
    sell_value = shares * best_bid
    hold = sell_value <= hv.net_value
    return hold, hv.net_value, sell_value


def main() -> int:
    if not TRADES_DB.exists():
        print(f"trades DB not found: {TRADES_DB}", file=sys.stderr)
        return 2

    con = _ro_conn(TRADES_DB)
    cur = con.cursor()

    # ---- position truth (entry economics + settled outcome) keyed by position_id AND trade_id
    pc = {}
    for r in cur.execute(
        """SELECT position_id, trade_id, city, target_date, direction, bin_label, size_usd,
                  shares, entry_price, p_posterior, realized_pnl_usd, exit_price,
                  settlement_price, exit_reason, phase, temperature_metric
           FROM position_current"""
    ):
        d = dict(r)
        pc[d["position_id"]] = d
    # alt index by trade_id (position_events.position_id may carry either id form)
    pc_by_trade = {v["trade_id"]: v for v in pc.values() if v.get("trade_id")}

    def resolve_pos(pid: str):
        return pc.get(pid) or pc_by_trade.get(pid)

    # ---- per-position usable monitor refreshes
    refreshes = defaultdict(list)
    n_total = n_usable = 0
    for r in cur.execute(
        """SELECT position_id, occurred_at, payload_json
           FROM position_events WHERE event_type='MONITOR_REFRESHED'
           ORDER BY occurred_at"""
    ):
        n_total += 1
        try:
            p = json.loads(r["payload_json"])
        except Exception:
            continue
        prob = p.get("last_monitor_prob")
        bid = p.get("last_monitor_best_bid")
        prob_fresh = p.get("last_monitor_prob_is_fresh")
        if not prob_fresh:
            continue
        if not (isinstance(prob, (int, float)) and math.isfinite(float(prob))):
            continue
        if not _finite_pos(bid):
            continue
        n_usable += 1
        refreshes[r["position_id"]].append(
            {
                "occurred_at": r["occurred_at"],
                "prob": float(prob),
                "bid": float(bid),
                "market_price": p.get("last_monitor_market_price"),
                "direction": p.get("direction"),
                "city": p.get("city"),
                "target_date": p.get("target_date"),
                "applied": p.get("applied_validations") or [],
            }
        )

    # ---- replay both legs per refresh; aggregate divergences per position
    per_pos = {}
    divergent_refreshes = []
    for pid, refs in refreshes.items():
        pos = resolve_pos(pid)
        # shares: prefer position_current.shares, else size/entry_price, else from payload? fall back 0
        shares = None
        city = refs[0]["city"]
        direction = refs[0]["direction"]
        target_date = refs[0]["target_date"]
        settled_outcome = None
        entry_price = None
        size_usd = None
        realized_pnl = None
        if pos:
            shares = pos.get("shares")
            if not _finite_pos(shares):
                ep = pos.get("entry_price")
                sz = pos.get("size_usd")
                shares = (sz / ep) if (_finite_pos(ep) and _finite_pos(sz)) else None
            entry_price = pos.get("entry_price")
            size_usd = pos.get("size_usd")
            realized_pnl = pos.get("realized_pnl_usd")
            sp = pos.get("settlement_price")
            if sp is not None:
                settled_outcome = "WON" if float(sp) > 0 else "LOST"
        n_hold_legacy = n_hold_canon = n_div = 0
        first_div = None
        max_div_pnl = 0.0
        for ref in refs:
            if not _finite_pos(shares):
                continue
            hrs = _hours_to_settlement(ref["occurred_at"], target_date or "")
            holdL, nvL, sv = _ev_gate_decision(shares, ref["prob"], ref["bid"], hrs, cost_aware=False)
            holdC, nvC, _ = _ev_gate_decision(shares, ref["prob"], ref["bid"], hrs, cost_aware=True)
            n_hold_legacy += int(holdL)
            n_hold_canon += int(holdC)
            if holdL != holdC:
                n_div += 1
                # canonical permits a sell the legacy held. If executed at this bid:
                # realized exit proceeds = shares*bid; counterfactual settle value vs that.
                # P&L delta of selling-now vs holding-to-settlement = sell_value - settle_value.
                settle_val = None
                if settled_outcome == "WON":
                    settle_val = shares * 1.0
                elif settled_outcome == "LOST":
                    settle_val = 0.0
                delta = (sv - settle_val) if settle_val is not None else None
                rec = {
                    "pid": pid, "city": city, "direction": direction,
                    "occurred_at": ref["occurred_at"], "prob": ref["prob"], "bid": ref["bid"],
                    "shares": shares, "hours": hrs, "nv_legacy": nvL, "nv_canon": nvC,
                    "sell_value": sv, "settled_outcome": settled_outcome,
                    "settle_value": settle_val, "pnl_delta_if_canon_executed": delta,
                }
                divergent_refreshes.append(rec)
                if first_div is None:
                    first_div = rec
                if delta is not None and abs(delta) > abs(max_div_pnl):
                    max_div_pnl = delta
        per_pos[pid] = {
            "pid": pid, "city": city, "direction": direction, "target_date": target_date,
            "entry_price": entry_price, "size_usd": size_usd, "shares": shares,
            "settled_outcome": settled_outcome, "realized_pnl": realized_pnl,
            "n_refresh": len(refs), "n_hold_legacy": n_hold_legacy,
            "n_hold_canon": n_hold_canon, "n_divergent": n_div,
            "first_div": first_div, "max_div_pnl": max_div_pnl,
        }

    # ---- emit report
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    W = lines.append
    W("# Exit-path replay: canonical+cost-aware vs legacy on REAL historical positions")
    W("")
    W("Authority: Wave-2 item 3 flip verification, 2026-06-12. READ-ONLY replay over "
      "`state/zeus_trades.db` (mode=ro, immutable). Uses the REAL `HoldValue` contract "
      "functions (not a re-implementation), flag branch selected per leg.")
    W("")
    W("## Flag taxonomy (what each flag actually gates)")
    W("")
    W("- **CANONICAL_EXIT_PATH** (read `src/execution/harvester.py:2525`, also helper "
      "`:108`): selects `mark_settled()` vs `compute_settlement_close()` at SETTLEMENT "
      "close. `mark_settled` (`src/execution/exit_lifecycle.py:2529`) *calls* "
      "`compute_settlement_close` + one `logger.info`. Same settlement_price in -> same "
      "realized P&L + same state transition out. **It gates settlement bookkeeping/event "
      "routing, NOT an exit decision. Zero P&L / zero decision divergence by construction.**")
    W("- **HOLD_VALUE_EXIT_COSTS** (read `src/config.py:727`, used "
      "`src/state/portfolio.py` EV gates `_buy_yes_exit`/`_buy_no_exit`): swaps the EV-gate "
      "hold-value from `shares*prob` (legacy, fee=0/time=0) to "
      "`shares*prob - fee - time - crowding`. **Replayed below.**")
    W("- **exit_bias_family_unify_enabled** (read `src/engine/evaluator.py:3417`, "
      "`src/engine/monitor_refresh.py:636`): subtracts a per-city bias SHIFT from "
      "member_extrema BEFORE p_raw, then identity-Platt, on the EXIT/monitor side so exit "
      "belief matches entry belief. **Changes the monitor PROBABILITY upstream; not "
      "replayable from the stored already-computed `last_monitor_prob` — DATA GAP, "
      "see §4.**")
    W("")
    W(f"## 1. Replay corpus")
    W("")
    W(f"- MONITOR_REFRESHED events scanned: **{n_total}**")
    W(f"- Usable refreshes (fresh prob + finite nonzero best_bid; the only refreshes on "
      f"which the live EV gate actually runs): **{n_usable}**")
    n_pos_replayed = sum(1 for v in per_pos.values() if _finite_pos(v["shares"]))
    W(f"- Distinct positions with >=1 usable refresh: **{len(per_pos)}** "
      f"(replayable with known shares: **{n_pos_replayed}**)")
    W("")
    W("## 2. HOLD_VALUE_EXIT_COSTS — per-position legacy vs canonical EV-gate")
    W("")
    W("`hold` = EV gate said HOLD (continue holding) at that refresh. `legacy_holds` / "
      "`canon_holds` count refreshes where each leg held; `divergent` counts refreshes where "
      "the legs DISAGREED (always canonical=sell-permit, legacy=hold, per the structural "
      "invariant).")
    W("")
    W("| position | city | dir | entry_px | size$ | shares | settled | refreshes | legacy_holds | canon_holds | divergent |")
    W("|---|---|---|---|---|---|---|---|---|---|---|")
    tot_div = 0
    for v in sorted(per_pos.values(), key=lambda x: (-x["n_divergent"], x["city"] or "")):
        if not _finite_pos(v["shares"]):
            continue
        tot_div += v["n_divergent"]
        ep = f'{v["entry_price"]:.3f}' if _finite_pos(v["entry_price"]) else "—"
        sz = f'{v["size_usd"]:.2f}' if _finite_pos(v["size_usd"]) else "—"
        W(f'| {v["pid"][:14]} | {(v["city"] or "")[:10]} | {v["direction"]} | {ep} | {sz} | '
          f'{v["shares"]:.2f} | {v["settled_outcome"] or "—"} | {v["n_refresh"]} | '
          f'{v["n_hold_legacy"]} | {v["n_hold_canon"]} | {v["n_divergent"]} |')
    W("")
    W(f"**Total divergent refreshes across all positions: {tot_div}.**")
    W("")
    W("## 3. Divergent-refresh detail + P&L delta vs settled truth")
    W("")
    if not divergent_refreshes:
        W("**No divergence.** At every usable refresh, the canonical cost-aware EV gate "
          "reached the SAME hold/sell verdict as the legacy zero-cost gate. The fee+time "
          "costs (fee = 0.05·p·(1−p) ≈ 0.006–0.012/share; time = bid·(h/24)·1e-4 ≈ "
          "negligible) were never large enough to cross the `shares*best_bid` vs net_value "
          "boundary on any real held position. The EV gate is also only one of several exit "
          "layers and frequently not the binding one (CI-separation / near-settlement / "
          "consecutive-confirm gates decide first).")
    else:
        W("Each row: canonical permitted a sell that legacy held. `pnl_delta_if_canon_executed` "
          "= (sell proceeds now `shares*bid`) − (settled value: shares if WON else 0). "
          "Negative = canonical would have SOLD a winner cheap (worse); positive = canonical "
          "would have escaped a loser (better). Conservative fill = the stored best_bid.")
        W("")
        W("| position | city | dir | at | prob | bid | shares | nv_legacy | nv_canon | sell_val | settled | ΔP&L |")
        W("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in divergent_refreshes:
            dlt = f'{r["pnl_delta_if_canon_executed"]:+.2f}' if r["pnl_delta_if_canon_executed"] is not None else "n/a"
            hr = f'{r["hours"]:.1f}h' if r["hours"] is not None else "—"
            W(f'| {r["pid"][:12]} | {(r["city"] or "")[:9]} | {r["direction"]} | '
              f'{r["occurred_at"][:16]} ({hr}) | {r["prob"]:.3f} | {r["bid"]:.3f} | '
              f'{r["shares"]:.2f} | {r["nv_legacy"]:.3f} | {r["nv_canon"]:.3f} | '
              f'{r["sell_value"]:.3f} | {r["settled_outcome"] or "—"} | {dlt} |')
        net = sum(r["pnl_delta_if_canon_executed"] for r in divergent_refreshes
                  if r["pnl_delta_if_canon_executed"] is not None)
        n_won = sum(1 for r in divergent_refreshes if r["settled_outcome"] == "WON")
        n_lost = sum(1 for r in divergent_refreshes if r["settled_outcome"] == "LOST")
        n_unsettled = sum(1 for r in divergent_refreshes if r["settled_outcome"] is None)
        W("")
        W(f"**Divergent-refresh outcome breakdown: WON sold={n_won} (harm), "
          f"LOST escaped={n_lost} (benefit), unsettled={n_unsettled}.**")
        W(f"**Net P&L delta if EVERY canonical divergence had executed (settled cases only): "
          f"{net:+.2f} USD.** NOTE: this is an UPPER BOUND on canonical's effect — a "
          "divergence at the EV gate only becomes a real sell if the upstream gates "
          "(consecutive-confirm count, CI-separation) ALSO permit exit on that cycle; the EV "
          "gate is the last layer, not the only one.")
    W("")
    W("## 4. exit_bias_family_unify_enabled — data-availability gap + analytic direction")
    W("")
    W("This flag changes the monitor PROBABILITY upstream (member_extrema bias shift). The "
      "MONITOR_REFRESHED payload stores only the already-computed `last_monitor_prob`; the "
      "raw member_extrema + `edli_per_city_v1` bias rows needed to recompute the shifted "
      "prob are not in the stored decision inputs. **Not replayable from this corpus without "
      "re-running the live monitor forecast pipeline against historical ensembles.**")
    W("")
    W("Analytic direction (from the flag note + code): it subtracts the SAME per-city bias "
      "the LIVE ENTRY reactor already subtracts (`event_reactor_adapter._EDLI_BIAS_FAMILY`, "
      "71 VERIFIED rows). Today entry is bias-corrected but exit/monitor is NOT (the legacy "
      "`full_transport_v1` family has 0 rows -> exit correction is permanently inert). So the "
      "flip removes an entry/exit ASYMMETRY: exit belief stops drifting from entry belief. "
      "FAIL-CLOSED: any missing row -> plain p_raw (today's behaviour), trading continues. "
      "Direction is toward CONSISTENCY, not toward more/less exiting per se; magnitude is the "
      "per-city bias_c (typically <1°C). This is the asymmetry implicated in the 2026-06-12 "
      "exit-blind losses and should be validated by the same settled-truth gate the note "
      "names (BEFORE_AFTER_bias_family_unify.md), not flipped blind.")
    W("")
    W("## 5. Verdict")
    W("")
    if not divergent_refreshes:
        W("- **CANONICAL_EXIT_PATH: SAFE_TO_FLIP.** Pure settlement-bookkeeping routing; "
          "`mark_settled` wraps `compute_settlement_close`; identical realized P&L and state "
          "transitions. No exit decision changes.")
        W("- **HOLD_VALUE_EXIT_COSTS: SAFE_TO_FLIP.** Replayed over every usable historical "
          "refresh: ZERO divergent decisions. Costs are too small to cross the EV-gate "
          "boundary on any real position, and the gate is monotone (canonical net_value <= "
          "legacy, so it can only ever permit MORE exits, never fewer — it cannot newly TRAP "
          "a position). On settled truth it changed nothing.")
    else:
        n_won = sum(1 for r in divergent_refreshes if r["settled_outcome"] == "WON")
        net = sum(r["pnl_delta_if_canon_executed"] for r in divergent_refreshes
                  if r["pnl_delta_if_canon_executed"] is not None)
        W("- **CANONICAL_EXIT_PATH: SAFE_TO_FLIP** (settlement-bookkeeping routing only).")
        if n_won == 0 and net >= 0:
            W(f"- **HOLD_VALUE_EXIT_COSTS: SAFE_TO_FLIP (settled-truth strictly better).** "
              f"{len(divergent_refreshes)} divergences, but 0 sold a winner and net settled "
              f"ΔP&L = {net:+.2f} USD (all benefit = loser-exits). Monotone (only ever permits "
              "MORE exits). CAVEAT: divergences sit on the belief≈bid knife-edge and the EV "
              "gate is the last exit layer, so the live effect is <= this upper bound.")
        else:
            W(f"- **HOLD_VALUE_EXIT_COSTS: FLIP_WITH_CAVEATS.** {len(divergent_refreshes)} "
              f"divergences; {n_won} would have sold a WINNER (net settled ΔP&L {net:+.2f}). "
              "Review §3 before flipping.")
    W("- **exit_bias_family_unify_enabled: FLIP_WITH_CAVEATS.** Not replayable from stored "
      "decision inputs (§4). Direction is entry/exit belief CONSISTENCY (removes a known "
      "asymmetry), fail-closed. Gate on the per-city before/after belief-delta + settled-truth "
      "review named in its flag note, not on this replay.")
    W("")
    W("## 6. Deletion scope — every consumer of the three flags")
    W("")
    W("(grep `src/ tests/`, 2026-06-12)")
    W("")
    W("**CANONICAL_EXIT_PATH:** read only at `src/execution/harvester.py` "
      "`_get_canonical_exit_flag()` (:108) used at :2334/:2525. Config: "
      "`config/settings.json:286`, `config/settings.example.json:160`. Tests pinning "
      "behaviour: `tests/test_exit_authority.py` (:91 default-False, :98/:103 True path) — "
      "these assert the flag READER, will survive a permanent-ON unless they assert default=False.")
    W("**HOLD_VALUE_EXIT_COSTS:** reader `src/config.py:720 hold_value_exit_costs_enabled()`; "
      "call sites `src/state/portfolio.py` :1247 :1326 :1416 :1504 (4 EV-gate seams). Config "
      "`:287`/example`:161`. Tests pinning OFF behaviour: "
      "`tests/test_hold_value_exit_costs.py` (:157 flag-OFF regression guard, :213/:245 "
      "patch return_value=False expecting NO `hold_value_exit_costs_enabled` breadcrumb) and "
      "`tests/test_live_safety_invariants.py:4491/:4534` (monkeypatch ->True). The OFF-pinning "
      "tests MUST be updated/removed when the legacy branch is deleted.")
    W("**exit_bias_family_unify_enabled:** readers `src/engine/evaluator.py:3417`, "
      "`src/engine/monitor_refresh.py:636`; emitted validations evaluator :4348/:4398, "
      "monitor :1033/:1161/:1231. Config `:284`/example(note only). Tests: "
      "`tests/test_bias_family_unify_d2.py` (:121 `_FF_ON`, :122 `_FF_OFF` — exercises BOTH "
      "legs; the `_FF_OFF` leg pins legacy behaviour and must be updated on deletion). Also "
      "`tests/test_k1_review_fixes.py:236` asserts `exit_bias_family_unify` NOT in applied "
      "(flag-OFF expectation).")
    W("")
    out = "\n".join(lines) + "\n"
    OUT_MD.write_text(out)
    con.close()
    print(f"WROTE {OUT_MD}")
    print(f"usable_refreshes={n_usable} positions={len(per_pos)} divergent_refreshes={len(divergent_refreshes)} total_div={tot_div}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
