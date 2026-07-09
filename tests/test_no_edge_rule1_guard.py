# Created: 2026-07-08
# Last reused or audited: 2026-07-08
"""Regression for no_edge_rule1_guard's English-only substring blindspot.

Background (2026-07-08): the operator's language law (converse in Chinese)
means most Stop-hook final messages land in Chinese, or in English phrasing
that paraphrases around the literal "no edge" string (e.g. "edge=0 on every
candidate", "无 settled-EV 可动"). The original _NO_EDGE_PHRASES list was
pure ASCII English literals, so RULE 1 never fired against those
conclusions even though they assert the same "no tradeable edge" substance.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"

spec = importlib.util.spec_from_file_location("dispatch_no_edge_test", DISPATCH_PATH)
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


def test_chinese_no_edge_conclusion_fires(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEUS_NO_EDGE_GUARD_OFF", raising=False)
    text = "结论:市场有效,今天无边可动。"
    payload = {"transcript_path": _write_transcript(tmp_path, text)}
    result = dispatch._run_advisory_check_no_edge_rule1_guard(payload)
    assert result == dispatch._BLOCK_SENTINEL


def test_paraphrased_edge_zero_conclusion_fires(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEUS_NO_EDGE_GUARD_OFF", raising=False)
    text = (
        "13 candidates a cycle, edge=0 on every one. No settled-EV can move "
        "this hour regardless."
    )
    payload = {"transcript_path": _write_transcript(tmp_path, text)}
    result = dispatch._run_advisory_check_no_edge_rule1_guard(payload)
    assert result == dispatch._BLOCK_SENTINEL


def test_legit_edge_conclusion_does_not_fire(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEUS_NO_EDGE_GUARD_OFF", raising=False)
    text = "Edge computed at 0.166 on the 2026-06-22 settlement chain — submitting."
    payload = {"transcript_path": _write_transcript(tmp_path, text)}
    result = dispatch._run_advisory_check_no_edge_rule1_guard(payload)
    assert result is None
