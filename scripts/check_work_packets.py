from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_KEYS = [
    "work_packet_id",
    "packet_type",
    "objective",
    "why_this_now",
    "truth_layer",
    "control_layer",
    "evidence_layer",
    "zones_touched",
    "invariants_touched",
    "required_reads",
    "files_may_change",
    "files_may_not_change",
    "acceptance",
    "evidence_required",
    "rollback",
]

def find_packets() -> list[Path]:
    live_packets = ROOT / "docs" / "work_packets"
    if live_packets.exists():
        return sorted(live_packets.rglob("*.md"))
    legacy_packets = ROOT / "work_packets"
    if legacy_packets.exists():
        return sorted(legacy_packets.rglob("*.md"))
    return []

def check_packet(path: Path) -> list[str]:
    text = path.read_text()
    errors: list[str] = []
    frontmatter = re.search(r"```yaml(.*?)```", text, re.S)
    if not frontmatter:
        return [f"{path}: missing yaml front matter block"]
    block = frontmatter.group(1)
    for key in REQUIRED_KEYS:
        if re.search(rf"^\s*{re.escape(key)}\s*:", block, re.M) is None:
            errors.append(f"{path}: missing key {key}")
    return errors

def main() -> int:
    packets = find_packets()
    if not packets:
        print("no work packet directory present; packet grammar check skipped")
        return 0
    errors = []
    for packet in packets:
        errors.extend(check_packet(packet))
    if errors:
        print("\n".join(errors))
        return 1
    print("work packet grammar ok")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
