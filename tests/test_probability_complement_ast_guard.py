from __future__ import annotations

# Created: 2026-06-07
# Authority basis: live-money bug audit — Polymarket YES/NO legs are independent
# executable assets; production code must not construct one side with ``1 - x``.

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


LIVE_PROBABILITY_PATHS = (
    "src/engine/event_reactor_adapter.py",
    "src/engine/evaluator.py",
    "src/engine/monitor_refresh.py",
    "src/engine/replacement_forecast_hook_factory.py",
    "src/events/candidate_evaluation.py",
    "src/events/continuous_redecision.py",
    "src/events/opportunity_selector.py",
    "src/events/reactor.py",
    "src/strategy/live_inference/live_admission.py",
    "src/strategy/market_analysis.py",
    "src/strategy/market_analysis_family_scan.py",
)


def _one_minus_expressions(relative_path: str) -> list[str]:
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Sub):
            continue
        left = node.left
        if not isinstance(left, ast.Constant) or left.value not in (1, 1.0):
            continue
        findings.append(f"{relative_path}:{node.lineno}: {ast.unparse(node)}")
    return findings


def test_live_probability_code_does_not_construct_complements_with_one_minus_x():
    findings: list[str] = []
    for relative_path in LIVE_PROBABILITY_PATHS:
        findings.extend(_one_minus_expressions(relative_path))

    assert not findings, "\n".join(findings)
