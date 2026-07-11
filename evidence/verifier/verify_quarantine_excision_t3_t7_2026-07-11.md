# verifier proof-of-done for T3 (RiskGuard consistency_lock) + T7 (semantic contamination cleanup)
HEAD (main repo): 6cc2d669c (branch p2-pending-exit-restart-redecision)
T3 worktree HEAD: ab4ef5198 (.claude/worktrees/agent-a94804f30a13a5dd3, branch claude/agent-a94804f30a13a5dd3)
T7 worktree HEAD: 1ab83355c (.claude/worktrees/agent-a794538c86262be51, branch claude/agent-a794538c86262be51)
Verifier: verifier
Date: 2026-07-11

## Claim
T3: consistency_lock is now three-valued (pass/degraded/mismatched) with proven-duplicate
classification, wired into RiskLevel.DATA_DEGRADED via _tick_once; quarantine vocabulary in
src/riskguard/ is zero except two documented residuals (T5 phase literal, Brier ORANGE
localization). T7: fixed lifecycle-terminal doc drift in 4 law surfaces, renamed
artifact_authority_status.yaml QUARANTINE->SUPERSEDED_UNREVIEWED with all consumers updated,
removed the dead occurred_at='QUARANTINE' sentinel from kernel SQL/db.py/ledger.py while
preserving the historical migration and re-pinning the schema fingerprint. 138 targeted tests
claimed passing, test_db.py 20 failures claimed pre-existing.

## Verdict
T3: VERIFIED — MERGE-READY yes (one out-of-scope observation, non-blocking).
T7: PARTIALLY REFUTED on claim 4 — MERGE-READY conditional on a 1-line fix in two files
(doc-only defect, no code/test impact).

---

## PACKET T3

### CHECK 1 — consistency_lock three-valued wiring into persisted risk level [STATUS: VERIFIED]
Full diff read (`git diff 66befaf38..ab4ef5198 -- src/riskguard/riskguard.py`):
- `_riskguard_unloadable_row_is_excluded_duplicate(row, loaded_positions)` requires
  non-empty `token_id` AND a loaded position of the same token_id with
  `loaded_shares >= excluded_shares` (riskguard.py, added ~line 265-291 in new file).
- Classification pass runs in a SECOND loop over `unloadable_raw`, after the first loop
  has finished populating `positions` for ALL loader_rows — so `positions` is complete
  regardless of loader-row order (riskguard.py ~424-460).
- `consistency_lock`: `"mismatched"` if `canonical_known_count != loader_position_count`;
  else `"pass"` only if `unaccounted_unloadable` is empty; else `"degraded"` (riskguard.py
  ~478-491).
- `_portfolio_consistency_level(consistency_lock)` returns `RiskLevel.GREEN` only for
  `"pass"`, else `RiskLevel.DATA_DEGRADED` (new function, riskguard.py ~114-124).
- `_tick_once` computes `portfolio_consistency_level` and passes it into
  `overall_level(...)` (riskguard.py ~2663-2669).
- `overall_level` (src/riskguard/risk_level.py:24-31) is `max(levels, key=order)` — GREEN
  < DATA_DEGRADED < YELLOW < ORANGE < RED — so a DATA_DEGRADED component cannot be
  overridden by a GREEN one.
- The `level` returned by `overall_level(...)` is INSERTed directly into the `risk_state`
  table (`level.value` at riskguard.py:2674, `INSERT INTO risk_state (level, ...)`) — this
  is the PERSISTED risk level the trader consumes, not merely a log line. Confirmed by
  reading the INSERT statement directly, not inferred.

### CHECK 2 — quarantine vocabulary zero except documented residuals [STATUS: VERIFIED]
`rg -n "quarantin" -i src/riskguard/` on T3 branch returns exactly 6 hits, all in two
families:
- riskguard.py:605 — `phase in {"settled", "voided", "quarantined", "admin_closed"}`, a
  position-lifecycle-phase fallback inside an exception handler for active-equity
  computation. This is the position lifecycle phase literal (T5's mechanism), untouched
  per the commit message's stated scope.
- riskguard.py:1742, 2517, 2546, 2684, 2690 — all `localized_orange_quarantine` /
  ORANGE-strategy-localization family (Brier scoring residual check for strategies behind
  a durable gate). Read in context (riskguard.py 1735-1745, 2505-2550): this mechanism
  computes whether a strategy's *historical Brier score* should be excluded from the
  pooled portfolio Brier calculation once it is durably gated — an entirely different
  concern from the row-exclusion/consistency_lock mechanism T3 fixes. It does not report
  a false "healthy" verdict while hiding missing exposure; it is a scoring-scope filter,
  not a truth-hiding mechanism. Confirmed as a genuinely separate mechanism, not a dodge.
- No other quarantine-vocabulary hits remain in src/riskguard/.

### CHECK 3 — tests: 103 passed, duplicate-classification tests match the claimed split [STATUS: VERIFIED]
`cd .claude/worktrees/agent-a94804f30a13a5dd3 && python -m pytest tests/test_riskguard.py -q --no-header`
→ `103 passed in 78.53s`. Matches the claim exactly.
Read the four relevant tests directly (tests/test_riskguard.py):
- `test_loader_excludes_unloadable_row_instead_of_failing_whole_tick` (:1348) — no
  `token_id` on the bad row → `classification == "excluded_unaccounted"`,
  `consistency_lock == "degraded"`, `_portfolio_consistency_level(...) == DATA_DEGRADED`.
- `test_loader_excluded_duplicate_row_does_not_degrade_consistency` (:1435) — matching
  `token_id`, loaded shares (10) >= excluded shares (5.07) →
  `classification == "excluded_duplicate"`, `consistency_lock == "pass"`, level GREEN.
- `test_loader_excluded_duplicate_with_insufficient_loaded_shares_still_degrades` (:1498)
  — matching `token_id` but loaded shares (1.0) < excluded shares (5.07) →
  `"excluded_unaccounted"`, `consistency_lock == "degraded"`.
- `test_loader_zero_exclusions_reports_pass` (:1546) — no exclusions → `"pass"` / GREEN.
These four tests directly exercise the exact split claimed (proof-of-safety token_id +
shares comparison; pass/degraded routing; DATA_DEGRADED wiring). Not a rubber-stamp: the
assertions check `consistency_lock`, `classification`, `excluded_duplicate_count`, and the
`_portfolio_consistency_level` mapping in the same test, so the whole chain from row
exclusion to risk-level routing is covered end-to-end by these unit tests.

### CHECK 4 — adversarial cases: empty token_id, canonical twin loads after the bad row [STATUS: VERIFIED]
- Empty `token_id`: `_riskguard_unloadable_row_is_excluded_duplicate` does
  `token_id = str(row.get("token_id") or "")`; `if not token_id: return False` — handled
  correctly (falls to `excluded_unaccounted`, degrades). Directly exercised by
  `test_loader_excludes_unloadable_row_instead_of_failing_whole_tick` (bad_row has no
  `token_id` key at all).
- Canonical twin loads AFTER the excluded row in loader order: the implementation is
  structurally order-independent by construction — the first loop
  (`for row in loader_rows: try: positions.append(...) except ValueError: ...`) runs to
  completion over ALL `loader_rows` before the second loop
  (`for row, reason in unloadable_raw: ... _riskguard_unloadable_row_is_excluded_duplicate(row, positions)`)
  ever runs, so `positions` is the FULL final list regardless of which row appeared first
  in `loader_rows`. The implementer's own code comment states this explicitly: "Classification
  pass (runs after ALL rows are loaded, since a dual-id duplicate's canonical counterpart
  may appear anywhere in loader_rows, not necessarily before the bad row)." No explicit
  test constructs "twin loads after the bad row" as a distinct ordering case, but the
  two-pass structure makes the ordering irrelevant by construction — verified by direct
  code reading, not by a dedicated test. Recommend (non-blocking) adding one explicit
  order-reversed test for defense against future refactors that might collapse the two
  loops back into one.

### Observation (non-blocking, out of stated packet scope)
`tick_with_portfolio(portfolio)` (riskguard.py:3048, called live from
`src/engine/cycle_runner.py:707` as the "graceful-degradation entry") computes its own
`level = overall_level(...)` at riskguard.py:3127-3132 using
`portfolio.portfolio_loader_degraded` — a DIFFERENT signal than `consistency_lock`, and
this call site was NOT extended with `portfolio_consistency_level`. This is a distinct code
path that never calls `_load_riskguard_portfolio_truth` (it receives an already-built
`PortfolioState`), so `consistency_lock` isn't available there to wire in — it's a separate
upstream-degraded-portfolio signal, not the same B052 row-exclusion bug T3 targets. The
packet spec scoped T3 to `riskguard.py:352-454`/`_tick_once`, which this observation does
not fall inside. Flagging for awareness, not a packet defect.

---

## PACKET T7

### CHECK 1 — §8.2 lifecycle grammar matches lifecycle_manager.py truth [STATUS: VERIFIED]
`python3 -c "from src.state.lifecycle_manager import TERMINAL_STATES; print(sorted(TERMINAL_STATES))"`
on the T7 branch → `['admin_closed', 'settled', 'voided']`.
`docs/authority/zeus_current_architecture.md:229-248` (§8.2) now reads: "Terminal phases:
voided, settled, admin_closed" and "`quarantined` is NOT terminal today: ... widened its
fold to `{quarantined, settled, voided}`" — matches TERMINAL_STATES exactly, correctly
cites the P0c fold widening, and correctly labels quarantined a review/investigation phase
pointing at T5 for retirement. VERIFIED.

### CHECK 2 — artifact_authority_status.yaml rename, all consumers updated [STATUS: VERIFIED]
`architecture/artifact_authority_status.yaml`: enum value is now `SUPERSEDED_UNREVIEWED`
(line 42), with a dated rename comment at lines 16-18. Repo-wide grep for the bare literal
`QUARANTINE` (case-sensitive, quoted forms) across all `.py` files found it only in: the
historical migration script and its two tests (occurred_at sentinel — see CHECK 3, correctly
untouched/historical), and `src/state/db.py` (a comment referencing the removal, not a
live literal). `maintenance_worker/core/archival_check_0.py` and
`tests/maintenance_worker/test_archival_check_0.py` — the commit's claimed consumers — were
both touched in the diff (`git show --stat 1ab83355c`) and the test passes
(`python -m pytest tests/maintenance_worker/test_archival_check_0.py -q` → part of the
20-passed run below). No stray consumer of the old `QUARANTINE` artifact-disposition value
remains. VERIFIED.

### CHECK 3 — occurred_at='QUARANTINE' sentinel removal [STATUS: VERIFIED]
- Kernel SQL (`architecture/2026_04_02_architecture_kernel.sql:33-42`), `src/state/db.py`
  (~5101-5103), and `src/state/ledger.py:309` diffs all confirmed: the literal
  `OR occurred_at = 'QUARANTINE'` is removed from the live CHECK/WHERE clauses; each site
  carries a dated comment pointing at the excision doc.
- Historical migration `scripts/migrations/202605_position_events_occurred_at_iso_check.py`
  retains its own self-contained `_CHECK_FRAGMENT = "LIKE '____-__-__T%' OR occurred_at =
  'QUARANTINE'"` and DDL, untouched — confirmed via grep, it does not import or reference
  the kernel SQL file.
- `architecture/_schema_fingerprint.txt` diff: hash changed from `6bf019f4...` to
  `5e28b622...` (re-pinned). `python -m pytest tests/test_schema_fingerprint.py
  tests/test_replay_schema_fingerprint.py -q` → 6 passed, confirming the new pin is
  actually correct (these tests recompute and compare the fingerprint, not merely check the
  file exists).
- `python -m pytest tests/state/test_position_events_check_constraint.py
  tests/test_migration_position_events_occurred_at_iso_check.py -q` → 9 passed. VERIFIED.

### CHECK 4 — packet extension: 3 AGENTS/domain-model terminal-list fixes match lifecycle_manager truth [STATUS: FAILED — partial]
Root `AGENTS.md:170` — CORRECT: "terminals are `voided`, `settled`, `admin_closed`" —
matches `TERMINAL_STATES = {admin_closed, settled, voided}` exactly.

`src/state/AGENTS.md:52` — WRONG: reads "Terminal: `voided`, `admin_closed`." — **missing
`settled`**, which IS in `TERMINAL_STATES`. The diff (`git show 1ab83355c -- src/state/AGENTS.md`)
shows the PRE-existing text was "Terminal: `voided`, `quarantined`, `admin_closed`" — this
commit correctly removed the wrong `quarantined` entry but did NOT add the missing
`settled` entry, so the pre-existing `settled` omission survives uncorrected.

`docs/reference/zeus_domain_model.md:181` (now ~184 after the insert) — WRONG: reads
"Terminal states: voided, admin_closed" — same defect, same pre-existing omission
(`git show` confirms prior text was "Terminal states: voided, quarantined, admin_closed",
also missing `settled`).

This directly contradicts the commit's own message, which claims: "Corrected all four to
the live grammar (terminals: voided/settled/admin_closed)" — only 2 of 4 cited surfaces
(root AGENTS.md and zeus_current_architecture.md §8.2) actually state all three terminals;
`src/state/AGENTS.md` and `zeus_domain_model.md` still omit `settled`. This is a real,
verifiable doc-accuracy defect (not merely a residual/pre-existing issue being left alone —
the commit touched both lines and had the correct answer available in its own commit
message and in the file it also edited, but propagated the incomplete list).
Severity: doc-only, no code/test/runtime impact — a 1-line fix in each of the two files
(`Terminal: voided, settled, admin_closed.` / `Terminal states: voided, settled,
admin_closed`).

### CHECK 5 — test evidence: 138 targeted tests / test_db.py 20 pre-existing failures [STATUS: VERIFIED]
- `python -m pytest tests/test_db.py -q --no-header` on T7 branch → `20 failed, 80 passed`.
  Spot-checked that this is a PRE-EXISTING baseline, not a T7 regression: created a temp
  worktree at the T7 branch's parent commit (`git worktree add -f /tmp/verify_t7_baseline
  66befaf38`), copied the untracked/gitignored `config/settings.json` needed for tests to
  boot, and re-ran `pytest tests/test_db.py -q` → identical `20 failed, 80 passed` with the
  SAME 20 test names failing on both commits. Confirms the 20 failures are pre-existing and
  unrelated to this commit's `db.py` change (a comment-only edit to the DDL block near the
  removed CHECK literal). Temp worktree removed after the check
  (`git worktree remove --force /tmp/verify_t7_baseline`).
- Spot-checked 3 of the "138 targeted tests": `tests/maintenance_worker/test_archival_check_0.py`,
  `tests/test_schema_fingerprint.py`, `tests/test_replay_schema_fingerprint.py`,
  `tests/state/test_position_events_check_constraint.py`,
  `tests/test_migration_position_events_occurred_at_iso_check.py` — all pass (20 + 6 + 9 =
  35 tests across these files, no failures). Did not attempt to enumerate/run the full 138
  file list (not provided by the executor in the dispatch); the subset run is consistent
  with the claim and covers every file this branch's diff actually touches.

## Missing evidence (if UNVERIFIED)
- T3 CHECK 4: no dedicated unit test for the "canonical twin loads after the bad row"
  ordering case — verified by code reading only, not by an executable regression test.
- T7 CHECK 5: did not independently obtain or run the full list of "138 targeted tests" the
  executor cited; ran the subset directly implicated by this branch's diff.

## Regressions (if FAILED)
- `src/state/AGENTS.md:52` and `docs/reference/zeus_domain_model.md:181`: both state
  "Terminal: voided, admin_closed" (or "Terminal states: voided, admin_closed"), omitting
  `settled`. Live truth (`src/state/lifecycle_manager.py` `TERMINAL_STATES`, verified by
  direct Python import) is `{admin_closed, settled, voided}`. Root `AGENTS.md:170` and
  `docs/authority/zeus_current_architecture.md` §8.2 both correctly list all three — the
  commit's own message claims all four surfaces were corrected to
  "voided/settled/admin_closed," but 2 of 4 remain wrong. Not a regression the commit
  introduced from scratch (the `settled` omission pre-dates this commit — confirmed via
  `git show 1ab83355c` diff context showing the pre-image also omitted `settled`), but the
  commit touched both lines, had the correct 3-item list in hand, and left the omission
  uncorrected while claiming in its own message that it fixed it.
