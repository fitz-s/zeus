# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Lock R3 Z0 source-of-truth correction and stale-plan cleanup.
# Reuse: Run when R3 plan docs, migration packet docs, or source-of-truth routing changes.
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z0.yaml
"""Z0 plan-lock antibodies for the R3 CLOB V2 source-of-truth rewrite."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PACKET = ROOT / "docs/operations/task_2026-04-26_polymarket_clob_v2_migration"
R3 = ROOT / "docs/operations/task_2026-04-26_ultimate_plan/r3"

ACTIVE_PLAN_DOCS = [
    MIGRATION_PACKET / "plan.md",
    MIGRATION_PACKET / "v2_system_impact_report.md",
    MIGRATION_PACKET / "open_questions.md",
    MIGRATION_PACKET / "AGENTS.md",
    MIGRATION_PACKET / "polymarket_live_money_contract.md",
    ROOT / "docs/operations/current_state.md",
    R3 / "R3_README.md",
    R3 / "ULTIMATE_PLAN_R3.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_dormant_tracker_in_active_plan_docs() -> None:
    """Z0 must remove dormant-tracker framing from active implementation surfaces."""
    pattern = re.compile(r"dormant[ -]?tracker", re.IGNORECASE)
    offenders = [str(path.relative_to(ROOT)) for path in ACTIVE_PLAN_DOCS if pattern.search(_read(path))]
    assert offenders == []


def test_no_v2_low_risk_drop_in_in_active_docs() -> None:
    """CLOB V2 must be framed as P0 live-money work, not low-risk drop-in."""
    pattern = re.compile(r"V2 low[ -]?risk|low[ -]?risk drop[ -]?in", re.IGNORECASE)
    offenders = [str(path.relative_to(ROOT)) for path in ACTIVE_PLAN_DOCS if pattern.search(_read(path))]
    assert offenders == []


def test_polymarket_live_money_contract_doc_exists() -> None:
    """The live-money contract is packet-local to avoid a new docs/architecture authority plane."""
    contract = MIGRATION_PACKET / "polymarket_live_money_contract.md"
    body = _read(contract)
    required = [
        "V2 SDK (`py-clob-client-v2`) is the only live placement path",
        "Heartbeat is mandatory for GTC/GTD live resting orders",
        "pUSD is BUY collateral; CTF outcome tokens are SELL inventory; never substitute",
        "No live placement may proceed when `CutoverGuard.current_state()` is not `LIVE_ENABLED`",
        "ExecutableMarketSnapshot freshness gates every command",
        "`MATCHED` is not `CONFIRMED`",
        "`VenueSubmissionEnvelope` contract layer",
        "Every cancel has a typed outcome: `CANCELED`, `CANCEL_FAILED`, or `CANCEL_UNKNOWN`",
    ]
    missing = [snippet for snippet in required if snippet not in body]
    assert missing == []


def test_v2_system_impact_report_has_falsified_premise_disclaimers() -> None:
    """The corrected impact report must explicitly preserve the busted-premise learnings."""
    body = _read(MIGRATION_PACKET / "v2_system_impact_report.md")
    required = [
        "pUSD as marketing label disclaimer",
        "V1 release date 2026-02-19",
        "Mandatory 10s heartbeat unsourced",
        "EIP-712 v1→v2 binary switch wrong",
        "`fee_rate_bps` removed is partial-truth",
        "`delayed` status unsourced",
        "`post_only` existed in V1 v0.34.2",
        "heartbeat existed in V1 v0.34.2",
    ]
    missing = [snippet for snippet in required if snippet not in body]
    assert missing == []


def test_no_live_path_imports_v1_sdk() -> None:
    """Z2 owns live-code SDK replacement; Z0 only installs the conditional antibody."""
    status = _read(R3 / "_phase_status.yaml")
    z2_complete = re.search(r"\n  Z2:\n(?:    .+\n)*?    status: COMPLETE\n", status) is not None
    if not z2_complete:
        pytest.skip("Z2 has not shipped; V1 live import gate activates post-Z2")

    live_paths = [
        ROOT / "src/data/polymarket_client.py",
        ROOT / "src/execution/executor.py",
        ROOT / "src/execution/exit_triggers.py",
    ]
    offenders = [str(path.relative_to(ROOT)) for path in live_paths if "from py_clob_client " in _read(path)]
    assert offenders == []
