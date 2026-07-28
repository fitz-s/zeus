# Created: 2026-07-08 (as test_no_edge_rule1_guard.py)
# Rewritten: 2026-07-28 — the guard it tests was inverted, not tuned.
"""Fire/silent regression for unsupported_edge_claim_guard.

Background (2026-07-28): the guard this file tests used to be
no_edge_rule1_guard, which blocked the CONCLUSION "no edge" / "market
efficient" via a phrase blocklist — contradicting loop/LEDGER.yaml's own
rule that "'insufficient evidence' is a legitimate, required conclusion".
On 2026-07-28 that guard fired on a session's own summary of the plan to fix
it, proving the trigger was lexical (matched the string wherever it
appeared) rather than structural. The guard was replaced with its mirror
image: it now fires on a POSITIVE tradeable-edge / deploy-or-scale-up claim
asserted without a settled-sample count, an interval, or settlement-graded
evidence — or with a stated count below the codebase's own floor
(MIN_SETTLED_N=30, matching loop/LEDGER.yaml min_n and
src/decision/selection_calibrator.py MIN_N). It never fires on a null
result, and is explicitly exempt on messages that merely discuss edges,
guards, or this mechanism — the 2026-07-28 incident is
test_meta_discussion_of_the_guard_itself_does_not_fire below.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"

spec = importlib.util.spec_from_file_location("dispatch_edge_claim_test", DISPATCH_PATH)
dispatch = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(DISPATCH_PATH.parent))
spec.loader.exec_module(dispatch)  # type: ignore[union-attr]


def _write_transcript(tmp_path: Path, assistant_text: str) -> str:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": assistant_text}]},
            }
        )
        + "\n"
    )
    return str(transcript)


def _run(tmp_path: Path, monkeypatch, text: str):
    monkeypatch.delenv("ZEUS_EDGE_CLAIM_GUARD_OFF", raising=False)
    payload = {"transcript_path": _write_transcript(tmp_path, text)}
    return dispatch._run_advisory_check_unsupported_edge_claim_guard(payload)


# ---------------------------------------------------------------------------
# FIRE cases — unsupported positive claims
# ---------------------------------------------------------------------------


def test_edge_asserted_with_no_evidence_at_all_fires(tmp_path, monkeypatch):
    text = "We have edge on this market. Let's deploy real capital tomorrow."
    result = _run(tmp_path, monkeypatch, text)
    assert result == dispatch._BLOCK_SENTINEL


def test_deploy_decision_paired_with_edge_noun_but_no_evidence_fires(tmp_path, monkeypatch):
    text = "Scaling up position size — the edge here is real, go live now."
    result = _run(tmp_path, monkeypatch, text)
    assert result == dispatch._BLOCK_SENTINEL


def test_sample_count_below_calibrator_floor_fires(tmp_path, monkeypatch):
    # n=12 is a real settled count, but under MIN_SETTLED_N=30 — still fires.
    text = "Edge confirmed: n=12 settled, win rate strongly favors buy_yes. Deploying."
    result = _run(tmp_path, monkeypatch, text)
    assert result == dispatch._BLOCK_SENTINEL


def test_confidence_language_without_interval_fires(tmp_path, monkeypatch):
    text = "There is tradeable alpha here — I'm confident, let's increase stake."
    result = _run(tmp_path, monkeypatch, text)
    assert result == dispatch._BLOCK_SENTINEL


# ---------------------------------------------------------------------------
# SILENT cases — null results, and supported positive claims
# ---------------------------------------------------------------------------


def test_null_result_conclusion_never_fires(tmp_path, monkeypatch):
    """The exact class this guard must never block — the old guard's defect."""
    text = "Insufficient evidence for edge on this market. No edge today; stopping here."
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_chinese_null_result_never_fires(tmp_path, monkeypatch):
    text = "结论:市场有效,今天无边可动。证据不足,停止。"
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_edge_claim_with_full_evidence_does_not_fire(tmp_path, monkeypatch):
    text = (
        "Edge confirmed: n=42 settled outcomes, Wilson lower bound 0.61, "
        "graded against real settlement. Deploying at current size."
    )
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_meta_discussion_of_the_guard_itself_does_not_fire(tmp_path, monkeypatch):
    """Regression for the 2026-07-28 incident: a session summarizing the plan
    to fix no_edge_rule1_guard was itself blocked because its prose matched a
    forbidden phrase. The replacement guard must never repeat this class of
    failure on discussion ABOUT edges/guards, even when the discussion
    mentions deploy/scale-up language in the course of describing the fix."""
    text = (
        "Plan: replace no_edge_rule1_guard in dispatch.py — the old guard "
        "blocked 'no edge' conclusions and forced the agent to keep "
        "searching until it found something to deploy. The new guard "
        "instead fires when the message asserts we have edge and should "
        "scale up without a settled-sample basis. RULE 1 is being repealed "
        "as contradicting loop/LEDGER.yaml."
    )
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_unrelated_code_deploy_does_not_fire(tmp_path, monkeypatch):
    """'deploy' alone (no edge/alpha noun anywhere) must never trigger."""
    text = "Tests pass. Deploying the hotfix to the worktree now."
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_bypass_env_suppresses_firing(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_EDGE_CLAIM_GUARD_OFF", "1")
    text = "We have edge on this market. Deploying real capital now."
    payload = {"transcript_path": _write_transcript(tmp_path, text)}
    result = dispatch._run_advisory_check_unsupported_edge_claim_guard(payload)
    assert result is None
