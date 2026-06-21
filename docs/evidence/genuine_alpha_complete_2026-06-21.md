# Genuine-Alpha COMPLETE artifact — 2026-06-21

Created: 2026-06-21
Last audited: 2026-06-21
Authority basis: lifecycle-alpha mission — bring the decision-q PROVENANCE fix
(writer + grader) onto the cold-center / lifecycle (#416 / Option C) lineage,
producing ONE final branch that carries the COMPLETE forecast+lifecycle fix AND
the immutable decision-q skill attribution, still a clean fast-forward from the
live daemon commit.

## Final artifact

- Branch: live/genuine-alpha-complete-20260621
- Code commits (before this doc): WRITER d626b0a30a, GRADER 6bb3716c51
- Based on: 5b5745b2f2 (Option C — grid-representativeness into served
  _mu_diagonal precision basis; carries Phase 1 entry-escalation + Phase 2/3
  exit-revival/accounting + #416 terminal-held grading fix).
- Commits added on top of base (cherry-pick, in order):
  - d626b0a30a WRITER (from 762b388447) — stamp q_live + q_lcb_5pct from
    ActionableTradeCertificate on profit-audit rows.
  - 6bb3716c51 GRADER (from a592ac8fdc) — attribute skill from the immutable
    decision-q certificate.
- Clean ff from live daemon c568b8fcf7: YES (merge-base --is-ancestor
  c568b8fcf7 HEAD true; base 5b5745b2f2 also an ancestor; branch = base + the two
  q-provenance commits, no merge commit, no rebase).

## How brought on

Branched off 5b5745b2f2; cherry-picked 762b388447 then a592ac8fdc.

- WRITER live_profit_audit.py: auto-merged cleanly (base Phase-3 write site already
  accepted/upserted q_live/q_lcb_5pct via insert_record; never passed). Only its
  test fixture conflicted.
- GRADER: conflicted on the SAME load_settled_positions #416 rewrote, the test file,
  and the computed schema fingerprint. Supporting files
  (settlement_attribution_schema.py 6-value CHECK + guarded rebuild,
  db_table_ownership.yaml, src/main.py) auto-merged.

## Conflict resolution — preserved from each side

### live_profit_audit.py (WRITER) — clean auto-merge
PRESERVED base Phase-3 compute_realized_edge_from_authorities + promotion gate + all
insert_record fields. LAYERED q-stamp: after the edge cert resolves,
_load_verified_certificate_payload on the same expected_edge_source_certificate_hash
yields q_live/q_lcb_5pct passed into insert_record. No new connection (INV-37).
Header "# Last audited: 2026-06-21".

### tests/events/test_live_profit_audit.py (WRITER) — both kept
_seed_authority_certificates: kept BOTH the base Phase-3 c_fee_adjusted comment (on
cost_payload) AND the writer's edge_extra injection (after edge_payload). The
writer's test_q_provenance_stamped_from_expected_edge_certificate landed unchanged.

### settlement_skill_attribution.py (GRADER) — the load-bearing conflict
Base (#416/W3) iterates trades.position_current (terminal-held phase IN
settled/economically_closed/admin_closed, keyed by position_id, q_live=None,
time-reconstructed posterior as q fallback). Grader (a592) was authored against the
OLD edli_live_profit_audit loader (audit_id key, cert hash on the row).

PRESERVED FROM BASE (#416): position_current iteration, terminal-held phase filter,
immutable entry-time bound (BLOCKER 2), and the entire Phase-3
writeback_settlement_pnl_to_audit accounting loop + settlement_pnl_written stat.
Nothing dropped.

LAYERED ON (q-provenance): _resolve_decision_q_from_certificate,
UNATTRIBUTABLE_Q_MISSING 6th category + its grade_position fail-closed gate
(decision_q_missing = q_live is None and decision_q_in_bin is None),
SkillWinRate.unattributable_q_missing + denominator exclusion, and removal of
decision_q_in_bin as a q authority (kept for observability only).

THE BRIDGE: position_current carries no cert hash, so new helper
_resolve_cert_hash_for_position(world_conn, condition_id, direction) resolves the
position's expected_edge_source_certificate_hash from the matching
edli_live_profit_audit fill row; the existing _resolve_decision_q_from_certificate
then walks to decision_certificates. No audit row / cert -> hash None -> q_live None
-> UNATTRIBUTABLE_Q_MISSING (never time-reconstructed). Single world_conn,
read-only — INV-37 preserved.

Bridge-key derivation (verified read-only, live zeus-world.db + zeus_trades.db,
2026-06-21):
- 76 terminal-held position_current rows.
- position_id != audit_id (0/76) — bridge cannot be by id.
- (condition_id, direction) resolves all 53 that have a cert; 23 have none ->
  UNATTRIBUTABLE_Q_MISSING.
- token_id deliberately excluded: the audit fill stores the NO-outcome token =
  position.no_token_id on 51/53 and position.token_id on only 2/53; matching on
  position.token_id would wrongly strand 51. (A first draft's token_id primary +
  fallback layer was removed — it masked the wrong-key bug, not added safety.)
- Ambiguity: 14 (cond,dir) pairs map to >1 cert hash; 12 share identical q_live, the
  other 2 differ only at the 3rd decimal (same side of every threshold) -> grade
  invariant. ORDER BY created_at DESC LIMIT 1 (latest entry) deterministic.

Why removing decision_q_in_bin does NOT break #416: the 23 unresolvable positions go
UNATTRIBUTABLE (excluded from the skill denominator) — the intended fail-closed
behavior. R1-R6 call grade_position directly with explicit non-None q_live (gate
never fires); BLOCKER2 asserts only decision_posterior_computed_at +
fresher_cycle_existed_at_decision (returned regardless of category); F3 updated to
seed a resolvable cert.

### tests/test_settlement_skill_attribution.py (GRADER) — base structure + adapted Q-tests
Took the base file as canonical (its #416 position_current/Phase-3 tests; the
grader's interleaved conflict regions were obsolete audit-loader fixtures).
PRESERVED every #416 test verbatim, with ONE surgical change:
test_F3_end_to_end_db_grade now seeds a VERIFIED ActionableTradeCertificate
(q_live=0.72) + stamps its hash on the bridging audit fill so the position resolves
a real decision-q -> SKILL_WIN (W2 pnl assertions pnl_usd=6.5/WON unchanged).
LAYERED ON: Q1-Q3 rewritten for the position_current+bridge design — Q1
(cert-resolvable -> real q -> SKILL_WIN), Q2 (unresolvable cert hash ->
UNATTRIBUTABLE, never SKILL/LUCK, q None, excluded from denominator), Q3 (no bridging
audit row -> UNATTRIBUTABLE).

### architecture/_schema_fingerprint.txt — regenerated, not hand-picked
Computed SHA-256 over live init_schema DDL; neither base nor grader hash was correct
for the merged schema. REGENERATED via scripts/check_schema_fingerprint.py
--write-pin -> d5eca505c4640ecb3e2766f6b949d240f89880fb3f26f9460d2b481e51561f25.
Verified: check (no args) reports OK; merged settlement_attribution CHECK contains
UNATTRIBUTABLE_Q_MISSING.

## RED -> GREEN proof
Neutering _resolve_cert_hash_for_position to return None:
    test_Q1_grader_populates_q_from_resolvable_certificate  FAILED
    AssertionError: q_live must be resolved from the immutable decision-q certificate
    assert None == 0.8 +- 1.0e-09   (Obtained: None, Expected: 0.8)
-> cert-resolvable position loses its real decision-q. Restored: Q1+Q2+Q3 pass.
Invariant genuinely gated: cert-resolvable -> real q used; missing ->
UNATTRIBUTABLE_Q_MISSING, never SKILL/LUCK.

## Test tails (committed branch state, both green)

q-provenance (writer + grader, no #416 regression):
    tests/events/test_live_profit_audit.py ......................... [100%]
    ============================== 41 passed in 3.07s ==============================
(pytest tests/test_settlement_skill_attribution.py tests/events/test_live_profit_audit.py -q -p no:cacheprovider)

Option C (representativeness / precision-center / method-unify):
    ...... [100%]
    ====================== 44 passed, 143 deselected in 1.05s ======================
(pytest tests/forecast tests/data tests/probability -k representativ-or-precision_center-or-method_unify -q -p no:cacheprovider)

## Invariants confirmed
- INV-37: single world_conn; trades + forecasts ATTACHed read-only; the per-position
  cert bridge issues only SELECTs on that connection; writer reuses the existing
  cert-payload loader (no second connection).
- Single-truth: win/loss via canonical grade_receipt; pnl from settlement payoff
  only (Phase-3 writeback unchanged); the cert is the SOLE skill-q authority.
- File-header provenance "# Last audited: 2026-06-21" on both edited source files +
  schema owner + both test files.
- No deploy, no merge to main.

## Architect cross-check (read-only, independent)
An independent architect agent cross-checked the resolution: "architecturally sound
and the correct design; the merge is fully resolved; the implemented resolution is
strictly safer than the proposal as written." Confirmed (condition_id, direction) is
correct and token_id is a trap (strands 51/53); trade_id / decision_snapshot_id /
aggregate_id share 0/76 value space with the audit table so none is usable; the DESC
tiebreak is deterministic and the multi-hash q differences are <=0.004 (same side of
every threshold); the 23 UNATTRIBUTABLE positions are correct fail-closed and do not
break #416 (the test file was reconciled to seed position rows); no INV-37 risk. No
hole found.

## Lint note (pre-existing, not introduced by the merge)
pyflakes flags 3 items in settlement_skill_attribution.py (f-string w/o placeholders
at line 404; unused filled_size; unused decision_q_certificate_hash). All three are
verbatim from the source commits (base / a592ac8fdc), not introduced here, and left
untouched to keep the cherry-picks faithful (scope discipline).
