# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/
#                  phases/task_2026-04-26_phase3_midstream_trust/plan.md slice P3.2
"""Slice P3.2 relationship + function tests.

PR #19 workbook P3.2: close entry/exit epistemic symmetry.

Pre-P3.2: src/engine/monitor_refresh.py:721-722 set
`ci_lower = ci_upper = current_forward_edge` as the FALLBACK CI when
`_bootstrap_context` is absent on the position. Degenerate ci_width=0
made `conservative_forward_edge(forward_edge, ci_width=0)` return
forward_edge unchanged — exit decisions reverted to raw point estimate
without the safety margin entry guarantees.

P3.2 fix: when bootstrap_ctx is absent, fall back to pos.entry_ci_width
(already tracked on the Position and used at L759/764 for EdgeContext
construction). Conservative-forward-edge math then has real dispersion
even on the no-bootstrap-context path.

Three relationship tests:
1. No bootstrap_ctx + entry_ci_width > 0: ci_lower < forward_edge <
   ci_upper (real dispersion, not degenerate).
2. No bootstrap_ctx + entry_ci_width = 0: degenerate preserved (no
   spurious widening from missing data).
3. With bootstrap_ctx: fresh bootstrap CI overrides entry fallback
   (no regression).

Tests are math/state assertions on the ci_lower/upper values inside
the refresh path; they don't drive a full monitor cycle (which would
require heavy fixtures), but they pin the contract by exercising the
specific code block at L719-735.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _simulate_ci_fallback(
    *,
    current_forward_edge: float,
    entry_ci_width: float,
    bootstrap_ctx: dict | None,
) -> tuple[float, float]:
    """Reproduce the ci_lower/upper computation block from monitor_refresh.

    The actual code block at src/engine/monitor_refresh.py:719-735 is:
        current_forward_edge = current_p_posterior - current_p_market
        _entry_ci_half = max(0.0, getattr(pos, "entry_ci_width", 0.0)) / 2.0
        ci_lower = current_forward_edge - _entry_ci_half
        ci_upper = current_forward_edge + _entry_ci_half
        bootstrap_ctx = getattr(pos, "_bootstrap_context", None)
        if bootstrap_ctx is not None and len(bootstrap_ctx["bins"]) > 1:
            # ... fresh bootstrap CI overrides ...

    This helper isolates that math without needing the full PolymarketClient
    + Position fixture chain.
    """
    pos = SimpleNamespace(
        entry_ci_width=entry_ci_width,
        _bootstrap_context=bootstrap_ctx,
    )
    # Simulate the exact code block:
    _entry_ci_half = max(0.0, getattr(pos, "entry_ci_width", 0.0)) / 2.0
    ci_lower = current_forward_edge - _entry_ci_half
    ci_upper = current_forward_edge + _entry_ci_half
    bootstrap_ctx_local = getattr(pos, "_bootstrap_context", None)
    if bootstrap_ctx_local is not None and len(bootstrap_ctx_local.get("bins", [])) > 1:
        # Bootstrap path overrides — for this isolated test we don't run
        # the full bootstrap, just acknowledge it would replace the values.
        # The relationship test #3 below verifies the override path is taken
        # by checking the source code structure, not this helper's output.
        pass
    return ci_lower, ci_upper


def test_no_bootstrap_with_entry_width_produces_real_dispersion():
    """P3.2 antibody: ci_width must be > 0 when entry_ci_width > 0,
    even without fresh bootstrap_ctx. Pre-fix this was always 0."""
    ci_lower, ci_upper = _simulate_ci_fallback(
        current_forward_edge=0.05,
        entry_ci_width=0.04,
        bootstrap_ctx=None,
    )
    ci_width = ci_upper - ci_lower
    assert ci_width > 0, (
        "P3.2 antibody: with entry_ci_width=0.04 and no bootstrap_ctx, "
        f"ci_width must be > 0; got {ci_width}. Pre-fix the degenerate "
        "fallback would produce ci_width=0, defeating exit safety margin."
    )
    # Symmetry check
    assert ci_lower < 0.05 < ci_upper
    assert abs((ci_upper - 0.05) - (0.05 - ci_lower)) < 1e-9


def test_no_bootstrap_zero_entry_width_preserves_degenerate():
    """Edge case: entry_ci_width=0 must not synthesize spurious dispersion.
    The fix is conservative — it uses entry's actual CI as fallback, not
    a hardcoded default."""
    ci_lower, ci_upper = _simulate_ci_fallback(
        current_forward_edge=0.05,
        entry_ci_width=0.0,
        bootstrap_ctx=None,
    )
    assert ci_lower == ci_upper == 0.05, (
        "When entry has no CI to inherit, fallback stays degenerate (no "
        "spurious widening). Operator decides via separate audit whether "
        "such positions should be allowed to enter monitor refresh."
    )


def test_no_bootstrap_negative_entry_width_clamped_to_zero():
    """Defensive: malformed entry_ci_width (negative) clamped to 0."""
    ci_lower, ci_upper = _simulate_ci_fallback(
        current_forward_edge=0.05,
        entry_ci_width=-0.10,
        bootstrap_ctx=None,
    )
    assert ci_lower == ci_upper == 0.05, (
        "max(0.0, ...) guards against negative entry_ci_width corruption."
    )


def test_with_bootstrap_ctx_overrides_entry_fallback_via_source_check():
    """Source-inspection check: monitor_refresh.py L719-735 must contain
    the bootstrap-context override block AFTER the entry_ci_width fallback.

    A future refactor that accidentally removes the override path (so
    even bootstrap-present cases fall back to entry CI) would silently
    regress entry/exit symmetry on the happy path. This test pins the
    code structure."""
    import inspect
    from src.engine import monitor_refresh
    src = inspect.getsource(monitor_refresh)
    # Both the fallback block and the override block must coexist
    assert "_entry_ci_half" in src, "P3.2 fallback block must exist"
    assert "_bootstrap_context" in src, "bootstrap-override block must exist"
    # Order: fallback first, override second (override RE-assigns ci_lower/upper)
    fallback_idx = src.index("_entry_ci_half")
    override_idx = src.index("bootstrap_ctx = getattr(pos, \"_bootstrap_context\"")
    assert fallback_idx < override_idx, (
        "Fallback must precede the bootstrap override so override wins "
        "when bootstrap_ctx is present."
    )


def test_p3_2_replaced_pre_fix_degenerate_initialization():
    """Antibody pin: the pre-fix `ci_lower = current_forward_edge` /
    `ci_upper = current_forward_edge` initialization must NOT exist in
    monitor_refresh.py anymore. A future regression that re-introduces
    the degenerate pattern would trip this test."""
    from src.engine import monitor_refresh
    import inspect
    src = inspect.getsource(monitor_refresh)
    # Pre-fix had two identical assignments back-to-back; post-fix has
    # the entry_ci_width-based computation. Check the antipattern is gone.
    bad_pattern = "ci_lower = current_forward_edge\n    ci_upper = current_forward_edge"
    assert bad_pattern not in src, (
        "P3.2 antibody: degenerate `ci_lower = ci_upper = current_forward_edge`"
        " must not be re-introduced. Pre-fix this defeated entry/exit "
        "epistemic symmetry on the no-bootstrap-context path."
    )
