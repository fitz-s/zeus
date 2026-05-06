#!/usr/bin/env python3
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P2-1 +
#                  repo_review_2026-05-01 SYNTHESIS K-C +
#                  P1_3_TRUTH_AUTHORITY_AUDIT.md grammar table §0
#                  + global epistemic_scaffold (~/CLAUDE.md) gate
# Purpose: lock the per-file baseline of bare `*source*: str` fields in
#          src/contracts/ so a 10th + bare source field cannot land silently.
"""Bare-source-string baseline scanner for src/contracts/.

Why this exists
---------------
The 2026-05-01 P1-3 audit found 9 bare `*source*: str` fields across
src/contracts/. None of them are external-authority values today
(they are internal label strings like `"polymarket_v2_adapter"`), so
the global epistemic_scaffold rule "wrap external-authority values in
ExternalParameter[T]" doesn't fire for any of them.

But the surface is fragile: if a future contract dataclass adds a
NEW `source: str` field that DOES carry external-authority semantics
(e.g., a vendor fee URL, a third-party calibration timestamp), and
that field bypasses the ExternalParameter[T] wrapper, provenance
evaporates the moment the row is constructed.

This scanner doesn't auto-classify each field as internal-vs-external
(that needs human judgement). It locks the per-file count of bare
`*source*: str` fields at the 2026-05-01 baseline so any NEW such
field forces an audit at PR review time:
- if internal label → bump the baseline, document why
- if external authority → wrap in ExternalParameter[T] / InheritedArtifact[T]

Same forcing pattern as `scripts/check_dynamic_sql.py` and
`scripts/check_identity_column_defaults.py`.

Patterns matched
----------------
The scanner matches dataclass / signature lines of the form:
    NAME: str
    NAME: str = "..."
    NAME: str | None = None
    NAME: str | None
where NAME contains the substring `source` (case-insensitive). Catches
`source`, `source_id`, `source_field`, `verification_source`,
`fee_source`, `depth_proof_source`, `imputation_source`, `edge_source`,
`forecast_source_role`, `bin_source`, etc.

NOT matched: function-body local variables, class methods returning str.
The intent is dataclass field declarations + function parameter
annotations at the top level of contract definitions.

CLI
---
    python3 scripts/check_contract_source_fields.py            # exit 0/2
    python3 scripts/check_contract_source_fields.py --json     # JSON

Test wrapper
------------
`tests/test_contract_source_fields_baseline.py` calls the same resolver.
Pre-commit hook should include the test file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "src" / "contracts"


# Pattern: leading whitespace, identifier containing "source", colon, str-or-
# Optional[str] annotation, optional default. Three accepted annotation forms:
#   - bare    : str
#   - PEP 604 : str | None
#   - typing  : Optional[str]
# Matches dataclass attrs and function-signature parameters. Intentionally
# unanchored to start-of-line so 4-space-indented function params match too.
#
# PR #35 P2 review: prior regex only matched `str` and `str | None`, so
# `Optional[str]` annotations slipped through (3 hidden cases:
# world_view/observations.py, world_view/settlements.py, and
# world_view/forecasts.py:data_source_version). Adding the Optional[str]
# alternative closed the gap and bumped the baseline accordingly.
_BARE_SOURCE_STR_PATTERN = re.compile(
    r"^\s+\w*source\w*\s*:\s*"
    r"(?:Optional\s*\[\s*str\s*\]|str(?:\s*\|\s*None)?)"
    r"(?:\s*=|\s*$|\s*,)",
    re.MULTILINE | re.IGNORECASE,
)


# Per-file baseline as of 2026-05-01 (16 fields across 7 files).
# Update both this dict AND the test wrapper baseline when a new field
# lands. The forcing-function principle: a NEW bare `*source*: str` /
# `Optional[str]` field requires deliberate human judgment (internal
# label → bump baseline; external authority → wrap in ExternalParameter[T]).
#
# PR #35 P2 review (2026-05-01): regex now also matches Optional[str].
# Three previously-hidden fields surfaced; all classified internal label
# (string identifier of an internal source/version, not external authority):
#   - src/contracts/world_view/forecasts.py:34   data_source_version: Optional[str]
#   - src/contracts/world_view/observations.py:29  source: Optional[str]
#   - src/contracts/world_view/settlements.py:28  source: Optional[str]
#
# golden-knitting-wand.md Phase 1 (2026-05-06, Fix B): new field surfaced
# in src/contracts/world_view/calibration.py via the cycle/source_id/
# horizon_profile stratification thread-through. Classified internal label
# (one of {'tigge_mars', 'ecmwf_open_data', ...}, enumerated values not
# external authority).
_BASELINE_PER_FILE: dict[str, int] = {
    "src/contracts/execution_intent.py": 6,
    "src/contracts/executable_market_snapshot_v2.py": 4,
    "src/contracts/semantic_types.py": 1,
    "src/contracts/expiring_assumption.py": 1,
    "src/contracts/world_view/calibration.py": 1,
    "src/contracts/world_view/forecasts.py": 2,
    "src/contracts/world_view/observations.py": 1,
    "src/contracts/world_view/settlements.py": 1,
}


def collect_per_file_counts() -> Counter:
    counts: Counter = Counter()
    for py_file in CONTRACTS_DIR.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n = len(_BARE_SOURCE_STR_PATTERN.findall(text))
        if n:
            rel = str(py_file.relative_to(REPO_ROOT))
            counts[rel] = n
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(argv)

    actual = collect_per_file_counts()
    baseline = _BASELINE_PER_FILE

    new_files = sorted(set(actual) - set(baseline))
    grown = sorted(
        f for f in actual if f in baseline and actual[f] > baseline[f]
    )
    shrunk = sorted(
        f for f in actual if f in baseline and actual[f] < baseline[f]
    )
    repaired = sorted(set(baseline) - set(actual))

    drift_lines: list[str] = []
    for f in new_files:
        drift_lines.append(
            f"NEW file: {f} has {actual[f]} bare *source*:str field(s). "
            "Audit each: is it an internal label (bump baseline) or an "
            "external-authority value that should be wrapped in "
            "ExternalParameter[T]? Decide before merging."
        )
    for f in grown:
        drift_lines.append(
            f"GROWN: {f} now has {actual[f]} (baseline {baseline[f]}). "
            f"Audit the {actual[f] - baseline[f]} new occurrence(s)."
        )
    for f in shrunk:
        drift_lines.append(
            f"SHRUNK: {f} now has {actual[f]} (baseline {baseline[f]}). "
            "If you wrapped a field in ExternalParameter — congrats; update "
            "_BASELINE_PER_FILE so the gate tightens."
        )
    for f in repaired:
        drift_lines.append(
            f"REPAIRED: {f} no longer has bare source-str fields "
            f"(baseline {baseline[f]}). Remove the entry from "
            "_BASELINE_PER_FILE."
        )

    if args.json:
        report = {
            "ok": not drift_lines,
            "total_actual": sum(actual.values()),
            "total_baseline": sum(baseline.values()),
            "new_files": new_files,
            "grown": grown,
            "shrunk": shrunk,
            "repaired": repaired,
            "actual_counts": dict(sorted(actual.items())),
            "baseline_counts": dict(sorted(baseline.items())),
        }
        print(json.dumps(report, indent=2))
        return 0 if not drift_lines else 2

    if drift_lines:
        print(
            f"[check_contract_source_fields] BLOCKED — {len(drift_lines)} "
            f"drift event(s) vs 2026-05-01 baseline (total "
            f"{sum(baseline.values())} fields across {len(baseline)} files):",
            file=sys.stderr,
        )
        for line in drift_lines:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nThis gate locks the bare-source-str surface so a new contract "
            "field cannot bypass the ExternalParameter[T] wrapper without "
            "operator review. Decide: internal label → bump baseline, "
            "external authority → wrap.",
            file=sys.stderr,
        )
        return 2

    print(
        f"[check_contract_source_fields] OK — {sum(actual.values())} bare "
        f"*source*:str field(s) across {len(actual)} files, all matching "
        "2026-05-01 baseline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
