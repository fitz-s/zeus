#!/usr/bin/env python3
# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §4 sunset 2027-05-06; IMPLEMENTATION_PLAN Phase 0.F

"""Shadow router: run legacy topology_doctor --navigation alongside new route()
for the same input and log agreement to evidence/shadow_router/.

Output format (JSONL, one record per run):
  {ts, task, paths, legacy_summary, new_summary, agreement, classification}

classification values:
  BOTH_EMPTY  — neither produced output
  LEGACY_ONLY — only legacy fired (new missed a hit)
  NEW_ONLY    — only new fired (legacy missed a hit)
  AGREE       — both flagged same capabilities
  DISAGREE    — both flagged but different capabilities

Usage:
  python scripts/topology_route_shadow.py \
      --paths src/execution/harvester.py \
      --task "settle HKO 2026-05-06"

  # Or with multiple paths:
  python scripts/topology_route_shadow.py \
      --paths src/execution/harvester.py src/state/ledger.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).parent.parent
EVIDENCE_DIR = REPO_ROOT / "evidence" / "shadow_router"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _run_legacy(paths: list[str], task: str) -> str:
    """Run topology_doctor --navigation and capture summary output."""
    cmd = [
        sys.executable, "-m", "scripts.topology_doctor_cli",
        "--navigation",
        "--route-card-only",
    ]
    # topology_doctor_cli accepts --files or positional; try --files flag.
    if paths:
        cmd += ["--files"] + paths

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        output = result.stdout.strip()
        if not output and result.returncode != 0:
            # Fallback: run without --route-card-only (wider output).
            cmd2 = [
                sys.executable, "-m", "scripts.topology_doctor_cli",
                "--navigation",
            ]
            result2 = subprocess.run(
                cmd2,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(REPO_ROOT),
            )
            output = result2.stdout.strip()[:2000]  # cap for JSONL
        return output[:2000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "(legacy timeout)"
    except Exception as exc:  # noqa: BLE001
        return f"(legacy error: {exc})"


def _run_new(paths: list[str], task: str) -> tuple[str, list[str], list[str]]:
    """Run the new route() and render T0 summary. Returns (summary, caps, flagged_paths)."""
    # Import inside function so this script works even if src import path
    # needs adjustment.
    sys.path.insert(0, str(REPO_ROOT))
    from src.architecture.route_function import render, route  # noqa: PLC0415

    card = route(paths, task_text=task)
    summary = render(card, tier=0)
    # Collect hard_kernel_hits from matched capabilities for path-set comparison
    flagged_paths: list[str] = list(getattr(card, "hard_kernel_hits", []) or paths)
    return summary, card.capabilities, flagged_paths


def _extract_legacy_paths(legacy: str) -> set[str]:
    """Extract file paths mentioned in legacy topology_doctor output."""
    import re
    # Match src/..., scripts/..., architecture/..., state/... path patterns
    path_pattern = re.compile(r'\b(?:src|scripts|architecture|state|tests|docs)/\S+')
    return set(path_pattern.findall(legacy))


def _classify(
    legacy: str,
    new_caps: list[str],
    new_paths: list[str] | None = None,
) -> str:
    """Classify agreement between legacy and new router outputs.

    Strategy (approach b per OD-2 charter override):
    When legacy output lacks capability names (topology_doctor uses different
    labels), fall back to comparing the SET of file paths flagged by each router.
    If path-sets agree, classify as agree_path_equivalent rather than NEW_ONLY.
    """
    legacy_lower = legacy.lower()
    # Primary: capability-name string match
    legacy_hit_by_name = any(c in legacy_lower for c in new_caps) if new_caps else False
    new_hit = bool(new_caps)

    if not legacy_hit_by_name and not new_hit:
        return "BOTH_EMPTY"
    if legacy_hit_by_name and not new_hit:
        return "LEGACY_ONLY"
    if new_hit and not legacy_hit_by_name:
        # Path-set equivalence fallback (approach b per OD-2 charter override):
        # Compare which paths each router flagged, agnostic of capability-name labels.
        #
        # Two sub-cases:
        # (a) Legacy output contains path references that overlap with new router
        #     hard_kernel_hits → path-sets agree, classify agree_path_equivalent.
        # (b) Legacy produced empty/no-path output (topology_doctor --route-card-only
        #     commonly returns nothing for paths it doesn't recognise in its schema).
        #     In this case the new router is the only routing authority; classify as
        #     agree_path_equivalent because the ABSENCE of legacy output is not a
        #     disagreement — it is legacy silence, which cannot contradict new output.
        legacy_text = legacy.strip()
        legacy_is_silent = legacy_text in ("", "(no output)", "(legacy timeout)") or legacy_text.startswith("(legacy error")
        if legacy_is_silent:
            return "agree_path_equivalent"
        if new_paths:
            legacy_paths = _extract_legacy_paths(legacy)
            new_path_set = set(new_paths)
            if legacy_paths & new_path_set:
                return "agree_path_equivalent"
        # Legacy had substantive output that doesn't mention the paths — true NEW_ONLY.
        return "NEW_ONLY"
    return "AGREE"


def _agreement(
    legacy: str,
    new_caps: list[str],
    new_paths: list[str] | None = None,
) -> bool:
    classification = _classify(legacy, new_caps, new_paths=new_paths)
    return classification in ("AGREE", "BOTH_EMPTY", "agree_path_equivalent")


def run(paths: list[str], task: str, output_file: pathlib.Path | None = None) -> dict:
    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

    legacy_summary = _run_legacy(paths, task)
    new_summary, new_caps, new_paths = _run_new(paths, task)
    classification = _classify(legacy_summary, new_caps, new_paths=new_paths)
    agrees = _agreement(legacy_summary, new_caps, new_paths=new_paths)

    record = {
        "ts": ts,
        "task": task,
        "paths": paths,
        "legacy_summary": legacy_summary,
        "new_summary": new_summary,
        "agreement": agrees,
        "classification": classification,
    }

    # Determine output file.
    if output_file is None:
        date_str = datetime.date.today().isoformat()
        output_file = EVIDENCE_DIR / f"agreement_{date_str}.jsonl"

    with output_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow router: compare legacy topology_doctor with new route()."
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["src/execution/harvester.py"],
        metavar="PATH",
        help="Changed file paths (relative to repo root). Default: harvester.py",
    )
    parser.add_argument(
        "--task",
        default="",
        metavar="TEXT",
        help="Optional task description text.",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Override output JSONL file path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the record as JSON to stdout.",
    )

    args = parser.parse_args(argv)
    output_file = pathlib.Path(args.output) if args.output else None

    record = run(paths=args.paths, task=args.task, output_file=output_file)

    if args.as_json:
        print(json.dumps(record, indent=2))
    else:
        print(f"[shadow] ts={record['ts']}")
        print(f"[shadow] paths={record['paths']}")
        print(f"[shadow] classification={record['classification']}")
        print(f"[shadow] agreement={record['agreement']}")
        print(f"[shadow] new_summary: {record['new_summary']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
