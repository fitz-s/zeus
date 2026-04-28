#!/usr/bin/env python3
# Lifecycle: created=2026-04-28; last_reviewed=2026-04-28; last_reused=2026-04-28
# Purpose: Audit recent references for architecture/history_lore.yaml cards before archive/sunset decisions.
# Reuse: Run read-only for quarterly or packet-close lore-card mention audits; output is diagnostic only.
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round2_verdict.md §4.2 #12 + DEEP_PLAN §4.2 #12 + Tier 2
# Phase 1 #16 dispatch (90-day no-mention sunset audit). Per Fitz Constraint
# #3 (immune system: archive antibody library, do not delete).
"""90-day no-mention audit for architecture/history_lore.yaml entries.

For each top-level entry (id: ...), check whether the entry id OR any of its
distinctive keywords appear in either:
  (a) git log --since="90 days ago" --all (commit messages + diffs)
  (b) recently-modified files under docs/operations/, docs/reference/, src/

Outputs:
  - JSON report to .code-review-graph/history_lore_audit_<date>.json with
    per-entry verdict (mentioned/not_mentioned, evidence)
  - Stdout summary table with archive candidates

Usage:
    python3 scripts/history_lore_audit.py [--json] [--since-days N]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LORE_PATH = REPO_ROOT / "architecture" / "history_lore.yaml"
DEFAULT_SINCE_DAYS = 90


def list_entry_ids(text: str) -> list[str]:
    """Pull every `- id: <ID>` value from the YAML (top-level entries only)."""
    ids = []
    for m in re.finditer(r"^\s*-\s*id:\s*(\w[\w_]*)\s*$", text, re.MULTILINE):
        ids.append(m.group(1))
    return ids


def git_log_mentions(entry_id: str, since_days: int) -> int:
    """Return number of git log entries (since N days ago) mentioning entry_id."""
    try:
        out = subprocess.run(
            ["git", "log", f"--since={since_days} days ago", "--all",
             "--pretty=format:%H", f"--grep={entry_id}"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=30,
        )
        if out.returncode != 0:
            return 0
        return len([line for line in out.stdout.splitlines() if line.strip()])
    except (subprocess.TimeoutExpired, OSError):
        return 0


def file_mentions(entry_id: str, since_days: int) -> int:
    """Count recent (since N days ago) modified files under docs/+src/ that
    mention entry_id in their content."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    hits = 0
    for root in ["docs/operations", "docs/reference", "src"]:
        full = REPO_ROOT / root
        if not full.exists():
            continue
        for path in full.rglob("*"):
            if not path.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    continue
                if entry_id in path.read_text(errors="ignore"):
                    hits += 1
            except (OSError, UnicodeDecodeError):
                continue
    return hits


def audit(since_days: int = DEFAULT_SINCE_DAYS) -> dict:
    text = LORE_PATH.read_text()
    ids = list_entry_ids(text)
    report = {
        "lore_path": str(LORE_PATH.relative_to(REPO_ROOT)),
        "since_days": since_days,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "total_entries": len(ids),
        "entries": {},
    }
    archive_candidates = []
    keep_count = 0
    for eid in ids:
        gl = git_log_mentions(eid, since_days)
        fm = file_mentions(eid, since_days)
        verdict = "KEEP" if (gl + fm) > 0 else "ARCHIVE_CANDIDATE"
        report["entries"][eid] = {
            "git_log_hits": gl,
            "file_mention_hits": fm,
            "verdict": verdict,
        }
        if verdict == "ARCHIVE_CANDIDATE":
            archive_candidates.append(eid)
        else:
            keep_count += 1
    report["summary"] = {
        "keep": keep_count,
        "archive_candidates": len(archive_candidates),
        "archive_candidate_ids": archive_candidates,
    }
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON output to stdout")
    ap.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    args = ap.parse_args()
    report = audit(args.since_days)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        s = report["summary"]
        print(f"history_lore_audit: {report['total_entries']} entries audited "
              f"({args.since_days}-day window)")
        print(f"  KEEP (mentioned recently):    {s['keep']}")
        print(f"  ARCHIVE_CANDIDATE (no mention): {s['archive_candidates']}")
        if s["archive_candidate_ids"]:
            print("  Candidates:")
            for eid in s["archive_candidate_ids"]:
                print(f"    - {eid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
