# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase B.5
#                  scripts/ci/pr_monitor.py
"""
Unit tests for the first-principle PR monitor.

Covers the four first-principle invariants:
  1. Meaningful findings only — unresolved + non-empty body emits;
     resolved or empty bodies stay silent.
  2. CI failure emits immediately; CI success / pending stay silent.
  3. Dedup persistence — same finding/failure does NOT re-emit across runs.
  4. Terminal state emits exactly once.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts.ci.pr_monitor import (
    CI_FAILURE_CONCLUSIONS,
    DEFAULT_STALE_AFTER_SECONDS,
    check_stale_silence,
    extract_ci_failures,
    extract_unresolved_findings,
    format_ci_fail_line,
    format_finding_line,
    format_stale_silence_line,
    format_terminal_line,
    is_terminal,
    load_state,
    save_state,
    tick_once,
)


def _state(**overrides):
    """Build a state dict with the new fields, suitable for tick_once tests."""
    base = {
        "reported_threads": set(),
        "reported_failures": set(),
        "last_event_at": None,
        "last_stale_emit_at": None,
        "monitor_started_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _thread(tid: str, body: str, *, resolved: bool = False, author: str = "copilot[bot]",
            path: str = "src/foo.py", line: int = 10) -> dict:
    return {
        "id": tid,
        "isResolved": resolved,
        "comments": {
            "nodes": [
                {"author": {"login": author}, "path": path, "line": line, "body": body}
            ]
        },
    }


def _check(name: str, conclusion: str | None) -> dict:
    return {"name": name, "conclusion": conclusion, "status": "COMPLETED" if conclusion else "IN_PROGRESS"}


def _pr_data(*, threads=(), checks=(), state="OPEN") -> dict:
    return {
        "reviewThreads": list(threads),
        "statusCheckRollup": list(checks),
        "state": state,
    }


# ---------------------------------------------------------------------------
# Invariant 1: meaningful findings only
# ---------------------------------------------------------------------------


def test_finding_extracted_when_unresolved_with_body():
    pr = _pr_data(threads=[_thread("T1", "Possible null deref on line 42")])
    findings = extract_unresolved_findings(pr)
    assert len(findings) == 1
    assert findings[0]["tid"] == "T1"
    assert findings[0]["author"] == "copilot[bot]"


def test_finding_silent_when_resolved():
    pr = _pr_data(threads=[_thread("T1", "Already fixed", resolved=True)])
    assert extract_unresolved_findings(pr) == []


def test_finding_silent_when_body_empty():
    pr = _pr_data(threads=[_thread("T1", "")])
    assert extract_unresolved_findings(pr) == []


def test_finding_silent_when_body_whitespace_only():
    pr = _pr_data(threads=[_thread("T1", "   \n   \t  ")])
    assert extract_unresolved_findings(pr) == []


def test_finding_silent_when_no_comments():
    pr = _pr_data(threads=[{"id": "T1", "isResolved": False, "comments": {"nodes": []}}])
    assert extract_unresolved_findings(pr) == []


def test_multiple_findings_extracted_in_order():
    pr = _pr_data(
        threads=[
            _thread("T1", "first finding"),
            _thread("T2", "second finding", author="codex[bot]"),
        ]
    )
    findings = extract_unresolved_findings(pr)
    assert [f["tid"] for f in findings] == ["T1", "T2"]
    assert findings[1]["author"] == "codex[bot]"


# ---------------------------------------------------------------------------
# Invariant 2: CI failure emits, success/pending silent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("conclusion", sorted(CI_FAILURE_CONCLUSIONS))
def test_ci_failure_emits_for_each_failure_conclusion(conclusion):
    pr = _pr_data(checks=[_check("pytest", conclusion)])
    failures = extract_ci_failures(pr)
    assert len(failures) == 1
    assert failures[0]["conclusion"] == conclusion


def test_ci_success_silent():
    pr = _pr_data(checks=[_check("pytest", "SUCCESS")])
    assert extract_ci_failures(pr) == []


def test_ci_pending_silent():
    pr = _pr_data(checks=[_check("pytest", None)])
    assert extract_ci_failures(pr) == []


def test_ci_skipped_silent():
    pr = _pr_data(checks=[_check("pytest", "SKIPPED"), _check("ruff", "NEUTRAL")])
    assert extract_ci_failures(pr) == []


def test_ci_mixed_only_failures_emit():
    pr = _pr_data(
        checks=[
            _check("pytest", "SUCCESS"),
            _check("ruff", "FAILURE"),
            _check("mypy", None),
            _check("integration", "TIMED_OUT"),
        ]
    )
    failures = extract_ci_failures(pr)
    names = {f["name"] for f in failures}
    assert names == {"ruff", "integration"}


# ---------------------------------------------------------------------------
# Invariant 3: dedup persistence
# ---------------------------------------------------------------------------


def test_dedup_state_roundtrip(tmp_path: Path):
    spath = tmp_path / "state.json"
    state = {"reported_threads": {"T1", "T2"}, "reported_failures": {"pytest:FAILURE"}}
    save_state(spath, state)
    loaded = load_state(spath)
    assert loaded["reported_threads"] == {"T1", "T2"}
    assert loaded["reported_failures"] == {"pytest:FAILURE"}


def test_dedup_state_missing_returns_empty(tmp_path: Path):
    spath = tmp_path / "nonexistent.json"
    loaded = load_state(spath)
    assert loaded["reported_threads"] == set()
    assert loaded["reported_failures"] == set()


def test_dedup_state_corrupt_returns_empty(tmp_path: Path):
    spath = tmp_path / "bad.json"
    spath.write_text("{not valid json")
    loaded = load_state(spath)
    assert loaded["reported_threads"] == set()
    assert loaded["reported_failures"] == set()


def test_tick_does_not_reemit_known_finding(monkeypatch, capsys):
    """tick_once with a thread id already in state must stay silent."""
    pr = _pr_data(threads=[_thread("T1", "duplicate")])
    monkeypatch.setattr("scripts.ci.pr_monitor.gh_pr_view_v2", lambda pr_num, repo=None: (pr, None))
    state = {"reported_threads": {"T1"}, "reported_failures": set()}
    result = tick_once(343, repo="x/y", state=state, as_json=False)
    out = capsys.readouterr().out
    assert out == ""
    assert result is None


def test_tick_reemits_new_finding_then_dedups(monkeypatch, capsys):
    pr = _pr_data(threads=[_thread("T1", "novel finding"), _thread("T2", "second novel")])
    monkeypatch.setattr("scripts.ci.pr_monitor.gh_pr_view_v2", lambda pr_num, repo=None: (pr, None))
    state = {"reported_threads": set(), "reported_failures": set()}
    # First tick emits both
    tick_once(343, repo="x/y", state=state, as_json=False)
    out = capsys.readouterr().out
    assert "PR#343 FINDING" in out
    assert "novel finding" in out
    assert "second novel" in out
    # Second tick stays silent
    tick_once(343, repo="x/y", state=state, as_json=False)
    out2 = capsys.readouterr().out
    assert out2 == ""


def test_tick_dedup_ci_failure(monkeypatch, capsys):
    pr = _pr_data(checks=[_check("pytest", "FAILURE")])
    monkeypatch.setattr("scripts.ci.pr_monitor.gh_pr_view_v2", lambda pr_num, repo=None: (pr, None))
    state = {"reported_threads": set(), "reported_failures": set()}
    tick_once(343, repo="x/y", state=state, as_json=False)
    out1 = capsys.readouterr().out
    assert "PR#343 CI_FAIL pytest:FAILURE" in out1
    tick_once(343, repo="x/y", state=state, as_json=False)
    out2 = capsys.readouterr().out
    assert out2 == ""


def test_tick_reemits_new_failure_after_rerun_with_different_conclusion(
    monkeypatch, capsys
):
    """If a check was FAILURE then becomes TIMED_OUT, that's a NEW failure."""
    state = {"reported_threads": set(), "reported_failures": set()}
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (_pr_data(checks=[_check("pytest", "FAILURE")]), None),
    )
    tick_once(343, repo="x/y", state=state, as_json=False)
    capsys.readouterr()
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (_pr_data(checks=[_check("pytest", "TIMED_OUT")]), None),
    )
    tick_once(343, repo="x/y", state=state, as_json=False)
    out = capsys.readouterr().out
    assert "PR#343 CI_FAIL pytest:TIMED_OUT" in out


# ---------------------------------------------------------------------------
# Invariant 4: terminal state emits and returns
# ---------------------------------------------------------------------------


def test_terminal_merged_returns_state():
    assert is_terminal(_pr_data(state="MERGED")) == "MERGED"


def test_terminal_closed_returns_state():
    assert is_terminal(_pr_data(state="CLOSED")) == "CLOSED"


def test_terminal_open_returns_none():
    assert is_terminal(_pr_data(state="OPEN")) is None


def test_terminal_draft_returns_none():
    assert is_terminal(_pr_data(state="DRAFT")) is None


def test_tick_emits_terminal_when_merged(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (_pr_data(state="MERGED"), None),
    )
    state = {"reported_threads": set(), "reported_failures": set()}
    result = tick_once(343, repo="x/y", state=state, as_json=False)
    assert result == "MERGED"
    out = capsys.readouterr().out
    assert "PR#343 TERMINAL state=MERGED" in out


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def test_finding_line_truncates_long_body():
    finding = {
        "tid": "T1",
        "author": "copilot[bot]",
        "path": "src/foo.py",
        "line": 42,
        "body": "x" * 1000,
    }
    line = format_finding_line(343, finding)
    assert line.startswith("PR#343 FINDING [copilot[bot]] src/foo.py:42:")
    # Total body section after ": " should be ≤ 280 chars
    body_part = line.split(": ", 1)[1]
    assert len(body_part) <= 280
    assert body_part.endswith("...")


def test_finding_line_normalizes_newlines():
    finding = {
        "tid": "T1",
        "author": "codex[bot]",
        "path": "src/bar.py",
        "line": 7,
        "body": "line1\nline2\rline3",
    }
    line = format_finding_line(343, finding)
    assert "\n" not in line and "\r" not in line
    assert "line1 line2 line3" in line


def test_ci_fail_line_includes_name_and_conclusion():
    line = format_ci_fail_line(343, {"name": "pytest-money-path", "conclusion": "FAILURE"})
    assert line == "PR#343 CI_FAIL pytest-money-path:FAILURE"


def test_terminal_line_format():
    assert format_terminal_line(343, "MERGED") == "PR#343 TERMINAL state=MERGED"


# ---------------------------------------------------------------------------
# GH_ERROR — broken gh CLI is meaningful, not silent (Phase B.7 first principle)
# ---------------------------------------------------------------------------


def test_tick_emits_gh_error_when_gh_fails(monkeypatch, capsys):
    """A gh CLI failure must surface as PR#NN GH_ERROR rather than silence
    (Phase B.7 fix to PR #343 Copilot finding root cause: silent broken-gh
    masks all other events). Renamed from test_tick_silent_when_gh_returns_none."""
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (None, "graphql 401 unauthorized"),
    )
    state = _state()
    result = tick_once(343, repo="x/y", state=state, as_json=False)
    assert result is None
    out = capsys.readouterr().out
    assert "PR#343 GH_ERROR graphql 401 unauthorized" in out


def test_tick_dedups_repeated_gh_error(monkeypatch, capsys):
    """Same gh error message twice → only one emit, not two (signal-only)."""
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (None, "same persistent failure"),
    )
    state = _state()
    tick_once(343, repo="x/y", state=state, as_json=False)
    capsys.readouterr()
    tick_once(343, repo="x/y", state=state, as_json=False, now=time.time() + 1)
    out = capsys.readouterr().out
    assert "GH_ERROR" not in out


def test_tick_emits_different_gh_errors_separately(monkeypatch, capsys):
    state = _state()
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (None, "error A"),
    )
    tick_once(343, repo="x/y", state=state, as_json=False)
    capsys.readouterr()
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (None, "error B"),
    )
    tick_once(343, repo="x/y", state=state, as_json=False)
    out = capsys.readouterr().out
    assert "PR#343 GH_ERROR error B" in out


def test_format_gh_error_line():
    from scripts.ci.pr_monitor import format_gh_error_line
    line = format_gh_error_line(343, "graphql error: not found")
    assert line == "PR#343 GH_ERROR graphql error: not found"


# ---------------------------------------------------------------------------
# JSON emit mode
# ---------------------------------------------------------------------------


def test_json_mode_emits_kind_per_event(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (
            _pr_data(
                threads=[_thread("T1", "json mode finding")],
                checks=[_check("pytest", "FAILURE")],
            ),
            None,
        ),
    )
    state = {"reported_threads": set(), "reported_failures": set()}
    tick_once(343, repo="x/y", state=state, as_json=True)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    parsed = [json.loads(line) for line in out]
    kinds = {p["kind"] for p in parsed}
    assert kinds == {"finding", "ci_fail"}


# ---------------------------------------------------------------------------
# Phase B.6: stale-silence detector
# ---------------------------------------------------------------------------


def test_stale_silence_disabled_when_threshold_zero():
    state = _state(monitor_started_at=0.0)
    assert check_stale_silence(state, threshold_seconds=0, now=10000.0) is None


def test_stale_silence_disabled_when_threshold_negative():
    state = _state(monitor_started_at=0.0)
    assert check_stale_silence(state, threshold_seconds=-1, now=10000.0) is None


def test_stale_silence_silent_when_no_anchor():
    """Without monitor_started_at or last_event_at, cannot judge staleness."""
    state = _state()  # all None
    assert check_stale_silence(state, threshold_seconds=900, now=10000.0) is None


def test_stale_silence_silent_within_threshold():
    state = _state(monitor_started_at=1000.0)
    # only 500s elapsed, threshold 900s → silent
    assert check_stale_silence(state, threshold_seconds=900, now=1500.0) is None


def test_stale_silence_fires_at_threshold():
    state = _state(monitor_started_at=1000.0)
    # exactly threshold reached
    elapsed = check_stale_silence(state, threshold_seconds=900, now=1900.0)
    assert elapsed == 900


def test_stale_silence_fires_past_threshold():
    state = _state(monitor_started_at=1000.0)
    elapsed = check_stale_silence(state, threshold_seconds=900, now=3000.0)
    assert elapsed == 2000


def test_stale_silence_uses_last_event_at_as_anchor_when_more_recent():
    """If a real event fired AFTER monitor_started_at, that becomes the anchor."""
    state = _state(monitor_started_at=1000.0, last_event_at=2000.0)
    # 500s after last_event_at → silent (even though >900s after start)
    assert check_stale_silence(state, threshold_seconds=900, now=2500.0) is None
    # 1000s after last_event_at → fires
    elapsed = check_stale_silence(state, threshold_seconds=900, now=3000.0)
    assert elapsed == 1000


def test_stale_silence_dedup_within_window():
    """Once fired, do not re-fire until threshold passes again or new event resets."""
    state = _state(monitor_started_at=1000.0, last_stale_emit_at=2000.0)
    # 500s after last stale emit → suppress
    assert check_stale_silence(state, threshold_seconds=900, now=2500.0) is None


def test_stale_silence_refires_after_threshold_since_last_stale_emit():
    """If still silent and threshold has passed AGAIN since last stale emit, re-fire."""
    state = _state(monitor_started_at=0.0, last_stale_emit_at=1000.0)
    # 1100s after last stale emit + total elapsed from anchor still >threshold → re-fire
    elapsed = check_stale_silence(state, threshold_seconds=900, now=2100.0)
    assert elapsed == 2100


def test_tick_emits_stale_when_threshold_passed(monkeypatch, capsys):
    """tick_once with empty PR + stale-after set emits STALE_SILENCE."""
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (_pr_data(), None),  # gh OK but PR has nothing
    )
    state = _state(monitor_started_at=1000.0)
    tick_once(
        343,
        repo="x/y",
        state=state,
        as_json=False,
        stale_after_seconds=900,
        now=2000.0,
    )
    out = capsys.readouterr().out
    assert "PR#343 STALE_SILENCE elapsed=1000s" in out
    assert "gh pr view 343" in out
    # last_stale_emit_at recorded for dedup
    assert state["last_stale_emit_at"] == 2000.0


def test_tick_does_not_emit_stale_when_threshold_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (_pr_data(), None),
    )
    state = _state(monitor_started_at=0.0)
    tick_once(
        343,
        repo="x/y",
        state=state,
        as_json=False,
        stale_after_seconds=0,
        now=999999.0,
    )
    out = capsys.readouterr().out
    assert "STALE_SILENCE" not in out


def test_real_event_resets_stale_clock(monkeypatch, capsys):
    """A real finding emit must reset last_stale_emit_at so next stale window starts fresh."""
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (_pr_data(threads=[_thread("T1", "real finding")]), None),
    )
    state = _state(monitor_started_at=1000.0, last_stale_emit_at=1500.0)
    tick_once(
        343,
        repo="x/y",
        state=state,
        as_json=False,
        stale_after_seconds=900,
        now=2000.0,
    )
    out = capsys.readouterr().out
    assert "PR#343 FINDING" in out
    # Stale window resets
    assert state["last_stale_emit_at"] is None
    assert state["last_event_at"] == 2000.0


def test_stale_silence_fires_when_gh_repeatedly_unreachable(monkeypatch, capsys):
    """Phase B.7 design: first GH_ERROR fires immediately; subsequent same-message
    failures dedup (silent); after threshold passes since the first GH_ERROR,
    STALE_SILENCE fires as a secondary signal. (The first GH_ERROR is the
    primary signal — stale is the backup if it's missed.)"""
    monkeypatch.setattr(
        "scripts.ci.pr_monitor.gh_pr_view_v2",
        lambda pr_num, repo=None: (None, "gh: persistent connection refused"),
    )
    state = _state(monitor_started_at=1000.0)
    # First tick emits GH_ERROR
    tick_once(
        343, repo="x/y", state=state, as_json=False,
        stale_after_seconds=900, now=2000.0,
    )
    out = capsys.readouterr().out
    assert "PR#343 GH_ERROR" in out
    # Second tick well past threshold from the first event → STALE_SILENCE fires
    tick_once(
        343, repo="x/y", state=state, as_json=False,
        stale_after_seconds=900, now=3500.0,
    )
    out = capsys.readouterr().out
    assert "PR#343 STALE_SILENCE" in out


def test_stale_silence_line_includes_actionable_hint():
    line = format_stale_silence_line(343, 1234, None)
    assert "PR#343 STALE_SILENCE" in line
    assert "elapsed=1234s" in line
    assert "last_event_at=never" in line
    assert "gh pr view 343" in line


def test_stale_silence_line_renders_last_event_timestamp():
    # 2026-05-26 18:00:00 UTC
    last = 1779831600.0
    line = format_stale_silence_line(343, 900, last)
    assert "last_event_at=2026-05" in line


def test_default_stale_threshold_is_15_minutes():
    assert DEFAULT_STALE_AFTER_SECONDS == 900


def test_state_roundtrip_preserves_stale_fields(tmp_path):
    spath = tmp_path / "state.json"
    state = _state(
        reported_threads={"T1"},
        last_event_at=2000.0,
        last_stale_emit_at=2100.0,
        monitor_started_at=1000.0,
    )
    save_state(spath, state)
    loaded = load_state(spath)
    assert loaded["last_event_at"] == 2000.0
    assert loaded["last_stale_emit_at"] == 2100.0
    assert loaded["monitor_started_at"] == 1000.0
    assert loaded["reported_threads"] == {"T1"}
