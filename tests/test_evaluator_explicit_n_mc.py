# Created: 2026-04-30
# Last reused/audited: 2026-05-08
# Lifecycle: created=2026-04-30; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Antibody for BLOCKER #2 (architect audit 2026-04-30) — evaluator entry
#          path must pass n_mc explicitly to p_vector / p_raw_vector calls so
#          that runtime n_mc never gets pinned to an import-time DEFAULT_N_MC
#          cache. Mirrors the existing pattern at monitor_refresh.py:205, 502.
# Reuse: Re-run when evaluator entry probability-vector call sites or ensemble
#        Monte Carlo configuration plumbing change.
# Authority basis: docs/reference/zeus_calibration_weighting_authority.md LAW 4
#                  forbidden move 7 (runtime n_mc >= 5000) + monitor_refresh
#                  reference pattern.
"""Antibody — evaluator entry path threads n_mc explicitly.

Pre-fix (architect audit 2026-04-30): src/engine/evaluator.py called
``day0.p_vector(bins)`` and ``ens.p_raw_vector(bins)`` without n_mc. The inner
functions re-resolved ``ensemble_n_mc()`` / ``day0_n_mc()`` at call time, so
runtime behavior was correct (10000 today). But the contract was implicit and
``ensemble_signal.py:101 DEFAULT_N_MC = ensemble_n_mc()`` is import-time-cached
— a future refactor that uses the cache instead of the function would freeze
n_mc to whatever was in settings.json at process start.

Post-fix: both call sites pass n_mc=ensemble_n_mc() / day0_n_mc() explicitly,
matching monitor_refresh.py:205, 502.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_PY = PROJECT_ROOT / "src" / "engine" / "evaluator.py"


def test_evaluator_imports_n_mc_resolvers():
    """Both ensemble_n_mc and day0_n_mc must be imported in evaluator."""
    txt = EVALUATOR_PY.read_text(encoding="utf-8")
    assert "ensemble_n_mc" in txt, "evaluator must import ensemble_n_mc"
    assert "day0_n_mc" in txt, "evaluator must import day0_n_mc"


def test_evaluator_threads_explicit_n_mc_into_p_vector_calls():
    """The two p_vector / p_raw_vector calls in evaluator must pass n_mc explicitly.

    Pre-fix sites:
        p_raw = day0.p_vector(bins)
        p_raw = ens.p_raw_vector(bins)

    Post-fix sites:
        p_raw = day0.p_vector(bins, n_mc=day0_n_mc())
        p_raw = ens.p_raw_vector(bins, n_mc=ensemble_n_mc())

    A naive grep for ``p_vector(bins)`` would reintroduce the implicit pattern
    on the entry path. This test fires if either explicit-n_mc form goes
    missing.
    """
    txt = EVALUATOR_PY.read_text(encoding="utf-8")
    assert "day0.p_vector(bins, n_mc=day0_n_mc())" in txt, (
        "evaluator day0 entry path must call day0.p_vector(bins, n_mc=day0_n_mc()) explicitly"
    )
    assert "ens.p_raw_vector(bins, n_mc=ensemble_n_mc())" in txt, (
        "evaluator multi-day entry path must call ens.p_raw_vector(bins, n_mc=ensemble_n_mc()) explicitly"
    )


def test_evaluator_no_implicit_p_vector_call_remains():
    """No bare p_vector(bins)/p_raw_vector(bins) without n_mc on the hot entry path.

    Defends against a future refactor that drops the n_mc kwarg via a copy/
    paste regression. If a legitimate test or scaffolding call needs the
    bare form, it should live outside src/engine/evaluator.py.
    """
    txt = EVALUATOR_PY.read_text(encoding="utf-8")
    # Bare-call regex: a line that calls p_vector(bins) or p_raw_vector(bins)
    # and immediately closes the paren — no kwargs.
    bad_phrases = [
        "day0.p_vector(bins)\n",
        "ens.p_raw_vector(bins)\n",
        "day0.p_vector(bins) ",
        "ens.p_raw_vector(bins) ",
    ]
    for bad in bad_phrases:
        assert bad not in txt, (
            f"evaluator.py must not contain implicit n_mc call {bad!r}; "
            f"pass n_mc=ensemble_n_mc() / day0_n_mc() explicitly"
        )


def test_monitor_refresh_pattern_unchanged():
    """Reference: monitor_refresh.py is the established pattern. Pin its shape."""
    monitor_path = PROJECT_ROOT / "src" / "engine" / "monitor_refresh.py"
    txt = monitor_path.read_text(encoding="utf-8")
    # monitor_refresh has historically passed n_mc=ensemble_n_mc() at line 205
    # and n_mc=day0_n_mc() at line 502. Pin the explicit pattern (line numbers
    # may drift but the kwarg pattern must remain).
    assert "n_mc=ensemble_n_mc()" in txt, (
        "monitor_refresh must continue to pass n_mc=ensemble_n_mc() explicitly"
    )
    assert "n_mc=day0_n_mc()" in txt, (
        "monitor_refresh must continue to pass n_mc=day0_n_mc() explicitly"
    )
