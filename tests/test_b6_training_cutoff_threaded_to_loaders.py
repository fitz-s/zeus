# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC re-audit Blocker B6 (2026-05-28). The producer
#   computes `today_str = datetime.now(timezone.utc).date().isoformat()` and writes it
#   into the canonical row as `training_cutoff`, but the loaders that actually feed
#   the fit (`load_bucket_residuals`, `fit_city_predictive_error`,
#   `paired_delta_coverage`, `_effective_coverage_months`) are invoked with either no
#   `settled_before` kwarg or `settled_before=None`. The stored `training_cutoff` is
#   therefore a lie: the fit consumed all settled rows up to "now", not up to the
#   declared cutoff. Two-row reproducibility audit cannot reproduce a row from its
#   stored cutoff. Fix: thread `today_str` into every loader.
"""B6 — training_cutoff must be the cutoff actually used by the fit, not a label only."""
from __future__ import annotations

import ast
import inspect

import pytest


_TARGET_LOADERS = {
    "load_bucket_residuals",
    "fit_city_predictive_error",
    "paired_delta_coverage",
    "_effective_coverage_months",
}


def _collect_loader_calls(tree: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in _TARGET_LOADERS:
            calls.append(node)
    return calls


def _kwarg_value(call: ast.Call, kw_name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == kw_name:
            return kw.value
    return None


def test_producer_threads_today_str_settled_before_into_all_fit_loaders():
    """Static analysis: every loader call inside `fit_all` MUST pass
    `settled_before=today_str` (the same variable that is written into
    `training_cutoff`). Variable name is brittle but matches the producer's
    local convention; this test is the antibody that catches drift.

    Pre-fix RED:
      - load_bucket_residuals: no settled_before kwarg
      - fit_city_predictive_error: no settled_before kwarg
      - paired_delta_coverage: settled_before=None
      - _effective_coverage_months: settled_before=None
    """
    from scripts import fit_full_transport_error_models as mod

    src = inspect.getsource(mod.fit_all)
    tree = ast.parse(src)
    calls = _collect_loader_calls(tree)
    assert len(calls) >= 4, (
        f"expected ≥4 loader calls (one per loader) inside fit_all; found {len(calls)}"
    )

    seen_loaders: set[str] = set()
    failures: list[str] = []
    for call in calls:
        loader_name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr
        seen_loaders.add(loader_name)
        value = _kwarg_value(call, "settled_before")
        if value is None:
            failures.append(f"{loader_name}: missing settled_before kwarg")
            continue
        # Accept either Name(today_str) or Attribute access on a known carrier;
        # the producer's local idiom is the bare `today_str` Name.
        if not (isinstance(value, ast.Name) and value.id == "today_str"):
            failures.append(
                f"{loader_name}: settled_before must be `today_str` "
                f"(the same value written into training_cutoff), got "
                f"{ast.dump(value)}"
            )

    missing = _TARGET_LOADERS - seen_loaders
    assert not missing, f"loaders not invoked in fit_all (test stale or fit refactor): {missing}"
    assert not failures, "\n".join(failures)


def test_training_cutoff_equals_today_str_assignment():
    """Cross-check: the row write uses `training_cutoff=today_str`. If this drifts
    apart from the loader cutoff, the antibody above must still catch the
    inconsistency. Quick sanity on the write line.
    """
    from scripts import fit_full_transport_error_models as mod

    src = inspect.getsource(mod.fit_all)
    # `training_cutoff=today_str` must appear at least once (per-row write).
    assert "training_cutoff=today_str" in src, (
        "training_cutoff must be set from today_str; B6 antibody requires this anchor"
    )
