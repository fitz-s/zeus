# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Relationship tests for EffectiveKellyContext and INV-kelly-effective enforcement (PR 7)
# Reuse: Inspect effective_kelly_context.py + evaluator.py before relying on these
"""R-EE tests for PR 7 — EffectiveKellyContext bucket policy and call-site threading.

R-EE.1: MissingEffectiveContextError / live-path warning behaviour
R-EE.2: AST audit — every _size_at_execution_price_boundary call passes effective_context
R-EE.3: FOK haircut >= FAK haircut (same bucket)
R-EE.4: Haircut monotonicity — wider spread = smaller or equal haircut
R-EE.7: fee_erased=True forces zero kelly size
R-EE.8: cycle_runtime passes EffectiveKellyContext from snapshot (structural check)
"""

from __future__ import annotations

import ast
import logging
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.contracts.effective_kelly_context import (
    EffectiveKellyContext,
    MissingEffectiveContextError,
    SPREAD_MID_THRESHOLD_USD,
    SPREAD_WIDE_THRESHOLD_USD,
    DEPTH_DEEP_THRESHOLD_SHARES,
    _HAIRCUT_TABLE,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_context(
    spread_usd: str = "0.02",
    depth: int = 200,
    order_type: str = "FOK",
    fee_erased: bool = False,
) -> EffectiveKellyContext:
    return EffectiveKellyContext(
        spread_usd=Decimal(spread_usd),
        depth_at_best_ask=depth,
        order_type=order_type,
        fee_erased=fee_erased,
    )


# ── R-EE.1: live-path raise + non-live warning on missing context ─────────────

def test_missing_context_raises_on_live_path():
    """INV-kelly-effective (A1 fixup): _size_at_execution_price_boundary raises
    MissingEffectiveContextError when effective_context=None on a live path.

    get_mode() returns "live" by default in the test environment; no patching
    needed for the raise-in-live branch.
    """
    from src.engine.evaluator import _size_at_execution_price_boundary

    with pytest.raises(MissingEffectiveContextError, match="INV-kelly-effective"):
        _size_at_execution_price_boundary(
            p_posterior=0.60,
            entry_price=0.50,
            fee_rate=0.02,
            sizing_bankroll=1000.0,
            kelly_multiplier=0.5,
            effective_context=None,
        )


def test_allow_missing_context_bypasses_raise_on_live_path(caplog):
    """allow_missing_context=True (pre-snapshot path) warns but does not raise on live."""
    from src.engine.evaluator import _size_at_execution_price_boundary

    with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
        result = _size_at_execution_price_boundary(
            p_posterior=0.60,
            entry_price=0.50,
            fee_rate=0.02,
            sizing_bankroll=1000.0,
            kelly_multiplier=0.5,
            effective_context=None,
            allow_missing_context=True,
        )
    assert result > 0.0
    assert any("INV-kelly-effective" in r.message for r in caplog.records)


def test_missing_context_logs_warning_on_non_live_path(caplog):
    """INV-kelly-effective: _size_at_execution_price_boundary logs WARNING (not
    raise) when effective_context=None on a non-live path (paper/replay/backtest).
    """
    from unittest.mock import patch
    from src.engine.evaluator import _size_at_execution_price_boundary

    with patch("src.config.get_mode", return_value="paper"):
        with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
            result = _size_at_execution_price_boundary(
                p_posterior=0.60,
                entry_price=0.50,
                fee_rate=0.02,
                sizing_bankroll=1000.0,
                kelly_multiplier=0.5,
                effective_context=None,
            )
    # Result is positive (graceful degrade — no haircut)
    assert result > 0.0
    # Warning must appear
    assert any("INV-kelly-effective" in r.message for r in caplog.records), (
        f"Expected INV-kelly-effective WARNING in log; records={[r.message for r in caplog.records]}"
    )


def test_effective_context_provided_applies_haircut():
    """When effective_context is wide+shallow, haircut reduces size vs tight+deep."""
    from src.engine.evaluator import _size_at_execution_price_boundary

    # Baseline: TIGHT+DEEP context → haircut=1.0 (no reduction)
    baseline = _size_at_execution_price_boundary(
        p_posterior=0.60,
        entry_price=0.50,
        fee_rate=0.02,
        sizing_bankroll=1000.0,
        kelly_multiplier=0.5,
        effective_context=_make_context(spread_usd="0.02", depth=500, order_type="FOK"),
    )
    # WIDE+SHALLOW FOK haircut = 0.30
    ctx = _make_context(spread_usd="0.12", depth=10, order_type="FOK")
    sized_with_context = _size_at_execution_price_boundary(
        p_posterior=0.60,
        entry_price=0.50,
        fee_rate=0.02,
        sizing_bankroll=1000.0,
        kelly_multiplier=0.5,
        effective_context=ctx,
    )
    # With 0.30 haircut, effective_kelly = 0.5 * 0.30 = 0.15 → much smaller
    assert sized_with_context < baseline * 0.5, (
        f"Expected haircut to halve size: baseline={baseline:.4f}, "
        f"with_context={sized_with_context:.4f}"
    )


# ── R-EE.2: AST audit — all call sites pass effective_context keyword ─────────

def test_ast_audit_all_kelly_callers_pass_effective_context():
    """AST-scan src/ (entire source tree) for _size_at_execution_price_boundary
    call sites; assert every call passes effective_context as an explicit keyword.

    A2 fixup: scope widened from src/engine/ to src/ so call sites added in
    src/execution/, src/strategy/, src/state/, or elsewhere are caught.

    ALLOW_LIST: files that intentionally omit effective_context (with documented
    reasons).  Each entry must include a comment justifying the exemption.
    """
    src_root = Path(__file__).parent.parent / "src"
    # Relative to repo root (src/...) to match the path format in error messages.
    repo_root = Path(__file__).parent.parent
    ALLOW_LIST = {
        # K2 backtest replay uses raw kelly_size (not _size_at_execution_price_boundary)
        # — listed for completeness; no actual call site.
        "src/backtest/executable_ev_replay.py",
    }
    target_fn = "_size_at_execution_price_boundary"

    missing = []
    for py_file in src_root.rglob("*.py"):
        rel = str(py_file.relative_to(repo_root))
        if rel in ALLOW_LIST:
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match func.id or func.attr
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name != target_fn:
                continue
            # Check for effective_context keyword
            kw_names = {kw.arg for kw in node.keywords}
            if "effective_context" not in kw_names:
                missing.append(f"{rel}:{node.lineno}")

    assert not missing, (
        f"Call sites missing effective_context keyword: {missing}\n"
        "All _size_at_execution_price_boundary calls must pass effective_context explicitly."
    )


# ── R-EE.3: FOK >= FAK haircut at every bucket ───────────────────────────────

def test_haircut_fok_always_gte_fak_same_bucket():
    """FOK haircut >= FAK haircut for every (spread_tier, depth_tier) combination.

    FOK is price-guaranteed (fill or kill), so only fill probability is discounted.
    FAK partial-fill at ask price compounds both price risk and depth risk.
    """
    for (spread_tier, depth_tier), (fok, fak) in _HAIRCUT_TABLE.items():
        assert fok >= fak, (
            f"FOK haircut must be >= FAK at ({spread_tier},{depth_tier}): "
            f"fok={fok}, fak={fak}"
        )


# ── R-EE.4: Haircut monotonicity — wider spread = smaller or equal haircut ───

@pytest.mark.parametrize("depth_tier,depth", [
    ("DEEP", DEPTH_DEEP_THRESHOLD_SHARES),
    ("SHALLOW", DEPTH_DEEP_THRESHOLD_SHARES - 1),
])
@pytest.mark.parametrize("order_type", ["FOK", "FAK"])
def test_haircut_tight_gte_mid_gte_wide(depth_tier, depth, order_type):
    """For fixed depth bucket and order_type: TIGHT >= MID >= WIDE haircut."""
    def hc(spread_usd_str: str) -> float:
        ctx = EffectiveKellyContext(
            spread_usd=Decimal(spread_usd_str),
            depth_at_best_ask=depth,
            order_type=order_type,
        )
        return ctx.haircut()

    tight = hc("0.01")   # TIGHT: < 0.05
    mid   = hc("0.07")   # MID:   >= 0.05 and < 0.10
    wide  = hc("0.12")   # WIDE:  >= 0.10

    assert tight >= mid, (
        f"TIGHT haircut must be >= MID for depth={depth_tier} order={order_type}: "
        f"tight={tight}, mid={mid}"
    )
    assert mid >= wide, (
        f"MID haircut must be >= WIDE for depth={depth_tier} order={order_type}: "
        f"mid={mid}, wide={wide}"
    )


# ── R-EE.7: fee_erased branch forces zero size ───────────────────────────────

def test_fee_erased_context_yields_zero_haircut():
    """EffectiveKellyContext.haircut() == 0.0 when fee_erased=True."""
    ctx = EffectiveKellyContext(
        spread_usd=Decimal("0.01"),
        depth_at_best_ask=500,
        order_type="FOK",
        fee_erased=True,
    )
    assert ctx.haircut() == 0.0


def test_fee_erased_context_yields_zero_size():
    """When EffectiveKellyContext.fee_erased=True, _size_at_execution_price_boundary
    returns 0.0 because effective_kelly_multiplier = km * 0.0 = 0.0."""
    from src.engine.evaluator import _size_at_execution_price_boundary

    ctx = EffectiveKellyContext(
        spread_usd=Decimal("0.01"),
        depth_at_best_ask=500,
        order_type="FOK",
        fee_erased=True,
    )
    result = _size_at_execution_price_boundary(
        p_posterior=0.70,
        entry_price=0.50,
        fee_rate=0.02,
        sizing_bankroll=1000.0,
        kelly_multiplier=0.5,
        effective_context=ctx,
    )
    assert result == 0.0, f"Expected 0.0 with fee_erased=True, got {result}"


# ── R-EE.8: cycle_runtime passes context from snapshot ───────────────────────

def test_cycle_runtime_imports_effective_kelly_context():
    """Structural check: cycle_runtime module imports EffectiveKellyContext,
    confirming threading is in place."""
    import importlib
    import src.engine.cycle_runtime as cr_module

    assert hasattr(cr_module, "EffectiveKellyContext") or True, (
        "EffectiveKellyContext must be imported in cycle_runtime"
    )
    # Read the module source and verify import
    src_path = Path(cr_module.__file__)
    src_text = src_path.read_text()
    assert "EffectiveKellyContext" in src_text, (
        "EffectiveKellyContext must be imported in cycle_runtime.py"
    )
    # Verify all three W2/W3/W4 call sites pass effective_context
    tree = ast.parse(src_text)
    target_fn = "_size_at_execution_price_boundary"
    call_sites_with_context = []
    call_sites_without = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name != target_fn:
            continue
        kw_names = {kw.arg for kw in node.keywords}
        if "effective_context" in kw_names:
            call_sites_with_context.append(node.lineno)
        else:
            call_sites_without.append(node.lineno)
    assert not call_sites_without, (
        f"cycle_runtime.py has {target_fn} calls without effective_context: "
        f"lines {call_sites_without}"
    )
    assert len(call_sites_with_context) >= 3, (
        f"Expected >= 3 call sites in cycle_runtime.py with effective_context, "
        f"found {len(call_sites_with_context)} at lines {call_sites_with_context}"
    )


# ── EffectiveKellyContext validators ─────────────────────────────────────────

def test_negative_depth_raises():
    """depth_at_best_ask < 0 must raise ValueError."""
    with pytest.raises(ValueError, match="depth_at_best_ask"):
        EffectiveKellyContext(
            spread_usd=Decimal("0.05"),
            depth_at_best_ask=-1,
            order_type="FOK",
        )


def test_negative_spread_raises():
    """spread_usd < 0 must raise ValueError."""
    with pytest.raises(ValueError, match="spread_usd"):
        EffectiveKellyContext(
            spread_usd=Decimal("-0.01"),
            depth_at_best_ask=100,
            order_type="FOK",
        )


def test_unknown_order_type_uses_fak_column():
    """Order types other than FOK fall through to FAK column (conservative)."""
    fak_ctx = EffectiveKellyContext(
        spread_usd=Decimal("0.07"),
        depth_at_best_ask=50,
        order_type="FAK",
    )
    gtc_ctx = EffectiveKellyContext(
        spread_usd=Decimal("0.07"),
        depth_at_best_ask=50,
        order_type="GTC",
    )
    assert fak_ctx.haircut() == gtc_ctx.haircut(), (
        "GTC and FAK must produce the same haircut (conservative fallback)"
    )
