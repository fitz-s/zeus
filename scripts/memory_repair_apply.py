#!/usr/bin/env python3
"""memory_repair_apply.py — W1.6b MEMORY citation repair lane (stdlib only)

Usage:
    python3 scripts/memory_repair_apply.py [--audit-file <path>] [--memory-dir <path>]

Reads the latest memory_audit_*.md (or --audit-file). For each STALE citation:
greps an anchor from the feedback file context, finds it in the target file,
and proposes a unified-diff patch (NOT applied). For each DEAD citation: prints
slug + manual-review recommendation. Empty audit (baseline=0) exits 0.

Paths are derived to be portable:
- ZEUS_ROOT: derived from __file__ (script lives at <zeus>/scripts/memory_repair_apply.py).
- MEMORY_DIR: --memory-dir CLI arg → ZEUS_MEMORY_DIR env var → default
  (~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory).
"""
# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/zeus_agent_runtime_compounding_plan_2026-05-16.md §4 W1.6b

from __future__ import annotations
import argparse, os, re, sys
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_MEMORY_BASE = Path.home() / ".claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus"


def _resolve_memory_dir(cli_arg: str | None) -> Path:
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("ZEUS_MEMORY_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_MEMORY_BASE / "memory"


# Populated by main() — placeholder for module-level helpers that need MEMORY_DIR.
MEMORY_DIR: Path = _DEFAULT_MEMORY_BASE / "memory"
_BASE: Path = _DEFAULT_MEMORY_BASE

_ROW = re.compile(r'^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*([\d\-]+)\s*\|\s*\*\*(STALE|DEAD)\*\*\s*\|')
_APAREN  = re.compile(r'\(`([^`\n]{2,60})`\)')
_ABTICK  = re.compile(r'`([^`\n]{3,60})`')


def _latest_audit() -> Path | None:
    cands = sorted(_BASE.glob("memory_audit_*.md"), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def _parse_entries(text: str) -> list[dict]:
    entries, in_sec = [], False
    for line in text.splitlines():
        if "## STALE / DEAD Citations" in line: in_sec = True; continue
        if in_sec and line.startswith("## "): break
        if in_sec:
            m = _ROW.match(line)
            if m:
                src, raw, cited, ls, verdict = m.groups()
                p = ls.split("-")
                entries.append({"src": src, "raw": raw, "cited": cited,
                                 "start": int(p[0]), "end": int(p[1]) if len(p) > 1 else None,
                                 "verdict": verdict})
    return entries


def _resolve(cited: str) -> Path | None:
    p = Path(cited)
    if p.is_absolute(): return p if p.is_file() else None
    d = ZEUS_ROOT / p
    if d.is_file(): return d
    if p.parent == Path("."):
        hits = list(ZEUS_ROOT.rglob(p.name))
        if hits: return hits[0]
    return None

def _anchor(fb_text: str, raw: str) -> str | None:
    idx = fb_text.find(raw)
    if idx == -1: return None
    post = fb_text[idx + len(raw): idx + len(raw) + 120]
    m = _APAREN.search(post)
    if m: return m.group(1)
    # Prefer identifier immediately after citation (e.g. ` — `func_name``)
    for m in _ABTICK.finditer(post):
        c = m.group(1)
        if c != raw and len(c) >= 4 and "/" not in c: return c
    # Pre-citation fallback REMOVED 2026-05-16 per pre-PR critic CRITICAL #2:
    # prose preamble (e.g. "Python sqlite3 `with conn:` ... See `src/db.py:42`")
    # was matching `with conn:` instead of an identifier near the citation,
    # producing wrong-anchor patches. "No anchor" is safer than wrong anchor —
    # _manual("no anchor found") triggers operator-eyeball review instead.
    return None

def _grep(path: Path, anc: str) -> list[int]:
    try: lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError: return []
    return [i + 1 for i, ln in enumerate(lines) if anc in ln]

def _diff(name: str, raw: str, s: int, e: int | None, ns: int) -> str:
    d = ns - s
    nr = raw.replace(str(s), str(ns), 1)
    if e: nr = nr.replace(f"-{e}", f"-{e+d}", 1)
    return f"--- a/{name}\n+++ b/{name}\n@@ citation patch @@\n-{raw}\n+{nr}\n"


def _manual(reason: str) -> None:
    print(f"  -> Manual review needed ({reason})")

def _handle_stale(e: dict) -> None:
    src, raw, cited, start, end = e["src"], e["raw"], e["cited"], e["start"], e["end"]
    print(f"\n[STALE] {src} → `{raw}`")
    fb_path = MEMORY_DIR / src
    if not fb_path.is_file(): return _manual(f"feedback file missing: {src}")
    target = _resolve(cited)
    if target is None:
        print(f"  NOTE: `{cited}` now missing — reclassify as DEAD")
        return _manual("file gone")
    anc = _anchor(fb_path.read_text(encoding="utf-8", errors="replace"), raw)
    if anc is None: return _manual("no anchor near citation; cannot locate moved content")
    hits = _grep(target, anc)
    if not hits: return _manual(f"anchor `{anc}` not in {target.name}; content may have moved")
    if len(hits) > 1: return _manual(f"anchor `{anc}` at {hits} — ambiguous")
    if hits[0] == start: print(f"  Anchor at same line {start} — citation may be correct"); return
    ns = hits[0]
    print(f"  Anchor `{anc}` at line {ns} (was {start})")
    print("  Proposed patch (NOT applied):\n")
    for ln in _diff(src, raw, start, end, ns).splitlines(): print(f"    {ln}")


def _handle_dead(e: dict) -> None:
    slug = e["src"].replace("feedback_", "").replace(".md", "")
    print(f"\n[DEAD] {e['src']} (slug: {slug}) → `{e['raw']}`")
    print(f"  Target `{e['cited']}` not found — manual review needed (renamed/deleted/moved)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit-file", metavar="PATH",
                    help="Specific memory_audit_*.md (default: latest by mtime)")
    ap.add_argument("--memory-dir", metavar="PATH",
                    help="MEMORY directory containing feedback_*.md (default: $ZEUS_MEMORY_DIR or "
                         "~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory)")
    args = ap.parse_args()

    global MEMORY_DIR, _BASE
    MEMORY_DIR = _resolve_memory_dir(args.memory_dir)
    _BASE = MEMORY_DIR.parent

    audit_path = Path(args.audit_file) if args.audit_file else _latest_audit()
    if audit_path is None:
        print("No memory_audit_*.md found. Run memory_audit.py first.")
        sys.exit(1)
    if not audit_path.is_file():
        print(f"ERROR: audit file not found: {audit_path}", file=sys.stderr)
        sys.exit(2)

    text = audit_path.read_text(encoding="utf-8", errors="replace")
    print(f"Audit file: {audit_path.name}")

    entries = _parse_entries(text)
    if not entries:
        print("No STALE/DEAD citations in audit; nothing to repair.")
        sys.exit(0)

    stale = [e for e in entries if e["verdict"] == "STALE"]
    dead  = [e for e in entries if e["verdict"] == "DEAD"]
    print(f"Found: {len(stale)} STALE, {len(dead)} DEAD")

    for e in stale: _handle_stale(e)
    for e in dead:  _handle_dead(e)

    print(f"\nSummary: {len(stale)} STALE, {len(dead)} DEAD processed.")
    print("Review proposed patches above. Apply manually; script does NOT modify files.")


if __name__ == "__main__":
    main()
