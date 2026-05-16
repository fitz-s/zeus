# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §7 probe3
"""
probe3 — skip token honored → companion_skip_token_used ADVISORY + log row written.

Setup: binding for modify_vendor_response with COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1.
       Env COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1 set.
       COMPANION_SKIP_LOG_PATH redirected to tmp_path.
Action: admit(intent="modify_existing", files=["src/data/vendor_response_x.py"])
Assert: decision.ok is True,
        exactly one issue with code == "companion_skip_token_used",
        AND log file contains exactly one record with correct profile,
        source_files, expected_companions, token_value.
Teardown: unset env var.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


_PROFILE = "modify_vendor_response"
_TOKEN = "COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1"
_TOKEN_KEY = "COMPANION_SKIP_NEEDS_HUMAN_REVIEW"
_COMPANION_DOC = "docs/reference/zeus_vendor_change_response_registry.md"
_SOURCE_FILE = "src/data/vendor_response_x.py"


def _make_vendor_binding() -> BindingLayer:
    cm = CoverageMap(
        profiles={
            _PROFILE: (
                "src/data/vendor_response_*.py",
                "src/ingest/vendor_response_*.py",
                "tests/test_vendor_response_*.py",
            ),
        },
        orphaned=("tmp/**",),
        hard_stop_paths=("src/execution/**",),
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
        companion_required={_PROFILE: (_COMPANION_DOC,)},
        companion_skip_tokens={_PROFILE: _TOKEN},
    )


BINDING = _make_vendor_binding()


class TestProbe3SkipTokenLogsAndAdmits:
    def test_skip_token_decision_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        log_file = tmp_path / "skip_log.jsonl"
        monkeypatch.setenv("COMPANION_SKIP_LOG_PATH", str(log_file))
        monkeypatch.setenv(_TOKEN_KEY, "1")

        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE_FILE],
            binding=BINDING,
        )
        assert result.ok is True

    def test_skip_token_emits_companion_skip_token_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        log_file = tmp_path / "skip_log.jsonl"
        monkeypatch.setenv("COMPANION_SKIP_LOG_PATH", str(log_file))
        monkeypatch.setenv(_TOKEN_KEY, "1")

        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE_FILE],
            binding=BINDING,
        )
        skip_issues = [i for i in result.issues if i.code == "companion_skip_token_used"]
        assert len(skip_issues) == 1

    def test_skip_token_does_not_emit_missing_companion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        log_file = tmp_path / "skip_log.jsonl"
        monkeypatch.setenv("COMPANION_SKIP_LOG_PATH", str(log_file))
        monkeypatch.setenv(_TOKEN_KEY, "1")

        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE_FILE],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" not in codes

    def test_skip_token_writes_log_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        log_file = tmp_path / "skip_log.jsonl"
        monkeypatch.setenv("COMPANION_SKIP_LOG_PATH", str(log_file))
        monkeypatch.setenv(_TOKEN_KEY, "1")

        admit(
            intent=Intent.modify_existing,
            files=[_SOURCE_FILE],
            binding=BINDING,
        )

        assert log_file.exists(), "Skip log file must be created"
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1, f"Expected exactly one log row, got {len(lines)}"

        record = json.loads(lines[0])
        assert record["profile"] == _PROFILE
        assert _SOURCE_FILE in record["source_files"]
        assert _COMPANION_DOC in record["expected_companions"]
        assert record["token_value"] == _TOKEN

    def test_wrong_token_value_does_not_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Only an exact match triggers the skip path (SCAFFOLD §8.2 literal match)."""
        log_file = tmp_path / "skip_log.jsonl"
        monkeypatch.setenv("COMPANION_SKIP_LOG_PATH", str(log_file))
        monkeypatch.setenv(_TOKEN_KEY, "yes")  # Wrong value — token expects "1"

        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE_FILE],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "companion_skip_token_used" not in codes
        assert "missing_companion" in codes
