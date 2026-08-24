# Created: 2026-06-05
# Last reused/audited: 2026-08-02
# Authority basis: MAJOR #1 antibody on the P1 sizing fix (commits a281ba14a2 +
#   efe91afdb5, branch fix/remove-live-caps) — the corr-ceiling
#   ``Σ stakes ≤ max_correlated_pct·B`` holds ONLY when
#   ``kelly_multiplier ≤ max_correlated_pct``. These are INDEPENDENT config
#   knobs, equal at 0.25 only by coincidence — the same coincidence that masked
#   the original bug. Iron rule 5: over-size = ruin.
# Lifecycle: created=2026-06-05; last_reviewed=2026-08-02; last_reused=2026-08-02
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


# ── Exact governed live fraction ─────────────────────────────────────────────

def test_governed_fraction_accepts_exactly_one_over_8():
    from src.main import assert_kelly_multiplier_matches_governed_fraction

    assert_kelly_multiplier_matches_governed_fraction(
        _cfg(kelly_multiplier=1.0 / 8.0, max_correlated_pct=0.25)
    )


@pytest.mark.parametrize(
    "value",
    [0.25, 0.03125, float("nan"), float("inf"), "0.125", "not-a-number", True],
)
def test_governed_fraction_rejects_drift(value):
    from src.main import assert_kelly_multiplier_matches_governed_fraction

    with pytest.raises(RuntimeError, match="KELLY_MULT_GOVERNANCE_MISMATCH"):
        assert_kelly_multiplier_matches_governed_fraction(
            _cfg(kelly_multiplier=value, max_correlated_pct=0.25)
        )


def test_governed_fraction_rejects_missing_value():
    from src.main import assert_kelly_multiplier_matches_governed_fraction

    cfg = _cfg(kelly_multiplier=1.0 / 8.0, max_correlated_pct=0.25)
    del cfg["sizing"]["kelly_multiplier"]
    with pytest.raises(RuntimeError, match="KELLY_MULT_GOVERNANCE_MISMATCH"):
        assert_kelly_multiplier_matches_governed_fraction(cfg)


@pytest.mark.parametrize("value", ["0.125", "not-a-number", True])
def test_boot_guard_reports_malformed_governed_fraction(value):
    from src.main import _run_boot_guards

    results = _run_boot_guards(
        _cfg(kelly_multiplier=value, max_correlated_pct=0.25)
    )
    names = {r[0]: r for r in results}
    assert names["kelly_mult_governed_fraction"][1] is False
    assert "KELLY_MULT_GOVERNANCE_MISMATCH" in names["kelly_mult_governed_fraction"][2]


def test_governed_fraction_is_registered_in_boot_guards():
    from src.main import _run_boot_guards

    results = _run_boot_guards(
        _cfg(kelly_multiplier=0.25, max_correlated_pct=0.25)
    )
    names = {r[0]: r for r in results}
    assert names["kelly_mult_governed_fraction"][1] is False
    assert "required=0.125 (1/8)" in names["kelly_mult_governed_fraction"][2]


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
    """kelly_multiplier=0.25 ≤ max_correlated_pct=0.25 (relationship fixture)
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
    """The correlated-ceiling guard passes on a relationship-valid config."""
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


# ── Item 1b: tracked, content-addressed risk-policy artifact ────────────────
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 1b. The Aug 1-3 defect (kelly_multiplier=0.25 in UNTRACKED
#   config/settings.json) was fixed by pinning ONE constant
#   (GOVERNED_KELLY_MULTIPLIER, guards above). This section covers the
#   generalization: EVERY risk-increasing sizing.* lever the live entry path
#   consumes (kelly_multiplier, max_correlated_pct, max_portfolio_heat_pct,
#   max_single_position_pct) must not exceed its ceiling in the tracked
#   config/risk_policy.yaml, verified at every boot.

import hashlib

REAL_RISK_POLICY_PATH = "config/risk_policy.yaml"

# Mirrors config/settings.example.json::sizing exactly — the governed
# operating point, which is also every ceiling in config/risk_policy.yaml.
_AT_CEILING_SIZING = {
    "kelly_multiplier": 0.125,
    "max_correlated_pct": 0.25,
    "max_portfolio_heat_pct": 0.5,
    "max_single_position_pct": 0.1,
}


def _sizing_cfg(**overrides):
    sizing = dict(_AT_CEILING_SIZING)
    sizing.update(overrides)
    return {"sizing": sizing}


def _write_policy(tmp_path, **overrides):
    policy = {
        "policy_version": "1",
        "kelly_multiplier_ceiling": 0.125,
        "max_correlated_pct_ceiling": 0.25,
        "max_portfolio_heat_pct_ceiling": 0.5,
        "max_single_position_pct_ceiling": 0.1,
    }
    policy.update(overrides)
    import yaml

    path = tmp_path / "risk_policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    return path


def test_risk_policy_passes_when_all_values_at_ceiling(tmp_path):
    from src.main import assert_risk_policy_artifact

    policy_path = _write_policy(tmp_path)
    assert_risk_policy_artifact(_sizing_cfg(), path=policy_path)


def test_risk_policy_breaches_when_kelly_multiplier_above_ceiling(tmp_path):
    """settings.json kelly_multiplier above the artifact ceiling -> fail closed."""
    from src.main import assert_risk_policy_artifact

    policy_path = _write_policy(tmp_path)
    with pytest.raises(RuntimeError, match="RISK_POLICY_BREACH"):
        assert_risk_policy_artifact(
            _sizing_cfg(kelly_multiplier=0.25), path=policy_path
        )


@pytest.mark.parametrize(
    "live_key,ceiling_key",
    [
        ("kelly_multiplier", "kelly_multiplier_ceiling"),
        ("max_correlated_pct", "max_correlated_pct_ceiling"),
        ("max_portfolio_heat_pct", "max_portfolio_heat_pct_ceiling"),
        ("max_single_position_pct", "max_single_position_pct_ceiling"),
    ],
)
def test_risk_policy_breaches_per_lever(tmp_path, live_key, ceiling_key):
    """Every checked lever independently breaches when raised above its
    own ceiling — not just kelly_multiplier."""
    from src.main import assert_risk_policy_artifact

    policy_path = _write_policy(tmp_path)
    ceiling = _AT_CEILING_SIZING[live_key]
    with pytest.raises(RuntimeError, match=f"RISK_POLICY_BREACH.*{live_key}"):
        assert_risk_policy_artifact(
            _sizing_cfg(**{live_key: ceiling * 2}), path=policy_path
        )


def test_risk_policy_artifact_missing_fails_closed(tmp_path):
    from src.main import assert_risk_policy_artifact

    missing_path = tmp_path / "does_not_exist.yaml"
    with pytest.raises(RuntimeError, match="RISK_POLICY_ARTIFACT_MISSING"):
        assert_risk_policy_artifact(_sizing_cfg(), path=missing_path)


def test_risk_policy_artifact_missing_ceiling_fails_closed(tmp_path):
    """A ceiling key absent from the artifact is a malformed artifact, not a
    silent pass-through."""
    from src.main import assert_risk_policy_artifact

    policy_path = tmp_path / "risk_policy.yaml"
    policy_path.write_text("policy_version: '1'\nkelly_multiplier_ceiling: 0.125\n")
    with pytest.raises(RuntimeError, match="RISK_POLICY_ARTIFACT_MALFORMED"):
        assert_risk_policy_artifact(_sizing_cfg(), path=policy_path)


def test_risk_policy_artifact_missing_policy_version_fails_closed(tmp_path):
    from src.main import assert_risk_policy_artifact

    policy = {
        "kelly_multiplier_ceiling": 0.125,
        "max_correlated_pct_ceiling": 0.25,
        "max_portfolio_heat_pct_ceiling": 0.5,
        "max_single_position_pct_ceiling": 0.1,
    }
    import yaml

    policy_path = tmp_path / "risk_policy.yaml"
    policy_path.write_text(yaml.safe_dump(policy))
    with pytest.raises(RuntimeError, match="RISK_POLICY_ARTIFACT_MALFORMED"):
        assert_risk_policy_artifact(_sizing_cfg(), path=policy_path)


def test_risk_policy_fires_on_nan_live_value(tmp_path):
    from src.main import assert_risk_policy_artifact

    policy_path = _write_policy(tmp_path)
    with pytest.raises(RuntimeError, match="RISK_POLICY_BREACH"):
        assert_risk_policy_artifact(
            _sizing_cfg(kelly_multiplier=float("nan")), path=policy_path
        )


def test_risk_policy_artifact_hash_and_version_logged(tmp_path, caplog):
    """policy_version + sha256 must be logged at every boot (audit trail)."""
    import logging

    from src.main import assert_risk_policy_artifact

    policy_path = _write_policy(tmp_path)
    expected_sha256 = hashlib.sha256(policy_path.read_bytes()).hexdigest()

    with caplog.at_level(logging.INFO, logger="zeus"):
        assert_risk_policy_artifact(_sizing_cfg(), path=policy_path)

    all_messages = [r.getMessage() for r in caplog.records]
    artifact_records = [m for m in all_messages if "risk_policy_artifact:" in m]
    assert artifact_records, f"expected a risk_policy_artifact log record; got: {all_messages}"
    logged = artifact_records[0]
    assert "policy_version=1" in logged
    assert f"sha256={expected_sha256}" in logged

    logged_keys = {m for m in all_messages if "risk_policy_effective_value:" in m}
    for live_key, _ in [
        ("kelly_multiplier", None),
        ("max_correlated_pct", None),
        ("max_portfolio_heat_pct", None),
        ("max_single_position_pct", None),
    ]:
        assert any(f"sizing.{live_key}=" in msg for msg in logged_keys), (
            f"expected an effective-value log line for sizing.{live_key}; got: {logged_keys}"
        )


def test_risk_policy_lowering_override_does_not_trip_guard(tmp_path):
    """DIRECTION LAW: a runtime/control-plane lever that only LOWERS effective
    risk must never trip this guard. The guard only reads cfg["sizing"], so a
    control-plane override living under an unrelated top-level key is
    structurally invisible to it — this proves that by construction."""
    from src.main import assert_risk_policy_artifact

    policy_path = _write_policy(tmp_path)
    cfg = _sizing_cfg(
        kelly_multiplier=0.05,
        max_correlated_pct=0.1,
        max_portfolio_heat_pct=0.2,
        max_single_position_pct=0.02,
    )
    # Simulate a control-plane tightening override coexisting in the same
    # raw config dict — must not affect the sizing-only guard.
    cfg["control_plane"] = {"edge_threshold_multiplier": 3.0, "entries_paused": True}
    assert_risk_policy_artifact(cfg, path=policy_path)


def test_risk_policy_artifact_registered_in_run_boot_guards_passes():
    """End-to-end against the REAL committed config/risk_policy.yaml."""
    from src.main import _run_boot_guards

    results = _run_boot_guards(_sizing_cfg())
    names = {r[0]: r for r in results}
    assert "risk_policy_artifact" in names, (
        f"guard not registered in _run_boot_guards; got: {sorted(names)}"
    )
    assert names["risk_policy_artifact"][1] is True, (
        f"governed sizing config must pass against the real artifact; got: "
        f"{names['risk_policy_artifact']}"
    )


def test_risk_policy_artifact_registered_in_run_boot_guards_fails():
    """End-to-end against the REAL committed config/risk_policy.yaml: a
    breaching live value must fail the wired guard, not just the bare
    function."""
    from src.main import _run_boot_guards

    results = _run_boot_guards(_sizing_cfg(max_single_position_pct=0.5))
    names = {r[0]: r for r in results}
    assert names["risk_policy_artifact"][1] is False, (
        f"breaching config must fail the guard; got: {names['risk_policy_artifact']}"
    )
    assert "RISK_POLICY_BREACH" in names["risk_policy_artifact"][2]
