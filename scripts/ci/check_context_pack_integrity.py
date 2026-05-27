#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:context_pack_schema_invalid
#                  architecture/context_pack_schema.yaml
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Validate Context Pack JSON output against architecture/context_pack_schema.yaml.

Reads a packs bundle (the output of
`scripts/topology_doctor_context_pack.py --format json`) and enforces the
integrity_rules declared in architecture/context_pack_schema.yaml:

  - schema_required_fields_present
  - failure_chain_id_exists           (resolves in failure_chains.yaml)
  - surface_id_exists                 (resolves in topology_surfaces.yaml)
  - relationship_test_path_exists     (real file)
  - fatal_misread_id_exists           (resolves in fatal_misreads.yaml)
  - invariant_id_exists               (in money_path_ci.yaml or money_path_objects.yaml or invariants.yaml)
  - scoped_agents_path_exists         (real AGENTS.md)
  - gate_script_exists                (real .py)
  - no_override_rule_in_enforcement   (resolves in topology_enforcement.yaml)
  - blocking_rule_has_enforcer        (script+test both exist)

Exit codes:
    0 — every rule passes
    1 — one or more violations
    2 — IO error / malformed yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def validate_pack(
    pack: dict[str, Any],
    *,
    repo: Path,
    surfaces: dict[str, Any],
    failure_chains: dict[str, Any],
    fatal_misreads: dict[str, Any],
    money_path_ci: dict[str, Any],
    money_path_objects: dict[str, Any],
    invariants_yaml: dict[str, Any],
    enforcement: dict[str, Any],
    required_fields: list[str],
) -> list[dict]:
    """Returns list of {rule, severity, message, pack_id} violations."""
    out: list[dict] = []
    pid = pack.get("id", "<no-id>")

    def fail(rule: str, msg: str, severity: str = "blocking") -> None:
        out.append({"rule": rule, "severity": severity, "message": msg, "pack_id": pid})

    # Rule: required fields present
    for field in required_fields:
        if field not in pack:
            fail("schema_required_fields_present", f"missing required field: {field!r}")

    # Surface ids resolve
    known_surfaces = set((surfaces.get("surfaces") or {}).keys())
    for m in pack.get("matched_surfaces") or []:
        sid = m.get("surface_id")
        if sid and sid not in known_surfaces:
            fail("surface_id_exists", f"unknown surface_id: {sid!r}")

    # FC ids resolve
    known_chains = set((failure_chains.get("chains") or {}).keys())
    for fc in pack.get("failure_chains") or []:
        fid = fc.get("id")
        if fid and fid not in known_chains:
            fail("failure_chain_id_exists", f"unknown failure_chain id: {fid!r}")

    # Fatal misread ids resolve
    misreads = fatal_misreads.get("misreads") or []
    known_misreads = {m["id"] for m in misreads if isinstance(m, dict) and m.get("id")}
    for fm in pack.get("fatal_misreads") or []:
        fmid = fm.get("id")
        # REVIEW_REQUIRED entries are explicitly unresolvable — caller flagged them
        if fmid and fmid not in known_misreads and "REVIEW_REQUIRED" not in (fm.get("reason") or ""):
            fail("fatal_misread_id_exists", f"unknown fatal_misread id: {fmid!r}")

    # Invariant ids resolve in one of the canonical sources.
    # Collect only IDs that match the canonical invariant naming patterns
    # (MP-XYZ-NNN, INV-NN, MP-NNN). Field names like `p_raw`, `applied_at`,
    # `final_limit_price` from money_path_objects.economic_objects.fields
    # are NOT invariants and must not be treated as such.
    import re as _re
    _INV_ID = _re.compile(r"^(MP-[A-Z]+-?\d+|INV-\d+)$")
    known_inv: set[str] = set()
    for inv_id in (money_path_ci.get("invariants") or {}):
        if _INV_ID.match(str(inv_id)):
            known_inv.add(inv_id)
    # money_path_objects: any required_invariants list referenced under
    # economic_objects / state_machines / etc. is a list of MP-* IDs.
    def _collect_invariant_refs(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("required_invariants", "invariant_ids") and isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and _INV_ID.match(item):
                            known_inv.add(item)
                else:
                    _collect_invariant_refs(v)
        elif isinstance(node, list):
            for item in node:
                _collect_invariant_refs(item)
    _collect_invariant_refs(money_path_objects)
    _collect_invariant_refs(money_path_ci)
    for inv in (invariants_yaml.get("invariants") or []):
        if isinstance(inv, dict) and inv.get("id"):
            iid = inv["id"]
            if _INV_ID.match(str(iid)):
                known_inv.add(iid)
    for inv in pack.get("active_invariants") or []:
        iid = inv.get("id")
        if iid and iid not in known_inv:
            # Some invariants in custom source are valid — only flag if source claims canonical
            if inv.get("source") in ("architecture/money_path_ci.yaml", "architecture/money_path_objects.yaml", "architecture/invariants.yaml"):
                fail("invariant_id_exists", f"invariant id {iid!r} not in declared source {inv.get('source')}")

    # Relationship test paths exist
    for t in pack.get("required_relationship_tests") or []:
        tp = t.get("path")
        if tp and not (repo / tp).exists():
            fail("relationship_test_path_exists", f"test path not in repo: {tp!r}")

    # Scoped agents paths exist
    for sa in pack.get("scoped_agents") or []:
        path = sa.get("path") if isinstance(sa, dict) else sa
        if path and not (repo / path).exists():
            fail("scoped_agents_path_exists", f"AGENTS path not in repo: {path!r}")

    # Gate scripts exist
    for g in pack.get("required_static_gates") or []:
        script = g.get("script")
        if not script or script == "REVIEW_REQUIRED":
            continue
        if not (repo / script).exists():
            fail("gate_script_exists", f"gate script not in repo: {script!r}")

    # no_override_rules resolve in enforcement registry
    enforce_rules = {
        r["id"] for r in (enforcement.get("blocking_structural") or [])
        if isinstance(r, dict) and r.get("id")
    } | set(enforcement.get("no_override_rules") or [])
    for nor in (pack.get("override") or {}).get("no_override_rules") or []:
        if nor not in enforce_rules:
            fail("no_override_rule_in_enforcement",
                 f"no_override_rule {nor!r} not in topology_enforcement.yaml")

    return out


def validate_enforcement_completeness(enforcement: dict[str, Any], repo: Path) -> list[dict]:
    """
    Blocking rules must have enforcer script + proving test.

    REVIEW_REQUIRED is an explicit placeholder for rules whose dedicated
    enforcer is deferred to a later phase. Such rules emit a lower-severity
    `advisory` finding rather than a blocking violation — they document
    the gap without blocking CI. The orchestrator skips REVIEW_REQUIRED
    enforcers at runtime; this check just notes them.
    """
    out: list[dict] = []
    for rule in (enforcement.get("blocking_structural") or []):
        if not isinstance(rule, dict):
            continue
        rid = rule.get("id", "<unknown>")
        enforcer = rule.get("enforcer", "")
        proving_test = rule.get("proving_test", "")
        if enforcer == "REVIEW_REQUIRED":
            out.append({
                "rule": "blocking_rule_has_enforcer",
                "severity": "advisory",
                "message": f"rule {rid!r} enforcer is REVIEW_REQUIRED placeholder (dedicated enforcer pending)",
                "pack_id": None,
            })
        elif not enforcer or not (repo / enforcer).exists():
            out.append({
                "rule": "blocking_rule_has_enforcer",
                "severity": "blocking",
                "message": f"rule {rid!r} declares enforcer {enforcer!r} which does not exist",
                "pack_id": None,
            })
        if proving_test == "REVIEW_REQUIRED":
            out.append({
                "rule": "blocking_rule_has_enforcer",
                "severity": "advisory",
                "message": f"rule {rid!r} proving_test is REVIEW_REQUIRED placeholder",
                "pack_id": None,
            })
        elif not proving_test or not (repo / proving_test).exists():
            out.append({
                "rule": "blocking_rule_has_enforcer",
                "severity": "blocking",
                "message": f"rule {rid!r} declares proving_test {proving_test!r} which does not exist",
                "pack_id": None,
            })
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--context-packs", help="Path to context packs JSON file (optional; enforcement-only run without)")
    p.add_argument("--repo-root", default=str(REPO_ROOT))
    p.add_argument("--strict", action="store_true", help="Treat advisory severity as failure too")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo_root)
    schema = _load_yaml(repo / "architecture" / "context_pack_schema.yaml")
    surfaces = _load_yaml(repo / "architecture" / "topology_surfaces.yaml")
    failure_chains = _load_yaml(repo / "architecture" / "failure_chains.yaml")
    fatal_misreads = _load_yaml(repo / "architecture" / "fatal_misreads.yaml")
    money_path_ci = _load_yaml(repo / "architecture" / "money_path_ci.yaml")
    money_path_objects = _load_yaml(repo / "architecture" / "money_path_objects.yaml")
    invariants_yaml = _load_yaml(repo / "architecture" / "invariants.yaml")
    enforcement = _load_yaml(repo / "architecture" / "topology_enforcement.yaml")
    required_fields = schema.get("required_fields") or []

    violations: list[dict] = []

    # Always validate enforcement registry coherence
    violations.extend(validate_enforcement_completeness(enforcement, repo))

    # Optionally validate context-pack bundle output
    if args.context_packs:
        cp_path = Path(args.context_packs)
        if not cp_path.exists():
            print(f"ERROR: --context-packs not found: {cp_path}", file=sys.stderr)
            return 2
        try:
            bundle = json.loads(cp_path.read_text())
        except json.JSONDecodeError as e:
            print(f"ERROR: --context-packs unparseable: {e}", file=sys.stderr)
            return 2
        for pack in bundle.get("packs") or []:
            violations.extend(
                validate_pack(
                    pack,
                    repo=repo,
                    surfaces=surfaces,
                    failure_chains=failure_chains,
                    fatal_misreads=fatal_misreads,
                    money_path_ci=money_path_ci,
                    money_path_objects=money_path_objects,
                    invariants_yaml=invariants_yaml,
                    enforcement=enforcement,
                    required_fields=required_fields,
                )
            )

    if args.json:
        print(json.dumps({"violations": violations, "count": len(violations)}, indent=2))
    else:
        if not violations:
            print("OK: context pack integrity rules all pass.")
        else:
            print(f"FAIL: {len(violations)} integrity violation(s):")
            for v in violations:
                tag = f"[{v['rule']}]"
                pid = f" pack={v['pack_id']}" if v.get("pack_id") else ""
                print(f"  {tag}{pid}: {v['message']}")

    blocking = [v for v in violations if v["severity"] == "blocking"]
    if args.strict:
        return 0 if not violations else 1
    return 0 if not blocking else 1


if __name__ == "__main__":
    sys.exit(main())
