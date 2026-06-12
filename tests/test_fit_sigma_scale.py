# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: OPERATOR LAW 2026-06-12 — the σ-scale (k) + uniform-mixture (w) correction is FITTED
#   by maximum likelihood (scripts/fit_sigma_scale.py), never hand-set. These tests prove the estimator
#   RECOVERS a known (k*, w*) from synthetic settled cells within its CI, and REFUSES on insufficient n.
"""Estimator antibodies for scripts/fit_sigma_scale.py (MLE σ-scale + uniform-mixture fit).

Invariants proven here:
  1. Synthetic recovery: cells generated from a KNOWN (k*, w*) -> the fit recovers (k*, w*) within CI.
  2. Refusal: a unit family with < min_cells settled cells -> artifact marks it fitted=False, k=1, w=0.
  3. Regression anchor: k=1, w=0 reproduces the materialized locally-Normal masses (no-op correction).
  4. Unit parsing: C-unit (1°C) and F-unit (2°F range) labels both parse with the correct step.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

import scripts.fit_sigma_scale as fs


# ---------------------------------------------------------------------------
# Synthetic cell construction
# ---------------------------------------------------------------------------

def _synthetic_cell(sigma_impl: float, n_interior: int, rng, k_true: float, w_true: float, step: float = 1.0):
    """Build a synthetic settled cell: an interior grid + 2 shoulders, with a winning bin SAMPLED from
    the TRUE q_adjusted(k_true, w_true). The fit must recover (k_true, w_true) from many such cells.
    """
    # Build labels mimicking the production q_json shape (°C interior + open shoulders).
    centers = [float(c) for c in range(0, n_interior)]
    items = []
    # open-low shoulder
    items.append([f"Will the highest temperature be {centers[0]-step:.0f}°C or below on June 9?", 0.0, centers[0] - step, True])
    for c in centers:
        items.append([f"Will the highest temperature be {c:.0f}°C on June 9?", 0.0, c, False])
    # open-high shoulder
    items.append([f"Will the highest temperature be {centers[-1]+step:.0f}°C or higher on June 9?", 0.0, centers[-1] + step, True])

    mode_index = 1 + n_interior // 2  # a central interior bin
    # Put the materialized q at the mode so sigma back-out returns ~sigma_impl.
    q_mode = float(fs._phi(0.5 / sigma_impl) - fs._phi(-0.5 / sigma_impl))
    items[mode_index][1] = q_mode
    # give the rest small masses so argmax is the mode
    for i, it in enumerate(items):
        if i != mode_index:
            it[1] = q_mode * 0.3 / (len(items) - 1)

    lo, hi = fs._cell_edges(items, mode_index, step)
    # TRUE q_adjusted: (1-w)*Normal(sigma_impl*k_true) + w*uniform
    base = fs._masses_from_edges(lo, hi, sigma_impl * k_true)
    u = 1.0 / len(items)
    q_true = (1.0 - w_true) * base + w_true * u
    q_true = q_true / q_true.sum()
    won = int(rng.choice(len(items), p=q_true))

    return {
        "city": "Syn", "target_date": "2026-06-09", "bucket": "A_24h",
        "n_bins": len(items), "sigma_impl": sigma_impl, "mode_index": mode_index,
        "items": items, "won_index": won, "step": step,
        "edges_lo": lo, "edges_hi": hi,
    }


def _synthetic_population(n_cells, k_true, w_true, seed=7):
    rng = np.random.default_rng(seed)
    cells = []
    for _ in range(n_cells):
        sigma = float(rng.uniform(0.7, 1.2))  # matches the surface's σ_implied median ≈0.9
        n_interior = int(rng.integers(8, 12))
        cells.append(_synthetic_cell(sigma, n_interior, rng, k_true, w_true))
    return cells


# ---------------------------------------------------------------------------
# 1. Synthetic recovery
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("k_true,w_true", [(2.0, 0.10), (1.5, 0.25)])
def test_fit_recovers_known_params_within_ci(k_true, w_true) -> None:
    cells = _synthetic_population(400, k_true, w_true, seed=3)
    k_hat, w_hat, nll = fs._fit_mle(cells)
    ci = fs._profile_ci(cells, k_hat, w_hat, nll)

    # point estimate is close to truth
    assert abs(k_hat - k_true) < 0.6, f"k_hat={k_hat} vs k_true={k_true}"
    assert abs(w_hat - w_true) < 0.12, f"w_hat={w_hat} vs w_true={w_true}"
    # truth lies inside the profile-likelihood CI
    assert ci["k"][0] - 1e-6 <= k_true <= ci["k"][1] + 1e-6, f"k_true {k_true} not in CI {ci['k']}"
    assert ci["w"][0] - 1e-6 <= w_true <= ci["w"][1] + 1e-6, f"w_true {w_true} not in CI {ci['w']}"


# ---------------------------------------------------------------------------
# 2. Refusal on insufficient n (via the same path main() uses)
# ---------------------------------------------------------------------------

def test_refusal_marks_family_unfitted(tmp_path, monkeypatch) -> None:
    """A family below min_cells must be written fitted=False, k=1, w=0 (materializer stays inert)."""
    import sys
    # 30 C cells < default 60 -> refuse. Build a tiny artifact via the public main() path.
    cells = _synthetic_population(30, 2.0, 0.1, seed=9)

    # Drive _build_cells indirectly: stub the DB read to return our synthetic cells for unit C.
    monkeypatch.setattr(fs, "_build_cells", lambda rows: ({"C": cells, "F": []}, "settled-synthetic"))
    monkeypatch.setattr(fs.sqlite3, "connect", lambda *a, **k: _FakeCon())

    out = tmp_path / "sigma_scale_fit.json"
    argv = ["fit_sigma_scale.py", "--out", str(out), "--min-cells", "60"]
    monkeypatch.setattr(sys, "argv", argv)
    rc = fs.main()
    assert rc == 0

    art = json.loads(out.read_text())
    assert art["families"]["C"]["fitted"] is False
    assert art["families"]["C"]["k"] == 1.0
    assert art["families"]["C"]["w"] == 0.0
    assert "INSUFFICIENT_CELLS" in art["families"]["C"]["refusal_reason"]
    # F (0 cells) also refused
    assert art["families"]["F"]["fitted"] is False


class _FakeCon:
    def cursor(self):
        return self
    def execute(self, *a, **k):
        return self
    def fetchall(self):
        return []
    def close(self):
        pass


# ---------------------------------------------------------------------------
# 3. Regression anchor: k=1, w=0 reproduces the locally-Normal masses (no-op)
# ---------------------------------------------------------------------------

def test_k1_w0_is_noop_relative_to_base_masses() -> None:
    rng = np.random.default_rng(1)
    cell = _synthetic_cell(0.9, 10, rng, k_true=1.0, w_true=0.0)
    base = fs._masses_from_edges(cell["edges_lo"], cell["edges_hi"], cell["sigma_impl"])
    q_adj = fs._cell_q_adjusted(cell, 1.0, 0.0)
    assert np.allclose(np.asarray(base), np.asarray(q_adj), atol=1e-12)


# ---------------------------------------------------------------------------
# 4. Unit-aware parsing: C (1°C) vs F (2°F range) step inference
# ---------------------------------------------------------------------------

def test_parse_c_unit_step_is_one() -> None:
    q = {
        "Will the highest temperature in Paris be 15°C or below on June 9?": 0.0,
        "Will the highest temperature in Paris be 16°C on June 9?": 0.1,
        "Will the highest temperature in Paris be 17°C on June 9?": 0.3,
        "Will the highest temperature in Paris be 18°C on June 9?": 0.2,
        "Will the highest temperature in Paris be 19°C or higher on June 9?": 0.0,
    }
    items, mode_index, step = fs._parse_cell(json.dumps(q))
    assert step == pytest.approx(1.0)
    assert items[mode_index][2] == pytest.approx(17.0)  # mode bin centre


def test_parse_f_unit_step_is_two_and_range_midpoint() -> None:
    q = {
        "Will the highest temperature in Atlanta be 67°F or below on June 8?": 0.0,
        "Will the highest temperature in Atlanta be between 68-69°F on June 8?": 0.1,
        "Will the highest temperature in Atlanta be between 70-71°F on June 8?": 0.4,
        "Will the highest temperature in Atlanta be between 72-73°F on June 8?": 0.2,
        "Will the highest temperature in Atlanta be 86°F or higher on June 8?": 0.0,
    }
    items, mode_index, step = fs._parse_cell(json.dumps(q))
    assert step == pytest.approx(2.0)
    assert items[mode_index][2] == pytest.approx(70.5)  # midpoint of 70-71


def test_winning_index_matches_f_range_bin() -> None:
    q = {
        "Will the highest temperature in Atlanta be 67°F or below on June 8?": 0.0,
        "Will the highest temperature in Atlanta be between 68-69°F on June 8?": 0.1,
        "Will the highest temperature in Atlanta be between 80-81°F on June 8?": 0.4,
    }
    items, _mode, step = fs._parse_cell(json.dumps(q))
    idx = fs._winning_index(items, "80-81°F", 81.0, step=step)
    assert idx is not None
    assert "80-81°F" in items[idx][0]
