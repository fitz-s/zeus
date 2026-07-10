# Created: 2026-06-29
# Last reused/audited: 2026-07-10
# Authority basis: capital-gated per-city rho-mix serving (frontier-consult validated). This pins the
#   EXTRACTED pure q-builder `_build_scaled_normal_uniform_q` and the rho = 1-exp(-C/W) mixture so a
#   refactor cannot silently change the served calibration q. The byte-identical-global contract for the
#   pure builder (k, w, floor_steps == family pair) is the load-bearing invariant: a city with no earned
#   capital serves pure global and is identical to today.
"""Unit tests for the extracted scaled-Normal+uniform q builder and the per-city rho mixture.

The full materialize path is NOT unit-testable here (it needs a persisted-current fusion capture the
fixtures cannot provide). These tests exercise the PURE math directly: the q builder against an
independent reference computation, and the rho mixture as pure dict arithmetic."""
from __future__ import annotations

import math

import pytest

import src.data.replacement_forecast_materializer as mat
from src.calibration.emos import bin_probability_settlement
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin


def _bins_C():
    # A small explicit Celsius family: two interior bins + two open-ended catch-alls.
    return [
        AifsTemperatureBin("le_20", upper_c=20.0, settlement_unit="C", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("b_21_23", lower_c=21.0, upper_c=23.0, settlement_unit="C", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("b_24_26", lower_c=24.0, upper_c=26.0, settlement_unit="C", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("ge_27", lower_c=27.0, settlement_unit="C", rounding_rule="wmo_half_up"),
    ]


def _ref_normal_q(bins, *, mu, sigma, half_step, rounding_rule):
    """Independent reference: raw Normal settlement masses, normalized. No floor, no uniform."""
    raw = {
        b.bin_id: bin_probability_settlement(
            mu=mu,
            sigma=sigma,
            bin_low=(None if b.lower_c is None else float(b.lower_c)),
            bin_high=(None if b.upper_c is None else float(b.upper_c)),
            half_step=half_step,
            rounding_rule=rounding_rule,
        )
        for b in bins
    }
    tot = sum(raw.values())
    return {k: v / tot for k, v in raw.items()}


def _call_builder(bins, *, mu, sigma, k, w, floor_steps, step=1.0):
    # The builder returns (q, capped_bins, uniform_applied); the helper exposes (q, capped_bins).
    q, capped, _applied = mat._build_scaled_normal_uniform_q(
        mu=mu,
        sigma_pred=sigma,
        k=k,
        uniform_w=w,
        floor_steps=floor_steps,
        bins=bins,
        half_step=step / 2.0,
        rounding_rule="wmo_half_up",
        day0_obs_extreme_c=None,
        settlement_step_c=step,
        settlement_sigma_floor_c=None,
        city_unit="C",
    )
    return q, capped


def _bins_interior_only():
    # An ALL-INTERIOR family (no open-ended catch-all) — the uniform mixture cannot trip the catch-all
    # coherence cap here, so q = renorm((1-w)*normal + w*uniform) is the EXACT independent reference.
    return [
        AifsTemperatureBin("b_19_20", lower_c=19.0, upper_c=20.0, settlement_unit="C", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("b_21_22", lower_c=21.0, upper_c=22.0, settlement_unit="C", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("b_23_24", lower_c=23.0, upper_c=24.0, settlement_unit="C", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("b_25_26", lower_c=25.0, upper_c=26.0, settlement_unit="C", rounding_rule="wmo_half_up"),
    ]


def test_golden_uniform_mix_matches_independent_reference():
    # (a) GOLDEN: for a chosen (mu, sigma, k, w) over an ALL-INTERIOR family the returned masses equal an
    # independent direct computation: q = renorm[(1-w)*normal_rescaled + w*uniform]. Interior bins have
    # no catch-all cap, so this is an exact, assumption-free check of the Normal+uniform math.
    bins = _bins_interior_only()
    mu, sigma, k, w = 22.3, 1.7, 0.9, 0.20
    q, capped = _call_builder(bins, mu=mu, sigma=sigma, k=k, w=w, floor_steps=0.0)
    assert capped == [], "interior-only family must never cap a catch-all"

    base = _ref_normal_q(bins, mu=mu, sigma=sigma * k, half_step=0.5, rounding_rule="wmo_half_up")
    n = len(bins)
    u = 1.0 / n
    mixed = {b: (1.0 - w) * base[b] + w * u for b in base}
    mtot = sum(mixed.values())
    ref = {b: v / mtot for b, v in mixed.items()}

    assert set(q) == set(ref)
    for b in ref:
        assert q[b] == pytest.approx(ref[b], abs=1e-12), f"bin {b}: {q[b]} != {ref[b]}"
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-12)


def test_golden_catchall_cap_constrained_redistribution_matches_hand_derivation():
    # (a') GOLDEN for the catch-all coherence cap + constrained redistribution. With open-ended bins and
    # a uniform pedestal, a far catch-all whose mixed mass exceeds its HONEST (un-floored, normalized)
    # mass is pinned at the honest mass and the residual is absorbed ONLY across the uncapped bins. We
    # re-derive that exact transform independently and assert byte-equality.
    bins = _bins_C()  # le_20 / b_21_23 / b_24_26 / ge_27 (two open-ended)
    mu, sigma, k, w = 22.3, 1.7, 1.0, 0.20
    q, capped = _call_builder(bins, mu=mu, sigma=sigma, k=k, w=w, floor_steps=0.0)

    half_step, rule = 0.5, "wmo_half_up"
    # Raw (un-normalized) Normal masses at sigma_used == sigma_pred (no floor). _total is their sum.
    raw = {
        b.bin_id: bin_probability_settlement(
            mu=mu, sigma=sigma, bin_low=(None if b.lower_c is None else float(b.lower_c)),
            bin_high=(None if b.upper_c is None else float(b.upper_c)), half_step=half_step, rounding_rule=rule,
        )
        for b in bins
    }
    total = sum(raw.values())
    base = {bid: m / total for bid, m in raw.items()}  # normalized q before mixing
    # Honest normalized mass for the open-ended bins (un-floored == base here since no floor widened σ).
    honest_norm = {b.bin_id: raw[b.bin_id] / total for b in bins if (b.lower_c is None) != (b.upper_c is None)}
    # Uniform mixture over all bins (day0 None => all eligible), renormalized.
    n = len(bins)
    u = 1.0 / n
    mixed = {bid: (1.0 - w) * base[bid] + w * u for bid in base}
    mtot = sum(mixed.values())
    qmix = {bid: v / mtot for bid, v in mixed.items()}
    # Constrained redistribution: pin capped open-ended bins, scale the uncapped to fill the residual.
    capped_now = {bid for bid, hn in honest_norm.items() if qmix[bid] > hn}
    for bid in capped_now:
        qmix[bid] = honest_norm[bid]
    capped_mass = sum(qmix[bid] for bid in capped_now)
    uncapped_mass = sum(v for bid, v in qmix.items() if bid not in capped_now)
    residual = 1.0 - capped_mass
    scale = residual / uncapped_mass
    ref = {bid: (v if bid in capped_now else v * scale) for bid, v in qmix.items()}

    assert set(capped) == capped_now and capped_now, "the far catch-all should be capped under the pedestal"
    for bid in ref:
        assert q[bid] == pytest.approx(ref[bid], abs=1e-12), f"bin {bid}: {q[bid]} != {ref[bid]}"
    for bid in capped_now:
        assert q[bid] <= honest_norm[bid] + 1e-12, "capped catch-all must not exceed its honest mass"
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-12)


def test_k1_w0_reproduces_unscaled_normal_masses():
    # (b) k=1, w=0 reproduces the un-scaled Normal masses exactly (the identity / no-correction path).
    bins = _bins_C()
    mu, sigma = 24.1, 2.0
    q, _capped = _call_builder(bins, mu=mu, sigma=sigma, k=1.0, w=0.0, floor_steps=0.0)
    ref = _ref_normal_q(bins, mu=mu, sigma=sigma, half_step=0.5, rounding_rule="wmo_half_up")
    for b in ref:
        assert q[b] == pytest.approx(ref[b], abs=1e-12)


def test_catchall_open_ended_never_exceeds_unfloored_mass():
    # (c) With a binding floor, an open-ended catch-all bin on the far side never exceeds its
    # un-floored (predictive-sigma) mass. mu=18 makes "le_20" the near catch-all and "ge_27" the
    # FAR catch-all; floor widens sigma -> would inflate ge_27 absent the cap.
    bins = _bins_C()
    mu, sigma = 18.0, 1.0
    # Un-floored (honest) far-catch-all mass for ge_27.
    ref_honest = _ref_normal_q(bins, mu=mu, sigma=sigma, half_step=0.5, rounding_rule="wmo_half_up")
    # Builder with a floor of 3 steps (3.0 degC) >> 1.0 => sigma widened to 3.0.
    q, capped = _call_builder(bins, mu=mu, sigma=sigma, k=1.0, w=0.0, floor_steps=3.0)
    assert q["ge_27"] <= ref_honest["ge_27"] + 1e-9, (
        "floored far catch-all must not exceed its honest un-floored mass (Paris >=26 invariant)"
    )
    assert "ge_27" in capped
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-9)


def test_w0_gate_off_for_non_C_unit():
    # The uniform-w gate stays EXACTLY `if uniform_w > 0.0 and city_unit == "C"` — an F-unit family
    # gets NO uniform mixture even with w>0 (fixing F is a separate future release).
    bins = [
        AifsTemperatureBin("le_70", upper_c=70.0, settlement_unit="F", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("b_71_73", lower_c=71.0, upper_c=73.0, settlement_unit="F", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("ge_74", lower_c=74.0, settlement_unit="F", rounding_rule="wmo_half_up"),
    ]
    q = mat._build_scaled_normal_uniform_q(
        mu=72.0,
        sigma_pred=2.0,
        k=1.0,
        uniform_w=0.30,  # w>0 but unit is F => gate must NOT fire
        floor_steps=0.0,
        bins=bins,
        half_step=0.5,
        rounding_rule="wmo_half_up",
        day0_obs_extreme_c=None,
        settlement_step_c=1.0,
        settlement_sigma_floor_c=None,
        city_unit="F",
    )[0]
    ref = _ref_normal_q(bins, mu=72.0, sigma=2.0, half_step=0.5, rounding_rule="wmo_half_up")
    for b in ref:
        assert q[b] == pytest.approx(ref[b], abs=1e-12), "F-unit must serve pure Normal (no uniform mix)"


def test_uniform_applied_flag_tracks_actual_mixture_fire():
    # The 3rd return (uniform_applied) drives uniform_mixture_w_applied provenance — it is True ONLY
    # when the mixture actually fired (w>0 AND C-unit), and False for w=0, or F-unit (gate off).
    bins = _bins_interior_only()
    _, _, applied_w = mat._build_scaled_normal_uniform_q(
        mu=22.3, sigma_pred=1.7, k=1.0, uniform_w=0.2, floor_steps=0.0, bins=bins, half_step=0.5,
        rounding_rule="wmo_half_up", day0_obs_extreme_c=None, settlement_step_c=1.0,
        settlement_sigma_floor_c=None, city_unit="C",
    )
    assert applied_w is True
    _, _, applied_w0 = mat._build_scaled_normal_uniform_q(
        mu=22.3, sigma_pred=1.7, k=1.0, uniform_w=0.0, floor_steps=0.0, bins=bins, half_step=0.5,
        rounding_rule="wmo_half_up", day0_obs_extreme_c=None, settlement_step_c=1.0,
        settlement_sigma_floor_c=None, city_unit="C",
    )
    assert applied_w0 is False  # w=0 => no mixture
    bins_f = [
        AifsTemperatureBin("b_71_72", lower_c=71.0, upper_c=72.0, settlement_unit="F", rounding_rule="wmo_half_up"),
        AifsTemperatureBin("b_73_74", lower_c=73.0, upper_c=74.0, settlement_unit="F", rounding_rule="wmo_half_up"),
    ]
    _, _, applied_f = mat._build_scaled_normal_uniform_q(
        mu=72.0, sigma_pred=2.0, k=1.0, uniform_w=0.3, floor_steps=0.0, bins=bins_f, half_step=0.5,
        rounding_rule="wmo_half_up", day0_obs_extreme_c=None, settlement_step_c=1.0,
        settlement_sigma_floor_c=None, city_unit="F",
    )
    assert applied_f is False  # F-unit gate off even with w>0


def test_golden_full_ladder_with_binding_settlement_floor_matches_hand_derivation():
    # (a'') GOLDEN over the FULL σ ladder: k (sharpen) BEFORE the floors, then a binding settlement σ
    # floor widens σ_used, then the catch-all cap pins each open-ended bin at its UN-FLOORED honest mass,
    # then the uniform-w mixture + constrained redistribution. Re-derive the exact transform and assert
    # byte-equality — this is the load-bearing "the extraction reproduces the prior in-line q" proof.
    bins = _bins_C()
    mu, sigma, k, w = 18.0, 1.0, 0.9, 0.20
    floor_c = 3.0  # settlement σ floor >> sigma*k => widens σ_used, would inflate the far catch-all
    q, capped, _ = mat._build_scaled_normal_uniform_q(
        mu=mu, sigma_pred=sigma, k=k, uniform_w=w, floor_steps=0.0, bins=bins, half_step=0.5,
        rounding_rule="wmo_half_up", day0_obs_extreme_c=None, settlement_step_c=1.0,
        settlement_sigma_floor_c=floor_c, city_unit="C",
    )

    half_step, rule = 0.5, "wmo_half_up"
    sigma_pred = sigma * k          # honest un-floored width (k applied)
    sigma_used = max(sigma_pred, floor_c)
    assert sigma_used == floor_c    # the floor binds in this fixture

    def _mass(b, s):
        return bin_probability_settlement(
            mu=mu, sigma=s, bin_low=(None if b.lower_c is None else float(b.lower_c)),
            bin_high=(None if b.upper_c is None else float(b.upper_c)), half_step=half_step, rounding_rule=rule,
        )

    # Per-bin mass at σ_used, with the catch-all cap at the σ_pred (honest) mass for open-ended bins.
    open_ended = {b.bin_id for b in bins if (b.lower_c is None) != (b.upper_c is None)}
    honest = {b.bin_id: _mass(b, sigma_pred) for b in bins if b.bin_id in open_ended}
    raw = {}
    for b in bins:
        m = _mass(b, sigma_used)
        if b.bin_id in open_ended and sigma_used > sigma_pred and honest[b.bin_id] < m:
            m = honest[b.bin_id]
        raw[b.bin_id] = m
    total = sum(raw.values())
    base = {bid: m / total for bid, m in raw.items()}
    honest_norm = {bid: honest[bid] / total for bid in honest}
    n = len(bins)
    u = 1.0 / n
    mixed = {bid: (1.0 - w) * base[bid] + w * u for bid in base}
    mtot = sum(mixed.values())
    qmix = {bid: v / mtot for bid, v in mixed.items()}
    capped_now = {bid for bid, hn in honest_norm.items() if qmix[bid] > hn}
    for bid in capped_now:
        qmix[bid] = honest_norm[bid]
    capped_mass = sum(qmix[bid] for bid in capped_now)
    uncapped_mass = sum(v for bid, v in qmix.items() if bid not in capped_now)
    residual = 1.0 - capped_mass
    scale = residual / uncapped_mass
    ref = {bid: (v if bid in capped_now else v * scale) for bid, v in qmix.items()}

    assert set(capped) == capped_now and capped_now, "the floor-widened far catch-all should be capped"
    for bid in ref:
        assert q[bid] == pytest.approx(ref[bid], abs=1e-12), f"bin {bid}: {q[bid]} != {ref[bid]}"
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# rho = 1 - exp(-C/W) mixture (pure dict arithmetic)
# ---------------------------------------------------------------------------


def test_rho_formula():
    # rho = 1 - exp(-C/W); C<=0 => rho=0.
    assert mat._city_rho_from_capital(0.0, 10) == 0.0
    assert mat._city_rho_from_capital(-3.0, 10) == 0.0
    assert mat._city_rho_from_capital(5.0, 10) == pytest.approx(1.0 - math.exp(-0.5), abs=1e-12)
    assert mat._city_rho_from_capital(20.0, 4) == pytest.approx(1.0 - math.exp(-5.0), abs=1e-12)
    # W<=0 => rho=0 (no eligible bin terms -> no capital scale).
    assert mat._city_rho_from_capital(5.0, 0) == 0.0


def test_rho_mix_endpoints_and_midpoint():
    qg = {"a": 0.6, "b": 0.3, "c": 0.1}
    qc = {"a": 0.2, "b": 0.5, "c": 0.3}
    # rho=0 => exactly global.
    m0 = mat._mix_q_by_rho(qg, qc, 0.0)
    for k in qg:
        assert m0[k] == pytest.approx(qg[k], abs=1e-12)
    # rho=1 => exactly city.
    m1 = mat._mix_q_by_rho(qg, qc, 1.0)
    for k in qc:
        assert m1[k] == pytest.approx(qc[k], abs=1e-12)
    # rho=0.5 => exact linear midpoint.
    mh = mat._mix_q_by_rho(qg, qc, 0.5)
    for k in qg:
        assert mh[k] == pytest.approx(0.5 * qg[k] + 0.5 * qc[k], abs=1e-12)
    assert sum(mh.values()) == pytest.approx(1.0, abs=1e-12)


def test_rho_mix_renormalizes_defensively():
    # If the two carriers don't each sum to 1 (e.g. q_lcb bound carriers), the mix still sums to 1
    # when renormalize=True. The bounds path uses renormalize=False (caller keeps raw carriers).
    qg = {"a": 0.4, "b": 0.2}  # sums 0.6
    qc = {"a": 0.1, "b": 0.5}  # sums 0.6
    m = mat._mix_q_by_rho(qg, qc, 0.5, renormalize=True)
    assert sum(m.values()) == pytest.approx(1.0, abs=1e-12)
    m_raw = mat._mix_q_by_rho(qg, qc, 0.5, renormalize=False)
    assert sum(m_raw.values()) == pytest.approx(0.6, abs=1e-12)


def test_rho_mix_bootstrap_draws_matches_served_probability_world():
    global_samples = {
        "a": [0.70, 0.50, 0.20],
        "b": [0.20, 0.30, 0.30],
        "c": [0.10, 0.20, 0.50],
    }
    city_samples = {
        "a": [0.30, 0.20, 0.10],
        "b": [0.50, 0.60, 0.40],
        "c": [0.20, 0.20, 0.50],
    }
    rho = 0.25

    mixed = mat._mix_q_samples_by_rho(global_samples, city_samples, rho)

    for draw_idx in range(3):
        assert sum(mixed[key][draw_idx] for key in mixed) == pytest.approx(1.0)
        for key in mixed:
            assert mixed[key][draw_idx] == pytest.approx(
                (1.0 - rho) * global_samples[key][draw_idx]
                + rho * city_samples[key][draw_idx]
            )


def test_rho_mix_bootstrap_draws_rejects_incoherent_carrier():
    with pytest.raises(ValueError, match="probability simplexes"):
        mat._mix_q_samples_by_rho(
            {"a": [0.8, 0.8], "b": [0.3, 0.3]},
            {"a": [0.4, 0.4], "b": [0.6, 0.6]},
            0.5,
        )


# ---------------------------------------------------------------------------
# SEAM COMPOSITION: candidate lookup -> q_global / q_city -> rho -> mix, exactly as
# _compute_posterior_payload wires it. Proves the three pieces compose to the served mixture and that
# rho derives from the earned capital + eligible-bin count. (The full materialize path needs a
# persisted-current fusion capture the fixtures cannot provide; this is the wiring-level proof.)
# ---------------------------------------------------------------------------


def test_seam_composition_capital_gated_mix_and_provenance(tmp_path, monkeypatch):
    import json as _json
    import src.config as _cfg

    bins = _bins_C()
    mu, sigma = 22.3, 1.7
    kg, wg = 0.90, 0.20          # global family pair
    keb, web, cap = 1.05, 0.05, 6.0  # city EB candidate + earned OOS score capital

    art = tmp_path / "sigma_scale_fit.json"
    art.write_text(_json.dumps({"_meta": {}, "families": {"C": {
        "fitted": True, "k": kg, "w": wg,
        "cities": {"Tokyo": {"k": keb, "w": web, "score_capital": cap}},
    }}}))
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)

    # The candidate lookup returns the city EB pair + capital (NOT a hard swap of the global).
    cand = mat._replacement_city_candidate_lookup("C", "Tokyo")
    assert cand == {"k": keb, "w": web, "score_capital": cap}

    # Build both carriers via the SAME pure builder the seam uses (global floor for both).
    q_global, _ = _call_builder(bins, mu=mu, sigma=sigma, k=kg, w=wg, floor_steps=0.0)
    q_city, _ = _call_builder(bins, mu=mu, sigma=sigma, k=keb, w=web, floor_steps=0.0)

    # W = eligible bin count (all bins, no day0). rho = 1 - exp(-C/W).
    W = len(q_global)
    rho = mat._city_rho_from_capital(cap, W)
    assert rho == pytest.approx(1.0 - math.exp(-cap / W), abs=1e-12)
    assert 0.0 < rho < 1.0  # a real, partial blend (not degenerate)

    served = mat._mix_q_by_rho(q_global, q_city, rho, renormalize=True)
    # The served q is the rho mixture and DIFFERS from pure global (the city actually moves it).
    for b in served:
        assert served[b] == pytest.approx((1.0 - rho) * q_global[b] + rho * q_city[b], abs=1e-12)
    assert sum(served.values()) == pytest.approx(1.0, abs=1e-12)
    assert any(abs(served[b] - q_global[b]) > 1e-6 for b in served), "the mix must move the served q"
