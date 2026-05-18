# Run #16 Track F — Position Lifecycle Correctness Audit

- **Date**: 2026-05-17
- **Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `d9094b1be8` (origin tip, pre-commit)
- **Worktree**: `.claude/worktrees/zeus-deep-alignment-audit-skill`
- **Mode**: READ-ONLY (DB queries + code grep only; no production code mutated)
- **DB**: `state/zeus_trades.db` (646 MB, mtime 2026-05-17 19:00 UTC)

## §0 — Mandate divergence note

`git pull --ff-only` was rejected (worktree diverged: 1 local commit `4b6c83e9cd` ahead, 12 origin commits behind). Resolved by `git rebase origin/fix/wave-2-lineage-and-k1-cleanup-2026-05-17` to bring worktree onto origin tip `d9094b1be8` before audit work. Local fix `4b6c83e9cd` was rebased cleanly (zero conflicts). No `--force` push performed; audit commit will be `--ff` from new tip.

The mandate-suggested probe `event_type='phase_transition'` does not match the schema — `position_events.event_type` has 19 distinct values (POSITION_OPEN_INTENT, ENTRY/EXIT_*, CHAIN_*, MONITOR_REFRESHED, SETTLED, ADMIN_VOIDED, MANUAL_OVERRIDE_APPLIED, DAY0_WINDOW_ENTERED) and **no dedicated `phase_transition` type**. Phase changes are piggybacked on lifecycle-action events via the `phase_before` / `phase_after` columns. This itself is **F112** below.

## §1 — Schema (the law)

`position_current.phase` CHECK constraint enumerates 9 legal phases:

```
pending_entry · active · day0_window · pending_exit · economically_closed
· settled · voided · quarantined · admin_closed
```

`position_events.{phase_before, phase_after}` CHECK enumerates the same 9 (nullable). Triggers: append-only (no UPDATE/DELETE), `env` required. UNIQUE `(position_id, sequence_no)` and UNIQUE `idempotency_key`.

`position_lots.state` uses a **DIFFERENT vocabulary** (7 values): OPTIMISTIC_EXPOSURE · CONFIRMED_EXPOSURE · EXIT_PENDING · ECONOMICALLY_CLOSED_OPTIMISTIC · ECONOMICALLY_CLOSED_CONFIRMED · SETTLED · QUARANTINED. No formal mapping between `position_current.phase` (9 values) and `position_lots.state` (7 values) exists in code or schema; verifying lot↔position consistency requires a translation table that is not codified. **Flagged below as F113.**

## §2 — Actual transitions inventory (last 7 days)

### §2.1 Current phase distribution (live snapshot)

| phase | n | earliest_updated_at | latest_updated_at |
|---|---|---|---|
| voided | 63 | 2026-05-15T18:48 | 2026-05-17T18:55 |
| pending_exit | 5 | 2026-05-17T22:13 | 2026-05-17T23:59 |
| economically_closed | 5 | 2026-05-17T19:50 | 2026-05-17T21:10 |
| active | 2 | 2026-05-17T23:58 | 2026-05-17T23:58 |
| day0_window | 1 | 2026-05-17T23:58 | 2026-05-17T23:58 |

Totals: **76 positions** in the last 7d (matches 76 POSITION_OPEN_INTENT events). Zero positions ever reached `settled`, `quarantined`, or `admin_closed`.

### §2.2 Distinct (phase_before → phase_after, event_type) transitions, ≥ 2026-05-10

| phase_before | phase_after | event_type | n |
|---|---|---|---|
| NULL | pending_entry | POSITION_OPEN_INTENT | 76 |
| pending_entry | pending_entry | ENTRY_ORDER_POSTED | 76 |
| pending_entry | voided | ENTRY_ORDER_VOIDED | 63 |
| active | pending_exit | EXIT_ORDER_REJECTED | 29 |
| pending_entry | active | ENTRY_ORDER_FILLED | 13 |
| active | active | CHAIN_SIZE_CORRECTED | 9 |
| pending_exit | economically_closed | EXIT_ORDER_FILLED | 5 |
| pending_entry | active | CHAIN_SYNCED | 3 |
| active | day0_window | DAY0_WINDOW_ENTERED | 1 |
| active | pending_exit | EXIT_ORDER_POSTED | 1 |
| pending_exit | pending_exit | EXIT_ORDER_POSTED | 1 |

**16 distinct positions transitioned into `active` (13 via FILLED + 3 via CHAIN_SYNCED).** Yet there are **29 `active→pending_exit` rows** across only 2 positions: 12 each on London `0a0e3b72-46e` + `7557a029-4ad`, plus 4 from positions that subsequently reached `economically_closed`. See §4.1 / F108.

## §3 — Code transitions inventory

**Zero raw `UPDATE position_current SET phase = ...` statements** survive in `src/` (excluding tests). All writes flow through `src/engine/lifecycle_events.py::build_position_current_projection` + `src/state/db.py::append_many_and_project`, which derive `position_current.phase` from the event's `phase_after`.

Phase-deriving call sites:
- `src/engine/lifecycle_events.py:200,244,255,277,427` — canonical builders (entry side, day0 entry).
- `src/execution/exit_lifecycle.py:467` — `phase_after = fold_lifecycle_phase(phase_before, "pending_exit").value` (exit side, including rejections — see F108).
- `src/execution/command_recovery.py:823` — recovery-side mirror of the same fold (also EXIT_ORDER_REJECTED path).
- `src/state/projection.py:73` — reader-side reconstruction.

There is no `set_phase` / `transition_phase` helper enforcing the legal DAG; the DAG is enforced only by the CHECK constraint (which allows any-to-any) plus per-callsite logic. No central transition validator.

## §4 — Orphan + skip findings

### §4.1 Orphans

| Cohort | Threshold | Found |
|---|---|---|
| `pending_entry > 24h` | julianday(now)-updated_at > 1.0 | **0** |
| `pending_exit > 12h` | julianday(now)-updated_at > 0.5 | **0** (5 current; max age ~2 h) |
| `day0_window past target_date` | target_date < 2026-05-17 | **0** (1 current = Karachi c30f28a5, target=today) |
| `economically_closed` not settled | any age | 5 (4 with target=2026-05-19, 1 with target=2026-05-18 — all pre-settlement, expected) |

No classical orphans. **BUT** see §4.3 / F111 for a near-orphan class (positions stuck in `pending_exit` accruing reject retries) that the 12 h window misses because retries refresh `updated_at`.

### §4.2 Skip-transitions (schema-allowed but suspicious)

| Path | Observed | Verdict |
|---|---|---|
| `pending_entry → economically_closed` | 0 | clean |
| `pending_entry → settled` | 0 | clean |
| `active → economically_closed` directly (skipping `pending_exit`) | **0 in events**, but **2 positions reached `economically_closed` without any `active→pending_exit` event** (Singapore 8f02dc01, Wuhan 7211cc19) | **F109** (silent state writes) |
| `economically_closed → *` reverse | 0 | clean |
| `settled → *` reverse | 0 | n/a (no settled rows) |
| `pending_exit → active` reverse | 0 events, but implied by 12 repeats of `active→pending_exit` for same position | **F108** (false log of phase_before) |

### §4.3 Karachi 5/17 — `c30f28a5-d4e` event trace

```
sq  occurred_at          event_type            phase_before    phase_after
1   2026-05-16T00:32:49  POSITION_OPEN_INTENT  NULL            pending_entry
2   2026-05-16T00:32:49  ENTRY_ORDER_POSTED    pending_entry   pending_entry
3   unknown_entered_at   CHAIN_SYNCED          pending_entry   active          ← F110
4   2026-05-16T06:40:21  ENTRY_ORDER_FILLED    pending_entry   active
5   2026-05-16T19:00:01  DAY0_WINDOW_ENTERED   active          day0_window
```

**Verdict**: structurally canonical (OPEN_INTENT → ENTRY_POSTED → CHAIN_SYNCED/FILLED → DAY0_WINDOW_ENTERED). Single defect: sq=3 `occurred_at='unknown_entered_at'` literal sentinel string instead of an ISO-8601 timestamp (`TEXT` schema accepts it, time-window queries break). Phase progression matches the legal DAG.

### §4.4 — 5/19 sibling traces

| position_id (short) | city | current phase | trace verdict |
|---|---|---|---|
| `c30f28a5-d4e` | Karachi (5/17) | day0_window | canonical (F110 only) |
| `8f02dc01-b6b` | Singapore | economically_closed | **F109** — `EXIT_ORDER_FILLED` at sq=5 claims `phase_before='pending_exit'` but no prior event recorded `active→pending_exit` |
| `7211cc19-e02` | Wuhan | economically_closed | **F109** — same shape as Singapore |
| `1bbb697b-161` | Munich | economically_closed | (not in mandate; observed same exit-event shape; F109 candidate) |
| `3a6f0728-c50` | London | economically_closed | (not in mandate; same shape; F109 candidate) |
| `43822a1f-e9e` | Miami (5/18) | economically_closed | (not in mandate; same shape; F109 candidate) |
| `e914a28a-420` | Jakarta | active | canonical (OPEN_INTENT→POSTED→FILLED), no defect |
| `bf0a16f5-f95` | Manila | active | canonical + **F110** at sq=3 (CHAIN_SYNCED `occurred_at='unknown_entered_at'`) |
| `0a0e3b72-46e` | London | pending_exit | **F108 + F111** — 13 retries, all log false `pb=active` |
| `7557a029-4ad` | London | pending_exit | **F108 + F111** — 13 retries, all log false `pb=active` |

## §5 — New findings (F108–F113)

### F108 — `EXIT_ORDER_REJECTED` falsely logs `phase_before='active'` on every retry

- **Severity**: SEV-2 (event-replay correctness; not directly financial)
- **Status**: NEW (Run #16 F)
- **Owner**: `src/execution/exit_lifecycle.py:460-463` (also mirrored in `src/execution/command_recovery.py:820-823`)
- **Evidence**: `0a0e3b72-46e` and `7557a029-4ad` each have 12 rows where `event_type='EXIT_ORDER_REJECTED'` records `phase_before='active', phase_after='pending_exit'` even though `position_current.phase` was already `pending_exit` from sequence_no=4 onward.
- **Root cause**: `phase_before = phase_for_runtime_position(state=getattr(position, "pre_exit_state", "") or "holding").value`. `pre_exit_state` defaults to `"holding"` → maps to `active`. The function never reads the actual persisted current phase; it always reconstructs `active` regardless of how many retries have already happened.
- **Effect**: Event-sourced replay (`src/state/projection.py:73`) would reconstruct a fictitious oscillation `active→pending_exit→active→pending_exit→...` 13 times for these positions. Any downstream consumer that counts "transitions into pending_exit" over-reports by N (e.g. SEV monitoring of exit-attempt distinct counts).
- **Cardinality**: 26 known false-transition rows in the last 7 days (12 × 2 positions + 1 short-tail entry on each). Will accrue +2 per retry cycle (~every 7 min) until backoff terminates.
- **Recommended fix (spec only — READ-ONLY pass)**: replace `getattr(position, "pre_exit_state", "") or "holding"` with a `SELECT phase FROM position_current WHERE position_id = ?` read; if read returns `pending_exit` and `event_type='EXIT_ORDER_REJECTED'`, set `phase_before='pending_exit'` (and `phase_after='pending_exit'` — same-phase, like the existing `EXIT_ORDER_POSTED` self-loop at sq=5).

### F109 — Silent `active → pending_exit` transition (event missing) for positions that reach `economically_closed`

- **Severity**: SEV-2 (gap in append-only event log; audit trail incomplete)
- **Status**: NEW (Run #16 F)
- **Owner**: `src/execution/exit_lifecycle.py` (EXIT_INTENT / EXIT_ORDER_POSTED emitter) + `src/state/projection.py`
- **Evidence**: All 5 currently `economically_closed` positions (Singapore 8f02dc01, Wuhan 7211cc19, Munich 1bbb697b, London 3a6f0728, Miami 43822a1f) have an `EXIT_ORDER_FILLED` row with `phase_before='pending_exit'`, but **no prior `EXIT_INTENT` / `EXIT_ORDER_POSTED` / `EXIT_ORDER_REJECTED` row** that would record `active→pending_exit`. Compare Singapore (sq=4 CHAIN_SIZE_CORRECTED active→active → sq=5 EXIT_ORDER_FILLED pending_exit→economically_closed): a transition exists in `phase_before` field but no event row exists for it.
- **Root cause hypothesis (unverified)**: the "happy path" exit (immediate fill, no rejection, no posted/intent) takes a code branch that updates `position_current.phase = pending_exit` and writes `EXIT_ORDER_FILLED` in one shot, without emitting the intermediate `EXIT_INTENT` or `EXIT_ORDER_POSTED` event. **0 `EXIT_INTENT` events exist in the 7d window** (Q3 above), so this is the dominant exit path.
- **Effect**: Event replay cannot reconstruct the moment the exit decision was made. Lineage from `decision_log` / `selection_hypothesis_fact` to the exit fill is broken at the exit-intent layer (relates to F7 lineage class).
- **Cardinality**: 5 of 5 economically_closed positions (100%).

### F110 — `position_events.occurred_at` carries literal `"unknown_entered_at"` string

- **Severity**: SEV-3 (data hygiene; breaks `julianday()` / time-window math but column type is TEXT so no SQL error)
- **Status**: NEW (Run #16 F)
- **Owner**: emitter of `CHAIN_SYNCED` events (`src/state/chain_reconciliation.py` based on `source_module`)
- **Evidence**: Karachi c30f28a5 sq=3 and Manila bf0a16f5 sq=3 both carry `occurred_at='unknown_entered_at'`. Other CHAIN_SYNCED rows in 7d either succeed or share this same defect (3 total CHAIN_SYNCED events; appears in at least 2/3).
- **Effect**: Any downstream query using `MIN(occurred_at)` / `julianday(occurred_at)` on filtered CHAIN_SYNCED rows produces `NULL` or garbage. Reflected in Q2 first_seen/last_seen output: one row's `last_seen` is literally the string `unknown_entered_at` (sort-order accident).
- **Recommended fix (spec)**: at emit site, fall back to `_utcnow().isoformat()` if the source provenance timestamp is unresolvable, and log a SEV-3 warning. Optionally add a CHECK constraint requiring `occurred_at` to start with `20` (cheap ISO-8601 sentinel).

### F111 — London positions STUCK in `pending_exit` with non-terminating reject retries (live-money HOT)

- **Severity**: **SEV-1 HOT** (live-money positions targeting 5/19 cannot exit)
- **Status**: NEW (Run #16 F)
- **Owner**: `src/execution/exit_lifecycle.py:595-935` (8 distinct EXIT_ORDER_REJECTED emit sites) + the backoff/quarantine controller (absent from this 12-retry trace)
- **Evidence**:
  - `0a0e3b72-46e` (London, opening_inertia, 5/19): 13 EXIT_ORDER_REJECTED between 22:13:38 and 23:59:37 (1h 46min, 12 retries; cadence ≈ 7–8 min, monotone, no backoff acceleration visible).
  - `7557a029-4ad` (London, opening_inertia, 5/19): 12 EXIT_ORDER_REJECTED between 22:27:33 and 23:59:39 (1h 32min; same cadence).
  - No `CHAIN_QUARANTINED` / `ADMIN_VOIDED` / `EXIT_BACKOFF_EXHAUSTED` event appended to either position despite `exit_backoff_exhausted` being a documented reason code in `src/state/db.py:7969`.
- **Effect**: Two live-money London 5/19 positions accruing rejection cost (~1¢ per attempt assumed) every 7 min with no terminating condition. If reject reason is structural (price/size gate, insufficient balance, market-side block), retries will continue indefinitely until 5/19 settlement forces resolution — by which time the position is unhedged into the settlement window.
- **Adjacency to F108**: F108 explains why each retry mis-logs `pb=active`; F111 is the underlying live-money issue (the retries themselves should not be open-ended).
- **Recommended fix (spec, NOT applied)**:
  1. Query `payload_json` for the 12 EXIT_ORDER_REJECTED rows to identify the actual reject reason (out of scope for this READ-ONLY audit — `payload_json` not selected in this run).
  2. Confirm whether `exit_lifecycle.py` has a retry-count terminator wired to the `EXIT_BACKOFF_EXHAUSTED` event_type (referenced in `src/state/db.py:8028` but not observed in the 7d event stream).
  3. If absent, gate retries by `exit_retry_count >= MAX_RETRIES` → emit `EXIT_BACKOFF_EXHAUSTED` → transition to `quarantined`.

### F112 — `position_events.event_type` enum lacks a dedicated phase-transition record

- **Severity**: SEV-3 (schema/contract; documentation as code)
- **Status**: NEW (Run #16 F)
- **Owner**: `state/zeus_trades.db` schema (`position_events.event_type` CHECK) + `src/state/db.py`
- **Evidence**: All 19 enumerated event types are lifecycle actions (POSITION_OPEN_INTENT, ENTRY/EXIT_*, CHAIN_*, MONITOR_REFRESHED, SETTLED, ADMIN_VOIDED, MANUAL_OVERRIDE_APPLIED, DAY0_WINDOW_ENTERED). None is a pure "phase transitioned without external trigger" event. Consequence: F109-class silent phase writes have no canonical event to record them; F108-class false transitions get attached to whatever lifecycle event happens to fire.
- **Effect**: Audit trail is **lifecycle-action-shaped** rather than **state-machine-shaped**. Reconstruction of `position_current.phase` from `position_events` REQUIRES every state-changing code path to remember to attach the phase delta to an action event — a per-callsite obligation rather than a centralized invariant.
- **Recommended fix (spec)**: add `PHASE_RECONCILED` to the event_type enum; require it whenever `phase_before != phase_after` and no other action-event will fire in the same transaction.

### F113 — No codified mapping between `position_current.phase` (9 values) and `position_lots.state` (7 values)

- **Severity**: SEV-3 (latent — would surface on lot↔position consistency audit)
- **Status**: NEW (Run #16 F)
- **Owner**: `src/state/position_lots.py` (or wherever lot-state transitions live) + schema docs
- **Evidence**: schema-grep above. Vocabularies differ; e.g. `position_current.phase=economically_closed` could correspond to `position_lots.state IN (ECONOMICALLY_CLOSED_OPTIMISTIC, ECONOMICALLY_CLOSED_CONFIRMED)`. No source-code constant or table encodes this mapping.
- **Effect**: A future consistency audit (`SELECT WHERE position_current.phase ≠ map(position_lots.state)`) cannot be written without first reverse-engineering the mapping from runtime behavior. F20 antibody (live-drift class) touched lot invariants but did not codify this mapping.

## §6 — Findings table (delta)

| F# | Title | Sev | Status | Owner | First seen | Last verified |
|---|---|---|---|---|---|---|
| F108 | `EXIT_ORDER_REJECTED` falsely logs `phase_before='active'` on every retry | SEV-2 | NEW (Run #16 F) | `src/execution/exit_lifecycle.py:460` + `src/execution/command_recovery.py:820` | Run #16 F | Run #16 F |
| F109 | Silent `active→pending_exit` transition (no event) on happy-path exits | SEV-2 | NEW (Run #16 F) | `src/execution/exit_lifecycle.py` (EXIT_INTENT/POSTED emitter) | Run #16 F | Run #16 F |
| F110 | `occurred_at='unknown_entered_at'` literal string on CHAIN_SYNCED events | SEV-3 | NEW (Run #16 F) | `src/state/chain_reconciliation.py` (CHAIN_SYNCED emit) | Run #16 F | Run #16 F |
| F111 | **London 0a0e3b72 + 7557a029 stuck in `pending_exit` with non-terminating retries (live 5/19)** | **SEV-1 HOT** | NEW (Run #16 F) | `src/execution/exit_lifecycle.py:595-935` + backoff controller | Run #16 F | Run #16 F |
| F112 | `event_type` enum lacks `PHASE_RECONCILED` record; transitions piggyback on actions | SEV-3 | NEW (Run #16 F) | `position_events.event_type` CHECK | Run #16 F | Run #16 F |
| F113 | No codified `position_current.phase ↔ position_lots.state` mapping | SEV-3 | NEW (Run #16 F) | `src/state/position_lots.py` | Run #16 F | Run #16 F |

## §7 — Karachi 5/17 impact summary

`c30f28a5-d4e` itself is on a clean path (F110 hygiene only). The Karachi-impacting class of findings from this run is **F111 (London 5/19 exit lock)**: not Karachi, but the same opening_inertia strategy + the same exit_lifecycle code path that would govern Karachi's own exit on 5/17 evening when DAY0 closes. **If c30f28a5's exit produces the same reject pattern as the London twins, Karachi will likewise lock in `pending_exit` with no terminator.** Recommend live-trading operator manually monitor c30f28a5's exit event stream in the next 6 hours and force-quarantine if reject count ≥ 3.

## §8 — Probe-output antibody

All queries written to `/tmp/run16_*.txt` with explicit `==MARKER==` blocks and `> file 2>&1; cat file` pattern (against intermittent terminal output interleaving observed in this session — see existing antibody catalog `vscode_tooling_antibodies.md`). Verification of file content done via `read_file` against `/tmp/` paths, not direct terminal output.

