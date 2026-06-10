# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis:
#   - Mission: automated 守護 settlement measurement loop (operator-approved
#     Phase-2 organ). GOAL = stable >51% AFTER-COST settlement win-rate on
#     traded markets.
#   - Settlement-grading SPINE (one-builder law): reuses
#     src.contracts.graded_receipt.grade_receipt (Direction Law + unit antibody
#     + BinKind membership). NO parallel grader.
#   - Join pattern reused from src.cron.settlement_attribution
#     (open_world_with_forecasts: WORLD main + forecasts ATTACHed, INV-37,
#     read-only on forecasts; _bin_from_label parse path).
#   - Direction Law (graded_receipt header / GOAL#36):
#       buy_yes WIN iff settled_bin == traded_bin
#       buy_no  WIN iff settled_bin != traded_bin
#   - Binomial CI: Clopper-Pearson exact (scipy beta.ppf) — frequentist, no
#     optimistic prior, the honest small-n sentinel (distinct from
#     evidence_report._bayesian_ci Beta(2,2) which shrinks toward 0.5).
"""settlement_guard_report — the automated daily 守護 settlement scorecard.

WHY THIS EXISTS
---------------
The trading system's GOAL is a stable, AFTER-COST settlement win-rate above the
51% bar on the markets it actually trades. Until now that verification was manual
archaeology (read the profit-audit ledger, hand-join to settlements, eyeball the
win-rate). This module is the missing organ: a read-only daily pass that grades
every executed fill against the spine-graded settlement truth and emits a
machine artifact + a human markdown + a one-line INFO log.

THE JOIN (the load-bearing relationship test)
---------------------------------------------
    executed fill            forecasts.market_events        forecasts.settlement_outcomes
    (edli_live_profit_audit) ─ condition_id ─▶ (city,       ─ (city,target_date,        ─▶ grade_receipt
     direction, avg_fill_price,                target_date,    temperature_metric) ─▶      (Direction Law)
     filled_size, fees)                        metric,         settlement_value,
                                               range_low/high) settlement_unit

The fill ledger captures the EXECUTED economics (entry price, size, captured fee
envelope) but NOT the graded outcome (settlement_outcome / pnl_usd are NULL on
the audit rows — they were never backfilled). So this report does the grading
itself, through the ONE truth function, and computes after-cost PnL from the
captured fill economics:

    cost_basis      = avg_fill_price * filled_size + fees
    payoff_per_share = 1.0 if graded.won else 0.0
    after_cost_pnl  = (payoff_per_share - avg_fill_price) * filled_size - fees

This is correct for BOTH directions because grade_receipt already applied the
Direction Law: a buy_no position whose settled value lands OUTSIDE the traded
bin has graded.won = True, so its NO token pays $1/share. The cost basis is the
price PAID for that NO token (avg_fill_price on the buy_no fill). Fees are the
captured venue fee envelope (NULL treated as 0.0 and counted as a coverage gap,
never silently assumed).

HONESTY DISCIPLINE
------------------
- n=0 produces a valid "n=0" report, never a crash (pre-first-fill state).
- Small n (< MIN_N_FOR_POINT_CLAIM) prints the CI, never a point win-rate claim.
- SUSPEND_CANDIDATE is REPORT-ONLY in v1 (no auto-gate): a city whose rolling
  win-rate CI UPPER bound < 0.50 is flagged for the operator.
- Strategy label is NOT recoverable from the filled audit rows in v1
  (order_policy NULL, no receipt linkage) — it is recorded as "unknown" rather
  than fabricated. Direction IS authoritative and is always broken out.
- Idempotent + cheap: read-only, one pass, no writes to any graded table.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# The after-cost bar the GOAL is measured against.
GOAL_WIN_RATE_BAR: float = 0.51
# Below this n we refuse a point win-rate claim and print only the CI.
MIN_N_FOR_POINT_CLAIM: int = 20
# Rolling windows (days) for the GOAL line.
ROLLING_WINDOWS_DAYS: tuple[int, ...] = (7, 30)
# A city whose rolling-window win-rate CI UPPER bound is below this gets
# flagged SUSPEND_CANDIDATE (report-only).
SUSPEND_CI_UPPER_BAR: float = 0.50
# Which rolling window drives the SUSPEND_CANDIDATE sentinel.
SUSPEND_WINDOW_DAYS: int = 30


# ---------------------------------------------------------------------------
# Binomial CI — Clopper-Pearson exact (the honest small-n sentinel)
# ---------------------------------------------------------------------------

def clopper_pearson_ci(
    n_wins: int,
    n_trials: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Exact Clopper-Pearson binomial confidence interval.

    Frequentist, no optimistic prior — the honest choice for a regression
    sentinel. (evidence_report._bayesian_ci uses a Beta(2,2) prior that pulls
    toward 0.5; for a SUSPEND sentinel we must NOT borrow optimism.)

    Returns (lower, upper). For n_trials == 0 returns (0.0, 1.0) — total
    ignorance, never a fabricated bound.
    """
    if n_trials <= 0:
        return 0.0, 1.0
    if n_wins < 0 or n_wins > n_trials:
        raise ValueError(
            f"clopper_pearson_ci: n_wins={n_wins} out of [0, {n_trials}]"
        )
    from scipy.stats import beta as scipy_beta

    alpha = 1.0 - confidence
    # Clopper-Pearson via the Beta quantile identity.
    if n_wins == 0:
        lower = 0.0
    else:
        lower = float(scipy_beta.ppf(alpha / 2.0, n_wins, n_trials - n_wins + 1))
    if n_wins == n_trials:
        upper = 1.0
    else:
        upper = float(
            scipy_beta.ppf(1.0 - alpha / 2.0, n_wins + 1, n_trials - n_wins)
        )
    return lower, upper


# ---------------------------------------------------------------------------
# Typed result objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GradedFill:
    """One executed fill graded against the spine settlement truth.

    Carries the executed economics AND the graded verdict so every downstream
    aggregate reads `won` / `after_cost_pnl_usd` from HERE — there is no second
    grading or PnL path.
    """

    condition_id: str
    city: str
    target_date: str
    metric: str
    direction: str
    traded_bin_label: str
    settled_value: float
    settlement_unit: str
    avg_fill_price: float
    filled_size: float
    fees_usd: float
    fees_were_null: bool          # coverage flag: fee envelope absent → treated as 0
    q_entry: Optional[float]      # q_live at entry, if captured (calibration input)
    won: bool
    settled_in_bin: bool
    cost_basis_usd: float
    after_cost_pnl_usd: float
    settled_at_utc: Optional[str]


@dataclass
class GroupStats:
    """Win-rate + after-cost economics for one grouping (city/metric/direction/overall)."""

    label: str
    n_settled: int = 0
    n_wins: int = 0
    after_cost_pnl_usd: float = 0.0
    cost_basis_usd: float = 0.0
    # Calibration: mean entry q vs realized win indicator (did entry q overstate?).
    sum_q_entry: float = 0.0
    n_with_q: int = 0
    n_wins_with_q: int = 0        # wins among the q-carrying rows only
    sum_brier: float = 0.0        # (q_entry - won)^2 accumulator
    sum_logloss: float = 0.0      # -[w*ln(q)+(1-w)*ln(1-q)] accumulator

    @property
    def n_losses(self) -> int:
        return self.n_settled - self.n_wins

    @property
    def win_rate(self) -> Optional[float]:
        """Point win-rate, or None when n is too small for an honest claim."""
        if self.n_settled < MIN_N_FOR_POINT_CLAIM:
            return None
        return self.n_wins / self.n_settled

    @property
    def win_rate_raw(self) -> Optional[float]:
        """Raw point estimate regardless of n (for the CI / display, not a claim)."""
        if self.n_settled <= 0:
            return None
        return self.n_wins / self.n_settled

    @property
    def ci_95(self) -> tuple[float, float]:
        return clopper_pearson_ci(self.n_wins, self.n_settled)

    @property
    def mean_entry_q(self) -> Optional[float]:
        if self.n_with_q <= 0:
            return None
        return self.sum_q_entry / self.n_with_q

    @property
    def calibration_gap(self) -> Optional[float]:
        """mean(entry q) - realized win-rate, both over the q-carrying rows.

        Positive => entry q OVERSTATED the eventual win-rate (overconfident).
        Computed over the SAME subset (rows that carried an entry q) so the two
        terms are comparable — mixing a q-subset mean with an all-rows win-rate
        would be an apples-to-oranges gap.
        """
        if self.n_with_q <= 0:
            return None
        mean_q = self.sum_q_entry / self.n_with_q
        realized_wr = self.n_wins_with_q / self.n_with_q
        return mean_q - realized_wr

    @property
    def brier(self) -> Optional[float]:
        if self.n_with_q <= 0:
            return None
        return self.sum_brier / self.n_with_q

    @property
    def log_loss(self) -> Optional[float]:
        if self.n_with_q <= 0:
            return None
        return self.sum_logloss / self.n_with_q

    @property
    def roi(self) -> Optional[float]:
        """After-cost PnL per dollar of cost basis."""
        if self.cost_basis_usd <= 0:
            return None
        return self.after_cost_pnl_usd / self.cost_basis_usd

    def add(self, fill: GradedFill) -> None:
        self.n_settled += 1
        if fill.won:
            self.n_wins += 1
        self.after_cost_pnl_usd += fill.after_cost_pnl_usd
        self.cost_basis_usd += fill.cost_basis_usd
        if fill.q_entry is not None:
            q = float(fill.q_entry)
            self.sum_q_entry += q
            self.n_with_q += 1
            if fill.won:
                self.n_wins_with_q += 1
            w = 1.0 if fill.won else 0.0
            self.sum_brier += (q - w) ** 2
            # Clamp q away from {0,1} so log-loss never blows up.
            qc = min(max(q, 1e-6), 1.0 - 1e-6)
            self.sum_logloss += -(w * math.log(qc) + (1.0 - w) * math.log(1.0 - qc))


@dataclass
class RollingGoalLine:
    """The GOAL line: rolling after-cost win-rate vs the 51% bar with CI."""

    window_days: int
    n_settled: int
    n_wins: int
    after_cost_pnl_usd: float
    ci_lower: float
    ci_upper: float

    @property
    def win_rate_raw(self) -> Optional[float]:
        if self.n_settled <= 0:
            return None
        return self.n_wins / self.n_settled

    @property
    def clears_bar_claim(self) -> Optional[bool]:
        """True only when n is large enough AND the CI LOWER bound clears the bar.

        At small n returns None (no claim) — the bar is a CI question, not a
        point question, and we never assert a point win-rate below MIN_N.
        """
        if self.n_settled < MIN_N_FOR_POINT_CLAIM:
            return None
        return self.ci_lower >= GOAL_WIN_RATE_BAR


@dataclass
class SettlementGuardReport:
    """The full daily 守護 scorecard."""

    generated_at_utc: str
    n_settled_total: int
    overall: GroupStats
    per_city_metric: dict = field(default_factory=dict)   # "City|metric" -> GroupStats
    per_direction: dict = field(default_factory=dict)      # "buy_yes"/"buy_no" -> GroupStats
    per_strategy: dict = field(default_factory=dict)       # strategy_label -> GroupStats
    rolling: dict = field(default_factory=dict)            # window_days -> RollingGoalLine
    suspend_candidates: list = field(default_factory=list)  # list[dict]
    fee_coverage_gap_count: int = 0                         # fills with NULL fee envelope
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bin construction (reuse the canonical parse path from settlement_attribution)
# ---------------------------------------------------------------------------

def _bin_from_market_event(
    range_low: Optional[float],
    range_high: Optional[float],
    settlement_unit: str,
):
    """Build a gradeable Bin from a market_events numeric range + settlement unit.

    Prefers the numeric range_low/range_high (clean, unit-unambiguous) over the
    question-string range_label. Returns None when the range cannot be turned
    into a valid Bin (caller skips the row rather than guessing). The label is
    synthesised from the numeric bound + unit so it can never carry a °F/°C
    token that contradicts the settlement unit (Bin.__post_init__ antibody).
    """
    from src.types.market import Bin

    if range_low is None and range_high is None:
        return None
    lo = float(range_low) if range_low is not None else None
    hi = float(range_high) if range_high is not None else None
    # Synthesise a unit-coherent label (never reuse the question string, which
    # may contain a °C/°F token that would trip the Bin label antibody).
    deg = "°C" if settlement_unit == "C" else "°F"
    if lo is not None and hi is not None and lo == hi:
        label = f"{lo:g}{deg}"
    elif lo is not None and hi is None:
        label = f"{lo:g}{deg} or higher"
    elif lo is None and hi is not None:
        label = f"{hi:g}{deg} or below"
    else:
        label = f"{lo:g}-{hi:g}{deg}"
    try:
        return Bin(low=lo, high=hi, unit=settlement_unit, label=label)
    except Exception:  # noqa: BLE001 — malformed bin → skip, never crash a batch
        return None


# ---------------------------------------------------------------------------
# The grading pass
# ---------------------------------------------------------------------------

def load_graded_fills(world_conn: sqlite3.Connection) -> list[GradedFill]:
    """Grade every executed fill against the spine settlement truth.

    Reads:
      - ``edli_live_profit_audit`` (WORLD): executed fills with captured
        economics (avg_fill_price, filled_size, fees). Only rows with
        ``filled_size > 0`` are real positions.
      - ``forecasts.market_events`` (ATTACHed): condition_id -> (city,
        target_date, temperature_metric, range_low/range_high).
      - ``forecasts.settlement_outcomes`` (ATTACHed, VERIFIED only):
        (city, target_date, metric) -> (settlement_value, settlement_unit).

    Each matched fill is graded through ``grade_receipt`` (the ONE truth
    function). Unit-mismatch rows are skipped with a WARN (the
    UnitMismatchError IS the structural guard). Returns one GradedFill per
    fill that matched a VERIFIED settlement.

    Caller must have ``forecasts`` ATTACHed (use ``open_world_with_forecasts``).
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.types.temperature import UnitMismatchError

    # 1. condition_id -> market metadata (one VERIFIED-relevant row per cid).
    #    A market_slug is single-metric, so condition_id -> one (city,date,metric).
    market_meta: dict[str, dict] = {}
    for cid, city, tdate, metric, rlo, rhi in world_conn.execute(
        """
        SELECT condition_id, city, target_date, temperature_metric,
               range_low, range_high
        FROM forecasts.market_events
        WHERE condition_id IS NOT NULL
        """
    ).fetchall():
        if cid in market_meta:
            continue
        market_meta[cid] = {
            "city": city,
            "target_date": tdate,
            "metric": (metric or "high"),
            "range_low": rlo,
            "range_high": rhi,
        }

    # 2. (city, target_date, metric) -> VERIFIED settlement.
    settlements: dict[tuple, dict] = {}
    settled_at: dict[tuple, Optional[str]] = {}
    for city, tdate, metric, value, unit, s_at in world_conn.execute(
        """
        SELECT city, target_date, temperature_metric,
               settlement_value, settlement_unit, settled_at
        FROM forecasts.settlement_outcomes
        WHERE authority = 'VERIFIED'
        """
    ).fetchall():
        if value is None:
            continue
        key = (city, tdate, (metric or "high"))
        if key not in settlements:
            settlements[key] = {
                "settlement_value": float(value),
                "settlement_unit": unit,
            }
            settled_at[key] = s_at

    # 3. Walk the executed fills and grade each.
    out: list[GradedFill] = []
    unit_mismatch = 0
    no_market = 0
    no_settlement = 0
    bad_bin = 0
    for (
        condition_id, direction, avg_fill_price, filled_size, fees,
        q_live,
    ) in world_conn.execute(
        """
        SELECT condition_id, direction, avg_fill_price, filled_size, fees,
               q_live
        FROM edli_live_profit_audit
        WHERE filled_size > 0
          AND avg_fill_price IS NOT NULL
        """
    ).fetchall():
        meta = market_meta.get(condition_id)
        if meta is None:
            no_market += 1
            continue
        key = (meta["city"], meta["target_date"], meta["metric"])
        s = settlements.get(key)
        if s is None:
            no_settlement += 1
            continue

        bin_obj = _bin_from_market_event(
            meta["range_low"], meta["range_high"], s["settlement_unit"]
        )
        if bin_obj is None:
            bad_bin += 1
            continue

        class _S:  # minimal settlement stand-in for grade_receipt
            settlement_value = s["settlement_value"]
            settlement_unit = s["settlement_unit"]

        try:
            graded = grade_receipt(bin_obj, direction, _S())
        except UnitMismatchError:
            unit_mismatch += 1
            logger.warning(
                "settlement_guard: unit mismatch cid=%s city=%s bin=%s — skipped",
                condition_id, meta["city"], bin_obj.label,
            )
            continue
        except ValueError:
            continue  # unknown direction — skip, never crash the batch

        price = float(avg_fill_price)
        size = float(filled_size)
        fees_were_null = fees is None
        fees_usd = 0.0 if fees_were_null else float(fees)

        # After-cost economics from the CAPTURED fill envelope.
        #   cost_basis  = price paid for the held token + fees
        #   payoff      = $1/share iff this position WON (Direction Law applied)
        cost_basis_usd = price * size + fees_usd
        payoff_per_share = 1.0 if graded.won else 0.0
        after_cost_pnl_usd = (payoff_per_share - price) * size - fees_usd

        out.append(
            GradedFill(
                condition_id=condition_id,
                city=meta["city"],
                target_date=meta["target_date"],
                metric=meta["metric"],
                direction=direction,
                traded_bin_label=bin_obj.label,
                settled_value=s["settlement_value"],
                settlement_unit=s["settlement_unit"],
                avg_fill_price=price,
                filled_size=size,
                fees_usd=fees_usd,
                fees_were_null=fees_were_null,
                q_entry=(float(q_live) if q_live is not None else None),
                won=graded.won,
                settled_in_bin=graded.settled_in_bin,
                cost_basis_usd=cost_basis_usd,
                after_cost_pnl_usd=after_cost_pnl_usd,
                settled_at_utc=settled_at.get(key),
            )
        )

    if unit_mismatch or no_market or no_settlement or bad_bin:
        logger.info(
            "settlement_guard: graded=%d skipped(unit_mismatch=%d no_market=%d "
            "no_settlement=%d bad_bin=%d)",
            len(out), unit_mismatch, no_market, no_settlement, bad_bin,
        )
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _parse_settled_date(settled_at_utc: Optional[str]) -> Optional[date]:
    """Parse a settlement timestamp into a UTC date for rolling-window filters."""
    if not settled_at_utc:
        return None
    s = str(settled_at_utc).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Fall back to a bare date prefix.
        try:
            return date.fromisoformat(str(settled_at_utc)[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()


def build_report(
    fills: list[GradedFill],
    *,
    now_utc: Optional[datetime] = None,
) -> SettlementGuardReport:
    """Aggregate graded fills into the daily scorecard.

    Pure function over the graded-fill list (no DB access) so it is trivially
    testable with synthetic fills. Produces an honest n=0 report when fills is
    empty rather than raising.
    """
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)
    today = now_utc.date()

    overall = GroupStats(label="overall")
    per_city_metric: dict[str, GroupStats] = {}
    per_direction: dict[str, GroupStats] = {}
    per_strategy: dict[str, GroupStats] = {}
    fee_gap = 0

    for f in fills:
        overall.add(f)
        cm_key = f"{f.city}|{f.metric}"
        per_city_metric.setdefault(cm_key, GroupStats(label=cm_key)).add(f)
        per_direction.setdefault(f.direction, GroupStats(label=f.direction)).add(f)
        # Strategy label is not recoverable from filled audit rows in v1.
        per_strategy.setdefault("unknown", GroupStats(label="unknown")).add(f)
        if f.fees_were_null:
            fee_gap += 1

    # Rolling GOAL lines.
    rolling: dict[int, RollingGoalLine] = {}
    for window in ROLLING_WINDOWS_DAYS:
        n = 0
        wins = 0
        pnl = 0.0
        for f in fills:
            d = _parse_settled_date(f.settled_at_utc)
            if d is None:
                continue
            if (today - d).days < window:
                n += 1
                if f.won:
                    wins += 1
                pnl += f.after_cost_pnl_usd
        lo, hi = clopper_pearson_ci(wins, n)
        rolling[window] = RollingGoalLine(
            window_days=window,
            n_settled=n,
            n_wins=wins,
            after_cost_pnl_usd=pnl,
            ci_lower=lo,
            ci_upper=hi,
        )

    # SUSPEND_CANDIDATE sentinel (report-only): per city+metric, on the
    # SUSPEND_WINDOW_DAYS rolling window, flag any whose CI UPPER bound < bar.
    suspend: list[dict] = []
    for cm_key in sorted(per_city_metric.keys()):
        n = 0
        wins = 0
        for f in fills:
            if f"{f.city}|{f.metric}" != cm_key:
                continue
            d = _parse_settled_date(f.settled_at_utc)
            if d is None or (today - d).days >= SUSPEND_WINDOW_DAYS:
                continue
            n += 1
            if f.won:
                wins += 1
        if n <= 0:
            continue
        lo, hi = clopper_pearson_ci(wins, n)
        if hi < SUSPEND_CI_UPPER_BAR:
            suspend.append({
                "city_metric": cm_key,
                "window_days": SUSPEND_WINDOW_DAYS,
                "n_settled": n,
                "n_wins": wins,
                "ci_lower": lo,
                "ci_upper": hi,
                "reason": (
                    f"rolling {SUSPEND_WINDOW_DAYS}d win-rate CI upper bound "
                    f"{hi:.3f} < {SUSPEND_CI_UPPER_BAR:.2f}"
                ),
            })

    notes: list[str] = []
    if not fills:
        notes.append(
            "n=0: no executed fills matched a VERIFIED settlement yet. This is "
            "the honest pre-first-settlement state, not an error."
        )
    if fee_gap:
        notes.append(
            f"fee coverage gap: {fee_gap} fill(s) had a NULL fee envelope; "
            "treated as 0.0 fees (after-cost PnL is an UPPER bound for those)."
        )
    notes.append(
        "strategy attribution unavailable in v1 (order_policy NULL on filled "
        "audit rows); all rows bucketed as strategy='unknown'. Direction "
        "breakdown is authoritative."
    )

    return SettlementGuardReport(
        generated_at_utc=now_utc.isoformat(),
        n_settled_total=overall.n_settled,
        overall=overall,
        per_city_metric=per_city_metric,
        per_direction=per_direction,
        per_strategy=per_strategy,
        rolling=rolling,
        suspend_candidates=suspend,
        fee_coverage_gap_count=fee_gap,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _group_to_dict(g: GroupStats) -> dict:
    lo, hi = g.ci_95
    return {
        "label": g.label,
        "n_settled": g.n_settled,
        "n_wins": g.n_wins,
        "n_losses": g.n_losses,
        # win_rate is None below MIN_N (honesty); win_rate_raw is the point est.
        "win_rate": g.win_rate,
        "win_rate_raw": g.win_rate_raw,
        "ci95_lower": lo,
        "ci95_upper": hi,
        "after_cost_pnl_usd": round(g.after_cost_pnl_usd, 6),
        "cost_basis_usd": round(g.cost_basis_usd, 6),
        "roi": g.roi,
        "mean_entry_q": g.mean_entry_q,
        "calibration_gap": g.calibration_gap,
        "brier": g.brier,
        "log_loss": g.log_loss,
        "n_with_entry_q": g.n_with_q,
    }


def report_to_json(report: SettlementGuardReport) -> dict:
    """Machine artifact: state/settlement_guard_report.json payload."""
    return {
        "schema_version": 1,
        "generated_at_utc": report.generated_at_utc,
        "goal_win_rate_bar": GOAL_WIN_RATE_BAR,
        "min_n_for_point_claim": MIN_N_FOR_POINT_CLAIM,
        "n_settled_total": report.n_settled_total,
        "overall": _group_to_dict(report.overall),
        "per_city_metric": {
            k: _group_to_dict(v) for k, v in sorted(report.per_city_metric.items())
        },
        "per_direction": {
            k: _group_to_dict(v) for k, v in sorted(report.per_direction.items())
        },
        "per_strategy": {
            k: _group_to_dict(v) for k, v in sorted(report.per_strategy.items())
        },
        "rolling": {
            str(w): {
                "window_days": r.window_days,
                "n_settled": r.n_settled,
                "n_wins": r.n_wins,
                "win_rate_raw": r.win_rate_raw,
                "after_cost_pnl_usd": round(r.after_cost_pnl_usd, 6),
                "ci95_lower": r.ci_lower,
                "ci95_upper": r.ci_upper,
                "clears_51pct_bar_claim": r.clears_bar_claim,
            }
            for w, r in sorted(report.rolling.items())
        },
        "suspend_candidates": report.suspend_candidates,
        "fee_coverage_gap_count": report.fee_coverage_gap_count,
        "notes": report.notes,
    }


def _fmt_rate(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100.0:.1f}%"


def _fmt_signed(value: float) -> str:
    return f"{value:+.2f}"


def report_to_markdown(report: SettlementGuardReport) -> str:
    """Human artifact: a dated markdown scorecard."""
    j = report_to_json(report)
    o = report.overall
    lines: list[str] = []
    lines.append(f"# Settlement Guard Report — {report.generated_at_utc}")
    lines.append("")
    lines.append(
        f"**GOAL:** stable AFTER-COST settlement win-rate > "
        f"{GOAL_WIN_RATE_BAR * 100:.0f}% on traded markets."
    )
    lines.append("")

    # GOAL line(s).
    lines.append("## GOAL line — rolling after-cost win-rate vs 51% bar")
    lines.append("")
    lines.append("| window | n | wins | win-rate | 95% CI | after-cost PnL | clears 51%? |")
    lines.append("|---|---|---|---|---|---|---|")
    for w in sorted(report.rolling.keys()):
        r = report.rolling[w]
        claim = r.clears_bar_claim
        claim_str = "—" if claim is None else ("YES" if claim else "no")
        wr = "n/a" if r.n_settled < MIN_N_FOR_POINT_CLAIM else _fmt_rate(r.win_rate_raw)
        ci = f"[{r.ci_lower * 100:.1f}%, {r.ci_upper * 100:.1f}%]"
        lines.append(
            f"| {w}d | {r.n_settled} | {r.n_wins} | {wr} | {ci} | "
            f"${_fmt_signed(r.after_cost_pnl_usd)} | {claim_str} |"
        )
    lines.append("")
    if o.n_settled < MIN_N_FOR_POINT_CLAIM:
        lines.append(
            f"> Small-n honesty: n={o.n_settled} < {MIN_N_FOR_POINT_CLAIM}; "
            "win-rate shown as a CI, never a point claim."
        )
        lines.append("")

    # Overall.
    lines.append("## Overall (all settled traded fills)")
    lines.append("")
    oj = j["overall"]
    lines.append(f"- settled trades: **{oj['n_settled']}** (wins {oj['n_wins']}, losses {oj['n_losses']})")
    lines.append(f"- win-rate: **{_fmt_rate(oj['win_rate'])}** (raw {_fmt_rate(oj['win_rate_raw'])}), 95% CI [{oj['ci95_lower']*100:.1f}%, {oj['ci95_upper']*100:.1f}%]")
    lines.append(f"- after-cost PnL: **${_fmt_signed(oj['after_cost_pnl_usd'])}** on ${oj['cost_basis_usd']:.2f} cost basis (ROI {('n/a' if oj['roi'] is None else f'{oj['roi']*100:.1f}%')})")
    if oj["mean_entry_q"] is not None:
        lines.append(
            f"- calibration: mean entry q={oj['mean_entry_q']:.3f} vs realized "
            f"win-rate={_fmt_rate(oj['win_rate_raw'])}; Brier={oj['brier']:.4f}, "
            f"log-loss={oj['log_loss']:.4f} (n_with_q={oj['n_with_entry_q']})"
        )
    else:
        lines.append("- calibration: entry q not captured on filled rows (q_live NULL) — Brier/log-loss unavailable")
    lines.append("")

    # By direction.
    lines.append("## By direction")
    lines.append("")
    lines.append("| direction | n | wins | win-rate | 95% CI | after-cost PnL |")
    lines.append("|---|---|---|---|---|---|")
    for k in sorted(report.per_direction.keys()):
        d = j["per_direction"][k]
        ci = f"[{d['ci95_lower']*100:.1f}%, {d['ci95_upper']*100:.1f}%]"
        lines.append(
            f"| {k} | {d['n_settled']} | {d['n_wins']} | {_fmt_rate(d['win_rate'])} | "
            f"{ci} | ${_fmt_signed(d['after_cost_pnl_usd'])} |"
        )
    lines.append("")

    # By city+metric.
    lines.append("## By city + metric")
    lines.append("")
    lines.append("| city|metric | n | wins | win-rate | 95% CI | after-cost PnL |")
    lines.append("|---|---|---|---|---|---|")
    for k in sorted(report.per_city_metric.keys()):
        d = j["per_city_metric"][k]
        ci = f"[{d['ci95_lower']*100:.1f}%, {d['ci95_upper']*100:.1f}%]"
        lines.append(
            f"| {k} | {d['n_settled']} | {d['n_wins']} | {_fmt_rate(d['win_rate'])} | "
            f"{ci} | ${_fmt_signed(d['after_cost_pnl_usd'])} |"
        )
    lines.append("")

    # SUSPEND candidates.
    lines.append("## Regression sentinels — SUSPEND_CANDIDATE (report-only)")
    lines.append("")
    if not report.suspend_candidates:
        lines.append("None. No city's rolling win-rate CI upper bound is below 50%.")
    else:
        lines.append("| city|metric | window | n | wins | 95% CI | reason |")
        lines.append("|---|---|---|---|---|---|")
        for s in report.suspend_candidates:
            ci = f"[{s['ci_lower']*100:.1f}%, {s['ci_upper']*100:.1f}%]"
            lines.append(
                f"| {s['city_metric']} | {s['window_days']}d | {s['n_settled']} | "
                f"{s['n_wins']} | {ci} | {s['reason']} |"
            )
    lines.append("")

    # Notes.
    if report.notes:
        lines.append("## Notes")
        lines.append("")
        for n in report.notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)


def one_line_summary(report: SettlementGuardReport) -> str:
    """The daemon-log one-liner the operator sees daily."""
    o = report.overall
    if o.n_settled == 0:
        return (
            "settlement_guard: n=0 settled fills (honest pre-first-settlement "
            "state); GOAL win-rate not yet measurable"
        )
    lo, hi = o.ci_95
    wr = (
        f"{o.win_rate * 100:.1f}%"
        if o.win_rate is not None
        else f"n={o.n_settled}<{MIN_N_FOR_POINT_CLAIM} (CI [{lo*100:.1f}%,{hi*100:.1f}%])"
    )
    suspend = (
        f" SUSPEND_CANDIDATES={len(report.suspend_candidates)}"
        if report.suspend_candidates
        else ""
    )
    return (
        f"settlement_guard: n={o.n_settled} settled, win-rate={wr}, "
        f"after-cost PnL=${o.after_cost_pnl_usd:+.2f} "
        f"(bar={GOAL_WIN_RATE_BAR*100:.0f}%){suspend}"
    )


# ---------------------------------------------------------------------------
# Orchestration: read graded truth, build report, write artifacts
# ---------------------------------------------------------------------------

def run_settlement_guard_report(
    *,
    now_utc: Optional[datetime] = None,
    json_path: Optional[str] = None,
    markdown_dir: Optional[str] = None,
    world_conn: Optional[sqlite3.Connection] = None,
) -> SettlementGuardReport:
    """Run the full daily pass: grade fills → build report → write artifacts.

    Read-only over graded tables. Idempotent (re-running the same day overwrites
    the same dated artifacts with identical content). Never raises on empty data
    — n=0 produces a valid report.

    Args:
        now_utc: report timestamp (default: utcnow).
        json_path: machine artifact path (default: state/settlement_guard_report.json).
        markdown_dir: dated-markdown dir (default: docs/evidence/settlement_guard/).
        world_conn: optional pre-ATTACHed WORLD+forecasts connection (tests).
            When None, opens via open_world_with_forecasts (read-only forecasts).

    Returns the SettlementGuardReport.
    """
    import pathlib

    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)

    if world_conn is not None:
        fills = load_graded_fills(world_conn)
    else:
        from src.cron.settlement_attribution import open_world_with_forecasts

        with open_world_with_forecasts(write_class="bulk") as conn:
            fills = load_graded_fills(conn)

    report = build_report(fills, now_utc=now_utc)

    # One-line INFO summary for the daemon log (operator sees it daily).
    logger.info(one_line_summary(report))

    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent

    # Machine artifact.
    jp = pathlib.Path(json_path) if json_path else repo_root / "state" / "settlement_guard_report.json"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(report_to_json(report), indent=2, sort_keys=True))

    # Human artifact (dated markdown).
    md_dir = (
        pathlib.Path(markdown_dir)
        if markdown_dir
        else repo_root / "docs" / "evidence" / "settlement_guard"
    )
    md_dir.mkdir(parents=True, exist_ok=True)
    md_name = f"{now_utc.date().isoformat()}_settlement_guard.md"
    (md_dir / md_name).write_text(report_to_markdown(report))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv: Optional[list[str]] = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Daily 守護 settlement-guard report: grades executed fills "
        "against VERIFIED settlement truth (via grade_receipt), computes "
        "after-cost win-rate vs the 51% GOAL bar with binomial CI, flags "
        "SUSPEND_CANDIDATE cities (report-only). Read-only.",
    )
    parser.add_argument("--json-path", default=None)
    parser.add_argument("--markdown-dir", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    report = run_settlement_guard_report(
        json_path=args.json_path, markdown_dir=args.markdown_dir,
    )
    print(one_line_summary(report))


if __name__ == "__main__":
    _cli()
