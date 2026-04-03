from __future__ import annotations

from pathlib import Path
import sys
from _yaml_bootstrap import import_yaml
yaml = import_yaml()

ROOT = Path(__file__).resolve().parents[1]

def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text())

def main() -> int:
    kernel = load_yaml("architecture/kernel_manifest.yaml")
    invariants = load_yaml("architecture/invariants.yaml")
    zones = load_yaml("architecture/zones.yaml")
    negative = load_yaml("architecture/negative_constraints.yaml")
    maturity = load_yaml("architecture/maturity_model.yaml")
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()

    required_files = [
        "docs/architecture/zeus_durable_architecture_spec.md",
        "docs/governance/zeus_change_control_constitution.md",
        "architecture/kernel_manifest.yaml",
        "architecture/invariants.yaml",
        "architecture/zones.yaml",
        "architecture/negative_constraints.yaml",
        "architecture/maturity_model.yaml",
    ]
    missing = [p for p in required_files if not (ROOT / p).exists()]
    if missing:
        print(f"missing required files: {missing}")
        return 1

    for atom in ("unit", "direction", "strategy_key", "phase", "event_type"):
        if atom not in kernel["semantic_atoms"]:
            print(f"missing semantic atom: {atom}")
            return 1

    for val in kernel["semantic_atoms"]["strategy_key"]["allowed"]:
        if val not in sql:
            print(f"strategy_key missing from sql constraint: {val}")
            return 1

    for val in kernel["semantic_atoms"]["phase"]["allowed"]:
        if val not in sql:
            print(f"phase missing from sql constraint: {val}")
            return 1

    for val in kernel["semantic_atoms"]["event_type"]["allowed"]:
        if val not in sql:
            print(f"event_type missing from sql constraint: {val}")
            return 1

    invariant_ids = {item["id"] for item in invariants["invariants"]}
    if len(invariant_ids) < 10:
        print("expected at least 10 invariants")
        return 1

    constraint_ids = {item["id"] for item in negative["constraints"]}
    if len(constraint_ids) < 8:
        print("expected at least 8 negative constraints")
        return 1

    if "K0_frozen_kernel" not in zones["zones"]:
        print("K0_frozen_kernel missing from zones")
        return 1

    if maturity["current_state"]["stage"] == maturity["target_state"]["stage"]:
        print("current stage should differ from target stage")
        return 1

    print("kernel manifests ok")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
