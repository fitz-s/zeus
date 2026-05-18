#!/usr/bin/env python3
# Created: 2026-05-16
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/zeus_agent_runtime_compounding_plan_2026-05-16.md §4 W1.2 + hook matcher contract for Edit/Write/MultiEdit/NotebookEdit

"""
citation_grep_gate.py -- PreToolUse advisory for Edit/Write/MultiEdit/NotebookEdit.

Scans old_string / new_string args for file:line and file Lline citations.
For each citation, verifies the referenced file exists and the line number
is within the file's current bounds (1 <= lineno <= total_lines). On drift,
returns an advisory listing the stale citations. Fail-open on any exception.

Future enhancement (not implemented): content-match within a ±5 line tolerance
window — would require the citation source text to anchor the match.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
CONTENT_FIELD_KEYS = frozenset({
    "content",
    "new_string",
    "newText",
    "old_string",
    "oldText",
    "source",
    "text",
})
CONTENT_CONTAINER_KEYS = frozenset({"cell", "cells", "changes", "edits"})
PATH_FIELD_KEYS = frozenset({
    "file",
    "file_path",
    "filepath",
    "filename",
    "notebook_path",
    "path",
})

# Matches:  path/to/file.py:42  or  path/to/file.py L42
# Groups: (filepath, line_number)
_CITATION_PATTERN = re.compile(
    r'([\w./\-]+\.(?:py|yaml|yml|json|md|ts|js|sh|txt))'
    r'(?::(\d+)|[ \t]+L(\d+))',
)

def _extract_citations(text: str) -> list[tuple[str, int]]:
    """Return list of (filepath, lineno) from all citations in text."""
    results: list[tuple[str, int]] = []
    for m in _CITATION_PATTERN.finditer(text):
        filepath = m.group(1)
        lineno_str = m.group(2) or m.group(3)
        if lineno_str:
            results.append((filepath, int(lineno_str)))
    return results


def _resolve_path(cited: str) -> Path | None:
    """Try repo-relative then absolute resolution."""
    p = REPO_ROOT / cited
    if p.exists():
        return p
    p2 = Path(cited)
    if p2.exists():
        return p2
    return None


def _check_citation(filepath: str, lineno: int) -> str | None:
    """
    Return None if citation is valid (file exists, lineno in bounds) or file
    not found (skip — may be a new file being created).
    Return drift description string if lineno is outside file bounds.
    """
    resolved = _resolve_path(filepath)
    if resolved is None:
        return None  # File not found -- skip (may be a new file being created)

    try:
        lines = resolved.read_text(errors="replace").splitlines()
    except OSError:
        return None

    total = len(lines)
    if lineno < 1 or lineno > total:
        return f"{filepath}:{lineno} — line {lineno} is outside file bounds (file has {total} lines)"

    return None  # valid


def _collect_text_fields(value: Any, *, key: str | None = None) -> list[str]:
    if isinstance(value, str):
        return [value] if key in CONTENT_FIELD_KEYS else []
    if isinstance(value, dict):
        texts: list[str] = []
        for nested_key, nested in value.items():
            key_text = str(nested_key)
            if key_text in PATH_FIELD_KEYS:
                continue
            if key_text in CONTENT_FIELD_KEYS or key_text in CONTENT_CONTAINER_KEYS:
                texts.extend(_collect_text_fields(nested, key=key_text))
            elif isinstance(nested, (dict, list, tuple)):
                texts.extend(_collect_text_fields(nested, key=key_text))
        return texts
    if isinstance(value, (list, tuple)):
        texts = []
        for nested in value:
            texts.extend(_collect_text_fields(nested, key=key))
        return texts
    return []


def _run_advisory_check_citation_grep_gate(input_data: dict[str, Any]) -> str | None:
    """
    PreToolUse advisory for edit tools.
    Parses old_string/new_string for :line and Lline citations.
    Returns advisory string on drift, None if all citations valid.
    Fail-open on any exception.
    """
    try:
        tool_input = input_data.get("tool_input", {})
        tool_name = input_data.get("tool_name", "")

        if tool_name not in SUPPORTED_TOOLS:
            return None

        # Collect text to scan. MultiEdit and NotebookEdit can nest edited
        # strings inside edits/cells, so recurse through tool_input instead of
        # enumerating only Edit/Write top-level keys.
        texts = _collect_text_fields(tool_input)

        if not texts:
            return None

        combined = "\n".join(texts)
        citations = _extract_citations(combined)

        if not citations:
            return None

        drifted: list[str] = []
        for filepath, lineno in citations:
            problem = _check_citation(filepath, lineno)
            if problem:
                drifted.append(f"  - {problem}")

        if not drifted:
            return None

        drift_list = "\n".join(drifted)
        return (
            "ADVISORY [citation_grep_gate]: The following file:line citations "
            "appear stale (line out of range or beyond file end). "
            "Grep-verify before proceeding:\n"
            + drift_list
        )

    except Exception:  # noqa: BLE001
        return None  # fail-open
