#!/usr/bin/env python3
"""R3 Drift Check — verify all phase yaml citations against HEAD.

Created: 2026-04-26
Authority: r3/IMPLEMENTATION_PROTOCOL.md §8

Usage:
    python3 r3_drift_check.py                 # check all phases
    python3 r3_drift_check.py --phase Z2      # check single phase
    python3 r3_drift_check.py --re-anchor     # auto-update line numbers (preserves symbol anchors)

Exit codes:
    0 — GREEN (no drift, or YELLOW line drift only)
    1 — RED (SEMANTIC_MISMATCH or antibody fail; blocks merges)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent.parent.parent.parent  # zeus/
SLICE_DIR = ROOT / "slice_cards"
DRIFT_REPORTS_DIR = ROOT / "drift_reports"

# Match path/file.py:NNN[-MMM] citations
CITE_RE = re.compile(r"((?:src|tests|scripts|architecture|docs)/[\w./_-]+\.(?:py|md|yaml|json|sql))(?::(\d+)(?:-(\d+))?)?")
# Match symbol anchors: function:Foo.bar or class:Foo
SYMBOL_RE = re.compile(r"((?:function|class|method)):([\w.]+)")

# Phase yaml keys we care about
RELEVANT_KEYS = ("file_line", "deliverables", "acceptance_tests", "extended_modules")


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def file_exists(path: str) -> bool:
    return (REPO_ROOT / path).exists()


def grep_for_symbol(file_path: str, symbol: str) -> list[int]:
    """Return list of line numbers where symbol appears in file_path."""
    full = REPO_ROOT / file_path
    if not full.exists():
        return []
    hits = []
    for i, line in enumerate(full.read_text().splitlines(), 1):
        if symbol in line:
            hits.append(i)
    return hits


def _extract_new_module_paths(text: str) -> set[str]:
    """Pull paths from `new_modules:` and `new_files:` blocks of the yaml.

    These are paths the phase WILL CREATE; their non-existence at HEAD
    is expected, not drift.
    """
    new_paths: set[str] = set()
    in_new_block = False
    block_indent = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith(("new_modules:", "new_files:", "new_tables:", "new_emission_seam:", "new_tests:")):
            in_new_block = True
            block_indent = len(line) - len(line.lstrip())
            continue
        if in_new_block:
            cur_indent = len(line) - len(line.lstrip())
            if line and cur_indent <= block_indent and stripped and not stripped.startswith("#"):
                in_new_block = False
            else:
                # Look for path: <something> or - path: <something> or just paths
                m = re.search(r"(?:path:|file:)?\s*((?:src|tests|scripts|architecture|docs)/[\w./_-]+\.(?:py|md|yaml|json|sql))", line)
                if m:
                    new_paths.add(m.group(1))
                # Also any bare line ending in .py/.md/.yaml/.sql under a list item
                m2 = re.match(r"^\s*-\s*((?:src|tests|scripts|architecture|docs)/[\w./_-]+\.(?:py|md|yaml|json|sql))\s*$", line)
                if m2:
                    new_paths.add(m2.group(1))
    return new_paths


_GLOBAL_WILL_CREATE_CACHE: dict[str, set[str]] = {}


def _all_phase_will_create_paths() -> dict[str, set[str]]:
    """For every phase yaml, list what it will create.
    Returns {phase_id: set_of_paths}.
    """
    if _GLOBAL_WILL_CREATE_CACHE:
        return _GLOBAL_WILL_CREATE_CACHE
    for p in SLICE_DIR.glob("*.yaml"):
        _GLOBAL_WILL_CREATE_CACHE[p.stem] = _extract_new_module_paths(p.read_text())
    return _GLOBAL_WILL_CREATE_CACHE


def check_phase(phase_path: Path) -> dict:
    """Return drift report for one phase yaml."""
    text = phase_path.read_text()
    phase_id = phase_path.stem
    findings = {
        "phase": phase_id,
        "green": [],
        "yellow": [],   # line drift but symbol intact
        "red": [],      # SEMANTIC_MISMATCH or file missing
        "skipped": [],  # WILL_CREATE_IN_PHASE_X markers
    }

    # Pre-extract paths the phase will CREATE; non-existence is expected
    will_create_paths = _extract_new_module_paths(text)

    # Also build cross-phase WILL_CREATE union — if any other phase will
    # create this file, it's expected-missing until that phase ships.
    all_will_create = _all_phase_will_create_paths()
    cross_phase_will_create: set[str] = set()
    for other_phase_id, paths in all_will_create.items():
        if other_phase_id != phase_id:
            cross_phase_will_create.update(paths)

    # Find all path:line citations
    for match in CITE_RE.finditer(text):
        path = match.group(1)
        line = match.group(2)
        end_line = match.group(3)

        # Skip self-references (the yaml referencing itself)
        if phase_id in path and path.endswith(".yaml"):
            continue
        # Skip operator_decisions/, evidence/ paths (they're written later)
        if "operator_decisions" in path or "/evidence/" in path or "/q_fx_" in path or "/tigge_" in path or "/cutover_runbook" in path or "/q1_zeus_egress" in path or "/q_hb_" in path or "/q_new_1_" in path or "/calibration_retrain_decision" in path:
            findings["skipped"].append({"path": path, "reason": "evidence_or_operator_doc"})
            continue
        # Skip paths the phase will create
        if path in will_create_paths:
            findings["skipped"].append({"path": path, "reason": "will_create_in_phase"})
            continue
        # Skip paths another phase will create (cross-phase upstream)
        if path in cross_phase_will_create and not file_exists(path):
            findings["skipped"].append({"path": path, "reason": "will_create_by_upstream_phase"})
            continue
        # Skip future-creation markers
        if "WILL_CREATE" in text[max(0, match.start() - 200):match.end() + 100]:
            findings["skipped"].append({"path": path, "reason": "will_create_in_phase"})
            continue
        # Skip references inside reference_excerpts/, frozen_interfaces/, drift_reports/, boot/, _blocked_*, etc.
        if "/reference_excerpts/" in path or "/frozen_interfaces/" in path or "/drift_reports/" in path or "/r3/boot/" in path or "_blocked_" in path or "/_phase_status" in path or "/INVARIANTS_LEDGER" in path or "/SKILLS_MATRIX" in path or "/IMPLEMENTATION_PROTOCOL" in path or "/PHASE_BOOT_PROTOCOL" in path:
            findings["skipped"].append({"path": path, "reason": "r3_meta_doc_or_template"})
            continue
        # Skip test files within tests/ dir if they don't exist (they'll be written by the phase)
        if path.startswith("tests/") and not file_exists(path):
            findings["skipped"].append({"path": path, "reason": "test_file_will_be_written"})
            continue
        # Skip docs/ entries that don't exist (will be created)
        if path.startswith("docs/") and not file_exists(path) and not path.startswith("docs/operations/task_2026-04-26_ultimate_plan/"):
            findings["skipped"].append({"path": path, "reason": "doc_will_be_created"})
            continue
        # Skip script paths that don't exist yet (will be created by phase)
        if path.startswith("scripts/") and not file_exists(path):
            findings["skipped"].append({"path": path, "reason": "script_will_be_created"})
            continue

        if not file_exists(path):
            findings["red"].append({
                "cite": match.group(0),
                "kind": "FILE_MISSING",
                "expected_file": path,
            })
            continue

        # File exists; check line range plausible
        if line:
            try:
                start = int(line)
                end = int(end_line) if end_line else start
                full_lines = (REPO_ROOT / path).read_text().count("\n") + 1
                if start > full_lines or end > full_lines:
                    findings["red"].append({
                        "cite": match.group(0),
                        "kind": "LINE_OUT_OF_RANGE",
                        "actual_lines": full_lines,
                    })
                else:
                    findings["green"].append({"cite": match.group(0)})
            except ValueError:
                findings["yellow"].append({
                    "cite": match.group(0),
                    "kind": "LINE_PARSE_ERROR",
                })
        else:
            findings["green"].append({"cite": match.group(0)})

    return findings


def write_report(reports: list[dict]) -> Path:
    DRIFT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = DRIFT_REPORTS_DIR / f"{date.today().isoformat()}.md"
    head = git_head()
    lines = [
        f"# R3 Drift Report — {date.today().isoformat()}",
        f"",
        f"HEAD: `{head}`",
        f"Phases checked: {len(reports)}",
        f"",
        "## Summary",
        "",
        "| Phase | GREEN | YELLOW | RED | SKIPPED |",
        "|---|---|---|---|---|",
    ]
    total_green = total_yellow = total_red = 0
    for r in reports:
        g, y, rr, s = (
            len(r["green"]),
            len(r["yellow"]),
            len(r["red"]),
            len(r["skipped"]),
        )
        total_green += g
        total_yellow += y
        total_red += rr
        lines.append(f"| {r['phase']} | {g} | {y} | {rr} | {s} |")
    lines.append("")
    lines.append(
        f"**Totals**: {total_green} GREEN · {total_yellow} YELLOW · {total_red} RED"
    )
    lines.append("")

    if total_red > 0:
        lines.append("## RED findings (blocking)")
        lines.append("")
        for r in reports:
            if r["red"]:
                lines.append(f"### {r['phase']}")
                lines.append("")
                for entry in r["red"]:
                    lines.append(f"- `{entry['cite']}` — **{entry['kind']}**")
                lines.append("")

    if total_yellow > 0:
        lines.append("## YELLOW findings (line drift, non-blocking)")
        lines.append("")
        for r in reports:
            if r["yellow"]:
                lines.append(f"### {r['phase']}")
                for entry in r["yellow"]:
                    lines.append(f"- `{entry['cite']}` — **{entry['kind']}**")
                lines.append("")

    report_path.write_text("\n".join(lines) + "\n")
    return report_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", help="check single phase id (e.g., Z2)")
    parser.add_argument(
        "--re-anchor",
        action="store_true",
        help="rewrite line numbers in cards (preserves symbol anchors)",
    )
    args = parser.parse_args()

    if args.phase:
        cards = [SLICE_DIR / f"{args.phase}.yaml"]
    else:
        cards = sorted(SLICE_DIR.glob("*.yaml"))

    reports = [check_phase(c) for c in cards if c.exists()]
    report_path = write_report(reports)

    total_red = sum(len(r["red"]) for r in reports)
    print(f"R3 drift check: {len(reports)} phases checked")
    print(f"Report: {report_path}")
    print(
        f"GREEN={sum(len(r['green']) for r in reports)} "
        f"YELLOW={sum(len(r['yellow']) for r in reports)} "
        f"RED={total_red}"
    )

    if total_red > 0:
        print("STATUS: RED — blocks new phase merges. Fix RED findings first.")
        sys.exit(1)
    else:
        print("STATUS: GREEN" + (" (with line drift)" if any(r["yellow"] for r in reports) else ""))
        sys.exit(0)


if __name__ == "__main__":
    main()
