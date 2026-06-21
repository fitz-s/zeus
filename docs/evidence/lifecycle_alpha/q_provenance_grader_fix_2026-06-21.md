# q-provenance GRADER fix — 2026-06-21

Created: 2026-06-21
Authority basis: immutable decision-q certificate authority for settlement skill
attribution (lifecycle-alpha)
Branch: `live/q-provenance-grader-20260621` (based on
`claude/agent-acbc793d072df305e` @ `db90aad6db`, the q-provenance WRITER side)

## Summary

The settlement grader `src/analysis/settlement_skill_attribution.py`
(`load_settled_positions`) now attributes SKILL vs LUCK from the system's
**actual immutable decision-time q**, resolved from the `ActionableTradeCertificate`
in `decision_certificates`, reached via the audit row's
`expected_edge_source_certificate_hash`. When that certificate is unresolvable the
position grades the new 6th category `UNATTRIBUTABLE_Q_MISSING` — never
SKILL_WIN/LUCKY_WIN, excluded from the skill denominator, and never silently
graded off the time-reconstructed posterior (which is demoted to a DEBUG aid).

Before this fix the grader read only `q_live`/`q_lcb_5pct` columns off
`edli_live_profit_audit` (NULL on every pre-writer-fix live row) and fell through
to the time-bounded posterior reconstruction as the skill authority.

## Position → certificate resolution path (verified read-only, 2026-06-21)

The decision-q is resolvable **directly off the audit row** — the
`expected_edge_source_certificate_hash` column already lives on
`edli_live_profit_audit` (stamped by the writer side at
`src/events/live_profit_audit.py:481`), so no walk through `edli_live_order_events`
is required.

| Step | Table.column (DB) | Join key |
|------|-------------------|----------|
| 1 | `edli_live_profit_audit.expected_edge_source_certificate_hash` (zeus-world) | `audit_id` is the position id; hash is read on the SELECT at `settlement_skill_attribution.py:792-805` |
| 2 | `decision_certificates.certificate_hash` (zeus-world), filtered `verifier_status='VERIFIED'` | `= expected_edge_source_certificate_hash` |
| 3 | `decision_certificates.payload_json` → `q_live`, `q_lcb_5pct` | JSON keys (verified present on a live `ActionableTradeCertificate`, e.g. `q_live=0.138…`, `q_lcb_5pct=0.067…`) |

This is the SAME cert + key path the writer stamps with
(`src/events/live_profit_audit.py:_load_verified_certificate_payload`, line ~552),
so writer and grader agree on the decision-q authority.

### Live ground truth (read-only, `?mode=ro`, zeus-world.db, 2026-06-21)

```
total_filled=59  resolvable_VERIFIED=54  q_in_payload=54  not_verified=0  no_cert=5
cert_types: {'ActionableTradeCertificate': 54}
```

All 59 live FILLED positions had `q_live=NULL` on the audit row (pre-writer
re-run). 54 resolve a VERIFIED `ActionableTradeCertificate` carrying both
`q_live` + `q_lcb_5pct`; 5 have no resolvable cert → those are the
`UNATTRIBUTABLE_Q_MISSING` cases. So the grader was silently grading all 59 off
the time-reconstructed posterior; the fix recovers the real decision-q for 54 and
brands the other 5 honestly.

## New grade category

`UNATTRIBUTABLE_Q_MISSING` — the immutable decision-q certificate is unresolvable,
so the system's actual decision-time belief is unknown; the outcome cannot be
attributed to skill or luck.

- Added to the DB-layer `category` CHECK in
  `src/state/schema/settlement_attribution_schema.py` (now a 6-value enum).
- Existing live tables carry the old 5-value CHECK (verified: live world.db table
  has the old CHECK + 29 rows). The CHECK is a table-level constraint SQLite
  cannot ALTER in place, so `_rebuild_stale_category_check` (mirrors the
  `no_trade_events_schema.py` guarded-rebuild pattern) rebuilds the table under a
  SAVEPOINT, copies all rows (row-count guard), and upgrades the CHECK. Idempotent
  and a no-op once current.
- Excluded from `SkillWinRate.skill_denominator` (like `STALE_DECISION`); surfaced
  in the log line as `UNATTRIBUTABLE_Q=<n>`.

### Migration verified (in-memory simulation of the stale live table)

```
pre rows: 2  post rows: 2
UNATTRIBUTABLE in new CHECK: True
persisted UNATTRIBUTABLE row OK, total now: 3
idempotent re-run OK, rows: 3
```

## TDD: RED → GREEN

Three new tests in `tests/test_settlement_skill_attribution.py`:
- `test_Q1_grader_populates_q_from_resolvable_certificate` — q resolved from the
  cert (NOT the NULL column / posterior); real decision-q drives SKILL_WIN.
- `test_Q2_unresolvable_cert_grades_unattributable_never_skill_or_lucky` — missing
  cert → `UNATTRIBUTABLE_Q_MISSING`, never SKILL/LUCK, q stays None, excluded from
  the skill denominator.
- `test_Q3_missing_hash_on_audit_row_grades_unattributable` — empty/NULL hash on
  the audit row → `UNATTRIBUTABLE_Q_MISSING`.

### RED (before implementation)

```
FAILED test_Q1_grader_populates_q_from_resolvable_certificate
FAILED test_Q2_unresolvable_cert_grades_unattributable_never_skill_or_lucky
FAILED test_Q3_missing_hash_on_audit_row_grades_unattributable
  AssertionError: assert 'LUCKY_WIN' == 'UNATTRIBUTABLE_Q_MISSING'
3 failed, 10 deselected
```

(The grader had no cert resolution and no `UNATTRIBUTABLE_Q_MISSING` branch, so an
unresolvable-q win fell through to LUCKY_WIN.)

### GREEN (after implementation)

```
tests/test_settlement_skill_attribution.py .............  [100%]
13 passed in 1.70s
```

`test_F3_end_to_end_db_grade` was updated (not weakened): it now seeds an immutable
decision-q cert and asserts `q_live` is resolved FROM the cert (0.72), since a
position with no resolvable cert is now correctly `UNATTRIBUTABLE_Q_MISSING`.

## Full required suite

```
tests/test_settlement_skill_attribution.py tests/events/test_live_profit_audit.py
...................................                                      [100%]
35 passed in 1.76s
```

35 = 13 grader (10 original + 3 new) + 22 writer-audit (21 original + the
base-branch q-provenance writer antibody
`test_q_provenance_stamped_from_expected_edge_certificate`). No regression on the
writer side.

Schema fingerprint re-pinned (`architecture/_schema_fingerprint.txt`) for the
intentional CHECK DDL change; `scripts/check_schema_fingerprint.py` → OK. 29
schema/registry tests pass (`-k "settlement_attribution or
assert_db_matches_registry or schema_fingerprint"`).

### Unrelated pre-existing failures (NOT caused by this change)

- 5 collection ImportErrors for `_QLCB_SOFT_ANCHOR_BASIS` in
  `src/data/replacement_forecast_materializer.py` — pre-existing on the base
  branch; the symbol genuinely does not exist there; no overlap with the 6 files
  in this diff.
- 2 `test_cascade_liveness_contract.py` failures (`test_operator_runbook_files_exist`,
  `test_every_scheduler_poller_for_state_machines_is_listed_in_contract`) — about
  `settlement_commands`/`wrap_unwrap_commands` runbook paths under
  `docs/archive/...` and scheduler pollers; this diff adds/removes no poller or
  runbook reference, and its `settlement_skill_attribution` poller allow-list
  assertion passes.

## Diff summary (vs `claude/agent-acbc793d072df305e`)

```
 architecture/_schema_fingerprint.txt              |   2 +-
 architecture/db_table_ownership.yaml              |  16 +-   (notes: 6th category + cert authority)
 src/analysis/settlement_skill_attribution.py      | 177 ++   (cert resolver + UNATTRIBUTABLE branch + demote posterior fallback + win-rate field)
 src/main.py                                       |  18 +-   (docstring category list)
 src/state/schema/settlement_attribution_schema.py |  81 +-   (CHECK +UNATTRIBUTABLE_Q_MISSING + guarded rebuild)
 tests/test_settlement_skill_attribution.py        | 286 ++   (Q1-Q3 + F3 cert seed)
 6 files changed, 534 insertions(+), 46 deletions(-)
```

Key code changes in `settlement_skill_attribution.py`:
- `_resolve_decision_q_from_certificate(world_conn, hash)` — reuses the grader's
  existing `world_conn` (INV-37: no new connection), reads VERIFIED cert payload
  `q_live`/`q_lcb_5pct`.
- `load_settled_positions` — selects `expected_edge_source_certificate_hash`,
  resolves cert q as the SOLE skill authority; when unresolvable sets
  `q_live=q_lcb_5pct=None` (does NOT fall back to the column or posterior).
- `grade_position` — UNATTRIBUTABLE gate evaluated first: `q_live is None and
  decision_q_in_bin is None` → `UNATTRIBUTABLE_Q_MISSING`.
- The time-reconstructed posterior (`decision_q_in_bin`) is no longer passed to
  `grade_position` from the live load path — it stays recorded as
  `decision_posterior_id`/`_computed_at` provenance (DEBUG aid), never the skill q.

## Provenance / honesty

- Read-only on live DBs (`?mode=ro`) throughout investigation.
- Not deployed, not merged.
- File headers updated to `Last audited: 2026-06-21` + the lifecycle-alpha
  authority basis on the grader, schema, and test files.
