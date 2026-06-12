# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator favorite-capture critique 2026-06-12 ("我们的每次选择
#   都在这种非常高风险的极端bin中做抉择,从来没有做一次那种真正高概率的市场事件进行买入");
#   READ-ONLY audit. SELECT-only over zeus-forecasts.db + zeus_trades.db via
#   file:...?mode=ro URI. Registered in SQLITE_CONNECT_ALLOWLIST.
"""Quantify the favorite-capture miss (READ-ONLY).

Part A: today's surface — every active family's market favorite (max YES ask) in
        the buyable band [0.80, 0.97], our posterior q for that bin, whether
        (q_lcb - ask) clears zero, and what the system selected.
Part B: settled history — favorite-capture counterfactual P&L vs our actual trades.
Part C: Denver 06-12 case study + equivalent-expression relative value.

No writes to any canonical DB. Emits a markdown report to stdout / files.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

FORECASTS_DB = "file:state/zeus-forecasts.db?mode=ro"
TRADES_DB = "file:state/zeus_trades.db?mode=ro"

BAND_LO, BAND_HI = 0.80, 0.97


def ro(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, uri=True)
    c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    return c


def latest_posterior(fc: sqlite3.Connection, city: str, date: str, metric: str):
    row = fc.execute(
        """SELECT q_json, q_lcb_json, q_ucb_json, computed_at, provenance_json,
                  posterior_method, family_id
           FROM forecast_posteriors
           WHERE city=? AND target_date=? AND temperature_metric=?
           ORDER BY computed_at DESC LIMIT 1""",
        (city, date, metric),
    ).fetchone()
    return row


def bins_for(fc: sqlite3.Connection, city: str, date: str):
    rows = fc.execute(
        """SELECT range_label, range_low, range_high, condition_id
           FROM market_events WHERE city=? AND target_date=?""",
        (city, date),
    ).fetchall()
    return {r["range_label"]: r for r in rows}


def books_for_conditions(tr: sqlite3.Connection, condition_ids: list[str], asof: str | None = None):
    """Latest YES+NO top-of-book per condition_id, using idx_snapshots_condition_captured.

    One indexed query per condition_id (condition_id, captured_at DESC) instead of a
    full-table LIKE scan over 2.9M rows.
    """
    best: dict[tuple, sqlite3.Row] = {}
    for cond in condition_ids:
        if not cond:
            continue
        if asof:
            q = ("SELECT condition_id, outcome_label, orderbook_top_bid, orderbook_top_ask, "
                 "captured_at, orderbook_depth_json FROM executable_market_snapshots "
                 "WHERE condition_id=? AND captured_at<=? ORDER BY captured_at DESC LIMIT 12")
            rows = tr.execute(q, (cond, asof)).fetchall()
        else:
            q = ("SELECT condition_id, outcome_label, orderbook_top_bid, orderbook_top_ask, "
                 "captured_at, orderbook_depth_json FROM executable_market_snapshots "
                 "WHERE condition_id=? ORDER BY captured_at DESC LIMIT 12")
            rows = tr.execute(q, (cond,)).fetchall()
        for r in rows:
            key = (r["condition_id"], r["outcome_label"])
            if key not in best:
                best[key] = r
    return best


def latest_books(tr: sqlite3.Connection, binmap: dict, asof: str | None = None):
    conds = [b["condition_id"] for b in binmap.values() if b["condition_id"]]
    return books_for_conditions(tr, conds, asof=asof)


def implied_yes(books: dict, condition_id: str) -> tuple[float | None, float | None]:
    """(yes_ask, yes_bid) for a condition from its YES book."""
    yes = books.get((condition_id, "YES"))
    if yes is None:
        return None, None
    try:
        ask = float(yes["orderbook_top_ask"])
        bid = float(yes["orderbook_top_bid"])
    except (TypeError, ValueError):
        return None, None
    return ask, bid


def no_ask(books: dict, condition_id: str) -> float | None:
    no = books.get((condition_id, "NO"))
    if no is None:
        return None
    try:
        return float(no["orderbook_top_ask"])
    except (TypeError, ValueError):
        return None


def active_families(fc: sqlite3.Connection, dates: list[str]):
    rows = fc.execute(
        f"""SELECT DISTINCT city, target_date, temperature_metric
            FROM forecast_posteriors
            WHERE target_date IN ({','.join('?' * len(dates))})
            ORDER BY target_date, city""",
        dates,
    ).fetchall()
    return rows


def part_a(fc, tr, dates):
    out = ["## PART A — today's buyable-favorite surface\n"]
    out.append("city|date|metric|fav_bin|mkt_yes_ask|our_q|our_q_lcb|edge_q_lcb_minus_ask|clears|in_band")
    out.append("---|---|---|---|---|---|---|---|---|---")
    fams = active_families(fc, dates)
    band_hits = 0
    band_clears = 0
    for f in fams:
        city, date, metric = f["city"], f["target_date"], f["temperature_metric"]
        post = latest_posterior(fc, city, date, metric)
        if not post:
            continue
        try:
            q = json.loads(post["q_json"])
            qlcb = json.loads(post["q_lcb_json"]) if post["q_lcb_json"] else {}
        except Exception:
            continue
        binmap = bins_for(fc, city, date)
        books = latest_books(tr, binmap)
        # market favorite = bin with max YES ask
        fav_label = None
        fav_ask = -1.0
        fav_cond = None
        for label, b in binmap.items():
            cond = b["condition_id"]
            ask, _ = implied_yes(books, cond)
            if ask is None:
                continue
            if ask > fav_ask:
                fav_ask, fav_label, fav_cond = ask, label, cond
        if fav_label is None:
            continue
        our_q = float(q.get(fav_label, 0.0))
        our_qlcb = float(qlcb.get(fav_label, 0.0)) if qlcb else 0.0
        edge = our_qlcb - fav_ask
        clears = edge > 0
        in_band = BAND_LO <= fav_ask <= BAND_HI
        if in_band:
            band_hits += 1
            if clears:
                band_clears += 1
        short = fav_label.split(" be ")[-1][:18] if " be " in fav_label else fav_label[:18]
        out.append(
            f"{city}|{date}|{metric}|{short}|{fav_ask:.3f}|{our_q:.3f}|{our_qlcb:.3f}|"
            f"{edge:+.3f}|{'Y' if clears else 'N'}|{'Y' if in_band else ''}"
        )
    out.append(f"\n**Buyable-band [{BAND_LO},{BAND_HI}] favorites: {band_hits}; "
               f"of those our q_lcb clears the ask: {band_clears}**\n")
    return "\n".join(out)


def part_b(fc, tr, since: str):
    """Favorite-capture counterfactual over settled families."""
    out = ["## PART B — settled-history favorite-capture counterfactual\n"]
    settled = fc.execute(
        """SELECT city, target_date, temperature_metric, winning_bin, settlement_value
           FROM settlement_outcomes
           WHERE authority='VERIFIED' AND target_date>=?
           ORDER BY target_date DESC""",
        (since,),
    ).fetchall()
    out.append(f"settled VERIFIED families since {since}: {len(settled)}")

    n_eval = 0
    n_band = 0
    n_win = 0
    pnl_per_100 = 0.0
    detail = []
    for s in settled:
        city, date, metric = s["city"], s["target_date"], s["temperature_metric"]
        winning = s["winning_bin"]
        if not winning:
            continue
        binmap = bins_for(fc, city, date)
        books = latest_books(tr, binmap)
        if not books:
            continue
        # favorite = max YES ask among bins
        fav_label = None
        fav_ask = -1.0
        fav_cond = None
        for label, b in binmap.items():
            ask, _ = implied_yes(books, b["condition_id"])
            if ask is None:
                continue
            if ask > fav_ask:
                fav_ask, fav_label, fav_cond = ask, label, b["condition_id"]
        if fav_label is None:
            continue
        if not (BAND_LO <= fav_ask <= BAND_HI):
            continue
        n_band += 1
        n_eval += 1
        # did favorite bin win? compare winning_bin to fav_label
        won = _bin_matches(winning, fav_label, binmap)
        if won:
            n_win += 1
            pnl = (1.0 - fav_ask) / fav_ask * 100.0  # profit per $100 staked
        else:
            pnl = -100.0
        pnl_per_100 += pnl
        detail.append(f"{city} {date} {metric}: fav_ask={fav_ask:.3f} won={'Y' if won else 'N'} pnl/$100={pnl:+.1f}")

    out.append(f"\nfavorites in band [{BAND_LO},{BAND_HI}] evaluated: {n_band}")
    if n_eval:
        out.append(f"win rate: {n_win}/{n_eval} = {n_win / n_eval:.3f}")
        out.append(f"net P&L per $100 staked (avg): {pnl_per_100 / n_eval:+.2f}")
    out.append("\nsample detail (first 25):")
    out.extend(detail[:25])
    return "\n".join(out)


def _bin_matches(winning_bin: str, fav_label: str, binmap: dict) -> bool:
    """Heuristic match of settlement winning_bin to a market range_label."""
    if not winning_bin:
        return False
    w = str(winning_bin).strip().lower()
    fl = fav_label.lower()
    # winning_bin is often the range_label or a "low-high" token
    if w in fl or fl in w:
        return True
    b = binmap.get(fav_label)
    if b is not None and b["range_low"] is not None and b["range_high"] is not None:
        # winning_bin may be a numeric like "76-77"
        try:
            parts = w.replace("°f", "").replace("f", "").split("-")
            lo = float(parts[0])
            if abs(lo - float(b["range_low"])) < 0.6:
                return True
        except Exception:
            pass
    return False


def part_c(fc, tr):
    """Denver case study + equivalent-expression relative value."""
    out = ["## PART C — Denver case study + equivalent-expression relative value\n"]
    for date in ("2026-06-12", "2026-06-13"):
        out.append(f"### Denver {date} high")
        post = latest_posterior(fc, "Denver", date, "high")
        if not post:
            out.append("  no posterior")
            continue
        out.append(f"  posterior computed_at={post['computed_at']} method={post['posterior_method']}")
        try:
            q = json.loads(post["q_json"])
            qlcb = json.loads(post["q_lcb_json"]) if post["q_lcb_json"] else {}
            qucb = json.loads(post["q_ucb_json"]) if post["q_ucb_json"] else {}
        except Exception:
            q, qlcb, qucb = {}, {}, {}
        binmap = bins_for(fc, "Denver", date)
        books = latest_books(tr, binmap)
        out.append("  bin | range | our_q | our_q_lcb | mkt_yes_ask | mkt_no_ask | yes_payout/$ | no_payout/$")
        for label, b in sorted(binmap.items(), key=lambda kv: (kv[1]["range_low"] is None, kv[1]["range_low"] or 0)):
            cond = b["condition_id"]
            yask, ybid = implied_yes(books, cond)
            nask = no_ask(books, cond)
            qv = q.get(label, 0.0)
            qlv = qlcb.get(label, 0.0)
            rng = f"{b['range_low']}-{b['range_high']}"
            yp = (1.0 / yask) if yask and yask > 0 else None
            npp = (1.0 / nask) if nask and nask > 0 else None
            short = label.split(" be ")[-1][:16] if " be " in label else label[:16]
            yp_s = f"{yp:.2f}x" if yp else "-"
            np_s = f"{npp:.2f}x" if npp else "-"
            ya_s = f"{yask:.3f}" if yask is not None else "-"
            na_s = f"{nask:.3f}" if nask is not None else "-"
            out.append(
                f"  {short} | {rng} | {qv:.3f} | {qlv:.3f} | {ya_s} | {na_s} | {yp_s} | {np_s}"
            )
        # provenance center
        try:
            prov = json.loads(post["provenance_json"])
            anchor = prov.get("anchor_value_c") or (prov.get("bayes_precision_fusion") or {}).get("anchor_value_c")
            sig = (prov.get("bayes_precision_fusion") or {}).get("predictive_sigma_c")
            out.append(f"  direction-law center anchor_value_c={anchor} predictive_sigma_c={sig}")
        except Exception:
            pass
        out.append("")
    return "\n".join(out)


def main():
    fc = ro(FORECASTS_DB)
    tr = ro(TRADES_DB)
    dates = ["2026-06-12", "2026-06-13"]
    sections = [
        f"# Favorite-capture audit — generated {datetime.now(timezone.utc).isoformat()}\n",
        part_a(fc, tr, dates),
        "\n",
        part_b(fc, tr, "2026-05-29"),
        "\n",
        part_c(fc, tr),
    ]
    report = "\n".join(sections)
    sys.stdout.write(report)
    with open("/tmp/favorite_capture_probe_output.md", "w") as fh:
        fh.write(report)


if __name__ == "__main__":
    main()
