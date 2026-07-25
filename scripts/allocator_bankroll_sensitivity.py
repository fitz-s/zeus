#!/usr/bin/env python3
# Lifecycle: created=2026-07-25; last_reviewed=2026-07-25; last_reused=never
# Purpose: Read-only, non-authoritative sensitivity probe -- does N-x bankroll
#   convert to N-x deployed capital, or do per-market caps / candidate supply
#   bind first? Recomputes desired notional via the REAL
#   src.strategy.kelly.kelly_size() function on frozen candidate inputs
#   recovered from recent global_single_order_auction decision_log cycles.
# Reuse: Non-authoritative analysis tool; never used to size a live order.
#   Re-derive the (p_posterior, price) inversion method below before trusting
#   numbers if kelly.py's Kelly boundary formula changes.
"""Bankroll sensitivity probe: does 3.5x bankroll convert to 3.5x capital?

NON-AUTHORITATIVE ANALYSIS TOOL. Does not run the live engine, does not
place or size any real order, and never fabricates its own edge model.

Method
------
The live entry pipeline (`global_single_order_auction` cycles) persists a
compressed candidate-evaluation blob to `decision_log.artifact_json` in
`state/zeus_trades.db` for every reactor cycle (zlib+base64 JSON --
`summary.candidate_evaluations_zlib_b64`). For every candidate that reached
real Kelly sizing (`full_kelly_target_shares > 0`) this script:

1. Recovers a self-consistent (p_posterior, price) pair by inverting the
   frozen `full_kelly_target_shares` against the Kelly formula
   f* = (p - price) / (1 - price) at kelly_mult=1.0, using the candidate's
   `limit_price` and a bankroll estimate `wealth_after_loss_usd + cost_usd`
   (net wealth immediately before the trade). This is a MODELED inversion of
   the frozen record, not a re-derivation of the live posterior -- it exists
   only so the same (p, price, bankroll) triple can be fed back into the
   real `kelly_size()` function.
2. Calls the actual `src.strategy.kelly.kelly_size()` library function
   (imported, not reimplemented) at bankroll=W and bankroll=3.5*W using the
   cycle's own live `fractional_kelly_multiplier`, and validates the
   inversion reproduces the recorded fractional-Kelly notional within
   tolerance.
3. Applies the live `RiskAllocator` `CapPolicy.max_per_market_micro` fixed-
   dollar cap (`src.risk_allocator.governor.load_cap_policy()`,
   `config/risk_caps.yaml`) as the depth/supply proxy.

`executable_market_snapshot_latest` only stores top-of-book price, not
size-at-price, so true per-price order-book depth is NOT reconstructable
offline from canonical DB truth. This script falls back to the governor's
fixed-dollar CapPolicy cap as the closest code-real, non-fabricated proxy for
"depth/candidate-supply binds before bankroll scaling converts" and says so
here rather than inventing a liquidity curve.
"""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.execution_price import ExecutionPrice  # noqa: E402
from src.risk_allocator.governor import CapPolicy, load_cap_policy  # noqa: E402
from src.strategy.kelly import kelly_size  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "state" / "zeus_trades.db"
AUCTION_MODE = "global_single_order_auction"
DEFAULT_MULTIPLIER = 3.5
RECONSTRUCTION_TOLERANCE = 1e-3  # relative tolerance on the self-consistency check


@dataclass(frozen=True)
class Candidate:
    """Frozen sizing inputs recovered from one candidate in one auction cycle."""

    cycle_id: int
    candidate_id: str
    condition_id: str
    side: str
    status: str
    price: float
    p_posterior: float
    bankroll: float
    kelly_mult: float
    recorded_full_notional: float
    recorded_fractional_notional: float


@dataclass(frozen=True)
class SensitivityResult:
    candidate: Candidate
    notional_1x: float
    notional_mult_x: float
    per_market_cap_usd: float
    capped_1x: float
    capped_mult_x: float
    depth_capped_1x: bool
    depth_capped_mult_x: bool
    reconstruction_delta: float


def _decode_blob(blob: str) -> Any:
    return json.loads(zlib.decompress(base64.b64decode(blob, validate=True)))


def extract_candidates(conn: sqlite3.Connection, cycle_limit: int = 30) -> tuple[list[Candidate], list[str], int]:
    """Recover frozen (p_posterior, price, bankroll, kelly_mult) candidates.

    Reads the most recent `cycle_limit` `global_single_order_auction` cycles
    and collects every detailed candidate that reached real Kelly sizing
    (full_kelly_target_shares > 0) in each. A resting/re-decided candidate
    (e.g. an unfilled maker order) recurs identically across many
    consecutive cycles -- summing it once per cycle would overweight one
    still-resting order as if it were many distinct trades, so the result
    is deduped to the single most recent (highest cycle_id) row per
    candidate_id.
    """

    warnings: list[str] = []
    rows = conn.execute(
        "SELECT id, artifact_json FROM decision_log WHERE mode = ? ORDER BY id DESC LIMIT ?",
        (AUCTION_MODE, cycle_limit),
    ).fetchall()
    if not rows:
        warnings.append(f"no '{AUCTION_MODE}' decision_log rows found -- report is empty.")
        return [], warnings, 0

    candidates: list[Candidate] = []
    for cycle_id, artifact_json in rows:
        try:
            artifact = json.loads(artifact_json)
            summary = artifact["summary"]
            kelly_mult = float(summary["fractional_kelly_multiplier"])
            blob = summary.get("candidate_evaluations_zlib_b64")
            if not blob:
                continue
            obj = _decode_blob(blob)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, zlib.error) as exc:
            warnings.append(f"cycle {cycle_id}: could not decode candidate evaluations ({exc}); skipped.")
            continue

        for c in obj.get("detailed", []):
            try:
                shares_full = float(c.get("full_kelly_target_shares") or 0)
                if shares_full <= 0:
                    continue
                price = float(c.get("limit_price") or 0)
                if not (0.0 < price < 1.0):
                    continue
                cost_usd = float(c.get("cost_usd") or 0)
                terminal = c.get("terminal_wealth") or {}
                wealth_after_loss = terminal.get("wealth_after_loss_usd")
                if wealth_after_loss is None:
                    continue
                bankroll = float(wealth_after_loss) + cost_usd
                if bankroll <= 0:
                    continue

                notional_full = shares_full * price
                f_star = notional_full / bankroll
                if not (0.0 < f_star < 1.0):
                    continue
                p_posterior = f_star * (1.0 - price) + price
                p_posterior = min(max(p_posterior, 0.0), 1.0)

                shares_frac = float(c.get("fractional_kelly_target_shares") or 0)
                candidates.append(
                    Candidate(
                        cycle_id=int(cycle_id),
                        candidate_id=str(c.get("candidate_id") or ""),
                        condition_id=str(c.get("condition_id") or ""),
                        side=str(c.get("side") or ""),
                        status=str(c.get("status") or ""),
                        price=price,
                        p_posterior=p_posterior,
                        bankroll=bankroll,
                        kelly_mult=kelly_mult,
                        recorded_full_notional=notional_full,
                        recorded_fractional_notional=shares_frac * price,
                    )
                )
            except (TypeError, ValueError):
                continue

    deduped: dict[str, Candidate] = {}
    for cand in candidates:
        key = cand.candidate_id or f"{cand.condition_id}:{cand.side}"
        current = deduped.get(key)
        if current is None or cand.cycle_id > current.cycle_id:
            deduped[key] = cand
    return list(deduped.values()), warnings, len(rows)


def compute_sensitivity(
    candidates: list[Candidate], multiplier: float, cap_policy: CapPolicy
) -> tuple[list[SensitivityResult], list[str]]:
    """Recompute desired notional at 1.0x and `multiplier`x bankroll via kelly_size()."""

    warnings: list[str] = []
    per_market_cap_usd = cap_policy.max_per_market_micro / 1_000_000.0
    results: list[SensitivityResult] = []
    for cand in candidates:
        ep = ExecutionPrice(value=cand.price, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")
        notional_1x = kelly_size(cand.p_posterior, ep, bankroll=cand.bankroll, kelly_mult=cand.kelly_mult)
        notional_mult_x = kelly_size(cand.p_posterior, ep, bankroll=cand.bankroll * multiplier, kelly_mult=cand.kelly_mult)

        delta = abs(notional_1x - cand.recorded_fractional_notional)
        rel_delta = delta / cand.recorded_fractional_notional if cand.recorded_fractional_notional > 0 else delta
        if rel_delta > RECONSTRUCTION_TOLERANCE:
            warnings.append(
                f"cycle {cand.cycle_id} candidate {cand.candidate_id[:12]}: recomputed notional "
                f"(${notional_1x:.4f}) diverges from the recorded fractional-Kelly notional "
                f"(${cand.recorded_fractional_notional:.4f}) by {rel_delta:.2%} -- inversion may not "
                "hold for this candidate; treat its row with caution."
            )

        results.append(
            SensitivityResult(
                candidate=cand,
                notional_1x=notional_1x,
                notional_mult_x=notional_mult_x,
                per_market_cap_usd=per_market_cap_usd,
                capped_1x=min(notional_1x, per_market_cap_usd),
                capped_mult_x=min(notional_mult_x, per_market_cap_usd),
                depth_capped_1x=notional_1x > per_market_cap_usd,
                depth_capped_mult_x=notional_mult_x > per_market_cap_usd,
                reconstruction_delta=rel_delta,
            )
        )
    return results, warnings


@dataclass(frozen=True)
class Aggregate:
    count: int
    sum_raw_1x: float
    sum_raw_mult_x: float
    sum_capped_1x: float
    sum_capped_mult_x: float
    count_capped_1x: int
    count_capped_mult_x: int

    @property
    def ratio_raw(self) -> float:
        return self.sum_raw_mult_x / self.sum_raw_1x if self.sum_raw_1x > 0 else 0.0

    @property
    def ratio_capped(self) -> float:
        return self.sum_capped_mult_x / self.sum_capped_1x if self.sum_capped_1x > 0 else 0.0


def aggregate(results: list[SensitivityResult]) -> Aggregate:
    return Aggregate(
        count=len(results),
        sum_raw_1x=sum(r.notional_1x for r in results),
        sum_raw_mult_x=sum(r.notional_mult_x for r in results),
        sum_capped_1x=sum(r.capped_1x for r in results),
        sum_capped_mult_x=sum(r.capped_mult_x for r in results),
        count_capped_1x=sum(1 for r in results if r.depth_capped_1x),
        count_capped_mult_x=sum(1 for r in results if r.depth_capped_mult_x),
    )


def render_text(
    results: list[SensitivityResult], agg: Aggregate, multiplier: float, warnings: list[str], cycles_scanned: int
) -> str:
    lines: list[str] = []
    lines.append("=" * 100)
    lines.append("ALLOCATOR BANKROLL SENSITIVITY (non-authoritative; read-only)")
    lines.append(f"Question: does {multiplier}x bankroll convert to {multiplier}x deployed capital?")
    lines.append("=" * 100)
    if warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for w in warnings:
            lines.append(f"  - {w}")

    lines.append("")
    lines.append(
        f"{'cycle':>8} {'condition_id':<20} {'side':<4} {'price':>6} {'p_post':>7} "
        f"{'bankroll':>10} {'@1x':>9} {'@Nx':>9} {'cap':>8} {'capped@1x':>10} {'capped@Nx':>10} depth"
    )
    lines.append("-" * 120)
    for r in sorted(results, key=lambda x: x.notional_1x, reverse=True):
        c = r.candidate
        depth_flag = "Y" if (r.depth_capped_1x or r.depth_capped_mult_x) else ""
        lines.append(
            f"{c.cycle_id:>8} {c.condition_id[:20]:<20} {c.side:<4} {c.price:>6.2f} {c.p_posterior:>7.4f} "
            f"{c.bankroll:>10.2f} {r.notional_1x:>9.2f} {r.notional_mult_x:>9.2f} {r.per_market_cap_usd:>8.0f} "
            f"{r.capped_1x:>10.2f} {r.capped_mult_x:>10.2f} {depth_flag:>5}"
        )

    opportunity_rate = agg.count / cycles_scanned if cycles_scanned > 0 else 0.0

    lines.append("")
    lines.append("AGGREGATE")
    lines.append("-" * 100)
    lines.append(f"  auction cycles scanned:            {cycles_scanned}")
    lines.append(f"  distinct positive-edge candidates: {agg.count}  (opportunity rate {opportunity_rate:.4f}/cycle)")
    lines.append(f"  sum notional @1.0x  (uncapped):    ${agg.sum_raw_1x:,.2f}")
    lines.append(f"  sum notional @{multiplier}x (uncapped):    ${agg.sum_raw_mult_x:,.2f}")
    lines.append(f"  uncapped ratio (kelly_size() alone, no caps applied): {agg.ratio_raw:.3f}x  (should equal {multiplier} exactly -- pure formula is linear in bankroll)")
    lines.append(f"  sum notional @1.0x  (per-market capped): ${agg.sum_capped_1x:,.2f}  ({agg.count_capped_1x}/{agg.count} candidates already capped at 1.0x)")
    lines.append(f"  sum notional @{multiplier}x (per-market capped): ${agg.sum_capped_mult_x:,.2f}  ({agg.count_capped_mult_x}/{agg.count} candidates capped at {multiplier}x)")
    lines.append(f"  CAPPED ratio (what actually converts to deployed capital): {agg.ratio_capped:.3f}x")

    lines.append("")
    lines.append("VERDICT")
    lines.append("-" * 100)
    if agg.count == 0:
        lines.append("  No candidates with positive Kelly edge in the sampled window -- no verdict possible.")
    elif agg.ratio_capped < multiplier * 0.9:
        lines.append(
            f"  Bankroll scaling does NOT fully convert: capped ratio {agg.ratio_capped:.2f}x << {multiplier}x. "
            "The fixed-dollar per-market CapPolicy cap (config/risk_caps.yaml, "
            "src.risk_allocator.governor.CapPolicy.max_per_market_micro) binds before bankroll scaling "
            "does, for the candidates sampled here."
        )
    else:
        lines.append(
            f"  The Kelly formula itself and the per-market cap do NOT bind in this sample: capped ratio "
            f"{agg.ratio_capped:.2f}x tracks the {multiplier}x uncapped ratio almost exactly, because every "
            f"observed candidate's notional (up to ${max((r.notional_mult_x for r in results), default=0.0):.2f} "
            f"at {multiplier}x) sits far below the ${agg.sum_capped_1x / agg.count if agg.count else 0:.0f}-scale "
            "per-market cap. At today's bankroll and edge sizes, per-candidate dollar caps are not what would "
            "stop 3.5x bankroll from converting to 3.5x capital deployed PER TRADE."
        )
        lines.append(
            f"  The more likely binding constraint this script cannot fully price: CANDIDATE SUPPLY. Only "
            f"{agg.count} distinct positive-Kelly-edge candidates surfaced across {cycles_scanned} recent "
            f"auction cycles ({opportunity_rate:.4f}/cycle). A single-order-auction design places at most one "
            "order per cycle regardless of bankroll size, so scaling bankroll does not create more "
            "opportunities per unit time -- it only changes the size of each opportunity when one appears. "
            "Whether 3.5x bankroll actually converts to 3.5x TOTAL capital deployed over time depends on "
            "opportunity arrival rate staying similar and on aggregate simultaneous exposure not crossing the "
            "portfolio-level max_per_event_micro / max_correlated_exposure_micro caps -- neither of which this "
            "per-candidate script measures."
        )
    lines.append(
        "  CAVEAT: true per-price order-book depth is not reconstructable offline from canonical DB "
        "truth (executable_market_snapshot_latest stores only top-of-book price, not size-at-price); "
        "the fixed-dollar per-market cap above is a proxy, not a live liquidity curve. This script also does "
        "not model per-event or correlated-family caps, which bind across multiple SIMULTANEOUSLY HELD "
        "candidates/positions rather than a single row here -- that needs a portfolio-level analysis, not "
        "a per-candidate one."
    )
    return "\n".join(lines)


def render_json(
    results: list[SensitivityResult], agg: Aggregate, multiplier: float, warnings: list[str], cycles_scanned: int
) -> str:
    payload = {
        "multiplier": multiplier,
        "warnings": warnings,
        "candidates": [
            {
                "cycle_id": r.candidate.cycle_id,
                "candidate_id": r.candidate.candidate_id,
                "condition_id": r.candidate.condition_id,
                "side": r.candidate.side,
                "status": r.candidate.status,
                "price": r.candidate.price,
                "p_posterior": round(r.candidate.p_posterior, 6),
                "bankroll": round(r.candidate.bankroll, 6),
                "kelly_mult": r.candidate.kelly_mult,
                "notional_1x": round(r.notional_1x, 6),
                "notional_mult_x": round(r.notional_mult_x, 6),
                "per_market_cap_usd": r.per_market_cap_usd,
                "capped_1x": round(r.capped_1x, 6),
                "capped_mult_x": round(r.capped_mult_x, 6),
                "depth_capped_1x": r.depth_capped_1x,
                "depth_capped_mult_x": r.depth_capped_mult_x,
                "reconstruction_relative_delta": round(r.reconstruction_delta, 6),
            }
            for r in results
        ],
        "aggregate": {
            "cycles_scanned": cycles_scanned,
            "opportunity_rate_per_cycle": round(agg.count / cycles_scanned, 6) if cycles_scanned > 0 else 0.0,
            "count": agg.count,
            "sum_raw_1x": round(agg.sum_raw_1x, 6),
            "sum_raw_mult_x": round(agg.sum_raw_mult_x, 6),
            "ratio_raw": round(agg.ratio_raw, 6),
            "sum_capped_1x": round(agg.sum_capped_1x, 6),
            "sum_capped_mult_x": round(agg.sum_capped_mult_x, 6),
            "ratio_capped": round(agg.ratio_capped, 6),
            "count_capped_1x": agg.count_capped_1x,
            "count_capped_mult_x": agg.count_capped_mult_x,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to zeus_trades.db (read-only)")
    parser.add_argument(
        "--cycles",
        type=int,
        default=300,
        help=(
            "Number of recent global_single_order_auction cycles to scan for candidates "
            "(deduped to one row per distinct candidate_id; a resting/re-decided candidate "
            "recurs across many consecutive cycles)"
        ),
    )
    parser.add_argument("--multiplier", type=float, default=DEFAULT_MULTIPLIER, help="Bankroll multiplier to test")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of the text table")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=5.0)
    try:
        candidates, extract_warnings, cycles_scanned = extract_candidates(conn, cycle_limit=args.cycles)
    finally:
        conn.close()

    cap_policy = load_cap_policy()
    results, compute_warnings = compute_sensitivity(candidates, args.multiplier, cap_policy)
    warnings = extract_warnings + compute_warnings
    agg = aggregate(results)

    if args.json:
        print(render_json(results, agg, args.multiplier, warnings, cycles_scanned))
    else:
        print(render_text(results, agg, args.multiplier, warnings, cycles_scanned))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
