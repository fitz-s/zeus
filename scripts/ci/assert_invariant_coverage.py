#!/usr/bin/env python3
# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: AGENTS.md money-path semantic CI directive; architecture/money_path_ci.yaml
"""Assert that selected tests cover required money-path invariants."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def split_words(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in value.replace(",", " ").split() if part]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification-json")
    parser.add_argument("--classification-file", type=Path)
    parser.add_argument("--invariants")
    parser.add_argument("--tests")
    parser.add_argument("--objects", type=Path, default=ROOT / "architecture/money_path_objects.yaml")
    parser.add_argument("--mapping", type=Path, default=ROOT / "architecture/money_path_ci.yaml")
    args = parser.parse_args(argv)

    mapping = load_yaml(args.mapping)
    _ = load_yaml(args.objects)
    classification: dict[str, Any] = {}
    if args.classification_file:
        classification = json.loads(args.classification_file.read_text(encoding="utf-8"))
    elif args.classification_json:
        classification = json.loads(args.classification_json)

    required = split_words(args.invariants) or list(classification.get("required_invariants") or [])
    selected = split_words(args.tests) or list(classification.get("tests") or [])
    selected_set = set(selected)
    invariant_specs = mapping.get("invariants") or {}

    failures: list[str] = []
    for invariant in required:
        spec = invariant_specs.get(invariant)
        if not spec:
            failures.append(f"unknown invariant: {invariant}")
            continue
        tests = spec.get("tests") or []
        if not tests:
            failures.append(f"{invariant}: no tests registered in architecture/money_path_ci.yaml")
            continue
        if not selected_set.intersection(tests):
            failures.append(f"{invariant}: none of registered tests selected ({', '.join(tests)})")

    missing_paths = [test for test in selected if "::" not in test and not (ROOT / test).exists()]
    if missing_paths:
        failures.append("selected tests missing on disk: " + ", ".join(sorted(missing_paths)))

    if classification.get("unregistered_objects"):
        failures.append("unregistered objects: " + ", ".join(classification["unregistered_objects"]))
    if classification.get("authority_fabricating_defaults"):
        failures.append("authority-fabricating defaults: " + ", ".join(classification["authority_fabricating_defaults"]))
    if classification.get("migration_policy_missing"):
        failures.append("migration semantic policy missing: " + ", ".join(classification["migration_policy_missing"]))

    if failures:
        print("FAIL: money-path invariant coverage")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("money-path invariant coverage OK")
    print("required_invariants=" + " ".join(required))
    print("selected_tests=" + " ".join(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
