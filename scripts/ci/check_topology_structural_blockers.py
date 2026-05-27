#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Orchestrator for Phase D structural blockers.

Runs each rule in architecture/topology_enforcement.yaml#blocking_structural
that has a real enforcer script, collects exit codes + JSON findings, and
emits an aggregate report.

Default behavior is to ONLY exit non-zero when a no_override rule fires.
This matches the refined-plan §3 narrow blocking contract: advisory
context (Phase C) handles the wide net; this gate hard-blocks only the
structural hazards that cannot be safely deferred.

Pass --strict to fail on any blocking rule (override_allowed=true rules
also count). Pass --include rule_id ... to limit which rules run.

Exit codes:
    0 — no blocking failures (no_override rules all green)
    1 — one or more no_override rule failures
    2 — orchestrator failure (yaml parse, missing enforcer, etc.)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


# Resolve self-path once at import time so the self-guard below cannot be
# fooled by relative-path tricks in the yaml.
_SELF_PATH = Path(__file__).resolve()


def run_enforcer(
    rule: dict,
    repo: Path,
    changed_files: list[str] | None,
    context_packs_path: str | None = None,
) -> dict:
    """
    Invoke the enforcer script and capture its result.
    Returns {rule_id, enforcer, exit_code, stdout, stderr, override_allowed}.

    SELF-RECURSION GUARD: if the rule's enforcer path resolves to THIS
    script, refuse to dispatch. Earlier versions of
    architecture/topology_enforcement.yaml listed this orchestrator as
    the `enforcer:` for several rules, which produced a subprocess
    fork bomb (every rule spawned another orchestrator, each of which
    spawned another for each self-referencing rule, exponentially).
    Operator crashed live 2026-05-26.

    Self-referencing rules are skipped with a SKIPPED result that the
    aggregator treats as informational, not a failure. They should be
    repointed at a dedicated enforcer or marked REVIEW_REQUIRED in the
    yaml.
    """
    rid = rule.get("id", "<unknown>")
    enforcer = rule.get("enforcer")
    if not enforcer or enforcer == "REVIEW_REQUIRED":
        return {
            "rule_id": rid,
            "enforcer": enforcer,
            "exit_code": 0,                 # not a failure — explicitly placeholder
            "stdout": "",
            "stderr": f"enforcer not implemented (placeholder: {enforcer!r})",
            "override_allowed": rule.get("override_allowed", True),
            "skipped": True,
        }
    enforcer_path = (repo / enforcer).resolve()
    if enforcer_path == _SELF_PATH:
        return {
            "rule_id": rid,
            "enforcer": enforcer,
            "exit_code": 0,
            "stdout": "",
            "stderr": (
                "SELF-RECURSION GUARD: rule's enforcer is this orchestrator. "
                "Repoint to a dedicated enforcer script or set enforcer: REVIEW_REQUIRED."
            ),
            "override_allowed": rule.get("override_allowed", True),
            "skipped": True,
        }
    if not enforcer_path.exists():
        return {
            "rule_id": rid,
            "enforcer": enforcer,
            "exit_code": 2,
            "stdout": "",
            "stderr": f"enforcer not found: {enforcer}",
            "override_allowed": rule.get("override_allowed", True),
        }
    cmd = [sys.executable, str(enforcer_path), "--repo-root", str(repo)]
    # Forward --context-packs to the integrity enforcer so pack-level rules
    # (failure_chain_id_exists, surface_id_exists, etc.) actually run rather
    # than only the enforcement-registry coherence checks. Other enforcers
    # don't accept the flag, so we only pass it to the one that does.
    if (
        context_packs_path
        and enforcer_path.name == "check_context_pack_integrity.py"
    ):
        cmd.extend(["--context-packs", context_packs_path])
    # Pass changed_files if the enforcer accepts it (we just try; if it errors
    # we fall back without).
    if changed_files is not None:
        cmd.extend(["--changed-files", *changed_files] if changed_files else [])
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False,
        )
        # If unknown flag, retry without --changed-files
        if out.returncode == 2 and "--changed-files" in (out.stderr or "") and changed_files is not None:
            out = subprocess.run(
                [sys.executable, str(repo / enforcer), "--repo-root", str(repo)],
                capture_output=True, text=True, timeout=60, check=False,
            )
    except subprocess.TimeoutExpired:
        return {
            "rule_id": rid,
            "enforcer": enforcer,
            "exit_code": 2,
            "stdout": "",
            "stderr": "enforcer timed out after 60s",
            "override_allowed": rule.get("override_allowed", True),
        }
    return {
        "rule_id": rid,
        "enforcer": enforcer,
        "exit_code": out.returncode,
        "stdout": out.stdout,
        "stderr": out.stderr,
        "override_allowed": rule.get("override_allowed", True),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=str(REPO_ROOT))
    p.add_argument("--changed-files", nargs="*", default=None)
    p.add_argument("--include", nargs="*", default=None,
                   help="Only run these rule_ids (default: all blocking_structural)")
    p.add_argument("--context-packs", default=None,
                   help="Path to context_packs JSON; forwarded to integrity enforcer "
                        "so pack-level rules also run (not just enforcement registry)")
    p.add_argument("--strict", action="store_true",
                   help="Fail on any blocking rule, not just no_override")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo_root)
    enforce = _load_yaml(repo / "architecture" / "topology_enforcement.yaml")
    rules = enforce.get("blocking_structural") or []
    if not rules:
        print("ERROR: no blocking_structural rules in topology_enforcement.yaml",
              file=sys.stderr)
        return 2

    if args.include:
        rules = [r for r in rules if r.get("id") in set(args.include)]
        if not rules:
            print(f"ERROR: --include {args.include} matched no rules", file=sys.stderr)
            return 2

    results: list[dict] = []
    for rule in rules:
        results.append(
            run_enforcer(
                rule, repo, args.changed_files,
                context_packs_path=args.context_packs,
            )
        )

    # Treat skipped placeholders as informational, not failures.
    failures = [r for r in results if r["exit_code"] != 0 and not r.get("skipped")]
    no_override_failures = [r for r in failures if not r["override_allowed"]]
    skipped = [r for r in results if r.get("skipped")]

    if args.json:
        print(json.dumps({
            "results": results,
            "failure_count": len(failures),
            "no_override_failure_count": len(no_override_failures),
        }, indent=2))
    else:
        print(f"Phase D structural blockers: {len(rules)} rule(s) evaluated.")
        if not failures:
            print("OK: all rules pass.")
        else:
            print(f"FAIL: {len(failures)} rule(s) failed:")
            for r in failures:
                tag = " (NO_OVERRIDE)" if not r["override_allowed"] else " (override allowed)"
                print(f"  - {r['rule_id']} via {r['enforcer']} exit={r['exit_code']}{tag}")
                if r["stdout"].strip():
                    for line in r["stdout"].splitlines()[:10]:
                        print(f"      {line}")

    if args.strict:
        return 0 if not failures else 1
    return 0 if not no_override_failures else 1


if __name__ == "__main__":
    sys.exit(main())
