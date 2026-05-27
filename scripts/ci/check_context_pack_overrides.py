#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:expired_override
#                  architecture/ci_overrides.yaml
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Validate architecture/ci_overrides.yaml entries.

Failure rules enforced (from ci_overrides.yaml#failure_rules):
  - override_expired
  - override_owner_missing
  - override_reason_empty
  - override_risk_accepted_empty
  - override_path_scope_wider_than_changed_files
  - override_followup_missing_for_p0_p1_surface
  - override_reviewer_approval_missing
  - override_attempts_no_override_rule
  - override_expiry_too_distant
  - override_id_collision

The expired_override rule is itself a no_override hazard in
topology_enforcement.yaml — so a malformed override file cannot itself
be bypassed by an override.

Exit codes:
    0 — every override is well-formed
    1 — one or more violations
    2 — IO error / malformed yaml
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]

MAX_EXPIRY_DAYS = 60   # ci_overrides.yaml#failure_rules:override_expiry_too_distant


def _parse_date(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def validate_override(
    override: dict,
    enforcement_no_override: set[str],
    today: dt.date,
    seen_ids: set[str],
) -> list[dict]:
    findings: list[dict] = []
    oid = override.get("id") or "<no-id>"

    def fail(rule: str, msg: str) -> None:
        findings.append({"override_id": oid, "rule": rule, "message": msg})

    # id collision
    if oid in seen_ids:
        fail("override_id_collision", f"duplicate override id {oid!r}")
    else:
        seen_ids.add(oid)

    # owner
    if not (override.get("owner") or "").strip():
        fail("override_owner_missing", "owner field empty")

    # reason
    if not (override.get("reason") or "").strip():
        fail("override_reason_empty", "reason field empty")

    # risk accepted
    if not (override.get("risk_accepted") or "").strip():
        fail("override_risk_accepted_empty", "risk_accepted field empty")

    # expiry date present and not past
    exp_s = override.get("expiry_date")
    expiry = _parse_date(exp_s) if exp_s else None
    if not expiry:
        fail("override_expired", f"expiry_date {exp_s!r} not a valid YYYY-MM-DD")
    elif expiry < today:
        fail("override_expired", f"expiry_date {exp_s!r} is in the past ({today})")
    else:
        # Compute distance — only valid if entry has a created_at (optional);
        # fall back to today as upper bound check (must be within MAX_EXPIRY_DAYS
        # from today, treating today as creation).
        created_s = override.get("created_at") or override.get("created")
        created = _parse_date(created_s) if created_s else None
        anchor = created or today
        if (expiry - anchor).days > MAX_EXPIRY_DAYS:
            fail("override_expiry_too_distant",
                 f"expiry {exp_s} is {(expiry - anchor).days} days from anchor "
                 f"{anchor}; max {MAX_EXPIRY_DAYS}")

    # reviewer approval
    if override.get("reviewer_approval_required") is True:
        approved = override.get("approved_by") or []
        if not approved:
            fail("override_reviewer_approval_missing",
                 "reviewer_approval_required=true but approved_by is empty")

    # path scope: allowed_changed_files must be subset of applies_to.changed_files
    applies = override.get("applies_to") or {}
    applies_files = set(applies.get("changed_files") or [])
    allowed = set(override.get("allowed_changed_files") or [])
    if allowed and not allowed.issubset(applies_files):
        extra = allowed - applies_files
        fail("override_path_scope_wider_than_changed_files",
             f"allowed_changed_files contains entries outside applies_to.changed_files: {sorted(extra)}")

    # follow_up for P0/P1 surfaces
    fu = override.get("follow_up") or {}
    if (override.get("risk_tier") in ("T0", "T1")) and not (
        fu.get("required") is True and (fu.get("issue_or_pr") or "").strip()
    ):
        fail("override_followup_missing_for_p0_p1_surface",
             "T0/T1 risk tier requires follow_up.required=true + non-empty issue_or_pr")

    # cannot override no_override rules
    rule_id = override.get("rule_id")
    if rule_id and rule_id in enforcement_no_override:
        fail("override_attempts_no_override_rule",
             f"rule {rule_id!r} is in topology_enforcement.yaml#no_override_rules")

    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=str(REPO_ROOT))
    p.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat schema-level/type errors (override entry not a dict, "
            "missing yaml file) as exit 2 instead of exit 1, so callers can "
            "distinguish 'overrides malformed' from 'overrides violate rules'."
        ),
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--today", default=None, help="Override today's date (YYYY-MM-DD) for testing")
    args = p.parse_args(argv)

    repo = Path(args.repo_root)
    overrides_path = repo / "architecture" / "ci_overrides.yaml"
    enforce_path = repo / "architecture" / "topology_enforcement.yaml"

    if not overrides_path.exists():
        print(f"ERROR: missing {overrides_path}", file=sys.stderr)
        return 2

    with overrides_path.open() as f:
        overrides_doc = yaml.safe_load(f) or {}
    with enforce_path.open() as f:
        enforce = yaml.safe_load(f) or {}

    no_override_rules = set(enforce.get("no_override_rules") or [])

    today = _parse_date(args.today) if args.today else dt.date.today()
    if today is None:
        print(f"ERROR: bad --today {args.today!r}", file=sys.stderr)
        return 2

    findings: list[dict] = []
    schema_failures: list[dict] = []
    seen_ids: set[str] = set()
    for override in (overrides_doc.get("overrides") or []):
        if not isinstance(override, dict):
            entry = {"override_id": None, "rule": "schema",
                     "message": f"override entry not a dict: {override!r}"}
            findings.append(entry)
            schema_failures.append(entry)
            continue
        findings.extend(validate_override(override, no_override_rules, today, seen_ids))

    if args.json:
        print(json.dumps({"violations": findings, "count": len(findings)}, indent=2))
    else:
        if not findings:
            n = len(overrides_doc.get("overrides") or [])
            print(f"OK: validated {n} override(s); no violations.")
        else:
            print(f"FAIL: {len(findings)} override violation(s):")
            for f in findings:
                print(f"  [{f['rule']}] {f.get('override_id','-')}: {f['message']}")

    if not findings:
        return 0
    if args.strict and schema_failures:
        # In strict mode, schema-level errors are a separate severity tier
        # (exit 2) so callers can distinguish them from rule violations.
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
