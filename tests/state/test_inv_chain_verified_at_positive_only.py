# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-position-model-refactor.md (Finding 1, PR A scaffold; flipped to passing by PR C0)
"""Antibody invariants: `chain_verified_at` is a POSITIVE-observation timestamp only.

Finding 1 (P1 confirmed bug): chain_reconciliation.py currently writes
`pos.chain_verified_at = now_iso` in branches where the local position is
ABSENT from the chain snapshot (lines 920 — local-only missing; 931 — exit
pending missing). Downstream classifier `classify_chain_state()` then
treats those timestamps as "recent positive verification", inverting the
intended semantics of CHAIN_EMPTY vs CHAIN_UNKNOWN.

The fix (PR C0, same session):
  1. Add `Position.last_chain_absence_observed_at`.
  2. Stop writing `chain_verified_at` on absence; write absence field instead.
  3. Classifier reads positive observations only.

This test is STRICT-XFAIL until PR C0 lands. Meta-verify protocol: PR C0
flips both invariants below to passing without modifying the test body.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CHAIN_RECON_PATH = REPO_ROOT / "src" / "state" / "chain_reconciliation.py"
PORTFOLIO_PATH = REPO_ROOT / "src" / "state" / "portfolio.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_position_has_chain_absence_observation_field() -> None:
    """Position dataclass must have a typed absence-observation timestamp distinct
    from `chain_verified_at`."""
    source = _read(PORTFOLIO_PATH)
    assert "last_chain_absence_observed_at" in source, (
        "Position is missing `last_chain_absence_observed_at` — required to split "
        "positive chain observation from absence observation (Finding 1)."
    )


def test_chain_reconciliation_absence_branches_do_not_advance_positive_timestamp() -> None:
    """Every `<expr>.chain_verified_at = ...` write in chain_reconciliation.py
    must be in a POSITIVE-observation context. A positive context has
    `chain_state` set to `"synced"` (or assigned from a positive-context
    `rescued`/`corrected` object) within ±10 lines. An absence context
    (`chain_state` set to `"local_only"` / `"exit_pending_missing"` /
    `"chain_only"`) within ±10 lines is a violation.

    Heuristic chosen because each reconcile branch is bounded by an explicit
    chain_state mutation in immediate vicinity.
    """
    source = _read(CHAIN_RECON_PATH)
    lines = source.splitlines()
    tree = ast.parse(source)

    POSITIVE_STATES = ("synced",)
    ABSENCE_STATES = ("local_only", "exit_pending_missing", "chain_only")

    violations: list[str] = []
    for sub in ast.walk(tree):
        if not isinstance(sub, ast.Assign):
            continue
        for target in sub.targets:
            if not (isinstance(target, ast.Attribute) and target.attr == "chain_verified_at"):
                continue
            lineno = sub.lineno
            lo = max(1, lineno - 10)
            hi = min(len(lines), lineno + 10)
            window = "\n".join(lines[lo - 1 : hi])
            positive_hit = any(f'chain_state = "{s}"' in window for s in POSITIVE_STATES)
            absence_hit = any(f'chain_state = "{s}"' in window for s in ABSENCE_STATES)
            if absence_hit and not positive_hit:
                violations.append(
                    f"{CHAIN_RECON_PATH.name}:{lineno}: chain_verified_at write near "
                    f"absence-state mutation (chain_state set to one of {ABSENCE_STATES})"
                )

    assert not violations, (
        "chain_verified_at must represent POSITIVE chain observation only. "
        "Use last_chain_absence_observed_at for absence reconciles. "
        "Detected absence-branch writes:\n  " + "\n  ".join(violations)
    )
