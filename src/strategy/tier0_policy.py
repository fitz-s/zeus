"""Tier-0 live research mode: admission policy + flat-stake sizing.

Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
item 6, operator-amended 2026-08-24. Per-claim bp size is NOT a constraint —
the objective is a clean, evidence-generating research book. Standing verdict
(same plan doc): cardinal q is disqualified from sizing (paired log-loss loses
to decision-time price in every month/bucket) and rich resting makers are
toxic (-10.3pp). Tier-0 is therefore: cheap-only, taker-only, one-per-cluster,
flat-stake — a measurement instrument, not a sized bet.

Pure decision layer, mirroring the existing rejection-reason idiom in
``src/strategy/live_inference/live_admission.py`` (``*_rejection_reason(...)
-> str | None``). No DB access, no imports from src.main (see
``_load_tier0_risk_policy`` below for why): callers supply every fact —
candidate price/mode/cluster identity, cluster occupancy, bankroll, and
aggregate/drawdown state — and get back a typed verdict or None (admit).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Decision-time executable side price must be strictly below this to be
# Tier-0 eligible (plan item 6, rule 1).
TIER0_MAX_ENTRY_PRICE: float = 0.25

# Only a marketable/taker entry is admissible; a resting maker order can sit
# at or rest toward the cap without ever crossing it, which is not the same
# guarantee as "this fill actually cost < 0.25" (plan item 6, rule 2 +
# "Rich resting makers toxic" standing verdict).
TIER0_ALLOWED_EXECUTION_MODE: str = "TAKER_LIMIT"

TIER0_REJECT_FLAG_OFF = "TIER0_MODE_DISABLED"
TIER0_REJECT_PRICE_TOO_HIGH = "TIER0_MAX_ENTRY_PRICE_EXCEEDED"
TIER0_REJECT_MAKER_REST = "TIER0_MAKER_REST_DISALLOWED"
TIER0_REJECT_LIMIT_CROSSES_CAP = "TIER0_LIMIT_PRICE_ABOVE_CAP"
TIER0_REJECT_CLUSTER_OCCUPIED = "TIER0_CLUSTER_OCCUPIED"
TIER0_REJECT_AGGREGATE_CEILING = "TIER0_AGGREGATE_OPEN_LOSS_CEILING_EXCEEDED"


@dataclass(frozen=True)
class Tier0CandidateFacts:
    """The exact candidate-local facts the admission verdict depends on.

    ``execution_price``/``limit_price`` are both the decision-time side price
    the plan's rule 1/2 apply to: for a taker order they are the same value
    (the marketable price the order will cross at); ``limit_price`` is kept
    separate because the money-path threads execution price and submitted
    limit price as distinct typed fields and rule 2 explicitly bounds the
    limit ("must not cross above 0.25"), not only the expected fill.
    """

    execution_price: float
    limit_price: float
    execution_mode: str
    cluster_key: tuple[str, str]


def tier0_price_rejection_reason(
    *,
    execution_price: float | int | None,
    limit_price: float | int | None,
    max_entry_price: float = TIER0_MAX_ENTRY_PRICE,
) -> str | None:
    """Reject a candidate whose executable side price or limit crosses the cap.

    Both must be strictly below ``max_entry_price``; the limit price bound is
    the "must not cross above 0.25" half of rule 2, independent of rule 1's
    execution-price bound (a taker order's limit and expected price are
    normally equal, but this checks each explicitly so a caller cannot admit
    by passing only the friendlier of the two).
    """

    try:
        price = float(execution_price)
        limit = float(limit_price)
        cap = float(max_entry_price)
    except (TypeError, ValueError):
        return f"{TIER0_REJECT_PRICE_TOO_HIGH}:inputs=missing"
    if not all(math.isfinite(v) for v in (price, limit, cap)):
        return f"{TIER0_REJECT_PRICE_TOO_HIGH}:inputs=nonfinite"
    if cap <= 0.0 or cap >= 1.0:
        raise ValueError(f"tier0 max_entry_price must be in (0, 1), got {max_entry_price!r}")
    if price <= 0.0 or price >= 1.0:
        return f"{TIER0_REJECT_PRICE_TOO_HIGH}:execution_price={price:.4f}:range=(0,1)"
    if price >= cap:
        return f"{TIER0_REJECT_PRICE_TOO_HIGH}:execution_price={price:.4f}:cap={cap:.4f}"
    if limit >= cap:
        return f"{TIER0_REJECT_LIMIT_CROSSES_CAP}:limit_price={limit:.4f}:cap={cap:.4f}"
    return None


def tier0_execution_mode_rejection_reason(
    *,
    execution_mode: str | None,
    allowed_mode: str = TIER0_ALLOWED_EXECUTION_MODE,
) -> str | None:
    """Reject anything but a taker/marketable-limit entry. No resting maker."""

    mode = str(execution_mode or "").strip().upper()
    if mode != allowed_mode:
        return f"{TIER0_REJECT_MAKER_REST}:execution_mode={mode or 'missing'}"
    return None


def tier0_cluster_occupied_rejection_reason(
    *,
    cluster_key: tuple[str, str],
    occupied_clusters: frozenset[tuple[str, str]] | set[tuple[str, str]],
) -> str | None:
    """Reject a second ENTRY into a (city, target_date) cluster already open.

    ``cluster_key`` is ``(city, target_date)`` — deliberately coarser than the
    existing weather-family mutex, which additionally partitions by metric
    (``weather_family_id(city, target_date, metric)``): two different metrics
    for the same city/date (e.g. temp-high and temp-low) are two different
    families today but must be ONE Tier-0 diversification unit, per the plan's
    "diversification unit = city-date cluster" correction. Structurally
    deletes scale-in for Tier-0: an occupied cluster has no second admissible
    entry regardless of price/edge on the new candidate.
    """

    city, target_date = cluster_key
    if not str(city or "").strip() or not str(target_date or "").strip():
        return f"{TIER0_REJECT_CLUSTER_OCCUPIED}:cluster_key=invalid"
    if cluster_key in occupied_clusters:
        return f"{TIER0_REJECT_CLUSTER_OCCUPIED}:city={city}:target_date={target_date}"
    return None


def tier0_aggregate_ceiling_rejection_reason(
    *,
    current_open_cost_usd: float | int,
    candidate_open_cost_usd: float | int,
    conservative_settled_bankroll_usd: float | int,
    aggregate_open_loss_pct_ceiling: float,
) -> str | None:
    """Reject a candidate whose admission would push aggregate open Tier-0
    cost above ``aggregate_open_loss_pct_ceiling`` of conservative settled
    bankroll.

    "Open cost" is the sum of entry cost (max loss on a binary YES/NO leg
    held to settlement) across open Tier-0 positions, not risk-adjusted by
    edge — a Tier-0 position's max loss is its full cost by construction.
    """

    try:
        current = float(current_open_cost_usd)
        candidate = float(candidate_open_cost_usd)
        bankroll = float(conservative_settled_bankroll_usd)
        ceiling_pct = float(aggregate_open_loss_pct_ceiling)
    except (TypeError, ValueError):
        return f"{TIER0_REJECT_AGGREGATE_CEILING}:inputs=missing"
    if not all(math.isfinite(v) for v in (current, candidate, bankroll, ceiling_pct)):
        return f"{TIER0_REJECT_AGGREGATE_CEILING}:inputs=nonfinite"
    if ceiling_pct <= 0.0:
        raise ValueError(f"aggregate_open_loss_pct_ceiling must be positive, got {aggregate_open_loss_pct_ceiling!r}")
    if bankroll <= 0.0:
        return f"{TIER0_REJECT_AGGREGATE_CEILING}:bankroll={bankroll:.2f}:nonpositive"
    if current < 0.0 or candidate < 0.0:
        return f"{TIER0_REJECT_AGGREGATE_CEILING}:cost=negative"
    ceiling_usd = ceiling_pct * bankroll
    projected = current + candidate
    if projected > ceiling_usd:
        return (
            f"{TIER0_REJECT_AGGREGATE_CEILING}:projected_open_cost={projected:.2f}:"
            f"ceiling={ceiling_usd:.2f}:bankroll={bankroll:.2f}:pct={ceiling_pct:.4f}"
        )
    return None


def tier0_admission_reason(
    *,
    enabled: bool,
    candidate: Tier0CandidateFacts,
    occupied_clusters: frozenset[tuple[str, str]] | set[tuple[str, str]],
    current_open_cost_usd: float | int,
    candidate_open_cost_usd: float | int,
    conservative_settled_bankroll_usd: float | int,
    aggregate_open_loss_pct_ceiling: float,
    max_entry_price: float = TIER0_MAX_ENTRY_PRICE,
    allowed_execution_mode: str = TIER0_ALLOWED_EXECUTION_MODE,
) -> str | None:
    """Full Tier-0 entry-admission verdict. None = admit.

    ``enabled=False`` (the ``tier0_research_mode`` flag is off) is a NOOP —
    every other check is skipped and the caller's ordinary admission path
    runs unchanged. This function does not know about ``entries_paused``; the
    caller must check that separately and first — Tier-0 never supersedes the
    global pause.

    Order matches the plan's rule numbering (price -> mode -> cluster ->
    aggregate ceiling) so the first-failing reason is deterministic and
    stable across calls with the same facts.
    """

    if not enabled:
        return None
    reason = tier0_price_rejection_reason(
        execution_price=candidate.execution_price,
        limit_price=candidate.limit_price,
        max_entry_price=max_entry_price,
    )
    if reason is not None:
        return reason
    reason = tier0_execution_mode_rejection_reason(
        execution_mode=candidate.execution_mode,
        allowed_mode=allowed_execution_mode,
    )
    if reason is not None:
        return reason
    reason = tier0_cluster_occupied_rejection_reason(
        cluster_key=candidate.cluster_key,
        occupied_clusters=occupied_clusters,
    )
    if reason is not None:
        return reason
    return tier0_aggregate_ceiling_rejection_reason(
        current_open_cost_usd=current_open_cost_usd,
        candidate_open_cost_usd=candidate_open_cost_usd,
        conservative_settled_bankroll_usd=conservative_settled_bankroll_usd,
        aggregate_open_loss_pct_ceiling=aggregate_open_loss_pct_ceiling,
    )


def tier0_flat_stake_shares(
    *,
    min_order_size_shares: float | int,
    share_granularity: float | int | None = None,
) -> float:
    """The flat Tier-0 stake: the smallest venue-legal order, in shares.

    Property under test: this never reads q, edge, or |q-p| — the signature
    carries no such parameter, so a caller cannot accidentally thread one in.
    Changing q must leave the returned stake unchanged for a fixed
    ``min_order_size_shares``/``share_granularity``.

    ``share_granularity`` defaults to ``min_order_size_shares`` itself (the
    snapshot's own floor is already the finest legal increment unless the
    caller knows of a coarser one) and the result is min_order_size rounded
    UP to the nearest multiple of that granularity, so the returned share
    count is always venue-legal even when the two differ.
    """

    try:
        min_shares = float(min_order_size_shares)
    except (TypeError, ValueError):
        raise ValueError(f"min_order_size_shares must be numeric, got {min_order_size_shares!r}") from None
    if not math.isfinite(min_shares) or min_shares <= 0.0:
        raise ValueError(f"min_order_size_shares must be positive, got {min_order_size_shares!r}")
    granularity = min_shares if share_granularity is None else float(share_granularity)
    if not math.isfinite(granularity) or granularity <= 0.0:
        raise ValueError(f"share_granularity must be positive, got {share_granularity!r}")
    steps = math.ceil(min_shares / granularity)
    return steps * granularity


def tier0_drawdown_kill_breached(
    *,
    tier0_start_equity_usd: float | int,
    tier0_realized_pnl_usd: float | int,
    drawdown_kill_pct: float,
) -> bool:
    """True when cumulative Tier-0 realized drawdown from start equity hits the kill.

    ``tier0_realized_pnl_usd`` is cumulative realized PnL attributable to
    Tier-0 positions since ``tier0_start_equity_usd`` was captured (negative
    = net loss). Drawdown is measured against start equity, not current
    equity, so a losing streak cannot "reset" its own kill threshold by
    shrinking the denominator.
    """

    try:
        start = float(tier0_start_equity_usd)
        pnl = float(tier0_realized_pnl_usd)
        pct = float(drawdown_kill_pct)
    except (TypeError, ValueError):
        raise ValueError("tier0 drawdown-kill inputs must be numeric") from None
    if not all(math.isfinite(v) for v in (start, pnl, pct)):
        raise ValueError("tier0 drawdown-kill inputs must be finite")
    if start <= 0.0:
        raise ValueError(f"tier0_start_equity_usd must be positive, got {tier0_start_equity_usd!r}")
    if pct <= 0.0:
        raise ValueError(f"drawdown_kill_pct must be positive, got {drawdown_kill_pct!r}")
    if pnl >= 0.0:
        return False
    drawdown_pct = -pnl / start
    return drawdown_pct >= pct


def check_tier0_drawdown_kill(
    *,
    tier0_start_equity_usd: float | int,
    tier0_realized_pnl_usd: float | int,
    drawdown_kill_pct: float,
    pause_fn,
    reason_code: str = "reversal_plan_tier0_drawdown_kill_breached",
) -> bool:
    """Orchestrate the drawdown-kill check: breach -> call ``pause_fn(reason_code)`` once.

    ``pause_fn`` is the caller-supplied control-plane pause request (e.g.
    ``src.control.control_plane.pause_entries``); this module never imports
    the control plane directly so it stays pure/no-DB/no-side-effect on its
    own. Returns whether the kill fired (so the caller can log/alert).
    """

    breached = tier0_drawdown_kill_breached(
        tier0_start_equity_usd=tier0_start_equity_usd,
        tier0_realized_pnl_usd=tier0_realized_pnl_usd,
        drawdown_kill_pct=drawdown_kill_pct,
    )
    if breached:
        pause_fn(reason_code)
    return breached


# ---------------------------------------------------------------------------
# Tier-0 risk ceilings loader.
#
# config/risk_policy.yaml's ``assert_risk_policy_artifact`` (src/main.py) only
# governs the ``RISK_POLICY_CHECKED_LEVERS`` it already enumerates
# (kelly_multiplier, max_correlated_pct, max_portfolio_heat_pct,
# max_single_position_pct) — all RISK-INCREASING sizing.* levers whose ceiling
# a live config value could exceed. aggregate_open_loss_pct_ceiling and
# drawdown_kill_pct are not sizing.* levers and are not risk-increasing in
# that sense (Tier-0 is a NEW, narrower gate that can only make entries
# harder to admit and stakes smaller than the pre-Tier-0 path, never larger),
# so they are read directly by this module rather than added to that guard's
# checked-levers tuple.
#
# This module cannot import ``src.main`` to reuse its
# ``_load_risk_policy_artifact`` loader: no module under src/strategy or
# src/control imports src.main anywhere in this codebase (verified via repo
# grep before writing this), which is a strong signal main.py imports
# strategy/control modules and a src.main import here would be circular.
# Instead this follows the existing precedent for a standalone, module-local
# YAML artifact loader: ``src/risk_allocator/governor.py::load_cap_policy``
# already reads a tracked policy YAML file directly with no shared loader.
# ---------------------------------------------------------------------------

RISK_POLICY_ARTIFACT_PATH = Path("config/risk_policy.yaml")

TIER0_DEFAULT_AGGREGATE_OPEN_LOSS_PCT_CEILING: float = 0.02
TIER0_DEFAULT_DRAWDOWN_KILL_PCT: float = 0.10


def load_tier0_risk_ceilings(
    path: Path = RISK_POLICY_ARTIFACT_PATH,
) -> dict[str, float]:
    """Read the ``tier0:`` block from the tracked risk-policy artifact.

    Fail-closed to the plan's documented defaults only when the artifact or
    the ``tier0:`` block is entirely absent (e.g. a pre-Tier-0 checkout) —
    never silently substitutes a default for a present-but-malformed value.
    """

    if not path.exists():
        return {
            "aggregate_open_loss_pct_ceiling": TIER0_DEFAULT_AGGREGATE_OPEN_LOSS_PCT_CEILING,
            "drawdown_kill_pct": TIER0_DEFAULT_DRAWDOWN_KILL_PCT,
        }
    import yaml  # local import: matches src/risk_allocator/governor.py::load_cap_policy idiom

    raw = path.read_text()
    loaded = yaml.safe_load(raw) or {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"TIER0_RISK_POLICY_MALFORMED: {path} must parse to a mapping")
    tier0 = loaded.get("tier0")
    if tier0 is None:
        return {
            "aggregate_open_loss_pct_ceiling": TIER0_DEFAULT_AGGREGATE_OPEN_LOSS_PCT_CEILING,
            "drawdown_kill_pct": TIER0_DEFAULT_DRAWDOWN_KILL_PCT,
        }
    if not isinstance(tier0, Mapping):
        raise ValueError(f"TIER0_RISK_POLICY_MALFORMED: {path} tier0 block must be a mapping")
    try:
        ceiling = float(tier0["aggregate_open_loss_pct_ceiling"])
        kill_pct = float(tier0["drawdown_kill_pct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"TIER0_RISK_POLICY_MALFORMED: {path} tier0 block missing/non-numeric "
            f"aggregate_open_loss_pct_ceiling or drawdown_kill_pct"
        ) from exc
    if not (0.0 < ceiling <= 1.0) or not (0.0 < kill_pct <= 1.0):
        raise ValueError(
            f"TIER0_RISK_POLICY_MALFORMED: {path} tier0 ceilings must be in (0, 1]: "
            f"aggregate_open_loss_pct_ceiling={ceiling}, drawdown_kill_pct={kill_pct}"
        )
    return {
        "aggregate_open_loss_pct_ceiling": ceiling,
        "drawdown_kill_pct": kill_pct,
    }
