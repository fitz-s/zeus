# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §5 Gate 3; IMPLEMENTATION_PLAN §6 days 61-64;
#                  ANTI_DRIFT_CHARTER §3 (ritual_signal M1); phase3_h_decision.md F-7

"""Gate 3: Commit-time diff verifier.

sunset_date: 2026-08-04  (90 days from authoring per ANTI_DRIFT_CHARTER §5)

Reads `git diff --cached --name-only HEAD` for staged changes. For each changed path:
  - Locates its capability via capabilities.yaml::hard_kernel_paths
  - For .py paths: AST-walks to confirm the changed function still carries
    the expected @capability decorator (checks _capability_id attribute)
  - For non-.py paths (F-7 mandatory condition, phase3_h_decision.md):
    path-match-only against capability hard_kernel_paths — NO AST walk
  - Reads commit message; if original_intent.out_of_scope_keywords matches
    the commit message text, rejects with structured error
  - Emits ritual_signal per evaluation

Feature flag: ZEUS_ROUTE_GATE_COMMIT=off skips all checks.

ritual_signal schema (CHARTER §3 M1):
  {
    "helper": "gate_commit_time",
    "task_id": "<sha256[:16] of sorted paths>",
    "fit_score": 1.0,
    "advisory_or_blocking": "blocking" | "advisory",
    "outcome": "applied" | "ignored" | "blocked",
    "diff_paths_touched": [...],
    "invocation_ts": "<iso8601>",
    "charter_version": "1.0.0",
    "cap_id": "<id>",
    "path": "<changed path>",
    "check_type": "ast_decorator" | "path_match" | "intent_match",
    "decision": "allow" | "refuse",
    "reason": "<human-readable>"
  }

Sample line:
  {"helper":"gate_commit_time","task_id":"abc123","fit_score":1.0,"advisory_or_blocking":"blocking",
   "outcome":"blocked","diff_paths_touched":["src/state/ledger.py"],"invocation_ts":"2026-05-06T...",
   "charter_version":"1.0.0","cap_id":"canonical_position_write","path":"src/state/ledger.py",
   "check_type":"ast_decorator","decision":"refuse","reason":"missing @capability decorator"}
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_CAPS_PATH = REPO_ROOT / "architecture" / "capabilities.yaml"
_RITUAL_SIGNAL_DIR = REPO_ROOT / "logs" / "ritual_signal"
_CHARTER_VERSION = "1.0.0"
_GATE_NAME = "gate_commit_time"
_SUNSET_DATE = "2026-08-04"


def _load_capabilities() -> list[dict]:
    with _CAPS_PATH.open() as f:
        return yaml.safe_load(f)["capabilities"]


def _task_id(paths: list[str]) -> str:
    payload = "|".join(sorted(paths))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _emit_signal(
    paths: list[str],
    cap_id: str,
    path: str,
    check_type: str,
    decision: str,
    reason: str,
    blocking: bool,
) -> None:
    _RITUAL_SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    out_path = _RITUAL_SIGNAL_DIR / f"{month}.jsonl"
    record = {
        "helper": _GATE_NAME,
        "task_id": _task_id(paths),
        "fit_score": 1.0,
        "advisory_or_blocking": "blocking" if blocking else "advisory",
        "outcome": "blocked" if decision == "refuse" else "applied",
        "diff_paths_touched": paths,
        "invocation_ts": datetime.now(timezone.utc).isoformat(),
        "charter_version": _CHARTER_VERSION,
        "cap_id": cap_id,
        "path": path,
        "check_type": check_type,
        "decision": decision,
        "reason": reason,
    }
    with out_path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _get_staged_paths() -> list[str]:
    """Return paths from `git diff --cached --name-only HEAD`."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "HEAD"],
            capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        if lines:
            return lines
        result2 = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
        )
        return [l.strip() for l in result2.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def _get_commit_message() -> str:
    editmsg = REPO_ROOT / ".git" / "COMMIT_EDITMSG"
    try:
        return editmsg.read_text(encoding="utf-8")
    except OSError:
        return ""


def _path_matches(diff_path: str, kernel_paths: list[str]) -> bool:
    dp = diff_path.replace("\\", "/")
    for kp in kernel_paths:
        kp = kp.replace("\\", "/")
        if dp == kp or dp.endswith("/" + kp) or kp.endswith("/" + dp):
            return True
    return False


def _has_capability_decorator_in_file(py_path: pathlib.Path, cap_id: str) -> bool:
    """AST-walk a .py file; return True if any function has @capability(cap_id)."""
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Name) and func.id == "capability":
                    if dec.args:
                        arg = dec.args[0]
                        if isinstance(arg, ast.Constant) and arg.value == cap_id:
                            return True
    return False


def _does_not_fit_triggered(commit_msg: str, original_intent: dict) -> bool:
    """Return True if commit_msg contains out_of_scope_keywords (case-insensitive word boundary)."""
    out_of_scope = original_intent.get("out_of_scope_keywords", [])
    msg_lower = commit_msg.lower()
    for kw in out_of_scope:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", msg_lower):
            return True
    does_not_fit = original_intent.get("does_not_fit", "")
    if isinstance(does_not_fit, str) and does_not_fit:
        if re.search(r"\b" + re.escape(does_not_fit.lower()) + r"\b", msg_lower):
            return True
    return False


def evaluate(
    staged_paths: list[str] | None = None,
    commit_msg: str | None = None,
) -> tuple[bool, list[str]]:
    """Evaluate staged changes against capabilities.

    Args:
        staged_paths: Override for staged paths (used in tests).
        commit_msg: Override for commit message (used in tests).

    Returns:
        (allowed: bool, messages: list[str])
    """
    if os.environ.get("ZEUS_ROUTE_GATE_COMMIT", "").lower() == "off":
        return True, ["[gate_commit_time] SKIPPED: ZEUS_ROUTE_GATE_COMMIT=off"]

    if staged_paths is None:
        staged_paths = _get_staged_paths()
    if commit_msg is None:
        commit_msg = _get_commit_message()

    if not staged_paths:
        return True, ["[gate_commit_time] ALLOWED: no staged paths"]

    caps = _load_capabilities()
    messages: list[str] = []
    refused = False

    for diff_path in staged_paths:
        abs_path = REPO_ROOT / diff_path
        is_py = diff_path.endswith(".py")

        for cap in caps:
            cap_id = cap["id"]
            kernel_paths = cap.get("hard_kernel_paths", [])
            original_intent = cap.get("original_intent", {})

            if not _path_matches(diff_path, kernel_paths):
                continue

            # Intent check (all paths)
            if commit_msg and _does_not_fit_triggered(commit_msg, original_intent):
                reason = (
                    f"commit message matches out_of_scope_keywords for capability "
                    f"{cap_id!r}. Message snippet: {commit_msg[:120]!r}"
                )
                _emit_signal(staged_paths, cap_id, diff_path, "intent_match", "refuse", reason, True)
                messages.append(f"[gate_commit_time] BLOCKED ({diff_path}): {reason}")
                refused = True
                continue

            # .py path: AST decorator check
            if is_py:
                if abs_path.is_file() and not _has_capability_decorator_in_file(abs_path, cap_id):
                    reason = (
                        f"changed .py file {diff_path!r} is a hard_kernel_path for "
                        f"capability {cap_id!r} but no @capability({cap_id!r}) decorator found"
                    )
                    _emit_signal(staged_paths, cap_id, diff_path, "ast_decorator", "refuse", reason, True)
                    messages.append(f"[gate_commit_time] BLOCKED ({diff_path}): {reason}")
                    refused = True
                else:
                    reason = "decorator present or file absent (allow)"
                    _emit_signal(staged_paths, cap_id, diff_path, "ast_decorator", "allow", reason, True)
                    messages.append(
                        f"[gate_commit_time] ALLOWED ({diff_path}): "
                        f"capability={cap_id!r} decorator check passed"
                    )
            else:
                # Non-.py path: path-match-only (F-7 mandatory condition)
                reason = "non-py path matched via hard_kernel_paths (no AST walk per F-7)"
                _emit_signal(staged_paths, cap_id, diff_path, "path_match", "allow", reason, False)
                messages.append(
                    f"[gate_commit_time] ALLOWED ({diff_path}): "
                    f"non-py capability={cap_id!r} — path-match-only (F-7)"
                )

    if not messages:
        messages.append("[gate_commit_time] ALLOWED: no capability paths matched")

    return not refused, messages


def main() -> None:
    """Entry point for pre-commit hook."""
    allowed, messages = evaluate()
    for msg in messages:
        dest = sys.stderr if "BLOCKED" in msg else sys.stdout
        print(msg, file=dest)
    sys.exit(0 if allowed else 1)


if __name__ == "__main__":
    main()
