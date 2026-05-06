# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §5 Gate 1; IMPLEMENTATION_PLAN §6 days 51-55;
#                  ANTI_DRIFT_CHARTER §3 (ritual_signal M1); phase3_h_decision.md §Phase4MandatoryConditions

"""Gate 1: Edit-time Write-tool capability hook.

sunset_date: 2026-08-04  (90 days from authoring per ANTI_DRIFT_CHARTER §5)

Receives a list of paths about to be written, calls route() from route_function,
reads reversibility.yaml to determine enforcement_default per capability class, and:
  - ALLOWS writes whose capability class is not "blocking"
  - REFUSES writes whose capability class is "blocking" when ARCH_PLAN_EVIDENCE
    is not set or does not point to an existing file
  - WARNS (log + allow) for advisory classes without evidence
  - Emits one ritual_signal JSON line per evaluation to logs/ritual_signal/YYYY-MM.jsonl

Feature flag: ZEUS_ROUTE_GATE_EDIT=off skips all checks (rollback per IMPLEMENTATION_PLAN §6).

ritual_signal schema (CHARTER §3 M1):
  {
    "helper": "gate_edit_time",
    "task_id": "<sha256[:16] of sorted paths>",
    "fit_score": 1.0,
    "advisory_or_blocking": "blocking" | "advisory",
    "outcome": "applied" | "ignored" | "blocked",
    "diff_paths_touched": [...],
    "invocation_ts": "<iso8601>",
    "charter_version": "1.0.0",
    "cap_id": "<id>",
    "severity": "<reversibility_class>",
    "decision": "allow" | "refuse" | "warn",
    "evidence_path": "<ARCH_PLAN_EVIDENCE or null>"
  }

Sample line:
  {"helper":"gate_edit_time","task_id":"abc123","fit_score":1.0,"advisory_or_blocking":"blocking",
   "outcome":"blocked","diff_paths_touched":["src/state/ledger.py"],"invocation_ts":"2026-05-06T...",
   "charter_version":"1.0.0","cap_id":"canonical_position_write","severity":"TRUTH_REWRITE",
   "decision":"refuse","evidence_path":null}
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import yaml

from src.architecture.route_function import route  # noqa: imported for patch-ability in tests

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_REVERSIBILITY_PATH = REPO_ROOT / "architecture" / "reversibility.yaml"
_RITUAL_SIGNAL_DIR = REPO_ROOT / "logs" / "ritual_signal"
_CHARTER_VERSION = "1.0.0"
_GATE_NAME = "gate_edit_time"
_SUNSET_DATE = "2026-08-04"


def _load_reversibility() -> dict[str, str]:
    """Return mapping reversibility_class_id -> enforcement_default."""
    with _REVERSIBILITY_PATH.open() as f:
        data = yaml.safe_load(f)
    result: dict[str, str] = {}
    for entry in data.get("reversibility_classes", []):
        cid = entry.get("id")
        default = entry.get("enforcement_default", "advisory")
        if cid:
            result[cid] = default
    return result



def _task_id(paths: list[str]) -> str:
    payload = "|".join(sorted(paths))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _emit_signal(
    paths: list[str],
    cap_id: str,
    severity: str,
    decision: str,
    blocking: bool,
    evidence_path: str | None,
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
        "severity": severity,
        "decision": decision,
        "evidence_path": evidence_path,
    }
    with out_path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def evaluate(paths: list[str]) -> tuple[bool, str]:
    """Evaluate a list of about-to-be-written paths.

    Returns:
        (allowed: bool, message: str)

    Side-effect: emits ritual_signal per evaluation.
    """
    if os.environ.get("ZEUS_ROUTE_GATE_EDIT", "").lower() == "off":
        return True, "[gate_edit_time] SKIPPED: ZEUS_ROUTE_GATE_EDIT=off"

    enforcement = _load_reversibility()
    card = route(paths)

    evidence_path = os.environ.get("ARCH_PLAN_EVIDENCE", "").strip() or None
    evidence_exists = bool(evidence_path and pathlib.Path(evidence_path).is_file())

    if not card.capabilities:
        _emit_signal(paths, "(none)", "WORKING", "allow", False, evidence_path)
        return True, "[gate_edit_time] ALLOWED: no capability matched"

    messages: list[str] = []
    refused = False

    for cap_id in card.capabilities:
        rev_class = card.reversibility
        default = enforcement.get(rev_class, "advisory")
        is_blocking = default == "blocking"

        if is_blocking and not evidence_exists:
            decision = "refuse"
            _emit_signal(paths, cap_id, rev_class, decision, True, evidence_path)
            messages.append(
                f"[gate_edit_time] BLOCKED: capability={cap_id!r} "
                f"reversibility_class={rev_class!r} enforcement=blocking. "
                f"Set ARCH_PLAN_EVIDENCE=<plan-file> and retry. "
                f"route_card token_tier=T0 cap=[{card.capabilities}] "
                f"rev={card.reversibility}"
            )
            refused = True
        elif is_blocking and evidence_exists:
            decision = "allow"
            _emit_signal(paths, cap_id, rev_class, decision, True, evidence_path)
            messages.append(
                f"[gate_edit_time] ALLOWED: capability={cap_id!r} "
                f"evidence={evidence_path!r}"
            )
        else:
            decision = "warn"
            _emit_signal(paths, cap_id, rev_class, decision, False, evidence_path)
            messages.append(
                f"[gate_edit_time] WARN: capability={cap_id!r} "
                f"reversibility_class={rev_class!r} enforcement={default!r} "
                f"(advisory — proceeding)"
            )

    combined = "\n".join(messages)
    return not refused, combined


def main_hook(stdin_json: str) -> None:
    """Entry point for Claude Code PreToolUse hook."""
    try:
        payload = json.loads(stdin_json)
    except json.JSONDecodeError as exc:
        print(f"[gate_edit_time] BLOCKED: malformed hook JSON: {exc}", file=sys.stderr)
        sys.exit(2)

    tool_input = payload.get("tool_input") or payload.get("input") or {}
    file_path = (
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or ""
    )
    if not file_path:
        sys.exit(0)

    allowed, message = evaluate([file_path])
    if not allowed:
        print(message, file=sys.stderr)
        sys.exit(2)
    if message:
        print(message, file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main_hook(sys.stdin.read())
