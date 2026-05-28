# Sentinel Mismatch Probe — 2026-05-25

**Status: CONFIRMED BUG — promote gate permanently fails on a complete refit**

---

## The Two Sides of the Contract

### WRITER side — `scripts/rebuild_calibration_pairs_v2.py`

`_rebuild_sentinel_payload()` at L438-474 emits the following JSON shape
when a full-scope rebuild finishes:

```python
{
    "status": status,           # "complete" when done
    "completed": status == "complete",  # True when done
    ...
    "scope": {
        "data_version": data_version_filter,   # None when run without --data-version
        ...
    },
}
```

`_rebuild_complete_sentinel_key()` at L244-272 encodes a `None`
`data_version_filter` as the literal string `"all"` via `_scope_part()`.

**Ground truth from `/private/tmp/ens_refit/full.db`:**

```
KEY:
  calibration_pairs_v2_rebuild_complete:metric=high:bin_source=canonical_v2:city=all:start=all:end=all:data_version=all:cycle=all:source_id=all:horizon=all:n_mc=10000

PAYLOAD (high sentinel, abbreviated to the fields under dispute):
  {
    "completed": true,
    "status": "complete",
    "scope": {
      "data_version": null,    ← stored as Python None → JSON null
      ...
    },
    ...
  }
```

Both sentinels (high and low) follow this exact pattern: `data_version=all`
in the key, `null` in `scope.data_version` in the payload.

---

### READER side — `scripts/promote_platt_models_v2.py`

`_sentinel_status_for_metrics()` at L165-240 applies three sequential
filters. The decisive filter is the **"full_complete"** check at L221-228:

```python
full_complete = [
    s
    for s in relevant
    if s["scope"].get("start") == "all"                          # key-scope field
    and s["scope"].get("end") == "all"                           # key-scope field
    and s["scope"].get("data_version") in wanted_dvs             # ← BUG #1
    and s["payload"].get("status") == "complete"                 # passes (payload has "status")
]
```

where `wanted_dvs` for metric `"high"` is:

```python
{"tigge_mx2t6_local_calendar_day_max_v1"}
```

**Note:** `s["scope"]` here is the *parsed key scope* produced by
`_parse_sentinel_key()` (L133-143), which returns the raw string values
from the colon-separated key. For the actual sentinel the key has
`data_version=all`, so `s["scope"].get("data_version")` == `"all"`.

---

## Bug Analysis

### Bug #1 — `data_version` scope mismatch (FATAL, gate never passes)

The `full_complete` filter at L226 requires:

```python
s["scope"].get("data_version") in wanted_dvs
```

For the actual sentinel: `s["scope"]["data_version"]` == `"all"`.
`wanted_dvs` == `{"tigge_mx2t6_local_calendar_day_max_v1"}`.
`"all" in {"tigge_mx2t6_local_calendar_day_max_v1"}` → **False**.

The `"all"` wildcard **is** accepted in the earlier `relevant` filter at
L197-202 (the `or s["scope"].get("data_version") == "all"` branch), so
the sentinel enters the candidate list — but then fails the `full_complete`
gate. The sentinel is therefore always `relevant` yet never `full_complete`.

Result: the `full_complete` list is empty. The code falls through to the
final `out[metric] = "missing"` at L239. The gate reports `"missing"` for
every metric, permanently refusing to promote.

### Bug #2 — `status` key check passes (not a bug here, only a confusion)

The `full_complete` filter does read `payload.get("status") == "complete"`.
The actual payload **does** contain `"status": "complete"` (confirmed from
DB), so this check passes. The `"completed": true` boolean field is a
redundant convenience field; `payload.get("status")` is the canonical
status key in the writer's `_rebuild_sentinel_payload()` at L453.
The task brief's characterisation of this as a `completed` vs `status`
mismatch is therefore **not the primary failure** — the status key aligns
correctly. The fatal mismatch is solely the `data_version` scope filter.

---

## Summary Table

| Dimension | Writer emits | Reader requires | Match? |
|-----------|-------------|-----------------|--------|
| `payload["status"]` | `"complete"` | `== "complete"` | **YES** |
| `payload["completed"]` | `true` | not checked here | n/a |
| key `data_version` part | `"all"` | in `wanted_dvs` OR `== "all"` (relevant) / in `wanted_dvs` only (full_complete) | **NO — full_complete filter excludes "all"** |
| key `start` part | `"all"` | `== "all"` | YES |
| key `end` part | `"all"` | `== "all"` | YES |

The gate can never be satisfied. A complete full-scope refit (`data_version=all`)
always lands in `_sentinel_status_for_metrics` as `"missing"`.

---

## Root Cause

`_sentinel_status_for_metrics()` was written to treat
`data_version=all` as a wildcard at the *candidate-inclusion* step but
then inadvertently tightened the *confirmation* step to require an exact
specific data_version. The comment block at L219-220 even names this
intent:

> "Then look for an exact-scope complete sentinel: data_version must
> match the wanted set (NOT just `all`), AND start/end == all."

That comment describes the intended behaviour for a *scoped* rebuild
(where the writer would have written a specific `data_version` value). But
the full-scope rebuild writes `data_version=all` regardless of the
data_version its pairs cover. So the gate's "NOT just `all`" restriction
can never be satisfied by the writer's natural output.

---

## Minimal Correct Fix

**Fix the READER** (`promote_platt_models_v2.py`), not the writer.

**Rationale:**

1. The writer's `data_version=all` sentinel is semantically correct:
   the full rebuild processed *all* data_versions. Changing it to emit
   `data_version=tigge_mx2t6_local_calendar_day_max_v1` would be a lie —
   the rebuild itself is not scoped to that version.
2. The writer's sentinel schema is consumed by multiple callers
   (`assert_rebuild_complete_sentinel`, `assert_no_overlapping_incomplete_rebuild_sentinel`,
   `refit_platt_v2.py`). Changing the writer's output format would require
   auditing all consumers.
3. The reader is the narrower surface with one semantic correction needed.

**The fix** at `_sentinel_status_for_metrics()` L221-228: accept
`data_version == "all"` (wildcard) in the `full_complete` filter, treating
it as covering all requested data_versions. Change:

```python
# BEFORE (buggy)
full_complete = [
    s
    for s in relevant
    if s["scope"].get("start") == "all"
    and s["scope"].get("end") == "all"
    and s["scope"].get("data_version") in wanted_dvs          # ← never True for full-scope refits
    and s["payload"].get("status") == "complete"
]
```

```python
# AFTER (correct)
full_complete = [
    s
    for s in relevant
    if s["scope"].get("start") == "all"
    and s["scope"].get("end") == "all"
    and (
        s["scope"].get("data_version") in wanted_dvs           # exact match (scoped refit)
        or s["scope"].get("data_version") == "all"             # wildcard match (full refit)
    )
    and s["payload"].get("status") == "complete"
]
```

The comment at L219-220 should also be updated to remove the
"NOT just `all`" language so it no longer contradicts the intended
behaviour.

**Preserving the in_progress guard (L211-218):** The existing guard for
`in_progress` sentinels scoped to a specific `data_version` (which must
not be masked by an older `data_version=all` complete sentinel) is
unaffected: it checks `s["scope"].get("data_version") in wanted_dvs`, so
an `in_progress` sentinel with `data_version=all` would not trigger it —
that is the correct behaviour because a global `in_progress` is already
represented in the final fallback check at L233-237.

---

## Files Involved

| File | Role |
|------|------|
| `scripts/rebuild_calibration_pairs_v2.py` L438-474 | Writer: emits sentinel payload with `status`, `completed`, `scope.data_version=null` |
| `scripts/rebuild_calibration_pairs_v2.py` L244-272 | Writer: encodes `null` data_version as `"all"` in the key |
| `scripts/promote_platt_models_v2.py` L221-228 | Reader: `full_complete` filter — the bug is here |
| `scripts/promote_platt_models_v2.py` L196-202 | Reader: `relevant` filter — correctly accepts `"all"` |
| `/private/tmp/ens_refit/full.db` zeus_meta | Ground truth: two sentinels, both `data_version=all` in key, `null` in payload scope |
