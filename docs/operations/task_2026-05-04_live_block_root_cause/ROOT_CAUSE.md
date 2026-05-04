# Live-Block 16-Day Root Cause — 2026-05-04

**Status:** root cause confirmed via live introspection of running daemon.
**Daemon under investigation:** PID 26019, started Sun May 3 20:46:53 CDT 2026, boot commit `11bbc8b2`.
**Symptom (user-visible):** "live 仍然无事发生" — every cycle prints `0 monitors, 0 exits, 0 candidates, 0 trades` despite GREEN risk and on-chain bankroll $199.40 flowing correctly.

---

## TL;DR

The daemon is locked by **5 stacked structural failures**, not one bug. The `auto_pause_failclosed.tombstone` file (content: `heartbeat_cancel_suspected`) is the immediate sticky lock, but it is downstream of (a) a logger missing `exc_info=True` that has hidden every ValueError traceback for 16 days, (b) a tombstone file that serves two unrelated failure modes via the same byte-existence check, and (c) an in-memory pause flag that does not honor DB row expiry.

The actual `raise ValueError` file:line for the 5-03+ regime is **still unknown** because the inner catch (`cycle_runtime.py:2988`) silences the traceback before the outer catch (which has `exc_info=True`) can see it. Capturing that traceback is gated on fixing the silent logger first.

---

## Empirical Evidence (ground truth, captured 2026-05-04 02:50–03:05 UTC)

### E1. DB pause history shows 16-day loop

```sql
SELECT issued_at, effective_until, reason
FROM control_overrides_history
WHERE override_id='control_plane:global:entries_paused'
ORDER BY issued_at DESC LIMIT 5;
```
| issued_at | effective_until | reason |
|---|---|---|
| 2026-05-04T02:04:09 | 2026-05-04T02:19:09 | auto_pause:ValueError |
| 2026-05-04T00:59:45 | 2026-05-04T01:14:45 | auto_pause:ValueError |
| 2026-05-02T21:19:51 | 2026-05-02T21:34:51 | auto_pause:ValueError |
| 2026-05-02T18:37:39 | 2026-05-02T18:52:39 | auto_pause:ValueError |
| 2026-05-01T20:14:56 | (NULL) | auto_pause:ValueError |

The `effective_until=NULL` rows predate the 5-01 hardening commit `aa6c6f1a`; rows after carry the 15-min window.

Code comment confirming long-running issue: `src/engine/cycle_runner.py:769-770`:
> "the recurring ValueError loop running since 2026-04-18"

### E2. stderr captures the early-period ERRORs (which were enum bugs, now fixed)

```
$ grep -E '\[src\.engine\.cycle_runner\] ERROR' logs/zeus-live.err
  | sed 's/.*ERROR: //' | sort | uniq -c
58 'CALIBRATION_IMMATURE' is not a valid RejectionStage   (5-01 only)
54 'ORACLE_EVIDENCE_UNAVAILABLE' is not a valid RejectionStage   (5-02 only)
0  ValueError                                              (5-03+)
0  Traceback                                               (5-03+)
0  Entry path raised                                       (5-03+)
```
Both enum members have since been added to `src/contracts/semantic_types.py:49` (`RejectionStage` class). The 5-03+ ValueError is **a different exception** that produces zero stderr signal.

### E3. Live introspection — refresh_control_state real behavior

Ran from daemon's venv at 03:01 UTC:
```
DB query alone           → entries_paused: False  (effective_until filter passes nothing)
Tombstone exists          → True, content "heartbeat_cancel_suspected"
After refresh_control_state() →
  _control_state["entries_paused"]      = True
  _control_state["entries_pause_source"] = None
  _control_state["entries_pause_reason"] = None
is_entries_paused() → True
```

Conclusion: **DB self-heal works**, but `control_plane.py:375-376` unconditionally overrides paused→True if tombstone file exists. This is the immediate lock.

### E4. Tombstone is NOT the cause of the 21:04 pause-row write

- Tombstone birth time = `May 3 21:31:21` (verified via `stat -f %SB`)
- 21:04 row written **27 minutes earlier** than tombstone existed
- `HeartbeatSupervisor._write_failclosed_tombstone` uses atomic `tmp.replace(path)` so birth time = last write
- Implication: at 21:00 / 21:02 cycles, paused=True came from **DB row 00:59:45 still being projected by view** (because view picks max history_id regardless of effective_until). Inspection of `control_overrides` view definition:
  ```sql
  CREATE VIEW control_overrides AS
  SELECT ... FROM control_overrides_history h1
  WHERE history_id = (SELECT MAX(history_id) FROM control_overrides_history h2
                      WHERE h2.override_id = h1.override_id)
  ```
- `query_control_override_state` (db.py:5478-5488) selects from this view with WHERE filter on `effective_until`. The view returns 1 stale row per override_id. The WHERE clause filters it out → query returns False. **Confirmed via direct SQL re-run at 03:01 UTC: 0 rows.**

So the fail-closed bias has two independent layers in series:
- **Pre-tombstone (21:00 → 21:31):** paused=True came from somewhere besides DB+tombstone — most likely an early-cycle code path before refresh_control_state finished, or `process_commands` early-exit. *Not fully traced.* Did not block the user-visible problem.
- **Post-tombstone (21:31 → now):** tombstone file existence forces paused=True via control_plane.py:375-376.

### E5. The exc_info gap

`src/engine/cycle_runtime.py:2988`:
```python
except Exception as e:
    deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e)
```
Only `str(e)` is captured. Python's `Enum("INVALID")` produces `'INVALID' is not a valid <EnumName>` which IS readable in str-form (E2 worked because the message itself named the bad enum value). But for the 5-03+ ValueError, str(e) clearly does not encode the file:line — meaning either the exception text isn't self-describing, or the exception isn't reaching this catch at all. Both possibilities require a working traceback to disambiguate.

The 5-01 hardening commit `aa6c6f1a` added `exc_info=True` to `cycle_runner.py:771-786` (outer catch) but did **not** touch this inner catch.

---

## Bug Inventory (10 items, A–F categories)

| ID | Category | File:Line | Description | Severity |
|---|---|---|---|---|
| **A1** | Observability | `cycle_runtime.py:2988` | `logger.error(... e)` no `exc_info=True` | **Critical** — root of 16-day blind spot |
| **A2** | Observability | TBD audit | Other except-Exception logger.error sites in src/engine/ + src/control/ may have same gap | High |
| **B1** | Tombstone design | `src/control/AGENTS.md:18` + `architecture/digest_profiles.py:1336,1382` + `control_plane.py:268` + `heartbeat_supervisor.py:213-217` | Single tombstone file shared by HeartbeatSupervisor and pause_entries. Constraint enforced by `src/control/AGENTS.md:18` ("Must reuse `auto_pause_failclosed.tombstone`; no second tombstone source") AND `architecture/digest_profiles.py:1382` ("Do not create a second tombstone or a new canonical state table in Z3"). **The constraint is real and architectural — split-file is forbidden.** | **Critical** — SF2, masks ValueError loop |
| **B2** | Tombstone design | `heartbeat_supervisor.py:208` | `_tombstone_written=True` cached, no recovery → no self-clear path | High |
| **B3** | Tombstone design | `control_plane.py:375-376` | `if tombstone_exists: entries_paused=True` unconditional, bypasses DB effective_until | High |
| **C1** | In-memory state | `control_plane.py:117-118` | `is_entries_paused()` reads stale `_control_state`; only refreshed at boot + cycle entry | Medium |
| **C2** | In-memory state | `control_plane.py:234-237` | DB row gets effective_until auto-15min, but in-memory flag has no expiry → split truth source | Medium |
| **D1** | Unknown ValueError | TBD (gated on A1 fix) | 5-03+ ValueError raise file:line unknown | **Critical** — actual production bug |
| **E1** | Historical debt | `control_overrides_history` table | 16+ rows with `effective_until=NULL` from pre-aa6c6f1a era | Low (no current functional impact, view picks latest) |
| **F1** | Already fixed (recorded) | `semantic_types.py:49` (RejectionStage enum) | 5-01/5-02 batches: missing CALIBRATION_IMMATURE / ORACLE_EVIDENCE_UNAVAILABLE | — closed |

---

## The Five Structural Failures (per Fitz K-1 lens)

**SF1 — Logger semantics conflate "what" with "where".**
`logger.error(... str(e))` records the exception class and its message but discards the traceback frame. Any catch site that expects to keep diagnosing must use `exc_info=True`. The 5-01 hardening got this right at the outer catch (cycle_runner.py) but the codebase has multiple inner catches (cycle_runtime.py:2988, others) that quietly preserved the pre-fix shape. **Antibody must enforce this at all except-Exception logger.error sites in entry/discovery paths**, not just the one cycle_runner.py:771 location.

**SF2 — One tombstone file, two failure modes.**
A single boolean `os.path.exists(tombstone)` check resolves to "entries paused?" — but two completely independent producers write that file (HeartbeatSupervisor for venue-API DNS/connection failures; pause_entries for application-level ValueError loops). When an operator sees `heartbeat_cancel_suspected` content they reason "heartbeat issue, fix is pending" — the ValueError loop in the other dimension stays invisible. `src/control/AGENTS.md:18` enforces the single-file constraint ("Must reuse `auto_pause_failclosed.tombstone`; no second tombstone source"); `architecture/digest_profiles.py:1382` reinforces ("Do not create a second tombstone"). **The fix cannot split into two files (architectural constraint forbids it). The fix is to make the single file owner-aware via JSON payload** — `{"owner": "heartbeat"|"valueerror", "reason": "...", "effective_until": "..."}` — so refresh_control_state can discriminate by parsing rather than file existence.

**SF3 — Tombstone is sticky without recovery.**
`HeartbeatSupervisor._tombstone_written` is set once and never reset. Even when venue heartbeat returns to HEALTHY (verified: 1+ hour of HTTP 200 to clob.polymarket.com/heartbeats), the tombstone persists. The only documented clear path is `_clear_auto_pause_tombstone()` invoked by an operator `resume` command. There is no automatic "venue is healthy for N consecutive cycles → clear" path. **Antibody: health-recovery hook must clear the heartbeat tombstone after N healthy ticks.**

**SF4 — DB has expiry, in-memory does not.**
`pause_entries` writes `effective_until = now + 15min` to DB and `_control_state["entries_paused"] = True` to memory. The DB row self-expires; the memory flag does not. `is_entries_paused()` reads memory. Memory only refreshes when `process_commands()` runs at cycle entry, and even then `refresh_control_state()` overrides with tombstone if the file exists. **Two truth sources, only one with expiry. Either delete the in-memory flag and always query DB, or align expiry semantics across both.**

**SF5 — VIEW projection masks history.**
`control_overrides` view returns the latest row per `override_id` regardless of effective_until. `query_control_override_state` patches this with a WHERE filter, but any other consumer that SELECTs the view directly will see expired rows as live. This is not currently load-bearing (only `query_control_override_state` consumes the view today) but it is a foot-gun that will bite future code that joins this view assuming "active rows only". Lower priority but worth noting.

---

## Why this stayed hidden 16 days (the meta-question)

The pre-aa6c6f1a logger had no `exc_info`, so 4-18 → 5-01 ValueError tracebacks went straight to /dev/null. The 5-01 hardening added `exc_info=True` only at the outer cycle_runner catch — but by that point the daemon was already locked by the tombstone (heartbeat had failed during the 5-01 deploy window), so cycles never reached the outer catch. By 5-03 the enum bugs producing the few visible str(e) ERRORs were patched; the next ValueError fell into cycle_runtime.py:2988's silent catch, which was never updated. Operators saw "heartbeat tombstone, will recover when heartbeat does" — which never untriggered itself. Each layer's failure was small; their composition was 16 days of dead live.

---

## What we still don't know

- **D1 — the actual 5-03+ ValueError raise file:line.** Strongly suspected to be in `evaluator.py` (candidate raises: `:714` _normalize_temperature_metric, `:1414` entry provenance context required, `:3478` ENS snapshot missing fetch_time, plus several `:33xx-:38xx` p_raw_topology assertions). Cannot be confirmed without first fixing A1 to surface a traceback. The fix plan's Phase 0 produces this.
- **A2 scope.** How many other `logger.error(... str(e))` sites in src/engine/ + src/control/ have the same gap. Quick grep audit at start of Phase 0.
