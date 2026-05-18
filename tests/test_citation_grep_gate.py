# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: .claude/hooks/citation_grep_gate.py runtime matcher contract
"""Regression tests for citation_grep_gate edit-tool coverage."""

from __future__ import annotations

import importlib.util
from pathlib import Path


HOOK_PATH = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "citation_grep_gate.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("citation_grep_gate_under_test", HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_multiedit_nested_edit_strings_are_scanned_for_stale_citations(tmp_path):
    module = _load_hook_module()
    cited = tmp_path / "citation_fixture.py"
    cited.write_text("print('only one line')\n", encoding="utf-8")

    advisory = module._run_advisory_check_citation_grep_gate(
        {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": str(cited),
                "edits": [
                    {
                        "old_string": f"See {cited}:99 before changing this block",
                        "new_string": "replacement",
                    }
                ],
            },
        }
    )

    assert advisory is not None
    assert "citation_grep_gate" in advisory
    assert f"{cited}:99" in advisory


def test_notebookedit_nested_content_is_scanned_for_stale_citations(tmp_path):
    module = _load_hook_module()
    cited = tmp_path / "notebook_ref.md"
    cited.write_text("one line\n", encoding="utf-8")

    advisory = module._run_advisory_check_citation_grep_gate(
        {
            "tool_name": "NotebookEdit",
            "tool_input": {
                "notebook_path": str(tmp_path / "analysis.ipynb"),
                "cell": {"source": f"markdown note cites {cited} L42"},
            },
        }
    )

    assert advisory is not None
    assert f"{cited}:42" in advisory or f"{cited} L42" in advisory


def test_non_edit_tools_are_not_scanned(tmp_path):
    module = _load_hook_module()
    cited = tmp_path / "bash_ref.py"
    cited.write_text("one line\n", encoding="utf-8")

    advisory = module._run_advisory_check_citation_grep_gate(
        {
            "tool_name": "Bash",
            "tool_input": {"command": f"echo {cited}:99"},
        }
    )

    assert advisory is None
