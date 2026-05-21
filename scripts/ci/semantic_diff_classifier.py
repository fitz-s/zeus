#!/usr/bin/env python3
# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: AGENTS.md money-path semantic CI directive; architecture/money_path_objects.yaml
"""Classify git diffs by money-path semantic object changes.

This gate is intentionally conservative: when a diff creates an unknown
money-path object, it emits a high-risk classification and can fail closed.
It does not try to prove correctness; it routes the changed semantic surface to
the invariant coverage gate.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[2]
RISK_ORDER = {"P3": 0, "P2": 1, "P1": 2, "P0": 3}

STATE_RE = re.compile(r"['\"]([A-Z][A-Z0-9_]{3,})['\"]")
ENUM_MEMBER_RE = re.compile(r"^\+\s+([A-Z][A-Z0-9_]{3,})\s*=")
ALTER_ADD_COLUMN_RE = re.compile(
    r"\bALTER\s+TABLE\s+(?P<table>[A-Za-z0-9_\"`\[\].]+)\s+ADD\s+COLUMN\s+(?P<column>[A-Za-z0-9_\"`\[\]]+)",
    re.IGNORECASE,
)
CREATE_TABLE_RE = re.compile(r"\bCREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?P<table>[A-Za-z0-9_\"`\[\].]+)", re.IGNORECASE)
DEFAULT_RE = re.compile(r"\bDEFAULT\s+(?P<value>'[^']+'|\"[^\"]+\"|[A-Za-z0-9_]+)", re.IGNORECASE)
CHECK_IN_RE = re.compile(r"\bCHECK\s*\([^)]+\bIN\s*\((?P<values>[^)]+)\)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s'\"),]+")
STRATEGY_RE = re.compile(r"(?:strategy_key|strategy)\s*[:=]\s*['\"]([a-zA-Z0-9_.:-]+)['\"]")
ERROR_CODE_RE = re.compile(r"['\"](REDEEM_[A-Z0-9_]+|[A-Z]+_ERROR_[A-Z0-9_]+)['\"]")
OUTPUT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:-]+(?:::[A-Za-z0-9_./:-]+)?$")
ECONOMIC_ASSIGN_RE = re.compile(
    r"\b(?P<left>p_raw|p_cal|p_market|p_posterior|edge|expected_profit|fill_price|market_price|display_price|current_price|limit_price|final_limit_price|mid)\b\s*(?:=|==|!=|<|>|<=|>=)\s*.*\b(?P<right>p_raw|p_cal|p_market|p_posterior|edge|expected_profit|fill_price|market_price|display_price|current_price|limit_price|final_limit_price|mid)\b"
)


@dataclass
class Classification:
    risk: str = "P3"
    changed_files: list[str] = field(default_factory=list)
    changed_segments: list[str] = field(default_factory=list)
    required_invariants: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    run_integration: bool = False
    new_db_tables: list[str] = field(default_factory=list)
    new_db_columns: list[str] = field(default_factory=list)
    new_states: list[str] = field(default_factory=list)
    new_error_codes: list[str] = field(default_factory=list)
    new_strategy_keys: list[str] = field(default_factory=list)
    new_external_calls: list[str] = field(default_factory=list)
    new_side_effects: list[str] = field(default_factory=list)
    economic_object_flows: list[str] = field(default_factory=list)
    unregistered_objects: list[str] = field(default_factory=list)
    authority_fabricating_defaults: list[str] = field(default_factory=list)
    migration_policy_missing: list[str] = field(default_factory=list)

    def bump(self, risk: str) -> None:
        if RISK_ORDER[risk] > RISK_ORDER[self.risk]:
            self.risk = risk

    def add_many(self, attr: str, values: Iterable[str]) -> None:
        current = getattr(self, attr)
        for value in values:
            if value and value not in current:
                current.append(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "changed_files": self.changed_files,
            "changed_segments": self.changed_segments,
            "required_invariants": self.required_invariants,
            "tests": self.tests,
            "run_integration": self.run_integration,
            "new_db_tables": self.new_db_tables,
            "new_db_columns": self.new_db_columns,
            "new_states": self.new_states,
            "new_error_codes": self.new_error_codes,
            "new_strategy_keys": self.new_strategy_keys,
            "new_external_calls": self.new_external_calls,
            "new_side_effects": self.new_side_effects,
            "economic_object_flows": self.economic_object_flows,
            "unregistered_objects": self.unregistered_objects,
            "authority_fabricating_defaults": self.authority_fabricating_defaults,
            "migration_policy_missing": self.migration_policy_missing,
        }


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def run_git(args: list[str], cwd: Path = ROOT) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True)


def diff_text(base: str | None, head: str | None, diff_file: Path | None) -> str:
    if diff_file:
        return diff_file.read_text(encoding="utf-8")
    if base and head:
        return run_git(["diff", "--unified=0", base, head])
    return run_git(["diff", "--unified=0", "HEAD"])


def changed_files(base: str | None, head: str | None, diff: str) -> list[str]:
    if base and head:
        out = run_git(["diff", "--name-only", base, head])
        return [line.strip() for line in out.splitlines() if line.strip()]
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line.removeprefix("+++ b/"))
    return sorted(set(files))


def added_lines(diff: str) -> list[tuple[str, str]]:
    current = ""
    out: list[tuple[str, str]] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line.removeprefix("+++ b/")
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append((current, line))
    return out


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def semantic_scan_enabled(path: str) -> bool:
    """Return true for code paths whose added literals can create money-path objects."""
    if path.startswith("src/"):
        return True
    if path.startswith("scripts/ci/"):
        return False
    if path.startswith("scripts/") and path.endswith((".py", ".sql")):
        return True
    return False


def all_registered_states(objects: dict[str, Any]) -> set[str]:
    states: set[str] = set()
    for machine in (objects.get("state_machines") or {}).values():
        states.update(str(s) for s in machine.get("states") or [])
    return states


def registered_economic_fields(objects: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for group in (objects.get("economic_objects") or {}).values():
        fields.update((group.get("fields") or {}).keys())
    return fields


def add_segment_routes(result: Classification, files: list[str], mapping: dict[str, Any]) -> None:
    segments = mapping.get("segments") or {}
    force_patterns = (mapping.get("risk_rules") or {}).get("force_integration_if_changed") or []
    for path in files:
        if matches_any(path, force_patterns):
            result.run_integration = True
            result.bump("P1")
        for segment, spec in segments.items():
            if matches_any(path, spec.get("files") or []):
                if segment not in result.changed_segments:
                    result.changed_segments.append(segment)
                result.add_many("required_invariants", spec.get("invariant_ids") or [])
                result.add_many("tests", spec.get("relationship_tests") or [])


def token_list(values: Iterable[str]) -> str:
    safe: list[str] = []
    for value in values:
        if not OUTPUT_TOKEN_RE.fullmatch(value):
            raise ValueError(f"unsafe GitHub output token: {value!r}")
        safe.append(value)
    return " ".join(safe)


def classify(diff: str, files: list[str], objects: dict[str, Any], mapping: dict[str, Any]) -> Classification:
    result = Classification(changed_files=files)
    add_segment_routes(result, files, mapping)
    registered_states = all_registered_states(objects)
    economic_fields = registered_economic_fields(objects)
    authority_defaults = set((objects.get("schema_objects") or {}).get("authority_fabricating_defaults") or [])
    side_effects = objects.get("side_effect_calls") or {}
    source_specs = objects.get("external_truth_sources") or {}

    for path, line in added_lines(diff):
        if not semantic_scan_enabled(path):
            continue
        body = line[1:]
        upper_body = body.upper()

        for match in CREATE_TABLE_RE.finditer(body):
            table = match.group("table").strip('"`[]')
            result.add_many("new_db_tables", [table])
            result.add_many("required_invariants", (mapping.get("risk_rules") or {}).get("schema_change_requires") or [])
            result.bump("P1")
        for match in ALTER_ADD_COLUMN_RE.finditer(body):
            table = match.group("table").strip('"`[]')
            column = match.group("column").strip('"`[]')
            result.add_many("new_db_columns", [f"{table}.{column}"])
            result.add_many("required_invariants", (mapping.get("risk_rules") or {}).get("schema_change_requires") or [])
            result.bump("P1")
        for match in DEFAULT_RE.finditer(body):
            value = match.group("value").strip("'\"")
            if value in authority_defaults:
                result.add_many("authority_fabricating_defaults", [f"{path}: DEFAULT {value}"])
                result.add_many("required_invariants", ["MP-SCH-001"])
                result.bump("P0")
        if "PRAGMA USER_VERSION" in upper_body or "_SCHEMA_PINNED_HASH" in upper_body:
            result.add_many("required_invariants", ["MP-SCH-002"])
            result.bump("P1")
        if (fnmatch.fnmatch(path, "scripts/migrations/**") or fnmatch.fnmatch(path, "scripts/migrate_*.py")) and (
            "ALTER TABLE" in upper_body or "CREATE TABLE" in upper_body
        ):
            full_file = ROOT / path
            text = full_file.read_text(encoding="utf-8") if full_file.exists() else diff
            if "Migration semantic policy:" not in text:
                result.add_many("migration_policy_missing", [path])
                result.add_many("required_invariants", ["MP-SCH-001"])
                result.bump("P0")

        error_codes = set(ERROR_CODE_RE.findall(body)) if "errorCode" in body else set()
        state_candidates = {m.group(1) for m in STATE_RE.finditer(body)}
        state_candidates.update(m.group(1) for m in ENUM_MEMBER_RE.finditer(line))
        for check in CHECK_IN_RE.finditer(body):
            raw_values = [v.strip().strip("'\"") for v in check.group("values").split(",")]
            state_candidates.update(v for v in raw_values if re.fullmatch(r"[A-Z][A-Z0-9_]{3,}", v))
        state_prefixes = (
            "REDEEM",
            "SUBMIT",
            "ACK",
            "FILLED",
            "REVIEW",
            "UNKNOWN",
            "REJECTED",
            "PARTIAL",
            "INTENT",
        )
        for state in sorted(state_candidates):
            if state in error_codes:
                continue
            if any(prefix in state for prefix in state_prefixes):
                result.add_many("new_states", [state])
                result.bump("P0")
                if state not in registered_states:
                    result.add_many("unregistered_objects", [f"state:{state}"])

        for error_code in error_codes:
            result.add_many("new_error_codes", [error_code])
            result.add_many("required_invariants", ["MP-RED-002"])
            result.bump("P1")

        for strategy in STRATEGY_RE.findall(body):
            result.add_many("new_strategy_keys", [strategy])
            result.add_many("required_invariants", ["MP-ECO-001", "MP-ECO-002"])
            result.bump("P1")

        for url in URL_RE.findall(body):
            result.add_many("new_external_calls", [url])
            result.add_many("required_invariants", ["MP-EXT-001", "MP-EXT-002"])
            result.bump("P1")
            if not any(
                str(pattern).lower() in url.lower()
                for source in source_specs.values()
                for pattern in (source.get("endpoint_patterns") or [])
            ):
                result.add_many("unregistered_objects", [f"external_endpoint:{url}"])
        lowered = body.lower()
        for source_name, source in source_specs.items():
            for pattern in source.get("endpoint_patterns") or []:
                if str(pattern).lower() in lowered:
                    result.add_many("new_external_calls", [source_name])
                    result.add_many("required_invariants", ["MP-EXT-001", "MP-EXT-002"])
                    result.bump("P1")

        for side_effect_name, spec in side_effects.items():
            for pattern in spec.get("patterns") or []:
                if str(pattern) in body:
                    result.add_many("new_side_effects", [side_effect_name])
                    result.add_many("required_invariants", spec.get("required_invariants") or [])
                    result.bump("P0")

        econ_match = ECONOMIC_ASSIGN_RE.search(body)
        if econ_match:
            left = econ_match.group("left")
            right = econ_match.group("right")
            result.add_many("economic_object_flows", [f"{left}<->{right}"])
            result.add_many("required_invariants", ["MP-ECO-001", "MP-ECO-002"])
            result.bump("P1")
            for field_name in (left, right):
                if field_name not in economic_fields:
                    result.add_many("unregistered_objects", [f"economic_field:{field_name}"])

    invariants = mapping.get("invariants") or {}
    for invariant in list(result.required_invariants):
        result.add_many("tests", (invariants.get(invariant) or {}).get("tests") or [])
    if result.risk in {"P0", "P1"}:
        result.run_integration = True
    return result


def write_github_output(path: str, result: Classification) -> None:
    data = result.to_dict()
    tests = token_list(data["tests"])
    invariants = token_list(data["required_invariants"])
    payload = json.dumps(data, sort_keys=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"risk={data['risk']}\n")
        fh.write("tests<<EOF\n")
        fh.write(tests + "\n")
        fh.write("EOF\n")
        fh.write("invariants<<EOF\n")
        fh.write(invariants + "\n")
        fh.write("EOF\n")
        fh.write(f"run_integration={'true' if data['run_integration'] else 'false'}\n")
        fh.write("classification_json<<EOF\n")
        fh.write(payload + "\n")
        fh.write("EOF\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--diff-file", type=Path)
    parser.add_argument("--objects", type=Path, default=ROOT / "architecture/money_path_objects.yaml")
    parser.add_argument("--mapping", type=Path, default=ROOT / "architecture/money_path_ci.yaml")
    parser.add_argument("--github-output")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--fail-on-unregistered", action="store_true")
    args = parser.parse_args(argv)

    objects = load_yaml(args.objects)
    mapping = load_yaml(args.mapping)
    diff = diff_text(args.base, args.head, args.diff_file)
    files = changed_files(args.base, args.head, diff)
    result = classify(diff, files, objects, mapping)
    data = result.to_dict()
    print(json.dumps(data, indent=2, sort_keys=True))
    if args.json_output:
        args.json_output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    github_output = args.github_output or os.environ.get("GITHUB_OUTPUT")
    if github_output:
        write_github_output(github_output, result)
    if args.fail_on_unregistered and (
        result.unregistered_objects or result.authority_fabricating_defaults or result.migration_policy_missing
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
