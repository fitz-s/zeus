# Created: 2026-06-24
# Last audited: 2026-06-24
# Authority basis: fill-realization bottleneck remediation (/tmp/fill_realization.md
#   §1-5, verified against src/engine/event_reactor_adapter.py + mode_consistent_ev.py
#   2026-06-24) + operator brief 2026-06-24 ("calibrate maker p_fill to realized-by-
#   spread band; no caps/floors/min-edge/taker-forcing") + coordinator refinement
#   2026-06-24 ("empirical-Bayes Beta-Binomial shrinkage, NOT an ad-hoc 0.19 fallback").
#   Finishes the recalibration loop the codebase already specifies at
#   mode_consistent_ev.py:60-73 ("when fill_tracker accumulates N facts at a band the
#   settlement loop MUST replace this prior with the measured conditional rate").
"""Realized-by-spread-band maker fill calibration (read-only).

WHY THIS EXISTS
---------------
The maker fill-probability prior fed into ``EV_maker = p_fill_maker x (q_fill_adj -
limit)`` (mode_consistent_ev.py:398-400) is a single all-band scalar
(``MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE = 0.19``). The realized fill rate is
steeply spread-dependent: ~24% at relative spread <= 0.10, collapsing to ~0% at
0.10-0.25 and ~4% at 0.25-0.50 (7-day venue_order_facts rollup). Because the
actionability gate is ``trade_score = EV_maker > 0`` and a CONSTANT positive p_fill
only SCALES EV, it can never zero out a positive ``(q_fill_adj - limit)``: +edge orders
in 0%-fill bands pass the gate, rest, and expire. This module supplies an HONEST
spread-conditioned probability so the EXISTING gate rejects un-fillable rests.

THE ESTIMATOR (empirical-Bayes Beta-Binomial shrinkage)
-------------------------------------------------------
Per spread band, the fill probability is the posterior mean of a Beta-Binomial:

    p_fill = (matched + alpha) / (total + alpha + beta)

where the Beta(alpha, beta) prior encodes the GLOBAL POOLED fill rate as its mean
(``m = alpha / (alpha + beta) = pooled_matched / pooled_total``) and a modest
pseudo-count as its strength (``alpha + beta = pseudo_count``, a few dozen). This
shrinks a sparse band SMOOTHLY toward the global rate while letting a well-sampled
band reflect its OWN rate:

  * A band with many samples and ~0% realized -> posterior ~ 0 (rejects un-fillable
    orders at the unchanged gate) — NO hard 0.19 cliff.
  * A band with few samples -> posterior sits near the global prior (no cliff, no
    cherry-picked fallback constant).
  * A well-sampled tight band at ~24% -> posterior ~ 0.24 (keeps trading).

LAW COMPLIANCE
--------------
This is purely an honest probability. There is NO cap, floor, min-edge, allowlist,
throttle, or taker-forcing. The rejection of an un-fillable rest comes ONLY from
``p_fill_band x edge <= 0`` at the pre-existing ``trade_score <= 0`` gate
(event_reactor_adapter.py:3516). Favorites (high realized fill) are correctly
UP-weighted; nothing artificial is added.

Read-only: SELECT-only over venue_order_facts -> venue_commands ->
executable_market_snapshots. No writes, no settings reads (the caller supplies the
fallback prior + provenance and the operator-tunable window/pseudo-count).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

# Relative-spread band UPPER edges, matching the cutpoints proven in the 7-day
# realized rollup (/tmp/fill_realization.md §2). A spread <= 0.10 is the tight,
# ~24%-fill band; 0.10-0.25 is the proven ~0%-fill band; etc. A spread above the
# last edge falls in the open ">last" band. These are bucket boundaries only — NOT
# a min-edge or price floor (the law against floors holds; rejection is purely via
# p_fill x edge <= 0).
SPREAD_BAND_EDGES: tuple[float, float, float, float] = (0.10, 0.25, 0.50, 1.00)

_UNMEASURABLE = "unmeasurable"

# Default trailing window for the realized rollup. Operator-tunable via the caller.
DEFAULT_WINDOW_DAYS = 7.0
# Default Beta prior strength (alpha + beta). A few dozen pseudo-observations: enough
# to stabilize a sparse band toward the global rate, light enough that a band with a
# couple hundred samples dominates its own prior. Operator-tunable via the caller.
DEFAULT_PSEUDO_COUNT = 24.0

# Provenance tag prefix for a band rate sourced from realized facts.
_MEASURED_SOURCE = "venue_order_facts_realized_by_spread_eb"

# Terminal/observed states that count an order as FILLED (any-fill, order-level).
_FILLED_STATES = ("MATCHED", "PARTIALLY_MATCHED")


def spread_band_label(rel_spread: float | None) -> str:
    """Bucket a relative spread into a band key.

    ``None``, non-finite, or non-positive spreads are ``"unmeasurable"`` (the
    two-sided book is absent/degenerate — the caller falls back to the prior).
    Otherwise the label is the band's upper edge formatted to 2 dp, or ``">1.00"``
    for the open top band.
    """
    if rel_spread is None:
        return _UNMEASURABLE
    try:
        s = float(rel_spread)
    except (TypeError, ValueError):
        return _UNMEASURABLE
    if not math.isfinite(s) or s < 0.0:
        return _UNMEASURABLE
    for edge in SPREAD_BAND_EDGES:
        if s <= edge:
            return f"<={edge:.2f}"
    return f">{SPREAD_BAND_EDGES[-1]:.2f}"


def beta_binomial_posterior_mean(
    *, matched: int, total: int, prior_mean: float, pseudo_count: float
) -> float:
    """Empirical-Bayes posterior mean of a Beta-Binomial fill rate.

    ``p_fill = (matched + alpha) / (total + alpha + beta)`` with
    ``alpha = prior_mean * pseudo_count`` and ``beta = (1 - prior_mean) * pseudo_count``
    (so ``alpha + beta = pseudo_count`` and the prior MEAN is ``prior_mean``).

    total=0 returns ``prior_mean`` exactly. The result is clamped to [0, 1].
    """
    m = min(1.0, max(0.0, float(prior_mean)))
    kappa = max(0.0, float(pseudo_count))
    alpha = m * kappa
    beta = (1.0 - m) * kappa
    denom = float(total) + alpha + beta
    if denom <= 0.0:
        return m
    p = (float(matched) + alpha) / denom
    return min(1.0, max(0.0, p))


@dataclass(frozen=True)
class BandRate:
    """Realized any-fill rollup for one spread band (distinct orders)."""

    label: str
    matched: int
    total: int

    @property
    def raw_rate(self) -> float | None:
        return (self.matched / self.total) if self.total > 0 else None


@dataclass(frozen=True)
class BandRateTable:
    """Per-band realized rollup + the global pooled rate (the EB prior mean)."""

    bands: dict[str, BandRate]
    pooled_matched: int
    pooled_total: int
    window_days: float

    @property
    def pooled_rate(self) -> float | None:
        return (self.pooled_matched / self.pooled_total) if self.pooled_total > 0 else None


def realized_maker_fill_rate_by_spread_band(
    conn: sqlite3.Connection,
    *,
    window_days: float = DEFAULT_WINDOW_DAYS,
) -> BandRateTable:
    """Roll ``venue_order_facts`` up to distinct orders, bucket by entry spread.

    Method (matches /tmp/fill_realization.md §2):
      * ``venue_order_facts`` is append-only (one row per observed transition);
        roll to the DISTINCT ``venue_order_id`` — filled iff it EVER reached
        MATCHED or PARTIALLY_MATCHED.
      * Join to the entry book via ``command_id -> venue_commands.snapshot_id ->
        executable_market_snapshots.{orderbook_top_bid, orderbook_top_ask}``.
      * relative spread = ``(ask - bid) / ((ask + bid) / 2)``.

    Read-only (SELECT-only). Returns per-band counts + the global pooled counts.
    """
    cutoff_days = max(0.0, float(window_days))
    rows = conn.execute(
        """
        WITH rolled AS (
            SELECT
                f.venue_order_id AS oid,
                f.command_id AS command_id,
                MAX(CASE WHEN f.state IN ('MATCHED','PARTIALLY_MATCHED')
                         THEN 1 ELSE 0 END) AS ever_filled
            FROM venue_order_facts f
            WHERE f.observed_at >= datetime('now', ?)
            GROUP BY f.venue_order_id
        )
        SELECT
            r.ever_filled AS ever_filled,
            s.orderbook_top_bid AS bid,
            s.orderbook_top_ask AS ask
        FROM rolled r
        JOIN venue_commands c ON c.command_id = r.command_id
        JOIN executable_market_snapshots s ON s.snapshot_id = c.snapshot_id
        """,
        (f"-{cutoff_days} days",),
    ).fetchall()

    bands: dict[str, list[int]] = {}  # label -> [matched, total]
    pooled_matched = 0
    pooled_total = 0
    for ever_filled, bid_raw, ask_raw in rows:
        bid = _coerce_float(bid_raw)
        ask = _coerce_float(ask_raw)
        rel = _relative_spread(bid, ask)
        label = spread_band_label(rel)
        if label == _UNMEASURABLE:
            # Degenerate one-sided/inverted book: excluded from band rates AND the
            # pooled prior (an unmeasurable spread carries no calibration signal).
            continue
        filled = 1 if int(ever_filled) == 1 else 0
        slot = bands.setdefault(label, [0, 0])
        slot[0] += filled
        slot[1] += 1
        pooled_matched += filled
        pooled_total += 1

    band_rates = {
        label: BandRate(label=label, matched=mt, total=tt)
        for label, (mt, tt) in bands.items()
    }
    return BandRateTable(
        bands=band_rates,
        pooled_matched=pooled_matched,
        pooled_total=pooled_total,
        window_days=cutoff_days,
    )


def maker_fill_probability_for_spread(
    rel_spread: float | None,
    *,
    conn: sqlite3.Connection | None,
    fallback_prior: float,
    fallback_source: str,
    window_days: float = DEFAULT_WINDOW_DAYS,
    pseudo_count: float = DEFAULT_PSEUDO_COUNT,
    _table: BandRateTable | None = None,
) -> tuple[float, str]:
    """Spread-conditioned maker p_fill via EB Beta-Binomial shrinkage.

    Returns ``(p_fill, source_tag)``.

    The EB prior MEAN is the global pooled realized fill rate; the band's posterior
    mean is ``(matched + alpha) / (total + alpha + beta)``. When the spread is
    unmeasurable OR no realized global evidence exists (cannot form a prior mean),
    the caller's ``fallback_prior``/``fallback_source`` are returned unchanged — the
    pre-existing static behavior, so the wiring degrades gracefully on a cold DB.

    ``_table`` is an injection hook for callers that already computed the rollup
    (e.g. a per-cycle cache); when ``None`` the rollup is computed from ``conn``.
    When both ``_table`` and ``conn`` are ``None`` there is nothing to condition on
    and the static fallback is returned.
    """
    label = spread_band_label(rel_spread)
    if label == _UNMEASURABLE:
        return float(fallback_prior), str(fallback_source)

    if _table is not None:
        table = _table
    elif conn is not None:
        table = realized_maker_fill_rate_by_spread_band(conn, window_days=window_days)
    else:
        # No precomputed table and no connection: nothing to condition on.
        return float(fallback_prior), str(fallback_source)
    pooled = table.pooled_rate
    if pooled is None:
        # No realized evidence at all -> no EB prior mean -> static fallback.
        return float(fallback_prior), str(fallback_source)

    band = table.bands.get(label)
    matched = band.matched if band is not None else 0
    total = band.total if band is not None else 0
    p_fill = beta_binomial_posterior_mean(
        matched=matched, total=total, prior_mean=pooled, pseudo_count=pseudo_count
    )
    source = (
        f"{_MEASURED_SOURCE}:band={label}:n={total}:k={matched}"
        f":m0={pooled:.4f}:kappa={pseudo_count:.0f}:win={table.window_days:.0f}d:basis=MEASURED"
    )
    return p_fill, source


def _coerce_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)  # snapshot stores top_bid/top_ask as TEXT
    except (TypeError, ValueError):
        return None


def _relative_spread(bid: float | None, ask: float | None) -> float | None:
    """(ask - bid) / mid; None for an absent/degenerate two-sided book.

    Mirrors src.strategy.live_inference.mode_consistent_ev.relative_spread so the
    band labeling matches the EV path's own spread definition.
    """
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    if mid <= 0.0:
        return None
    return (ask - bid) / mid
