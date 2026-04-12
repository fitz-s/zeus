"""Market fusion: α-weighted posterior and VWMP.

Spec §4.5: p_posterior = α × p_cal + (1-α) × p_market
α depends on calibration maturity, ensemble spread, model agreement,
lead time, and market freshness.
"""

import numpy as np

from src.config import settings
from src.contracts.alpha_decision import AlphaDecision
from src.types.temperature import TemperatureDelta

# Spread thresholds defined in °F, auto-converted via .to() for any unit.
# This prevents the Rainstorm bug where 2.0 was used for both °F and °C cities.
# HARDCODED(setting_key="edge.spread_tight_f", note_key="edge._spread_tight_f_note",
#           tier=2, replace_after="1000+ ENS snapshots per city",
#           data_needed="per-city spread distribution percentiles")
SPREAD_TIGHT = TemperatureDelta(settings["edge"]["spread_tight_f"], "F")
# HARDCODED(setting_key="edge.spread_wide_f", note_key="edge._spread_wide_f_note",
#           tier=2, replace_after="1000+ ENS snapshots per city",
#           data_needed="per-city spread distribution percentiles")
SPREAD_WIDE = TemperatureDelta(settings["edge"]["spread_wide_f"], "F")

# HARDCODED(setting_key="edge.base_alpha", note_key="edge._base_alpha_note",
#           tier=1, replace_after="100+ settlements",
#           data_needed="Model Brier vs Market Brier per calibration level")
BASE_ALPHA_BY_LEVEL = {
    1: settings["edge"]["base_alpha"]["level1"],
    2: settings["edge"]["base_alpha"]["level2"],
    3: settings["edge"]["base_alpha"]["level3"],
    4: settings["edge"]["base_alpha"]["level4"],
}
TAIL_ALPHA_SCALE = 0.5  # Validated: sweep [0.5, 0.6, ..., 1.0], 0.5 is optimal


def vwmp(best_bid: float, best_ask: float,
         bid_size: float, ask_size: float) -> float:
    """Volume-Weighted Micro-Price. Spec §4.1.

    If total_size = 0: fall back to mid-price + log warning.
    Per CLAUDE.md: never use mid-price for edge calculations (VWMP required).
    """
    total = bid_size + ask_size
    if total == 0:
        # CLAUDE.md: VWMP with total size = 0 → fall back to mid-price + log
        import logging
        logging.getLogger(__name__).warning(
            "VWMP total_size=0, falling back to mid-price: bid=%.3f ask=%.3f",
            best_bid, best_ask
        )
        return (best_bid + best_ask) / 2.0
    return (best_bid * ask_size + best_ask * bid_size) / total


def compute_alpha(
    calibration_level: int,
    ensemble_spread: TemperatureDelta,
    model_agreement: str,
    lead_days: float,
    hours_since_open: float,
    city_name: str = "",
    season: str = "",
) -> AlphaDecision:
    """Compute α for model-market blending. Spec §4.5.

    Higher α → trust model more. Lower α → trust market more.
    Clamped to [0.20, 0.85].

    α is adjusted by PER-DECISION signals (validated 2026-03-31):
    - D4: ENS spread (tight → +0.10, wide → -0.15)
    - D3: tail bin scaling (applied in compute_posterior, not here)
    - Lead days (short → +0.05, long → -0.05)

    DEPRECATED: per-city α override via alpha_overrides table.
    D1 analysis showed MAE→α mapping has r=+0.032 (no signal).
    Override lookup kept for manual experimentation but table is empty
    and no longer auto-populated by the weekly cycle.

    ensemble_spread must be a TemperatureDelta. This is a hard rule:
    spread thresholds are unit-aware and must not silently fall back to bare floats.
    """
    if not isinstance(ensemble_spread, TemperatureDelta):
        raise TypeError(
            "compute_alpha requires ensemble_spread to be TemperatureDelta. "
            "Wrap raw spreads with the city settlement unit first."
        )

    # Per-city override lookup (DEPRECATED — kept for manual experiments only)
    # Was part of the dynamic-α per-city approach. alpha_overrides has 0 rows.
    # Do NOT auto-populate this table; per-decision adjustments are superior.
    base = _get_alpha_override(city_name, season)
    if base is None:
        base = BASE_ALPHA_BY_LEVEL[calibration_level]
    a = base

    # Ensemble spread adjustments — typed thresholds prevent °C/°F confusion
    # D4 analysis (2026-03-31): spread IS predictive of per-decision accuracy
    # (r=+0.214, tight Brier 0.114 vs wide 0.269). Sweep showed bonus=0.10
    # gives -0.00825 Brier improvement vs -0.00460 at the old bonus=0.05.
    tight = SPREAD_TIGHT.to(ensemble_spread.unit)
    wide = SPREAD_WIDE.to(ensemble_spread.unit)
    if ensemble_spread < tight:
        a += 0.10  # was 0.05, increased per D4
    if ensemble_spread > wide:
        a -= 0.15  # was 0.10, increased per D4

    # Model agreement adjustments
    if model_agreement == "SOFT_DISAGREE":
        a -= 0.10
    if model_agreement == "CONFLICT":
        a -= 0.20

    # Lead time adjustments
    if lead_days <= 1:
        a += 0.05
    if lead_days >= 5:
        a -= 0.05

    # Market freshness: recently-opened markets have unreliable prices
    if hours_since_open < 12:
        a += 0.10
    if hours_since_open < 6:
        a += 0.05  # Cumulative with above

    return AlphaDecision(
        value=max(0.20, min(0.85, a)),
        optimization_target="risk_cap",
        evidence_basis="D1 resolution: conservative blending weight, not pure Brier minimizer",
        ci_bound=0.05,
    )


# Cache to avoid repeated DB lookups within a cycle
_alpha_override_cache: dict[tuple[str, str], float | None] = {}
_alpha_cache_ts: float = 0.0


def _get_alpha_override(city_name: str, season: str) -> float | None:
    """Look up per-city alpha override. Returns None if no override exists."""
    import time
    global _alpha_override_cache, _alpha_cache_ts

    if not city_name or not season:
        return None

    # Cache for 300s to avoid DB thrashing during a cycle
    now = time.time()
    if now - _alpha_cache_ts > 300:
        _alpha_override_cache.clear()
        _alpha_cache_ts = now

    key = (city_name, season)
    if key in _alpha_override_cache:
        return _alpha_override_cache[key]

    try:
        from src.state.db import get_world_connection
        conn = get_world_connection()
        row = conn.execute(
            "SELECT alpha FROM alpha_overrides "
            "WHERE city = ? AND season = ? AND source = 'validated_optimal'",
            (city_name, season),
        ).fetchone()
        conn.close()

        result = float(row["alpha"]) if row else None
        _alpha_override_cache[key] = result

        if result is not None:
            import logging
            logging.getLogger(__name__).info(
                "Using validated α=%.3f for %s/%s", result, city_name, season,
            )
        return result

    except Exception:
        _alpha_override_cache[key] = None
        return None


def compute_posterior(
    p_cal: np.ndarray,
    p_market: np.ndarray,
    alpha: float,
    bins: list = None,
) -> np.ndarray:
    """Compute α-weighted posterior, normalized to sum=1.0. Spec §4.5.

    p_posterior = normalize(α_per_bin × p_cal + (1-α_per_bin) × p_market)

    D3 analysis (2026-03-31): tail bins are 5.3× harder for the model
    (Brier 0.67 vs 0.11). Per-bin α scaling at 0.5 for tails reduces
    overall Brier by 0.042. When bins are provided, tail bins get
    α_tail = α × TAIL_ALPHA_SCALE.

    p_market sums to vig (~0.95-1.05), not 1.0, so the blend must
    be re-normalized. CLAUDE.md types: p_posterior sums to 1.0.
    """
    if bins is not None and len(bins) == len(p_cal):
        alpha_vec = np.array([alpha_for_bin(alpha, b) for b in bins], dtype=float)
        raw = alpha_vec * p_cal + (1.0 - alpha_vec) * p_market
    else:
        raw = alpha * p_cal + (1.0 - alpha) * p_market

    total = raw.sum()
    if total > 0:
        return raw / total
    return raw


def alpha_for_bin(alpha: float, bin) -> float:
    """Return the effective alpha for one bin, including tail scaling."""
    is_tail = (hasattr(bin, 'low') and bin.low is None) or (hasattr(bin, 'high') and bin.high is None)
    if not is_tail and hasattr(bin, 'label'):
        label = bin.label.lower()
        is_tail = 'or below' in label or 'or higher' in label or 'or above' in label
    if is_tail:
        return max(0.20, float(alpha) * TAIL_ALPHA_SCALE)
    return float(alpha)
