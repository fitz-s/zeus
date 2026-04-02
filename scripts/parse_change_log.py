"""Parse change_log.md → change_outcomes.json for autonomy gate.

Single source of truth = change_log.md (human readable).
Derived data = change_outcomes.json (script readable).
"""

import json
import re
import sys
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
CHANGE_LOG = MEMORY_DIR / "change_log.md"
OUTCOMES_JSON = MEMORY_DIR / "change_outcomes.json"

# Match: ## [CHANGE-NNN] YYYY-MM-DD — title
HEADER_RE = re.compile(r"^## \[(CHANGE-\d+)\]\s+(\d{4}-\d{2}-\d{2})")
# Match: **Verdict: IMPROVEMENT** or - Verdict: REGRESSION etc.
VERDICT_RE = re.compile(r"Verdict:\s*\*{0,2}\s*[✅⏳❌]*\s*([A-Z_]+)")
# Match: P&L: +$6.70 or P&L: -$2.32
PNL_RE = re.compile(r"P&L.*?([+-]?\$[\d.]+)")


def parse_change_log(text: str) -> list[dict]:
    entries = []
    current = None

    for line in text.splitlines():
        header = HEADER_RE.match(line)
        if header:
            if current:
                entries.append(current)
            current = {
                "id": header.group(1),
                "date": header.group(2),
                "verdict": "PENDING",
                "pnl_delta": None,
                "regressions": 0,
            }
            continue

        if current is None:
            continue

        verdict = VERDICT_RE.search(line)
        if verdict:
            v = verdict.group(1).strip("✅⏳❌ ")
            if v in ("IMPROVEMENT", "REGRESSION", "NEUTRAL", "PENDING"):
                current["verdict"] = v

        pnl = PNL_RE.search(line)
        if pnl:
            try:
                current["pnl_delta"] = float(pnl.group(1).replace("$", ""))
            except ValueError:
                pass

        if "regression" in line.lower() and "0" not in line:
            # crude regression detection from narrative
            current["regressions"] = max(current["regressions"], 1)

    if current:
        entries.append(current)

    return entries


def main():
    if not CHANGE_LOG.exists():
        print("No change_log.md found. Writing empty outcomes.", file=sys.stderr)
        OUTCOMES_JSON.write_text("[]")
        return

    text = CHANGE_LOG.read_text()
    entries = parse_change_log(text)
    OUTCOMES_JSON.write_text(json.dumps(entries, indent=2))
    print(f"Parsed {len(entries)} changes → {OUTCOMES_JSON}")


if __name__ == "__main__":
    main()
