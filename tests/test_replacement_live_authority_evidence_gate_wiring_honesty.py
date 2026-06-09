from __future__ import annotations

# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: wiring-audit 2026-06-09 (Fitz #4 code-provenance + iron rule 5).
#   Operator directive 2026-06-08 REMOVED the settlement-evidence promotion gate
#   `replacement_live_authority_evidence_gate` from the live authority path — LIVE_AUTHORITY
#   is now FLAG-ONLY. But the gate function stayed DEFINED, stayed IMPORTED into the reactor,
#   and stale comments still asserted it "gates"/"both pass" — a dead-but-ADVERTISED guard. A
#   future operator/auditor trusting those comments could arm trade_authority believing a
#   settlement-evidence proof protects real capital, when it does not (q_lcb + fractional
#   Kelly + RiskGuard are the only remaining bounds).
#
# RELATIONSHIP ANTIBODY (make the error CATEGORY unconstructable): code and comments MUST
# agree about whether the evidence gate gates LIVE_AUTHORITY.
#
#   - If `replacement_live_authority_evidence_gate` is CALLED anywhere in src/ (the gate is
#     wired), comments describing it are fine.
#   - If it has ZERO call-sites (the current operator-directed flag-only state), then
#       (a) the reactor must NOT import it (a dead import advertises a dependency it lacks),
#       (b) no live-path file may contain the specific FALSE assertions that it gates the
#           path ("both_evidence_live_authority", "evidence_gate` (:5640) both pass").
#
# This turns "dead guard + lying comment" into a CI red the moment it reappears.

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GATE_NAME = "replacement_live_authority_evidence_gate"

# The two live-path files in the authority chain (definer + consumer).
RUNTIME_POLICY = "src/data/replacement_forecast_runtime_policy.py"
REACTOR = "src/engine/event_reactor_adapter.py"

# The specific FALSE precondition claims removed 2026-06-09. Either of these reappearing while
# the gate is uncalled means a comment asserts a guard the code does not enforce.
FORBIDDEN_FALSE_ASSERTIONS = (
    "both_evidence_live_authority",
    "evidence_gate` (:5640) both pass",
)


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _gate_call_sites() -> list[str]:
    """Every Call whose function resolves to GATE_NAME, across src/."""
    sites: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
            if name == GATE_NAME:
                sites.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return sites


def test_evidence_gate_code_and_comments_agree():
    """If the evidence gate is not called on the live path, the reactor must not import it and
    no comment may assert it gates LIVE_AUTHORITY. If it IS called, this is a no-op."""
    call_sites = _gate_call_sites()
    if call_sites:
        # The gate is wired into the live path — comments describing it are legitimate.
        return

    # Gate is dead (zero call-sites). Enforce that nothing advertises it as a live guard.
    reactor_src = _src(REACTOR)
    # (a) the reactor must not IMPORT an uncalled gate (dead import).
    assert f"import {GATE_NAME}" not in reactor_src, (
        f"{REACTOR} imports {GATE_NAME} but never calls it — a dead import that advertises a "
        "settlement-evidence dependency the live path no longer has. Remove the import "
        "(operator-directed flag-only LIVE_AUTHORITY) or re-wire the gate with a real call."
    )
    # (b) no live-path file may assert the gate gates authority while it is uncalled.
    for rel in (RUNTIME_POLICY, REACTOR):
        src = _src(rel)
        for phrase in FORBIDDEN_FALSE_ASSERTIONS:
            assert phrase not in src, (
                f"{rel} contains the stale claim {phrase!r} asserting "
                f"{GATE_NAME} gates LIVE_AUTHORITY, but the gate has ZERO call-sites "
                "(operator directive 2026-06-08 made LIVE_AUTHORITY flag-only). A comment must "
                "not advertise a settlement-evidence proof that no longer guards real capital."
            )
