#!/usr/bin/env python3
# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: architecture/test_quality.yaml; architecture/test_topology.yaml trust policy
"""Validate money-path test quality metadata."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def tracked_money_path_tests() -> list[str]:
    tests_dir = ROOT / "tests" / "money_path"
    if not tests_dir.exists():
        return []
    return sorted(str(path.relative_to(ROOT)) for path in tests_dir.glob("test_*.py"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality", type=Path, default=ROOT / "architecture/test_quality.yaml")
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args(argv)

    quality = load_yaml(args.quality)
    acceptable = set((quality.get("money_path_test_quality") or {}).get("acceptable_falsifying_proof") or [])
    entries: dict[str, Any] = quality.get("tests") or {}
    failures: list[str] = []

    for test in tracked_money_path_tests():
        spec = entries.get(test)
        if not spec:
            failures.append(f"{test}: missing architecture/test_quality.yaml entry")
            continue
        if not spec.get("protects"):
            failures.append(f"{test}: missing protects invariant list")
        proof = spec.get("falsifying_proof") or {}
        if proof.get("type") not in acceptable:
            failures.append(f"{test}: invalid falsifying_proof.type={proof.get('type')!r}")
        if not proof.get("description"):
            failures.append(f"{test}: missing falsifying_proof.description")
        path = ROOT / test
        if not path.exists():
            failures.append(f"{test}: missing on disk")
            continue
        header = "\n".join(path.read_text(encoding="utf-8").splitlines()[:15])
        if "Created:" not in header or "Last reused/audited:" not in header:
            failures.append(f"{test}: missing lifecycle freshness header")

    if args.collect and entries:
        collect_targets = [test for test in entries if (ROOT / test).exists()]
        if collect_targets:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q", *collect_targets],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            if proc.returncode != 0:
                failures.append("pytest collection failed:\n" + proc.stdout + proc.stderr)

    if failures:
        print("FAIL: money-path test quality")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("money-path test quality OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
