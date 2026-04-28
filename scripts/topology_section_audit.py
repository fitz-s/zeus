#!/usr/bin/env python3
# Lifecycle: created=2026-04-28; last_reviewed=2026-04-28; last_reused=2026-04-28
# Purpose: Audit architecture/topology.yaml sections for recent usage and replacement/sunset candidates.
# Reuse: Run read-only for quarterly or packet-close topology-section audits; report-out writes non-authority evidence only.
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round2_verdict.md §2.1 D1 + §4.2 #10 + DEEP_PLAN §4.2 + Tier 2
# Phase 2 ITEM #14 dispatch (90-day catch-history audit per topology.yaml
# section). Per Fitz Constraint #3 (immune system: prove relevance before keep).
"""90-day catch-history audit for architecture/topology.yaml sections.

For each top-level section, classify into 4 tiers (STRATIFIED per T2P1-1):

  KEEP_STRONG       Section content cited recently AND target back-references
                    section identity (bidirectional cite). Load-bearing.
  KEEP_MARGINAL     One-channel only: either cited recently OR target back-refs.
                    Marginal value; consider replacing with code or pruning fields.
  SUNSET_CANDIDATE  No mention in either channel in 90-day window. Per round-2
                    verdict §2.1 D1: empirically-decidable archive trigger.
  REPLACE_WITH_PYTHON  Architectural section best expressed as code (zones,
                    runtime_modes, profile dispatch). Per opponent §3.3 +
                    proponent A6 (verdict §1.2 routers convergence).

Outputs:
  - Stratified report (text or JSON) — per-section verdict + evidence count
  - Per-section keyword-extracted "what does this section claim?"
  - Recommendation (operator decides) per section

Usage:
    python3 scripts/topology_section_audit.py [--json] [--since-days N] \\
            [--report-out <path>]
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
TOPOLOGY = REPO_ROOT / "architecture" / "topology.yaml"
DEFAULT_SINCE_DAYS = 90

# Sections that are clearly architectural (would benefit from python encoding
# rather than YAML). Per opponent §3.3 + verdict §2.1 D1.
PYTHON_REPLACEMENT_CANDIDATES = {
    "coverage_roots": "FS-walk-derivable; zones.py runtime introspection",
    "registry_directories": "FS-walk-derivable; package __init__.py registries",
    "module_manifest": "Already a manifest; possible runtime_modes.py introspectable",
    "module_reference_layer": "Already a manifest; possible runtime_modes.py introspectable",
    "runtime_artifact_inventory": "FS-walk + scripts/* output classification",
    "docs_mode_excluded_roots": "FS-walk-derivable",
    "docs_subroots": "FS-walk-derivable",
    "core_map_profiles": "Profile dispatch; could move to topology_navigator.py",
    "digest_profiles": "Profile dispatch; could move to topology_navigator.py",
}


def list_top_sections(text: str) -> list[str]:
    """Pull every top-level YAML key (lines starting with letter:)."""
    sections = []
    for m in re.finditer(r"^([a-z_][\w_]*)\s*:", text, re.MULTILINE):
        name = m.group(1)
        if name not in ("schema_version", "metadata"):
            sections.append(name)
    return sections


def section_text(text: str, name: str) -> str:
    """Extract the YAML body for one top-level section."""
    pat = re.compile(r"^" + re.escape(name) + r":[ \t]*\n(.*?)(?=^[a-z_][\w_]*:|\Z)",
                     re.DOTALL | re.MULTILINE)
    m = pat.search(text)
    return m.group(1) if m else ""


def git_log_mentions(keyword: str, since_days: int) -> int:
    """Count git commits matching keyword (regex-safe) in last N days."""
    try:
        out = subprocess.run(
            ["git", "log", f"--since={since_days} days ago", "--all",
             "--pretty=format:%H", f"--grep={keyword}"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=30,
        )
        if out.returncode != 0:
            return 0
        return len([line for line in out.stdout.splitlines() if line.strip()])
    except (subprocess.TimeoutExpired, OSError):
        return 0


def file_back_references(section: str, since_days: int) -> int:
    """Count recently-modified docs/src files that mention this section name."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    hits = 0
    for root in ["docs/operations", "docs/reference", "src", "scripts"]:
        full = REPO_ROOT / root
        if not full.exists():
            continue
        for path in full.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".md", ".yaml"):
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    continue
                if section in path.read_text(errors="ignore"):
                    hits += 1
            except (OSError, UnicodeDecodeError):
                continue
    return hits


def classify_section(name: str, body: str, since_days: int) -> dict:
    """Return STRATIFIED verdict + evidence per section."""
    forward_cites = body.count("/")  # rough: paths cited inside section body
    git_hits = git_log_mentions(name, since_days)
    file_hits = file_back_references(name, since_days)
    bidirectional = git_hits > 0 and file_hits > 0
    one_channel = (git_hits > 0) ^ (file_hits > 0)

    if name in PYTHON_REPLACEMENT_CANDIDATES:
        verdict = "REPLACE_WITH_PYTHON"
        rationale = PYTHON_REPLACEMENT_CANDIDATES[name]
    elif bidirectional:
        verdict = "KEEP_STRONG"
        rationale = "bidirectional cite within last %d days" % since_days
    elif one_channel:
        verdict = "KEEP_MARGINAL"
        rationale = ("git-log only" if git_hits > 0 else "file-mention only") + \
                    " within last %d days" % since_days
    else:
        verdict = "SUNSET_CANDIDATE"
        rationale = "no mention in either channel in last %d days" % since_days

    return {
        "section": name,
        "verdict": verdict,
        "rationale": rationale,
        "body_size_chars": len(body),
        "body_path_cites": forward_cites,
        "git_log_hits_90d": git_hits,
        "file_mention_hits_90d": file_hits,
    }


def audit(since_days: int = DEFAULT_SINCE_DAYS) -> dict:
    text = TOPOLOGY.read_text()
    sections = list_top_sections(text)
    results = []
    for s in sections:
        body = section_text(text, s)
        results.append(classify_section(s, body, since_days))

    counts = {
        "KEEP_STRONG": 0,
        "KEEP_MARGINAL": 0,
        "SUNSET_CANDIDATE": 0,
        "REPLACE_WITH_PYTHON": 0,
    }
    for r in results:
        counts[r["verdict"]] += 1

    return {
        "topology_path": str(TOPOLOGY.relative_to(REPO_ROOT)),
        "since_days": since_days,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "total_sections": len(sections),
        "stratified_counts": counts,
        "sections": results,
    }


def render_report_md(report: dict) -> str:
    lines = [
        "# Topology section audit — " + report["audited_at"][:10],
        "",
        f"Source: `{report['topology_path']}`",
        f"Window: {report['since_days']} days",
        f"Total sections: {report['total_sections']}",
        "",
        "## Stratified counts",
        "",
        "| Verdict | Count |",
        "|---|---|",
    ]
    for k, v in report["stratified_counts"].items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## Per-section detail")
    lines.append("")
    lines.append("| Section | Verdict | Body chars | Path cites | Git hits | File hits | Rationale |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in report["sections"]:
        lines.append(
            f"| `{r['section']}` | **{r['verdict']}** | {r['body_size_chars']} | "
            f"{r['body_path_cites']} | {r['git_log_hits_90d']} | "
            f"{r['file_mention_hits_90d']} | {r['rationale']} |"
        )
    lines.append("")
    lines.append("## Recommendations (operator decides per-section)")
    lines.append("")
    lines.append("- **KEEP_STRONG**: retain in YAML.")
    lines.append("- **KEEP_MARGINAL**: investigate whether one-channel hits are real or coincidental name match; consider field-level pruning.")
    lines.append("- **SUNSET_CANDIDATE**: archive (per round-2 verdict §2.1 D1 + Fitz Constraint #3 immune-system retention).")
    lines.append("- **REPLACE_WITH_PYTHON**: defer to Phase 3+ when zones.py / runtime_modes.py / topology_navigator.py replacements land.")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON output to stdout")
    ap.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    ap.add_argument("--report-out", help="Write markdown report to this path")
    args = ap.parse_args()

    report = audit(args.since_days)
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.report_out:
        Path(args.report_out).write_text(render_report_md(report))
        print(f"wrote report: {args.report_out}")
        c = report["stratified_counts"]
        print(f"counts: KEEP_STRONG={c['KEEP_STRONG']} KEEP_MARGINAL={c['KEEP_MARGINAL']} "
              f"SUNSET_CANDIDATE={c['SUNSET_CANDIDATE']} REPLACE_WITH_PYTHON={c['REPLACE_WITH_PYTHON']}")
    else:
        print(render_report_md(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
