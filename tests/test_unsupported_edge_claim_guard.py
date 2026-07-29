# Lifecycle: created=2026-07-08; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Fire/silent regression for the unsupported_edge_claim_guard Stop hook — the advisory-only replacement for no_edge_rule1_guard.
# Reuse: Re-run this file before trusting the guard's behavior; it is the executable spec for fire/silent/known-limit cases.
#
# History: created 2026-07-08 as test_no_edge_rule1_guard.py. Rewritten
# 2026-07-28 when the guard it tests was inverted, not tuned. Rewritten again
# 2026-07-28 after adversarial review (GPT-5.6 Pro) on PR #452 found the
# replacement guard itself defective: it hard-blocked (reproducing the
# original defect's shape pointed the other way), its evidence predicate
# contradicted its own message text (releasing on ANY one of three claimed-
# required categories, and on bare keywords with no number), and its sample-
# scope inference was unfixable prose-parsing dressed up as precision. This
# revision tests the corrected shape: ADVISORY ONLY (never _BLOCK_SENTINEL),
# ALL THREE evidence categories required with actual numeric values (not
# keywords) to count as "supported", and the known, accepted limitations
# (global sample-scope inference, meta-discussion bypass) documented via
# test rather than chased with more regex.
"""Fire/silent regression for unsupported_edge_claim_guard.

Background (2026-07-28): the guard this file tests used to be
no_edge_rule1_guard, which BLOCKED the CONCLUSION "no edge" / "market
efficient" via a phrase blocklist — contradicting loop/LEDGER.yaml's own
rule that "'insufficient evidence' is a legitimate, required conclusion".
On 2026-07-28 that guard fired on a session's own summary of the plan to fix
it, proving the trigger was lexical (matched the string wherever it
appeared) rather than structural. Deleting that guard is the fix; the
replacement below is a mirror-shaped ADVISORY reminder for the opposite
failure mode, not a substitute enforcement mechanism — it never blocks.

It fires (returns a non-None advisory string, never `_BLOCK_SENTINEL`) on a
POSITIVE tradeable-edge / deploy-or-scale-up claim that is missing ANY of a
settled-sample count, an interval, or a settlement-graded reference (each
requiring an actual number near the token, not just the keyword) — or
states a count below the codebase's own floor (MIN_SETTLED_N=30, matching
loop/LEDGER.yaml min_n and src/decision/selection_calibrator.py MIN_N). It
never fires on a null result, and is exempt on messages that merely discuss
edges, guards, or this mechanism — a documented, accepted bypass for an
advisory lint (see test_meta_discussion_bypass_is_a_known_accepted_limit).
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


def _assert_fires_advisory_only(result) -> None:
    """A firing result must be a non-blocking advisory string: truthy, and
    NEVER the block sentinel. This is the structural guarantee the 2026-07-28
    review demanded — the guard must be incapable of blocking, by return
    shape, not by convention."""
    assert result is not None
    assert result != dispatch._BLOCK_SENTINEL
    assert isinstance(result, str) and result.strip()


# ---------------------------------------------------------------------------
# FIRE cases — unsupported positive claims (advisory, never blocking)
# ---------------------------------------------------------------------------


def test_edge_asserted_with_no_evidence_at_all_fires(tmp_path, monkeypatch):
    text = "We have edge on this market. Let's deploy real capital tomorrow."
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)


def test_deploy_decision_paired_with_edge_noun_but_no_evidence_fires(tmp_path, monkeypatch):
    text = "Scaling up position size — the edge here is real, go live now."
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)


def test_sample_count_below_calibrator_floor_fires(tmp_path, monkeypatch):
    # n=12 is a real settled count, but under MIN_SETTLED_N=30 — still fires.
    text = "Edge confirmed: n=12 settled, win rate strongly favors buy_yes. Deploying."
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)
    assert "n=12" in result and "30" in result


def test_confidence_language_without_interval_fires(tmp_path, monkeypatch):
    text = "There is tradeable alpha here — I'm confident, let's increase stake."
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)


def test_only_sample_count_present_still_fires(tmp_path, monkeypatch):
    """All THREE categories (count, interval, settlement reference) are
    required to count as 'supported' — a count alone is not enough, matching
    what the advisory text actually asks for."""
    text = "Edge confirmed: n=42 settled. Let's deploy real capital tomorrow."
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)
    assert "interval" in result.lower()


def test_only_interval_present_still_fires(tmp_path, monkeypatch):
    text = "Edge confirmed: Wilson lower bound 0.61. Let's deploy real capital tomorrow."
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)
    assert "settled-sample count" in result.lower() or "sample count" in result.lower()


def test_only_settlement_reference_present_still_fires(tmp_path, monkeypatch):
    text = (
        "Edge confirmed, graded against real settlement. "
        "Let's deploy real capital tomorrow."
    )
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)


def test_bare_keyword_with_no_number_does_not_count_as_evidence(tmp_path, monkeypatch):
    """Regression for the 2026-07-28 review finding: saying 'Wilson' or
    'settled outcomes' with no attached number used to release the guard.
    Evidence categories now require an actual value near the token."""
    text = (
        "Edge confirmed via Wilson bound on settled outcomes. "
        "Deploying real capital now."
    )
    result = _run(tmp_path, monkeypatch, text)
    _assert_fires_advisory_only(result)


# ---------------------------------------------------------------------------
# SILENT cases — null results, and fully-supported positive claims
# ---------------------------------------------------------------------------


def test_null_result_conclusion_never_fires(tmp_path, monkeypatch):
    """The exact class this guard must never touch — the old guard's defect."""
    text = "Insufficient evidence for edge on this market. No edge today; stopping here."
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_chinese_null_result_never_fires(tmp_path, monkeypatch):
    text = "结论:市场有效,今天无边可动。证据不足,停止。"
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_edge_claim_with_all_three_evidence_categories_does_not_fire(tmp_path, monkeypatch):
    """Only ALL THREE together (count + interval + settlement reference,
    each with an actual number where applicable) count as supported."""
    text = (
        "Edge confirmed: n=42 settled outcomes, Wilson lower bound 0.61, "
        "graded against real settlement. Deploying at current size."
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


# ---------------------------------------------------------------------------
# Known, accepted limitations — documented via test, not chased with regex
# ---------------------------------------------------------------------------


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


def test_meta_discussion_bypass_is_a_known_accepted_limit(tmp_path, monkeypatch):
    """KNOWN LIMIT (documented in the guard's module docstring, not a bug to
    fix): the meta-discussion exemption is a trivial bypass. A message that
    both references 'the guard' AND makes an unsupported positive claim is
    exempt anyway. Acceptable for an advisory lint that exists to catch the
    common case without re-creating the lexical-block failure mode — a
    smarter regex cannot fully close this without risking false positives on
    genuine discussion, which costs more than the miss."""
    text = "The guard is working; we have confirmed edge, scale up."
    result = _run(tmp_path, monkeypatch, text)
    assert result is None


def test_aggregate_sample_count_can_mask_a_thin_cell_known_limit(tmp_path, monkeypatch):
    """KNOWN LIMIT: sample-scope inference is global/best-effort, not tied to
    the specific claim. A large aggregate count elsewhere in the message can
    satisfy has_count even though it doesn't actually back the deploy
    decision. This is why the guard is advisory only — the calibrator and
    ledger, which see real per-cell counts, remain the authority."""
    text = (
        "Overall backtest has n=500 settled across all markets. Edge "
        "confirmed here, Wilson lower bound 0.61, graded against real "
        "settlement. Deploying on this specific thin market."
    )
    result = _run(tmp_path, monkeypatch, text)
    # The (unrelated) aggregate n=500 satisfies has_count even though it does
    # not describe THIS market's sample — documented limitation, not a bug.
    assert result is None
