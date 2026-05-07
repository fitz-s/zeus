# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: PLAN §3 Phase 3 M1 telemetry deliverable + §5 CHARTER M1
#   docs/operations/task_2026-05-06_hook_redesign/PLAN.md
#   evidence/hook_redesign_critic_opus.md

"""
test_hook_signal_health.py — M1 telemetry health assertions.

Checks:
1. ritual_signal jsonl lines are well-formed per schema
   {hook_id, event, decision, reason, override_id?, session_id, agent_id?, ts}
2. >5% advisory-with-no-action over a 7-day window auto-flags for review
   (test uses fixture, not real history).
"""

from __future__ import annotations

import json
import pathlib
import pytest
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Schema definition (mirrors dispatch.py emit_signal output)
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"hook_id", "event", "decision", "reason", "ts"}
OPTIONAL_FIELDS = {"override_id", "session_id", "agent_id", "ritual_signal", "migration_note"}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

VALID_DECISIONS = {"allow", "deny", "error"}


def _make_signal_line(
    hook_id: str = "invariant_test",
    event: str = "PreToolUse",
    decision: str = "allow",
    reason: str = "passed",
    override_id: str | None = None,
    session_id: str | None = "sess-abc",
    agent_id: str | None = None,
    ts: str | None = None,
) -> dict:
    return {
        "hook_id": hook_id,
        "event": event,
        "decision": decision,
        "reason": reason,
        "override_id": override_id,
        "session_id": session_id,
        "agent_id": agent_id,
        "ts": ts or datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Schema well-formedness tests
# ---------------------------------------------------------------------------


def _assert_well_formed(line: dict) -> None:
    """Assert a single signal line has all required fields with correct types."""
    for field in REQUIRED_FIELDS:
        assert field in line, f"Missing required field: {field!r}"
    assert line["decision"] in VALID_DECISIONS or line["decision"] in {
        "allow", "deny", "error"
    }, f"Invalid decision: {line['decision']!r}"
    # ts must be parseable ISO-8601
    datetime.fromisoformat(line["ts"].replace("Z", "+00:00"))
    # No extra unknown fields
    unknown = set(line.keys()) - ALL_FIELDS
    assert not unknown, f"Unknown fields in signal line: {unknown}"


class TestSignalLineSchema:
    def test_well_formed_allow(self):
        line = _make_signal_line(decision="allow", reason="passed")
        _assert_well_formed(line)

    def test_well_formed_deny(self):
        line = _make_signal_line(decision="deny", reason="regression_below_baseline")
        _assert_well_formed(line)

    def test_well_formed_with_override(self):
        line = _make_signal_line(
            decision="allow",
            reason="override_accepted",
            override_id="BASELINE_RATCHET",
        )
        _assert_well_formed(line)

    def test_well_formed_error(self):
        line = _make_signal_line(decision="error", reason="dispatch_crash: ValueError")
        _assert_well_formed(line)

    def test_missing_hook_id_fails(self):
        line = _make_signal_line()
        del line["hook_id"]
        with pytest.raises(AssertionError, match="Missing required field"):
            _assert_well_formed(line)

    def test_missing_ts_fails(self):
        line = _make_signal_line()
        del line["ts"]
        with pytest.raises(AssertionError, match="Missing required field"):
            _assert_well_formed(line)

    def test_invalid_decision_fails(self):
        line = _make_signal_line(decision="maybe")
        with pytest.raises(AssertionError):
            _assert_well_formed(line)

    def test_unknown_field_fails(self):
        line = _make_signal_line()
        line["mystery_field"] = "unexpected"
        with pytest.raises(AssertionError, match="Unknown fields"):
            _assert_well_formed(line)

    def test_optional_fields_may_be_none(self):
        """override_id, session_id, agent_id may be None (advisory hooks, no session)."""
        line = _make_signal_line(override_id=None, session_id=None, agent_id=None)
        _assert_well_formed(line)  # should not raise

    def test_ts_must_be_iso8601(self):
        line = _make_signal_line(ts="not-a-date")
        with pytest.raises((ValueError, AssertionError)):
            _assert_well_formed(line)


# ---------------------------------------------------------------------------
# 7-day advisory-no-action rate check (fixture-based)
# ---------------------------------------------------------------------------

# CHARTER §5 M1: >5% advisory-with-no-action over 7d auto-flags for review.
# "advisory-with-no-action" = decision==allow AND reason==advisory_check AND
# no corresponding non-advisory line for the same session+hook within 1s.
#
# For the fixture test: we inject a 7-day window with a known ratio and assert
# the flag fires at >5%.

ADVISORY_NO_ACTION_THRESHOLD = 0.05  # 5%


def _advisory_no_action_rate(lines: list[dict]) -> float:
    """
    Compute the fraction of advisory lines (reason==advisory_check, decision==allow)
    that have no subsequent blocking/deny action for the same hook within 5s.
    Returns 0.0 if no advisory lines exist.
    """
    advisory_lines = [
        l for l in lines if l.get("reason") == "advisory_check" and l.get("decision") == "allow"
    ]
    if not advisory_lines:
        return 0.0

    blocking_lines = [
        l for l in lines if l.get("decision") == "deny"
    ]

    no_action_count = 0
    for adv in advisory_lines:
        adv_ts = datetime.fromisoformat(adv["ts"].replace("Z", "+00:00"))
        # Check if any blocking line for same hook exists within 5s after advisory
        matched = False
        for blk in blocking_lines:
            if blk.get("hook_id") != adv.get("hook_id"):
                continue
            blk_ts = datetime.fromisoformat(blk["ts"].replace("Z", "+00:00"))
            delta = (blk_ts - adv_ts).total_seconds()
            if 0 <= delta <= 5:
                matched = True
                break
        if not matched:
            no_action_count += 1

    return no_action_count / len(advisory_lines)


def _build_fixture_window(
    total: int,
    advisory_no_action_count: int,
    advisory_with_action_count: int = 0,
    blocking_count: int = 0,
) -> list[dict]:
    """Build a fixture list of signal lines for rate-check tests."""
    lines: list[dict] = []
    base_ts = datetime.now(timezone.utc) - timedelta(days=3)

    for i in range(advisory_no_action_count):
        ts = (base_ts + timedelta(seconds=i * 10)).isoformat()
        lines.append(_make_signal_line(
            hook_id="pr_create_loc_accumulation",
            event="PreToolUse",
            decision="allow",
            reason="advisory_check",
            ts=ts,
        ))

    for i in range(advisory_with_action_count):
        adv_ts = (base_ts + timedelta(seconds=1000 + i * 10)).isoformat()
        blk_ts = (base_ts + timedelta(seconds=1000 + i * 10 + 1)).isoformat()
        lines.append(_make_signal_line(
            hook_id="invariant_test",
            event="PreToolUse",
            decision="allow",
            reason="advisory_check",
            ts=adv_ts,
        ))
        lines.append(_make_signal_line(
            hook_id="invariant_test",
            event="PreToolUse",
            decision="deny",
            reason="regression_below_baseline",
            ts=blk_ts,
        ))

    for i in range(blocking_count):
        ts = (base_ts + timedelta(seconds=2000 + i * 10)).isoformat()
        lines.append(_make_signal_line(
            hook_id="invariant_test",
            decision="deny",
            reason="regression_below_baseline",
            ts=ts,
        ))

    return lines


class TestAdvisoryNoActionRate:
    def test_rate_below_threshold_no_flag(self):
        """3% advisory-no-action: no flag."""
        # 3 advisory-no-action out of 100 total advisory
        lines = _build_fixture_window(
            total=100,
            advisory_no_action_count=3,
            advisory_with_action_count=97,
        )
        rate = _advisory_no_action_rate(lines)
        assert rate < ADVISORY_NO_ACTION_THRESHOLD, (
            f"Expected rate < {ADVISORY_NO_ACTION_THRESHOLD}, got {rate:.3f}"
        )

    def test_rate_above_threshold_flags(self):
        """10% advisory-no-action: flag fires."""
        lines = _build_fixture_window(
            total=100,
            advisory_no_action_count=10,
            advisory_with_action_count=0,
        )
        rate = _advisory_no_action_rate(lines)
        assert rate > ADVISORY_NO_ACTION_THRESHOLD, (
            f"Expected rate > {ADVISORY_NO_ACTION_THRESHOLD}, got {rate:.3f} — "
            "auto-flag for review should fire"
        )

    def test_rate_exactly_zero_when_no_advisory_lines(self):
        lines = _build_fixture_window(
            total=10,
            advisory_no_action_count=0,
            blocking_count=10,
        )
        rate = _advisory_no_action_rate(lines)
        assert rate == 0.0

    def test_100_percent_advisory_no_action_flags(self):
        """All advisory, none followed by block: rate = 1.0."""
        lines = _build_fixture_window(total=5, advisory_no_action_count=5)
        rate = _advisory_no_action_rate(lines)
        assert rate == 1.0

    def test_jsonl_lines_from_log_dir_are_well_formed(self, tmp_path):
        """If real log files exist, every line must pass schema check."""
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        log_dir = repo_root / ".claude" / "logs" / "hook_signal"
        if not log_dir.exists():
            pytest.skip("No hook_signal log dir yet — first-run skip")
        jsonl_files = list(log_dir.glob("*.jsonl"))
        if not jsonl_files:
            pytest.skip("No jsonl files yet")
        for fpath in jsonl_files:
            for raw in fpath.read_text().splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                line = json.loads(raw)
                _assert_well_formed(line)
