# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ANTI_DRIFT_CHARTER §7 (INV-HELP-NOT-GATE); IMPLEMENTATION_PLAN Phase 3 day 48-50
"""INV-HELP-NOT-GATE mid-drift check — Phase 3 deliverable.

Three concrete assertions per ANTI_DRIFT_CHARTER §7:

  1. test_no_helper_blocks_unrelated_capability
     No helper's forbidden_files (or blocking_paths equivalent) may intersect
     capability paths that are outside its declared scope_capabilities.

  2. test_every_invocation_emits_ritual_signal
     Every ritual_signal log entry must carry all schema-required fields
     (no silently malformed lines).

  3. test_does_not_fit_returns_zero
     Helpers that have a does_not_fit: refuse_with_advice or log_and_advisory
     policy must not carry forbidden_files crossing capability boundaries
     (structural enforcement of the zero-exit contract; actual subprocess test
     deferred to Phase 5 when gates have entry_points).

Phase 5 will extend this file with subprocess invocation tests once enforcement
gates (§5) have runnable entry_points.
"""
from __future__ import annotations

import json
import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
CAPS_PATH = REPO_ROOT / "architecture" / "capabilities.yaml"
RITUAL_LOG_DIR = REPO_ROOT / "logs" / "ritual_signal"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_helpers() -> list[dict]:
    """Return list of helper dicts from SKILL.md frontmatter in .agents/skills/."""
    helpers = []
    for skill_md in SKILLS_DIR.rglob("SKILL.md"):
        text = skill_md.read_text()
        parts = text.split("---", 2)
        if len(parts) < 2:
            continue
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            fm = {}
        if not isinstance(fm, dict):
            fm = {}
        fm["_path"] = str(skill_md)
        fm["_name"] = fm.get("name", skill_md.parent.name)
        helpers.append(fm)
    return helpers


def _load_capabilities() -> list[dict]:
    """Return capabilities list from capabilities.yaml."""
    with CAPS_PATH.open() as f:
        return yaml.safe_load(f)["capabilities"]


def _capability_owners_of(path: str) -> set[str]:
    """Return capability IDs whose hard_kernel_paths contain `path`."""
    caps = _load_capabilities()
    owners: set[str] = set()
    for cap in caps:
        for kp in cap.get("hard_kernel_paths", []):
            kp_norm = kp.replace("\\", "/")
            path_norm = path.replace("\\", "/")
            if path_norm == kp_norm or path_norm.endswith("/" + kp_norm) or kp_norm.endswith("/" + path_norm):
                owners.add(cap["id"])
    return owners


def _ritual_log_entries(days: int = 30) -> list[dict]:
    """Read all ritual_signal log lines from the last N days (best-effort)."""
    import datetime
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    entries = []
    if not RITUAL_LOG_DIR.exists():
        return entries
    for log_file in sorted(RITUAL_LOG_DIR.rglob("*.jsonl")):
        for line in log_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                obj = {"_malformed": line}
            try:
                ts_str = obj.get("invocation_ts", "")
                ts = datetime.datetime.fromisoformat(ts_str)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
            entries.append(obj)
    return entries


# ---------------------------------------------------------------------------
# Assertion 1: no helper blocks unrelated capability
# ---------------------------------------------------------------------------

def test_no_helper_blocks_unrelated_capability():
    """ANTI_DRIFT_CHARTER §7 — assertion 1.

    A helper's forbidden_files (or blocking_paths equivalent) must intersect
    only capabilities declared in its scope_keywords / scope_capabilities.
    Cross-capability blocking is the structural shape of 禁书 drift.
    """
    helpers = _load_helpers()
    violations = []

    for helper in helpers:
        name = helper["_name"]
        blocking_paths: list[str] = helper.get("forbidden_files", []) or []
        declared_caps: set[str] = set(helper.get("scope_capabilities", []))

        for blocked_path in blocking_paths:
            owners = _capability_owners_of(blocked_path)
            if owners and not owners.issubset(declared_caps):
                cross_caps = owners - declared_caps
                violations.append(
                    f"{name} blocks {blocked_path!r} (owned by {sorted(cross_caps)}) "
                    f"but declares scope_capabilities={sorted(declared_caps)}"
                )

    assert not violations, (
        "INV-HELP-NOT-GATE: helpers block capability paths outside their declared scope:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Assertion 2: every ritual_signal log line is schema-compliant and cap_id
# resolves to capabilities.yaml (Phase 5.A production assertion).
# ---------------------------------------------------------------------------

_REQUIRED_SIGNAL_FIELDS = {
    "helper",
    "task_id",
    "fit_score",
    "advisory_or_blocking",
    "outcome",
    "invocation_ts",
    "charter_version",
}

# Gate helpers that predate cap_id field (Phase 4.D gap; their entries lack the
# field entirely — not a schema violation, just a field-absent older format).
_HELPERS_WITHOUT_CAP_ID = frozenset({"gate2_live_auth_token", "replay_correctness_gate"})

# Capabilities.yaml restored to canonical 16 entries on 2026-05-06 from
# stash@{0}^3 (Phase 5.A discovery: 4.B re-authoring had hallucinated 6 entries
# during the off-branch incident; recovery executor restored the 6-entry stash
# version, masking the regression). The deferred-cap-id allowlist is removed —
# every gate-emitted cap_id must now resolve to capabilities.yaml.
_PHASE4D_DEFERRED_CAP_IDS: frozenset[str] = frozenset()


def test_every_invocation_emits_ritual_signal():
    """ANTI_DRIFT_CHARTER §7 — assertion 2 (Phase 5.A production version).

    Every ritual_signal log entry is checked for:
      (a) schema compliance — all required fields present.
      (b) cap_id resolution — cap_id (when present) resolves to capabilities.yaml
          OR is the '(none)' sentinel (no capability matched) OR is a documented
          Phase 4.D deferred ID (_PHASE4D_DEFERRED_CAP_IDS).

    No new orphaned cap_ids beyond _PHASE4D_DEFERRED_CAP_IDS are permitted.
    """
    entries = _ritual_log_entries(days=30)

    if not entries:
        pytest.skip(
            "No ritual_signal entries in the last 30 days — "
            "log not yet populated (Phase 5 wires telemetry)"
        )

    caps = _load_capabilities()
    known_cap_ids = {c["id"] for c in caps}

    schema_violations: list[str] = []
    orphaned_cap_ids: set[str] = set()

    for entry in entries:
        if "_malformed" in entry:
            schema_violations.append(f"Malformed JSON: {entry['_malformed']!r}")
            continue

        # (a) Schema compliance — only required fields; cap_id is gated per helper.
        helper = entry.get("helper", "")
        expected_fields = set(_REQUIRED_SIGNAL_FIELDS)
        if helper not in _HELPERS_WITHOUT_CAP_ID:
            # gate_edit_time, gate_commit_time, gate_runtime emit cap_id
            expected_fields.add("cap_id")

        missing = expected_fields - set(entry.keys())
        if missing:
            schema_violations.append(
                f"Entry missing fields {sorted(missing)}: helper={helper!r} "
                f"task_id={entry.get('task_id')!r}"
            )

        # (b) cap_id resolution.
        cap_id = entry.get("cap_id")
        if cap_id is None or cap_id == "(none)":
            # Absent or sentinel — OK.
            continue
        if cap_id not in known_cap_ids and cap_id not in _PHASE4D_DEFERRED_CAP_IDS:
            orphaned_cap_ids.add(cap_id)

    assert not schema_violations, (
        "INV-HELP-NOT-GATE: ritual_signal log entries missing required fields:\n"
        + "\n".join(f"  - {v}" for v in schema_violations)
    )
    assert not orphaned_cap_ids, (
        "INV-HELP-NOT-GATE: ritual_signal log entries reference cap_ids not in "
        "capabilities.yaml and not in _PHASE4D_DEFERRED_CAP_IDS (new orphans):\n"
        + "\n".join(f"  - {cid}" for cid in sorted(orphaned_cap_ids))
    )


# ---------------------------------------------------------------------------
# Assertion 3: does_not_fit returns zero (structural check)
# ---------------------------------------------------------------------------

def test_does_not_fit_returns_zero():
    """ANTI_DRIFT_CHARTER §7 — assertion 3.

    Structural enforcement: no helper may have a forbidden_files field that
    would cause a cross-capability block (the structural mechanism that
    produces non-zero exits or BLOCK on out-of-scope tasks).

    Full subprocess invocation is a Phase 5 deliverable (requires runnable
    entry_points on enforcement gates). Phase 3 version verifies the
    structural precondition: no helper has a forbidden_files field at all
    (because forbidden_files is the Help-Inflation Ratchet step 3 — "new gate").
    If a helper gains forbidden_files, it must appear in scope_capabilities.
    """
    helpers = _load_helpers()
    violations = []

    for helper in helpers:
        name = helper["_name"]
        forbidden = helper.get("forbidden_files", [])
        if forbidden:
            scope_caps = set(helper.get("scope_capabilities", []))
            if not scope_caps:
                violations.append(
                    f"{name} has forbidden_files={forbidden!r} but no scope_capabilities declared. "
                    f"CHARTER §7: forbidden_files without scope_capabilities is unconstrained blocking."
                )

    assert not violations, (
        "INV-HELP-NOT-GATE: helpers have forbidden_files without scope_capabilities:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
