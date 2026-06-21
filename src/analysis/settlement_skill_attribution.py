# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis:
#   - Operator skill-vs-luck law 2026-06-12 (verbatim): "wu预测92不是结算在92就算赢了
#     说明这是一单完全运气获胜跟我们的系统无关 甚至会假装我们的系统正常因为'盈利了'
#     昨天3单全部刚好踩在结算哪一个温度上就已经说明问题". A LUCKY win masquerades as
#     system health and poisons the learning loop; the >51% settlement win-rate goal
#     must count SKILL, not luck.
#   - Settlement-grading SPINE (one-builder law): reuses
#     src.contracts.graded_receipt.grade_receipt (Direction Law + unit antibody +
#     BinKind membership). NO parallel grader.
#   - Join pattern + bin construction reused from
#     src.analysis.settlement_guard_report.load_graded_fills and
#     src.cron.settlement_attribution.open_world_with_forecasts (WORLD main +
#     forecasts ATTACHed read-only, INV-37).
#   - Market-implied probability semantic reused from
#     src.strategy.live_inference.market_anchor (the all-in execution price IS the
#     market's implied probability of the held token paying).
"""settlement_skill_attribution — grade every settled position into a skill category.

WHY THIS EXISTS
---------------
A profitable settlement is NOT evidence the system works. The operator's 06-12
losses landed EXACTLY on the settled bin 3/3 (the market priced those bins 2-2.5x
our q and won) — systematic miscalibration. Conversely a win where our own
freshest data DISAGREED with the position (Denver: our fresh NBM hourly said 90.0,
so our NO on 90-91 should lose, but the stale 0.79 posterior held and it happened
to win) is a LUCKY win that tells us nothing about skill. Counting either as a
plain win/loss poisons the learning loop and lets a lucky win fake system health.

This organ grades each SETTLED position into a typed category by comparing THREE
quantities:
  (1) our position direction + traded bin,
  (2) our DECISION-TIME q (q_live on the fill) AND the FRESHEST data available at
      settlement-eve (the latest forecast_posteriors cycle for the family),
  (3) the settled outcome (grade_receipt) + the market's final price (the fill
      price IS the market-implied probability).

THE FIVE CATEGORIES
-------------------
  SKILL_WIN          won AND our fresh-data q supported the position.
  LUCKY_WIN          won BUT our own freshest data disagreed (Denver-if-92) —
                     a MISS in skill accounting.
  SKILL_LOSS         lost but the position was right under fresh data (honest
                     variance).
  MISCALIBRATED_LOSS lost AND the market priced the settled bin a large factor
                     above our q AND the market was right (the 3-loss shape).
  STALE_DECISION     the decision-time posterior was older than the family
                     freshness budget / a strictly-fresher cycle existed
                     unconsumed (born-stale gets its own brand regardless of
                     outcome).

The skill win-rate that matters:
    SKILL_WIN / (SKILL_WIN + LUCKY_WIN + SKILL_LOSS + MISCALIBRATED_LOSS)
STALE_DECISION rows are excluded from the denominator (the decision was born
stale — its outcome carries no skill signal either way).

THRESHOLD DERIVATION (no bare magic numbers)
--------------------------------------------
  "market disagreed by a large factor" = market_in_bin_prob / our_q_in_bin >=
  LARGE_FACTOR. LARGE_FACTOR = 2.0 is the LOWER edge of the operator's directly
  observed 2.0-2.5x band on the 06-12 losses (derivation_note records this on
  every row). It is a data-anchored boundary, not an invented constant: the band
  came from the three real settled losses, and the lower edge is the conservative
  (fewer false MISCALIBRATED) choice.

  "fresh data supports the position" = the freshest posterior's q for the held
  token > 0.5 (a buy_no position is supported when fresh P(settle OUT of bin) >
  0.5; a buy_yes when fresh P(settle IN bin) > 0.5). 0.5 is the direction-neutral
  decision boundary, not a tunable.

HONESTY DISCIPLINE
------------------
- Read-only over graded/forecast tables; the ONLY write is the
  settlement_attribution row (sole writer).
- Idempotent per position (UNIQUE(position_id) + skip-if-present).
- A position with no VERIFIED settlement is NOT graded (never fabricated).
- A position whose fresh-data lane is absent is graded on the data we DO have,
  with fresh_q_supports_position recorded NULL — never guessed.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Lower edge of the operator's directly-observed 2.0-2.5x market-vs-q band on the
# 06-12 settled losses. A MISCALIBRATED_LOSS requires the market to have priced
# the settled bin at least this many times our q (and to have been right).
LARGE_FACTOR: float = 2.0
LARGE_FACTOR_DERIVATION = (
    "LARGE_FACTOR=2.0 = lower edge of operator-observed 2.0-2.5x market/q band on "
    "the 06-12 three settled losses (the conservative edge: fewer false "
    "MISCALIBRATED). market_in_bin_prob = 1 - avg_fill_price (fill price IS the "
    "market-implied prob of the held token paying)."
)

# Direction-neutral support boundary for the freshest-data q.
SUPPORT_BOUNDARY: float = 0.5

# Default family freshness budget (hours). A decision posterior older than this,
# OR a strictly-fresher posterior cycle existing before the decision, brands the
# position STALE_DECISION. 6.0h = one full forecast cycle interval (00/06/12/18Z);
# a decision consuming a cycle already superseded by the next 6-hourly cycle is
# born stale. Recorded on each row as freshness_budget_hours.
DEFAULT_FRESHNESS_BUDGET_HOURS: float = 6.0


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillGrade:
    """The skill-vs-luck verdict for one settled position.

    Carries the THREE quantities with provenance so every downstream reader (the
    skill win-rate, the report) reads the category and its inputs from HERE.
    """

    position_id: str
    condition_id: Optional[str]
    city: Optional[str]
    target_date: Optional[str]
    metric: Optional[str]
    direction: str
    traded_bin_label: str
    category: str
    won: bool
    counts_as_skill_win: bool
    # Quantity 1 — our position economics.
    avg_fill_price: Optional[float]
    q_live: Optional[float]
    q_lcb_5pct: Optional[float]
    q_in_bin: Optional[float]
    market_in_bin_prob: Optional[float]
    market_q_ratio: Optional[float]
    # Quantity 2a — decision-time posterior provenance.
    decision_posterior_id: Optional[str]
    decision_posterior_computed_at: Optional[str]
    decision_posterior_age_hours: Optional[float]
    # Quantity 2b — freshest settlement-eve data.
    fresh_posterior_id: Optional[str]
    fresh_posterior_computed_at: Optional[str]
    fresh_q_supports_position: Optional[bool]
    fresh_q_in_bin: Optional[float]
    fresh_input_identity: Optional[str]
    fresh_input_age_hours: Optional[float]
    # Quantity 3 — settlement + market truth.
    settled_value: float
    settlement_unit: str
    settled_in_bin: bool
    settled_at: Optional[str]
    # Staleness provenance.
    freshness_budget_hours: float
    fresher_cycle_existed_at_decision: Optional[bool]
    # Derivation.
    large_factor_threshold: float
    derivation_note: str
    rationale: str


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp to an aware UTC datetime, or None."""
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(str(ts)[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_between(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    """Hours from a to b (b - a), or None if either is missing."""
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# q-in-bin derivation (direction-aware)
# ---------------------------------------------------------------------------

def _q_in_bin_from_position(direction: str, q_held: Optional[float]) -> Optional[float]:
    """Our probability that the settle lands IN the traded bin.

    q_held is q_live — the probability our position's held token PAYS (the
    edge-side probability the reactor captured at entry). The Direction Law maps
    that to P(settle in bin):
      - buy_yes pays when settled_in_bin → q_in_bin = q_held.
      - buy_no  pays when NOT settled_in_bin → q_in_bin = 1 - q_held.
    Returns None when q_held is absent (never guessed).
    """
    if q_held is None:
        return None
    q = float(q_held)
    if direction == "buy_no":
        return max(0.0, min(1.0, 1.0 - q))
    return max(0.0, min(1.0, q))


def _market_in_bin_prob(direction: str, avg_fill_price: Optional[float]) -> Optional[float]:
    """Market-implied probability the settle lands IN the traded bin.

    The all-in execution price IS the market's implied probability the held token
    pays (market_anchor semantic). Map via the Direction Law:
      - buy_yes: the held YES token pays iff settled_in_bin → market P(in_bin) = price.
      - buy_no:  the held NO token pays iff NOT settled_in_bin → market P(in_bin) = 1 - price.
    Returns None when the fill price is absent.
    """
    if avg_fill_price is None:
        return None
    p = float(avg_fill_price)
    if direction == "buy_no":
        return max(0.0, min(1.0, 1.0 - p))
    return max(0.0, min(1.0, p))


# ---------------------------------------------------------------------------
# The grader (pure — testable with synthetic inputs)
# ---------------------------------------------------------------------------

def grade_position(
    *,
    position_id: str,
    direction: str,
    traded_bin_label: str,
    won: bool,
    settled_in_bin: bool,
    settled_value: float,
    settlement_unit: str,
    settled_at: Optional[str],
    condition_id: Optional[str] = None,
    city: Optional[str] = None,
    target_date: Optional[str] = None,
    metric: Optional[str] = None,
    avg_fill_price: Optional[float] = None,
    q_live: Optional[float] = None,
    q_lcb_5pct: Optional[float] = None,
    decision_time: Optional[str] = None,
    decision_posterior_id: Optional[str] = None,
    decision_posterior_computed_at: Optional[str] = None,
    fresh_posterior_id: Optional[str] = None,
    fresh_posterior_computed_at: Optional[str] = None,
    fresh_q_held: Optional[float] = None,
    fresh_input_identity: Optional[str] = None,
    fresher_cycle_existed_at_decision: Optional[bool] = None,
    decision_q_in_bin: Optional[float] = None,
    freshness_budget_hours: float = DEFAULT_FRESHNESS_BUDGET_HOURS,
    large_factor: float = LARGE_FACTOR,
) -> SkillGrade:
    """Grade ONE settled position into a skill category.

    All settlement truth (won / settled_in_bin) MUST come from grade_receipt
    upstream — this function never re-derives win/loss; it only classifies the
    SKILL quality of an already-graded outcome by comparing the three quantities.

    fresh_q_held: the freshest posterior's q for the HELD token (same direction
    semantic as q_live). When provided it drives fresh-data support; when None,
    fresh support is unknown (recorded NULL) and the decision falls back to the
    decision-time q.

    decision_q_in_bin: our DECISION-TIME posterior's P(settle IN bin), used as
    "our q for the settled bin" when q_live is absent on the fill row. The
    operator's framing IS the forecast q for the bin, not just the captured
    fill-row q_live — and q_live is NULL on every live profit-audit row today
    (data-provenance gap: the executor does not persist q_live on the projection).
    Falling back to the posterior keeps the MISCALIBRATED ratio computable from
    the genuine system belief. q_live (when present) takes precedence.
    """
    # --- Quantity 1 derivations ---
    # Our P(settle in bin): prefer the captured fill-row q_live; fall back to the
    # decision-time posterior's in-bin mass when q_live is absent (the live state).
    q_in_bin = _q_in_bin_from_position(direction, q_live)
    if q_in_bin is None and decision_q_in_bin is not None:
        q_in_bin = max(0.0, min(1.0, float(decision_q_in_bin)))
    market_in_bin = _market_in_bin_prob(direction, avg_fill_price)
    market_q_ratio: Optional[float] = None
    if market_in_bin is not None and q_in_bin is not None and q_in_bin > 0.0:
        market_q_ratio = market_in_bin / q_in_bin

    # --- Quantity 2 derivations (ages + fresh support) ---
    dt_decision = _parse_ts(decision_time)
    dt_decision_post = _parse_ts(decision_posterior_computed_at)
    dt_fresh_post = _parse_ts(fresh_posterior_computed_at)
    dt_settled = _parse_ts(settled_at)

    decision_posterior_age_hours = _hours_between(dt_decision_post, dt_decision)
    fresh_input_age_hours = _hours_between(dt_fresh_post, dt_settled)

    fresh_q_in_bin = _q_in_bin_from_position(direction, fresh_q_held)
    # Fresh support: the freshest posterior's q for the HELD token > 0.5.
    fresh_supports: Optional[bool]
    if fresh_q_held is None:
        fresh_supports = None
    else:
        fresh_supports = float(fresh_q_held) > SUPPORT_BOUNDARY

    # --- STALENESS gate (born-stale brand, evaluated FIRST) ---
    # A decision is born stale if a strictly-fresher cycle existed before it, OR
    # the consumed posterior was older than the freshness budget at decision time.
    born_stale = False
    if fresher_cycle_existed_at_decision is True:
        born_stale = True
    elif (
        decision_posterior_age_hours is not None
        and decision_posterior_age_hours > freshness_budget_hours
    ):
        born_stale = True

    # --- Skill-support signal: prefer FRESH data; fall back to decision-time q ---
    # "position supported" = the evidence says the held token should pay.
    if fresh_supports is not None:
        position_supported = fresh_supports
        support_source = "fresh_posterior"
    elif q_in_bin is not None:
        # No fresh lane: use the decision-time q for the held token via q_in_bin.
        # buy_no supported when P(in_bin) < 0.5 (NO pays out of bin); buy_yes when > 0.5.
        if direction == "buy_no":
            position_supported = q_in_bin < SUPPORT_BOUNDARY
        else:
            position_supported = q_in_bin > SUPPORT_BOUNDARY
        support_source = "decision_q"
    else:
        position_supported = None
        support_source = "none"

    # --- Categorize ---
    note = LARGE_FACTOR_DERIVATION + (
        f" freshness_budget={freshness_budget_hours:.1f}h."
    )

    if born_stale:
        category = "STALE_DECISION"
        counts_as_skill_win = False
        rationale = (
            f"born-stale: "
            + (
                "a strictly-fresher posterior cycle existed before the decision"
                if fresher_cycle_existed_at_decision
                else f"decision posterior age "
                f"{decision_posterior_age_hours:.1f}h > budget {freshness_budget_hours:.1f}h"
            )
            + "; outcome carries no skill signal (excluded from skill denominator)."
        )
    elif won:
        # WON: SKILL if the evidence supported the position, else LUCKY.
        if position_supported is True:
            category = "SKILL_WIN"
            counts_as_skill_win = True
            rationale = (
                f"won AND {support_source} supported the position "
                f"(held-token q > {SUPPORT_BOUNDARY}); real skill."
            )
        elif position_supported is False:
            category = "LUCKY_WIN"
            counts_as_skill_win = False
            rationale = (
                f"won BUT {support_source} DISAGREED with the position "
                f"(held-token q <= {SUPPORT_BOUNDARY}) — the Denver-if-92 shape; "
                f"a lucky win, counts as a MISS in skill accounting."
            )
        else:
            # No support evidence at all — cannot certify skill; treat as lucky
            # (conservative: an uncertifiable win does not earn skill credit).
            category = "LUCKY_WIN"
            counts_as_skill_win = False
            rationale = (
                "won but no fresh/decision q was available to certify the "
                "position — uncertifiable win earns no skill credit (counts as MISS)."
            )
    else:
        # LOST: MISCALIBRATED if the market priced the settled bin a large factor
        # above our q AND the market was right; else honest SKILL_LOSS (variance).
        #
        # "market was right" = the market leaned toward the ACTUAL outcome more
        # than we did, i.e. the sign of (market_in_bin - q_in_bin) agrees with the
        # realized settled_in_bin. When the settle landed IN bin, the market was
        # right iff it priced in-bin ABOVE our q (market_in_bin > q_in_bin); when
        # it landed OUT, iff the market priced in-bin BELOW our q. This is the
        # sign test, NOT a brittle 0.5 cutoff (the 06-12 losses had market_in_bin
        # == 0.50 exactly, which a `> 0.5` boundary wrongly excluded).
        if market_in_bin is None or q_in_bin is None:
            market_was_right = False
        elif settled_in_bin:
            market_was_right = market_in_bin > q_in_bin
        else:
            market_was_right = market_in_bin < q_in_bin
        large_disagreement = (
            market_q_ratio is not None and market_q_ratio >= large_factor
        )
        # The 3-loss shape: settled landed where the market priced high and we
        # priced low. For a buy_no loss, settled_in_bin is True and the market's
        # in-bin prob was a large multiple of ours.
        if large_disagreement and market_was_right:
            category = "MISCALIBRATED_LOSS"
            counts_as_skill_win = False
            rationale = (
                f"lost AND market priced the settled bin "
                f"{market_q_ratio:.2f}x our q (>= {large_factor:.1f}x) AND the "
                f"market was right (settled {'IN' if settled_in_bin else 'OUT of'} "
                f"the traded bin) — systematic miscalibration, the 3-loss shape."
            )
        else:
            category = "SKILL_LOSS"
            counts_as_skill_win = False
            ratio_str = (
                f"{market_q_ratio:.2f}x" if market_q_ratio is not None else "n/a"
            )
            rationale = (
                f"lost but NOT a large market/q disagreement (ratio {ratio_str} "
                f"< {large_factor:.1f}x) — honest variance, the position was "
                f"defensible under our evidence."
            )

    return SkillGrade(
        position_id=position_id,
        condition_id=condition_id,
        city=city,
        target_date=target_date,
        metric=metric,
        direction=direction,
        traded_bin_label=traded_bin_label,
        category=category,
        won=won,
        counts_as_skill_win=counts_as_skill_win,
        avg_fill_price=avg_fill_price,
        q_live=q_live,
        q_lcb_5pct=q_lcb_5pct,
        q_in_bin=q_in_bin,
        market_in_bin_prob=market_in_bin,
        market_q_ratio=market_q_ratio,
        decision_posterior_id=decision_posterior_id,
        decision_posterior_computed_at=decision_posterior_computed_at,
        decision_posterior_age_hours=decision_posterior_age_hours,
        fresh_posterior_id=fresh_posterior_id,
        fresh_posterior_computed_at=fresh_posterior_computed_at,
        fresh_q_supports_position=fresh_supports,
        fresh_q_in_bin=fresh_q_in_bin,
        fresh_input_identity=fresh_input_identity,
        fresh_input_age_hours=fresh_input_age_hours,
        settled_value=settled_value,
        settlement_unit=settlement_unit,
        settled_in_bin=settled_in_bin,
        settled_at=settled_at,
        freshness_budget_hours=freshness_budget_hours,
        fresher_cycle_existed_at_decision=fresher_cycle_existed_at_decision,
        large_factor_threshold=large_factor,
        derivation_note=note,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Bin construction (reuse settlement_guard_report's canonical numeric-range path)
# ---------------------------------------------------------------------------

def _bin_from_market_event(range_low, range_high, settlement_unit: str):
    """Delegate to the canonical numeric-range bin builder in settlement_guard_report."""
    from src.analysis.settlement_guard_report import _bin_from_market_event as _bld
    return _bld(range_low, range_high, settlement_unit)


# ---------------------------------------------------------------------------
# Data loading + the THREE quantities from the live DBs
# ---------------------------------------------------------------------------

def _load_market_meta(world_conn: sqlite3.Connection) -> dict:
    """condition_id -> market metadata (city/target_date/metric/range)."""
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
    return market_meta


def _load_settlements(world_conn: sqlite3.Connection) -> tuple[dict, dict]:
    """(city,date,metric) -> VERIFIED settlement + settled_at."""
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
    return settlements, settled_at


def _fresh_posterior_for_family(
    world_conn: sqlite3.Connection,
    city: Optional[str],
    target_date: Optional[str],
    metric: Optional[str],
    bin_obj,
    *,
    before: Optional[str] = None,
) -> Optional[dict]:
    """Latest forecast_posteriors row for a family (the freshest settlement-eve data).

    Reads the LATEST (max computed_at) posterior for (city, target_date, metric),
    parses its q_json, and extracts the q-mass for the traded bin label. When
    ``before`` is given, only rows computed at or before it are considered (used
    to detect the decision-time posterior). Returns None when the family has no
    posterior or the bin's mass cannot be located (never fabricated).

    forecast_posteriors lives on the ATTACHed 'forecasts' DB. q_json is a JSON
    mapping of bin_label -> probability (the YES/in-bin mass per bin).
    """
    params: list = [city, target_date, (metric or "high")]
    time_clause = ""
    if before:
        time_clause = " AND computed_at <= ?"
        params.append(before)
    row = world_conn.execute(
        f"""
        SELECT posterior_id, computed_at, q_json
        FROM forecasts.forecast_posteriors
        WHERE city = ? AND target_date = ? AND temperature_metric = ?
        {time_clause}
        ORDER BY computed_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        return None
    posterior_id, computed_at, q_json = row
    in_bin_yes = _bin_yes_mass_from_q_json(q_json, bin_obj)
    return {
        "posterior_id": str(posterior_id),
        "computed_at": computed_at,
        "in_bin_yes": in_bin_yes,  # P(settle IN bin) per this posterior, or None
    }


def _bin_yes_mass_from_q_json(q_json, bin_obj) -> Optional[float]:
    """Extract P(settle IN the traded bin) from a posterior q_json payload.

    q_json maps bin_label -> probability. We match the traded bin by its label
    first; if absent, we sum the mass of any bin whose numeric center lands inside
    the traded bin's [low, high] range. Returns None when nothing matches (the
    fresh lane is then 'absent' for this position — recorded NULL, never guessed).
    """
    if not q_json:
        return None
    try:
        payload = json.loads(q_json) if isinstance(q_json, str) else q_json
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    # Some payloads nest under 'q' or 'bins'; accept either shape.
    mapping = payload
    for key in ("q", "bins", "probabilities"):
        if key in payload and isinstance(payload[key], dict):
            mapping = payload[key]
            break
    # Exact label match.
    if bin_obj is not None and bin_obj.label in mapping:
        try:
            return float(mapping[bin_obj.label])
        except (TypeError, ValueError):
            pass
    # Numeric containment fallback.
    if bin_obj is None:
        return None
    from src.data.market_scanner import _parse_temp_range

    total = 0.0
    matched = False
    for label, prob in mapping.items():
        parsed = _parse_temp_range(str(label))
        if parsed is None or parsed == (None, None):
            continue
        lo, hi = parsed
        center = None
        if lo is not None and hi is not None:
            center = (lo + hi) / 2.0
        elif lo is not None:
            center = lo
        elif hi is not None:
            center = hi
        if center is None:
            continue
        try:
            if bin_obj.contains(center):
                total += float(prob)
                matched = True
        except Exception:  # noqa: BLE001
            continue
    return total if matched else None


# ---------------------------------------------------------------------------
# Load + grade every settled position
# ---------------------------------------------------------------------------

def _ensure_trades_attached(world_conn: sqlite3.Connection) -> bool:
    """ATTACH zeus_trades.db as 'trades' (read-only join) on the single conn.

    W3 (2026-06-20): the grader reads ``trades.position_current``, which lives in
    zeus_trades.db. INV-37 requires cross-DB access on a SINGLE connection (ATTACH),
    never an independent connection. This join is READ-ONLY (the grader's only
    write target is WORLD.settlement_attribution), so no trades write-lock is taken;
    the canonical lock order (zeus-forecasts < zeus-world < zeus_trades) is honoured
    because WORLD already holds the bulk lock and trades is attached for reads only.
    Idempotent: a no-op when 'trades' is already attached. Returns True when the
    schema is available.
    """
    from src.state.db import _zeus_trade_db_path

    attached = {row[1] for row in world_conn.execute("PRAGMA database_list").fetchall()}
    if "trades" not in attached:
        world_conn.execute("ATTACH DATABASE ? AS trades", (str(_zeus_trade_db_path()),))
    return True


def load_settled_positions(world_conn: sqlite3.Connection) -> list[SkillGrade]:
    """Grade every settled position in the real ledger into a skill category.

    W3 (2026-06-20): grades the real position ledger ``trades.position_current``
    (305 rows / 138 conditions on the live DB), NOT the 58-fill
    ``edli_live_profit_audit`` subset it formerly read. The audit subset capped the
    grader's visibility at the EDLI filled-fills it happened to record; the dollar
    ledger and far more settled positions live in ``position_current``. Joins
    position_current → forecasts.market_events (bin range) →
    forecasts.settlement_outcomes (VERIFIED). Grades win/loss via the canonical
    grade_receipt (the ONE truth function), then classifies skill quality. The
    freshest settlement-eve posterior and the decision-time posterior are looked up
    per family. q_live is absent on position_current, so grade_position falls back
    to the decision-time posterior (its documented behaviour).

    Caller must have 'forecasts' ATTACHed (open_world_with_forecasts); 'trades' is
    ATTACHed here (read-only, INV-37 single-connection).
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.types.temperature import UnitMismatchError

    _ensure_trades_attached(world_conn)
    market_meta = _load_market_meta(world_conn)
    settlements, settled_at = _load_settlements(world_conn)

    out: list[SkillGrade] = []
    for (
        position_id, condition_id, direction, entry_price, shares, created_at,
    ) in world_conn.execute(
        """
        SELECT position_id, condition_id, direction, entry_price, shares, updated_at
        FROM trades.position_current
        WHERE entry_price IS NOT NULL
          AND direction IS NOT NULL
          AND condition_id IS NOT NULL
        """
    ).fetchall():
        # position_current's per-share avg fill is ``entry_price``; ``shares`` is the
        # filled size. (There is no avg_fill_price column on this ledger.)
        audit_id = position_id
        avg_fill_price = entry_price
        filled_size = shares
        q_live = None       # not stored on position_current — posterior fallback
        q_lcb_5pct = None
        meta = market_meta.get(condition_id)
        if meta is None:
            continue
        key = (meta["city"], meta["target_date"], meta["metric"])
        s = settlements.get(key)
        if s is None:
            continue  # no VERIFIED settlement — not gradeable, never fabricated

        bin_obj = _bin_from_market_event(
            meta["range_low"], meta["range_high"], s["settlement_unit"]
        )
        if bin_obj is None:
            continue

        class _S:
            settlement_value = s["settlement_value"]
            settlement_unit = s["settlement_unit"]

        try:
            graded = grade_receipt(bin_obj, direction, _S())
        except UnitMismatchError:
            logger.warning(
                "skill_attribution: unit mismatch cid=%s city=%s bin=%s — skipped",
                condition_id, meta["city"], bin_obj.label,
            )
            continue
        except ValueError:
            continue

        # Quantity 2b — freshest posterior at settlement-eve (latest cycle).
        fresh = _fresh_posterior_for_family(
            world_conn, meta["city"], meta["target_date"], meta["metric"], bin_obj
        )
        # Quantity 2a — decision-time posterior (latest at/<= decision_time).
        decision_post = _fresh_posterior_for_family(
            world_conn, meta["city"], meta["target_date"], meta["metric"], bin_obj,
            before=created_at,
        )

        fresh_q_held = _held_q_from_in_bin(direction, fresh.get("in_bin_yes") if fresh else None)

        # A strictly-fresher cycle existed at decision iff the family's latest
        # posterior is newer than the one the decision consumed.
        fresher_existed = None
        if fresh is not None and decision_post is not None:
            d_fresh = _parse_ts(fresh.get("computed_at"))
            d_dec = _parse_ts(decision_post.get("computed_at"))
            if d_fresh is not None and d_dec is not None:
                fresher_existed = d_fresh > d_dec

        grade = grade_position(
            position_id=str(audit_id),
            direction=direction,
            traded_bin_label=bin_obj.label,
            won=graded.won,
            settled_in_bin=graded.settled_in_bin,
            settled_value=s["settlement_value"],
            settlement_unit=s["settlement_unit"],
            settled_at=settled_at.get(key),
            condition_id=condition_id,
            city=meta["city"],
            target_date=meta["target_date"],
            metric=meta["metric"],
            avg_fill_price=float(avg_fill_price),
            q_live=(float(q_live) if q_live is not None else None),
            q_lcb_5pct=(float(q_lcb_5pct) if q_lcb_5pct is not None else None),
            decision_time=created_at,
            decision_posterior_id=(decision_post.get("posterior_id") if decision_post else None),
            decision_posterior_computed_at=(decision_post.get("computed_at") if decision_post else None),
            decision_q_in_bin=(decision_post.get("in_bin_yes") if decision_post else None),
            fresh_posterior_id=(fresh.get("posterior_id") if fresh else None),
            fresh_posterior_computed_at=(fresh.get("computed_at") if fresh else None),
            fresh_q_held=fresh_q_held,
            fresh_input_identity=(
                f"forecast_posteriors:{fresh['posterior_id']}" if fresh else None
            ),
            fresher_cycle_existed_at_decision=fresher_existed,
        )
        out.append(grade)

    return out


def writeback_settlement_pnl_to_audit(world_conn: sqlite3.Connection) -> int:
    """W2 (Phase 3, 2026-06-20): write settlement-derived pnl onto audit rows.

    The realized-profit loop previously left ``pnl_usd`` / ``settlement_outcome``
    NULL on every ``edli_live_profit_audit`` row (1345/1345 on the live DB): no
    event in PROFIT_AUDIT_TRIGGER_EVENTS ever carries market-settlement pnl (the
    only settlement-ish member, ``Reconciled``, is venue-order existence
    reconciliation). This closes that wire from the settlement side.

    For every FILLED audit row on a market with a VERIFIED ``settlement_outcomes``
    row, the settled payoff is taken from the SAME ``grade_receipt`` truth function
    the grader uses (``settled_payoff = 1.0 if won else 0.0``), and::

        pnl_usd = (settled_payoff - avg_fill_price) * filled_size - fees

    ``pnl_usd`` is therefore derived from settlement payoff ONLY — never from a
    market price or win-rate (operator settlement-only-truth law). UNVERIFIED /
    absent settlements are skipped (never fabricated). The audit table is in the
    WORLD MAIN schema, so this UPDATE runs on the single ``world_conn`` (INV-37:
    no independent connection, no cross-DB write). Returns the row count written.

    Caller must have 'forecasts' ATTACHed (open_world_with_forecasts) and is
    responsible for the enclosing SAVEPOINT/commit.
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.types.temperature import UnitMismatchError

    market_meta = _load_market_meta(world_conn)
    settlements, _settled_at = _load_settlements(world_conn)

    written = 0
    for (
        audit_id, condition_id, direction, avg_fill_price, filled_size, fees,
    ) in world_conn.execute(
        """
        SELECT audit_id, condition_id, direction, avg_fill_price, filled_size, fees
        FROM edli_live_profit_audit
        WHERE filled_size > 0
          AND avg_fill_price IS NOT NULL
          AND direction IS NOT NULL
        """
    ).fetchall():
        meta = market_meta.get(condition_id)
        if meta is None:
            continue
        key = (meta["city"], meta["target_date"], meta["metric"])
        s = settlements.get(key)
        if s is None:
            continue  # no VERIFIED settlement — not gradeable, never fabricated

        bin_obj = _bin_from_market_event(
            meta["range_low"], meta["range_high"], s["settlement_unit"]
        )
        if bin_obj is None:
            continue

        class _S:
            settlement_value = s["settlement_value"]
            settlement_unit = s["settlement_unit"]

        try:
            graded = grade_receipt(bin_obj, direction, _S())
        except (UnitMismatchError, ValueError):
            continue

        settled_payoff = 1.0 if graded.won else 0.0
        fee_total = float(fees) if fees is not None else 0.0
        pnl_usd = (settled_payoff - float(avg_fill_price)) * float(filled_size) - fee_total
        settlement_outcome = "WON" if graded.won else "LOST"
        world_conn.execute(
            """
            UPDATE edli_live_profit_audit
            SET pnl_usd = ?, settlement_outcome = ?
            WHERE audit_id = ?
            """,
            (pnl_usd, settlement_outcome, audit_id),
        )
        written += 1
    return written


def _held_q_from_in_bin(direction: str, in_bin_yes: Optional[float]) -> Optional[float]:
    """Convert a posterior's P(settle in bin) to q for the HELD token.

    buy_yes holds YES (pays in-bin) → q_held = in_bin_yes.
    buy_no  holds NO  (pays out-of-bin) → q_held = 1 - in_bin_yes.
    """
    if in_bin_yes is None:
        return None
    v = max(0.0, min(1.0, float(in_bin_yes)))
    if direction == "buy_no":
        return 1.0 - v
    return v


# ---------------------------------------------------------------------------
# Persistence — the SOLE writer of settlement_attribution
# ---------------------------------------------------------------------------

def _row_exists(world_conn: sqlite3.Connection, position_id: str) -> bool:
    row = world_conn.execute(
        "SELECT 1 FROM settlement_attribution WHERE position_id = ? LIMIT 1",
        (position_id,),
    ).fetchone()
    return row is not None


def persist_grade(
    world_conn: sqlite3.Connection,
    grade: SkillGrade,
    *,
    now_utc: Optional[datetime] = None,
) -> bool:
    """Write (idempotent UPSERT) one SkillGrade row. Returns True if written.

    Idempotent per position via UNIQUE(position_id): an existing row is
    re-graded in place (ON CONFLICT DO UPDATE) so a re-run with newer settlement
    truth refreshes the verdict without duplicating. The sole writer.
    """
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)
    graded_at = now_utc.isoformat()
    attribution_id = str(uuid.uuid4())

    world_conn.execute(
        """
        INSERT INTO settlement_attribution (
            attribution_id, position_id, condition_id, city, target_date,
            temperature_metric, direction, traded_bin_label, category, won,
            counts_as_skill_win, avg_fill_price, q_live, q_lcb_5pct, q_in_bin,
            market_in_bin_prob, market_q_ratio, decision_posterior_id,
            decision_posterior_computed_at, decision_posterior_age_hours,
            fresh_posterior_id, fresh_posterior_computed_at,
            fresh_q_supports_position, fresh_q_in_bin, fresh_input_identity,
            fresh_input_age_hours, settled_value, settlement_unit, settled_in_bin,
            settled_at, freshness_budget_hours, fresher_cycle_existed_at_decision,
            large_factor_threshold, derivation_note, rationale, graded_at,
            schema_version
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(position_id) DO UPDATE SET
            category = excluded.category,
            won = excluded.won,
            counts_as_skill_win = excluded.counts_as_skill_win,
            q_in_bin = excluded.q_in_bin,
            market_in_bin_prob = excluded.market_in_bin_prob,
            market_q_ratio = excluded.market_q_ratio,
            decision_posterior_id = excluded.decision_posterior_id,
            decision_posterior_computed_at = excluded.decision_posterior_computed_at,
            decision_posterior_age_hours = excluded.decision_posterior_age_hours,
            fresh_posterior_id = excluded.fresh_posterior_id,
            fresh_posterior_computed_at = excluded.fresh_posterior_computed_at,
            fresh_q_supports_position = excluded.fresh_q_supports_position,
            fresh_q_in_bin = excluded.fresh_q_in_bin,
            fresh_input_identity = excluded.fresh_input_identity,
            fresh_input_age_hours = excluded.fresh_input_age_hours,
            settled_value = excluded.settled_value,
            settled_in_bin = excluded.settled_in_bin,
            settled_at = excluded.settled_at,
            fresher_cycle_existed_at_decision = excluded.fresher_cycle_existed_at_decision,
            rationale = excluded.rationale,
            graded_at = excluded.graded_at
        """,
        (
            attribution_id, grade.position_id, grade.condition_id, grade.city,
            grade.target_date, grade.metric, grade.direction,
            grade.traded_bin_label, grade.category, int(grade.won),
            int(grade.counts_as_skill_win), grade.avg_fill_price, grade.q_live,
            grade.q_lcb_5pct, grade.q_in_bin, grade.market_in_bin_prob,
            grade.market_q_ratio, grade.decision_posterior_id,
            grade.decision_posterior_computed_at, grade.decision_posterior_age_hours,
            grade.fresh_posterior_id, grade.fresh_posterior_computed_at,
            (None if grade.fresh_q_supports_position is None else int(grade.fresh_q_supports_position)),
            grade.fresh_q_in_bin, grade.fresh_input_identity,
            grade.fresh_input_age_hours, grade.settled_value, grade.settlement_unit,
            int(grade.settled_in_bin), grade.settled_at, grade.freshness_budget_hours,
            (None if grade.fresher_cycle_existed_at_decision is None
             else int(grade.fresher_cycle_existed_at_decision)),
            grade.large_factor_threshold, grade.derivation_note, grade.rationale,
            graded_at, SCHEMA_VERSION,
        ),
    )
    return True


# ---------------------------------------------------------------------------
# The skill win-rate read function
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillWinRate:
    """The skill-attributed win-rate (the rate that matters)."""

    skill_win: int
    lucky_win: int
    skill_loss: int
    miscalibrated_loss: int
    stale_decision: int

    @property
    def skill_denominator(self) -> int:
        """SKILL_WIN + LUCKY_WIN + SKILL_LOSS + MISCALIBRATED_LOSS (excludes STALE)."""
        return self.skill_win + self.lucky_win + self.skill_loss + self.miscalibrated_loss

    @property
    def skill_win_rate(self) -> Optional[float]:
        d = self.skill_denominator
        if d <= 0:
            return None
        return self.skill_win / d

    @property
    def naive_win_rate(self) -> Optional[float]:
        """The MISLEADING raw win-rate (counts lucky wins) — for contrast only."""
        wins = self.skill_win + self.lucky_win
        d = wins + self.skill_loss + self.miscalibrated_loss
        if d <= 0:
            return None
        return wins / d


def compute_skill_win_rate(world_conn: sqlite3.Connection) -> SkillWinRate:
    """Read the persisted grades and compute the skill-attributed win-rate."""
    counts = {
        "SKILL_WIN": 0, "LUCKY_WIN": 0, "SKILL_LOSS": 0,
        "MISCALIBRATED_LOSS": 0, "STALE_DECISION": 0,
    }
    for category, n in world_conn.execute(
        "SELECT category, COUNT(*) FROM settlement_attribution GROUP BY category"
    ).fetchall():
        if category in counts:
            counts[category] = int(n)
    return SkillWinRate(
        skill_win=counts["SKILL_WIN"],
        lucky_win=counts["LUCKY_WIN"],
        skill_loss=counts["SKILL_LOSS"],
        miscalibrated_loss=counts["MISCALIBRATED_LOSS"],
        stale_decision=counts["STALE_DECISION"],
    )


def skill_win_rate_log_line(rate: SkillWinRate) -> str:
    """The one-line INFO summary the operator sees at each grading."""
    swr = rate.skill_win_rate
    nwr = rate.naive_win_rate
    swr_s = "n/a" if swr is None else f"{swr * 100:.1f}%"
    nwr_s = "n/a" if nwr is None else f"{nwr * 100:.1f}%"
    return (
        f"settlement_skill_attribution: SKILL win-rate={swr_s} "
        f"(naive={nwr_s}) | SKILL_WIN={rate.skill_win} LUCKY_WIN={rate.lucky_win} "
        f"SKILL_LOSS={rate.skill_loss} MISCALIBRATED_LOSS={rate.miscalibrated_loss} "
        f"STALE={rate.stale_decision} (denom={rate.skill_denominator})"
    )


# ---------------------------------------------------------------------------
# Orchestration: grade every settled position + backfill
# ---------------------------------------------------------------------------

def run_settlement_skill_attribution(
    *,
    now_utc: Optional[datetime] = None,
    world_conn: Optional[sqlite3.Connection] = None,
    only_new: bool = True,
) -> dict:
    """Grade every settled position and persist the skill category (idempotent).

    Read-only over graded/forecast tables; the ONLY write is the
    settlement_attribution row (sole writer). Idempotent per position. Backfills
    every historically-settled position on first run (only_new=True skips rows
    already graded; pass only_new=False to force a full re-grade).

    Returns a stats dict: graded, skipped_existing, by_category, skill_win_rate.
    """
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)

    if world_conn is not None:
        return _run_with_conn(world_conn, now_utc=now_utc, only_new=only_new)

    from src.cron.settlement_attribution import open_world_with_forecasts

    with open_world_with_forecasts(write_class="bulk") as conn:
        return _run_with_conn(conn, now_utc=now_utc, only_new=only_new)


def _run_with_conn(
    world_conn: sqlite3.Connection,
    *,
    now_utc: datetime,
    only_new: bool,
) -> dict:
    grades = load_settled_positions(world_conn)

    graded = 0
    skipped = 0
    by_category: dict[str, int] = {}

    pnl_written = 0
    world_conn.execute("SAVEPOINT skill_attr_batch")
    try:
        for g in grades:
            if only_new and _row_exists(world_conn, g.position_id):
                skipped += 1
                continue
            persist_grade(world_conn, g, now_utc=now_utc)
            graded += 1
            by_category[g.category] = by_category.get(g.category, 0) + 1
        # W2 (2026-06-20): settlement->audit pnl writeback runs in the same batch
        # so a graded settlement and its audit-row pnl are committed atomically.
        # Always runs (independent of only_new) so re-grades refresh pnl in place.
        pnl_written = writeback_settlement_pnl_to_audit(world_conn)
        world_conn.execute("RELEASE skill_attr_batch")
    except Exception:
        world_conn.execute("ROLLBACK TO SAVEPOINT skill_attr_batch")
        logger.exception("settlement_skill_attribution batch failed; rolled back")
        raise

    try:
        world_conn.commit()
    except Exception:  # noqa: BLE001 — autocommit conns have no explicit commit
        pass

    rate = compute_skill_win_rate(world_conn)
    logger.info(skill_win_rate_log_line(rate))

    return {
        "graded": graded,
        "skipped_existing": skipped,
        "total_settled_positions": len(grades),
        "by_category": by_category,
        "skill_win_rate": rate.skill_win_rate,
        "naive_win_rate": rate.naive_win_rate,
        "skill_denominator": rate.skill_denominator,
        "settlement_pnl_written": pnl_written,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv: Optional[list[str]] = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Grade every settled position into a skill category "
        "(SKILL_WIN / LUCKY_WIN / SKILL_LOSS / MISCALIBRATED_LOSS / "
        "STALE_DECISION) and compute the skill-attributed win-rate. The sole "
        "writer of settlement_attribution.",
    )
    parser.add_argument(
        "--full-regrade", action="store_true", default=False,
        help="Re-grade ALL settled positions (only_new=False), not just new ones.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stats = run_settlement_skill_attribution(only_new=not args.full_regrade)
    print(f"settlement_skill_attribution stats: {stats}")


if __name__ == "__main__":
    _cli()
