#!/usr/bin/env python3
# Created: 2026-06-12
# Authority basis: docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md (Component 3)
# Purpose: UserPromptSubmit advisory hook — inject a strong codegraph-first
#          banner + real file:line context for code-task prompts. Fail-open.
"""Strong codegraph context injection (advisory; never blocks)."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

# Code-task signal: a path, a dotted/camel/snake symbol, or an action verb.
_CODE_SIGNAL = re.compile(
    r"""(
        [\w/]+\.(?:py|yaml|yml|sql|sh|js|ts|md)\b   # a file path
      | \b(?:fix|refactor|debug|trace|implement|add|where|why|how\s+does|
            caller|callee|impact|review|rename|wire|hook|daemon|executor|
            calc|function|class|method|module|edit|change)\b
      | \b[a-z_]+\.[a-z_]+\(                          # call expr foo.bar(
      | \b[a-z]+_[a-z_]+\b                            # snake_case symbol
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_REVIEW_SIGNAL = re.compile(
    r"\b(review|pr\b|diff|blast\s*radius|impact|affected\s+test)\b", re.IGNORECASE
)

_MAX_CONTEXT_LINES = 35
_TIMEOUT_S = 8


def _run_codegraph_context(prompt: str) -> tuple[bool, str]:
    """Seam: shell to the codegraph CLI. Returns (ok, text). Monkeypatched in tests."""
    cg = shutil.which("codegraph")
    if not cg:
        return (False, "Not initialized")
    try:
        proc = subprocess.run(
            [cg, "context", prompt[:400]],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except Exception:
        return (False, "error")
    out = proc.stdout or ""
    if (
        proc.returncode != 0
        or "Not initialized" in out
        or "Not initialized" in (proc.stderr or "")
    ):
        return (False, out or proc.stderr or "Not initialized")
    return (True, out)


def _is_code_task(prompt: str) -> bool:
    return bool(_CODE_SIGNAL.search(prompt or ""))


def _cap(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) <= _MAX_CONTEXT_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:_MAX_CONTEXT_LINES]) + "\n… (call codegraph_context for the full graph)"


def _run_advisory_check_codegraph_context_inject(payload: dict[str, Any]) -> str | None:
    prompt = payload.get("prompt", "") or payload.get("user_prompt", "")
    if not _is_code_task(prompt):
        return None

    review = bool(_REVIEW_SIGNAL.search(prompt))
    ok, text = _run_codegraph_context(prompt)

    if not ok:
        return (
            "[codegraph] index missing or errored — structural lookups will be "
            "slow. Run `codegraph init -i` once for this checkout, then use "
            "codegraph_context/codegraph_trace before grep/Read."
        )

    banner = (
        "[codegraph — USE FIRST] This is a code task. codegraph is the live "
        "indexed graph of this repo; query it (codegraph_context / "
        "codegraph_trace / codegraph_callers) BEFORE grep/Read — it is faster "
        "and ~20x cheaper in tokens. Relevant context for your prompt:"
    )
    body = _cap(text)
    extra = (
        "\n[code-review-graph] Review/impact task — also use code-review-graph "
        "for blast radius, affected tests, and risk-ordered review."
        if review
        else ""
    )
    return f"{banner}\n{body}{extra}"
