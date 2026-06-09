# Created: 2026-06-05
# Last reused/audited: 2026-06-05
# Authority basis: MAJOR #1 antibody on the P1 sizing fix (commits a281ba14a2 +
#   efe91afdb5, branch fix/remove-live-caps) — the corr-ceiling
#   ``Σ stakes ≤ max_correlated_pct·B`` holds ONLY when
#   ``kelly_multiplier ≤ max_correlated_pct``. These are INDEPENDENT config
#   knobs, equal at 0.25 only by coincidence — the same coincidence that masked
#   the original bug. Iron rule 5: over-size = ruin.
# Lifecycle: created=2026-06-05; last_reviewed=2026-06-05; last_reused=2026-06-05
# Purpose: Boot-guard antibody — assert_kelly_multiplier_within_correlated_ceiling makes kelly_multiplier > max_correlated_pct (and any non-finite knob) FATAL at boot, closing the over-size door (iron rule 5 = ruin) the K1–K8 suite cannot see.
# Reuse: Re-run when the guard, _run_boot_guards wiring, or evaluate_kelly's corr-ceiling sizing (f_cap_corr = max_correlated_pct) changes.
"""ANTIBODY for the unguarded over-size door (MAJOR #1).

The P1 fix (FIX A) makes the corr-weighted budget ``f_cap_corr·B`` with
``f_cap_corr = max_correlated_pct`` so that ``Σ corr-weighted stakes ≤
max_correlated_pct·B`` (INV-K1). That bound rests on the algebra
``stake = (f*·m / f_cap_corr)·(f_cap_corr·B − committed)`` collapsing to
``≤ (max_correlated_pct·B − committed)`` ONLY because ``f*·m ≤ kelly base cap =
kelly_multiplier`` and the raw cap ``f_cap_raw = kelly_multiplier``.

If the operator sets ``kelly_multiplier > max_correlated_pct``, then the raw
base cap exceeds the corr ceiling, ``f*·m / f_cap_corr`` can exceed 1, and the
per-bet corr-weighted stake can exceed ``(max_correlated_pct·B − committed)`` —
so ``Σ`` breaches ``max_correlated_pct·B``. The critic reproduced 3 same-cycle
same-city bets summing to $51 > $42.50 ceiling at
``kelly_multiplier=0.5, max_correlated_pct=0.25, B=170`` (20% over-size), even
with the INV-K3 single cap holding.

The K1–K8 suite CANNOT catch this: it always sizes with
``kelly_multiplier == 0.25 == max_correlated_pct`` (the masking coincidence). It
never varies ``kelly_multiplier`` above the ceiling, so the door is invisible to
it. This file is the missing antibody: a FAIL-CLOSED boot guard that makes
``kelly_multiplier > max_correlated_pct`` unconstructable (FATAL at daemon
start), plus a relationship test proving the breach the guard prevents.
"""
from __future__ import annotations

import pytest

from src.events.money_path_adapters import evaluate_kelly
from src.sizing.sizing_context import SizingContext
from src.state.portfolio import PortfolioState, correlated_committed_usd, total_exposure_usd
from src.contracts.execution_price import ExecutionPrice

BANKROLL = 170.0
NEAR_CITY = "New York City"


def _cfg(kelly_multiplier, max_correlated_pct):
    """Minimal raw-config dict the boot guard consumes (matches the
    ``settings._data`` shape the real boot path feeds to ``_run_boot_guards``)."""
    return {
        "sizing": {
            "kelly_multiplier": kelly_multiplier,
            "max_correlated_pct": max_correlated_pct,
        }
    }


# ── The boot guard fires when kelly_multiplier > max_correlated_pct ──────────

def test_guard_raises_when_kelly_mult_exceeds_corr_ceiling():
    """kelly_multiplier=0.5 > max_correlated_pct=0.25 → FATAL RuntimeError.

    This is the exact over-size door the critic reproduced ($51 > $42.50). The
    guard must make it unconstructable at boot, not silently breach mid-trade.
    """
    from src.main import assert_kelly_multiplier_within_correlated_ceiling
    with pytest.raises(RuntimeError, match="KELLY_MULT_EXCEEDS_CORR_CEILING"):
        assert_kelly_multiplier_within_correlated_ceiling(
            _cfg(kelly_multiplier=0.5, max_correlated_pct=0.25)
        )


def test_guard_passes_when_kelly_mult_within_corr_ceiling():
    """kelly_multiplier=0.25 ≤ max_correlated_pct=0.25 (the current valid config)
    → no error. Equality is the boundary and is allowed (the bound holds with
    equality: f*·m/f_cap ≤ 1)."""
    from src.main import assert_kelly_multiplier_within_correlated_ceiling
    assert_kelly_multiplier_within_correlated_ceiling(
        _cfg(kelly_multiplier=0.25, max_correlated_pct=0.25)
    )


def test_guard_passes_when_kelly_mult_strictly_below_corr_ceiling():
    """A strictly-below value (0.20 < 0.25) also passes — only the breach fires."""
    from src.main import assert_kelly_multiplier_within_correlated_ceiling
    assert_kelly_multiplier_within_correlated_ceiling(
        _cfg(kelly_multiplier=0.20, max_correlated_pct=0.25)
    )


def test_guard_registered_in_run_boot_guards():
    """The guard must be WIRED into _run_boot_guards (else it never runs at
    boot). With a breaching config the named guard tuple must report failed."""
    from src.main import _run_boot_guards
    results = _run_boot_guards(_cfg(kelly_multiplier=0.5, max_correlated_pct=0.25))
    names = {r[0]: r for r in results}
    assert "kelly_mult_corr_ceiling" in names, (
        f"guard not registered in _run_boot_guards; got: {sorted(names)}"
    )
    assert names["kelly_mult_corr_ceiling"][1] is False, (
        f"breaching config must fail the guard; got: {names['kelly_mult_corr_ceiling']}"
    )


def test_guard_registered_passes_on_valid_config():
    """And the registered guard passes on the valid (≤) config."""
    from src.main import _run_boot_guards
    results = _run_boot_guards(_cfg(kelly_multiplier=0.25, max_correlated_pct=0.25))
    names = {r[0]: r for r in results}
    assert names["kelly_mult_corr_ceiling"][1] is True, (
        f"valid config must pass the guard; got: {names['kelly_mult_corr_ceiling']}"
    )


# ── Non-finite inputs must FAIL-CLOSED (NaN/inf bypass the > comparison) ─────

def test_guard_fires_on_nan_kelly_multiplier():
    """A NaN kelly_multiplier must make the guard FIRE, not silently pass.

    ``float('nan') > max_corr`` is ALWAYS False, so without an explicit finite
    check a NaN kelly_multiplier slips past the fail-closed guard (the over-size
    door stays open). Consistent with the other fail-closed sizing inputs, a
    non-finite value must be rejected.
    """
    from src.main import assert_kelly_multiplier_within_correlated_ceiling
    with pytest.raises(RuntimeError, match="KELLY_MULT_EXCEEDS_CORR_CEILING|NON_FINITE"):
        assert_kelly_multiplier_within_correlated_ceiling(
            _cfg(kelly_multiplier=float("nan"), max_correlated_pct=0.25)
        )


def test_guard_fires_on_nan_max_correlated_pct():
    """A NaN max_correlated_pct must also FIRE: ``x > nan`` is always False, so
    the comparison can never catch a breach against a NaN ceiling."""
    from src.main import assert_kelly_multiplier_within_correlated_ceiling
    with pytest.raises(RuntimeError, match="KELLY_MULT_EXCEEDS_CORR_CEILING|NON_FINITE"):
        assert_kelly_multiplier_within_correlated_ceiling(
            _cfg(kelly_multiplier=0.25, max_correlated_pct=float("nan"))
        )


def test_guard_fires_on_inf_kelly_multiplier():
    """An infinite kelly_multiplier is unambiguously an over-size — fail-closed."""
    from src.main import assert_kelly_multiplier_within_correlated_ceiling
    with pytest.raises(RuntimeError, match="KELLY_MULT_EXCEEDS_CORR_CEILING|NON_FINITE"):
        assert_kelly_multiplier_within_correlated_ceiling(
            _cfg(kelly_multiplier=float("inf"), max_correlated_pct=0.25)
        )


# ── The breach the guard prevents: Σ corr-weighted stakes > ceiling ─────────

def _kelly_safe_price(value=0.50):
    return ExecutionPrice(
        value=value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


def _size_with_mult(*, new_city, extra_reserved, kelly_multiplier, p_posterior=0.95):
    """Size one same-cycle bet against a running reservation at a chosen
    kelly_multiplier (tight CI so the variance haircut is ~1.0 — isolates the
    ceiling breach driven by kelly_multiplier alone)."""
    state = PortfolioState(positions=[])
    corr_committed = correlated_committed_usd(
        state, new_city=new_city, extra_reserved=extra_reserved
    )
    raw_committed = total_exposure_usd(state) + sum(
        float(usd) for _, usd in (extra_reserved or [])
    )
    ctx = SizingContext.from_candidate_proof_with_portfolio(
        q_posterior=p_posterior,
        q_lcb_5pct=p_posterior - 0.01,
        lead_days=1.0,
        bankroll_usd=BANKROLL,
        corr_committed_usd=corr_committed,
        raw_committed_usd=raw_committed,
    )
    proof = evaluate_kelly(
        kelly_decision_id="k_over",
        p_posterior=p_posterior,
        execution_price=_kelly_safe_price(0.50),
        bankroll_usd=BANKROLL,
        sizing_context=ctx,
        kelly_multiplier=kelly_multiplier,
    )
    return proof.size_usd


def test_breach_is_real_when_kelly_mult_above_ceiling():
    """DOCUMENTS the breach: with kelly_multiplier=0.5 > max_correlated_pct=0.25,
    6 same-cycle same-city bets sum ABOVE max_correlated_pct·B=$42.50.

    Each individual bet is AT the single-position ceiling (0.05×B=$8.50) — the
    INV-K3 single-cap antibody IS working. The corr-ceiling breach comes from
    ACCUMULATION: 6 × $8.50 = $51 > $42.50. This is the structural gap the boot
    guard closes: when kelly_multiplier > max_correlated_pct, each same-city bet
    saturates the single cap and the corr budget overflows. The guard makes
    kelly_multiplier > max_correlated_pct FATAL at boot, closing the door.

    (Not an assertion that the sizing path is wrong; it is correct given its inputs.
    The defect is an operator supplying a kelly_multiplier the corr ceiling cannot
    absorb when bets accumulate. The 3-bet version (pre-2026-06-08) failed because
    with the restored single-position ceiling 3×$8.50=$25.50 < $42.50 ceiling —
    6 bets are needed to reproduce the accumulation breach in the live config.)
    """
    MAX_CORRELATED_PCT = 0.25  # config default
    reserved: list[tuple[str, float]] = []
    sizes: list[float] = []
    # 6 bets required: each capped at max_single_position_pct×B=$8.50 by INV-K3,
    # and 6×$8.50=$51 > max_correlated_pct×B=$42.50 proves the corr-ceiling breach.
    for _ in range(6):
        s = _size_with_mult(
            new_city=NEAR_CITY,
            extra_reserved=list(reserved),
            kelly_multiplier=0.5,  # > max_correlated_pct → the over-size door
        )
        sizes.append(s)
        reserved.append((NEAR_CITY, s))
    total = sum(sizes)
    ceiling = BANKROLL * MAX_CORRELATED_PCT
    assert total > ceiling, (
        f"expected the over-size breach to be real (so the boot guard is "
        f"load-bearing): Σ={total:.4f} should exceed ceiling={ceiling:.4f} when "
        f"kelly_multiplier(0.5) > max_correlated_pct(0.25)"
    )
