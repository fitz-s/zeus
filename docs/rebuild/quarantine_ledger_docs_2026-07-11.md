# Quarantine Excision — Docs Census Ledger (2026-07-11)

READ-ONLY census per `quarantine_excision_2026-07-11.md`. Scope: `rg -l -i quarantin docs/`
+ root `AGENTS.md` + `REVIEW.md` (0 hits) + `.github/` (0 hits) + scoped
`src/**/AGENTS.md` (3 hits: state, control, engine, confirmed exhaustive against all
24 `src/**/AGENTS.md` files). No edits made.

Bucket counts (post coverage-diff pass, see bottom section): AUTHORITY 6 files (incl.
2 scoped src AGENTS.md w/ law text) · REFERENCE 12 files · OPERATIONS 26 files
(14 current-fact, 12 historical-packet) · EXEMPT 105 files (31 `docs/evidence/**` +
13 historical ops packets/consult-transcripts + 63 `docs/archive/**`, gitignored/
untracked, found on re-check) · REVIEW-DOCTRINE 3 files, 0 protective hits ·
OUT-OF-SCOPE (different "quarantine" domains, not the trading disease) 5 files
flagged inline · SELF (this excision doc + this ledger + 9 sibling investigator
ledgers) 11 files, not census subjects. Total hit surface re-measured 2026-07-11
second pass: 156 files (63 archive + 11 self + 82 substantive).

---

## AUTHORITY (law text — replacement sentence drafted for each)

### `AGENTS.md` (root) §2 Trading Machine Invariants — 2 hits

L170 lifecycle terminals list:
- OLD: `...terminals are \`voided\`, \`quarantined\`, \`admin_closed\`; \`unknown\` is transient/recovery only.`
- NEW: `...terminals are \`voided\`, \`admin_closed\`; \`unknown\` is transient/recovery only. Chain-only unknown assets never enter the Position lifecycle — they live as typed \`ChainOnlyFact\` records with a scoped entry block and worst-case exposure, not a lifecycle phase.`
- Owning T: T5.

L172 reconciliation law:
- OLD: `Chain exists, not local -> quarantine unknown asset and evaluate forced exit.`
- NEW: `Chain exists, not local -> materialize a scoped \`ChainOnlyFact\` (entry block limited to its own condition_id/market family + worst-case exposure counted into risk caps) and evaluate forced exit.`
- Owning T: T2 (scoped block + exposure worst-case), T5 (ChainOnlyFact target shape).

### `docs/authority/zeus_current_architecture.md` — 4 hits (T7's named target)

§8.2 Lifecycle Grammar (L235-243), terminal list `voided/quarantined/admin_closed` +
sentence `Quarantine is not a normal holding state.`:
- NEW terminal list: `voided`, `admin_closed`.
- NEW sentence replacing "Quarantine is not a normal holding state": `Chain-only
  unknown assets are not Position rows and carry no lifecycle phase — they are typed
  \`ChainOnlyFact\` records read directly by the risk view.`
- Owning T: T5 (already named by T7 as the doc to fix — this ledger supplies the exact
  replacement text T7 left undrafted).

§9 Governance Identity L272:
- OLD: `If exact attribution is missing, fail, quarantine, or mark the record degraded.`
- NEW: `If exact attribution is missing, fail or mark the record degraded —
  DATA_DEGRADED is the only non-failing lane for missing truth input.`
- Owning T: T2 (DATA_DEGRADED replaces quarantine as the third option).

L351 advisory command list, `acknowledge_quarantine_clear`:
- Action: DELETE the line (no replacement — T6 kills the ack command lane once nothing
  mints the state it acknowledges).
- Owning T: T6.

### `docs/authority/statistical_calibration_addendum_2026-06-13.md` — different domain: CALIBRATION ROW LABEL, not lifecycle/position quarantine

A6 heading + body (L86-91), D2 cross-ref (L177-179): "QUARANTINED rows" = settlement
rows whose bin is ambiguous across competing sources (CAR interval-widening / exclude
treatment). Rename target: `QUARANTINED` → `AMBIGUOUS_SETTLEMENT` (or
`ambiguous-settlement rows`/`ambiguous-settlement labels`/`ambiguous-settlement
treatment` throughout). This is a legitimate boundary-reject pattern (RESHAPE-AND-RENAME,
B2), not disease — same family as T8's `replacement_forecast_calibration_quarantine,
calibration/*` tail. No T1-T7 owns this by name; T8 census must pick it up.

### `docs/authority/consult2_crossvalidation_fable5_2026-06-13.md` — same calibration-label domain

L23, L40, L46: `QUARANTINED` / `quarantine treatment` / `quarantined covariate
distributions` — same rename as above (`ambiguous-settlement`). B2, T8 tail.

### `src/state/AGENTS.md` L51-53 (scoped AGENTS.md law, not just doc prose)

- OLD: `...Terminal: \`voided\`, \`quarantined\`, \`admin_closed\`. Runtime sentinel: \`unknown\`...`
- **VERIFIED WRONG TODAY, not just T5-future-stale** (checked against
  `src/state/lifecycle_manager.py:82-126` directly): `LEGAL_LIFECYCLE_FOLDS[QUARANTINED]`
  was widened from `{QUARANTINED}` to `{QUARANTINED, SETTLED, VOIDED}` by P0c
  (2026-07-04, `chain_mirror_state_model_2026-07-04.md` §5); `TERMINAL_STATES` is
  derived programmatically as "fold == {phase}" and the code comment at L118-121
  states explicitly: `"QUARANTINED is ALSO no longer terminal"`. Live
  `TERMINAL_STATES = {SETTLED, VOIDED, ADMIN_CLOSED}` — `quarantined` is NOT in it.
  This doc line has been factually wrong for the ~1 week since P0c landed,
  independent of the T5 excision. Same class of bug T7 already caught in
  `zeus_current_architecture.md` §8.2 ("wrong today, fix now").
- NEW: `Terminal: \`voided\`, \`admin_closed\`. \`quarantined\` is NOT terminal
  today — its fold widened to \`{quarantined, settled, voided}\` (P0c 2026-07-04) so
  the chain-mirror reconciler can close a quarantined row once chain truth grades it;
  it is an investigation status, not a terminal phase, and is retired entirely once
  quarantine excision T5 lands.`
- Owning T: **fix now** (doc-accuracy bug, independent of T5 timing) — full sentence
  rewrite (drop the interim-state caveat) sequenced with T5.

### Correction to root `AGENTS.md` §2 L170 and `docs/reference/zeus_domain_model.md` L181 — same "wrong today" upgrade

The AUTHORITY section above drafted L170's replacement assuming the fix ships with
T5. Same code-check applies here: **`quarantined` is already not terminal in live
code** (`lifecycle_manager.py` TERMINAL_STATES, verified above), so root AGENTS.md
L170 (`terminals are \`voided\`, \`quarantined\`, \`admin_closed\``) and
`docs/reference/zeus_domain_model.md` L181 (`Terminal states: voided, quarantined,
admin_closed`) are BOTH currently-wrong statements, not merely statements that will
go stale later. Recommend landing the terminal-list correction (drop `quarantined`
from all three: root AGENTS.md, src/state/AGENTS.md, zeus_domain_model.md) as its
own tiny fix-now packet, ahead of / independent of the T1-T8 wave plan — it is a
pure accuracy bug already live for a week. The ChainOnlyFact sentence addition (the
part of the L170/L52 replacement describing the FUTURE T5 shape) still waits for T5;
only the "drop quarantined from the terminal list" clause is fix-now.

### `src/control/AGENTS.md` L16

- Command table lists `acknowledge_quarantine_clear` among the 8 supported commands.
- Action: DELETE from the list when T6 lands (control_plane.py loses the command).
- Owning T: T6.

### `src/engine/AGENTS.md` L39-41

- OLD: `...convergence check (SYNCED/VOID/QUARANTINE) must complete before the evaluator...`
- NEW: `...convergence check (SYNCED/VOID/CHAIN_ONLY) must complete before the
  evaluator...` — rename the third reconciliation class to match ChainOnlyFact.
  Owning T: T2/T5.

---

## REFERENCE (mark for rewrite, owning T target where known)

- `docs/reference/zeus_execution_lifecycle_reference.md` — 18 hits, heaviest reference
  file. Full §2.x QUARANTINED state machine description (enum value, transitions,
  timeout at L209-214, phase_for_runtime_position mapping). This IS the reference doc
  T5 retires the subject of — needs a full rewrite pass describing ChainOnlyFact +
  scoped block instead of a lifecycle phase, sequenced AFTER T5 lands (can't describe
  the new shape accurately until the code exists). Owning T: T5.
- `docs/reference/zeus_domain_model.md` L181 — terminal states list `voided,
  quarantined, admin_closed`. Same fix as AGENTS.md. Owning T: T5.
- `docs/reference/schema_cheatsheet.md` L546 — `decision_integrity_quarantine` table
  entry. Rewrite once T4's fact-validity re-implementation lands (table renamed/folded).
  Owning T: T4.
- `docs/reference/zeus_vendor_change_response_registry.md` — 4 hits (L84, 295, 422,
  452). Different domain: `observation_instants_v2.authority CHECK IN
  ('VERIFIED','UNVERIFIED','QUARANTINED')` — data-authority flag for bad observation
  rows, not position lifecycle. Legitimate boundary-reject (B2), not named by T1-T7.
  T8 tail (day0/observation ingest cluster) must pick up the rename
  (`QUARANTINED`→e.g. `REJECTED`/`INADMISSIBLE`) and this doc follows.
- `docs/reference/zeus_data_and_replay_reference.md` L221,226 — same
  `VERIFIED/UNVERIFIED/QUARANTINED` three-level authority flag. Same T8-tail dependency
  as above.
- `docs/reference/zeus_math_spec.md` — 3 hits. L19 `CWA_STATION_IS_LEGACY_QUARANTINE_PATH_NOT_DEAD_CODE`
  (a history-lore card name, see also flagged under OPERATIONS below — schema-invalid
  status per allday_improvement_loop_design finding); L328 "log + quarantine the
  settlement" (settlement data-error handling, boundary-reject, B2); L559 "Quarantined
  — do not store as a calibration pair" (calibration exclude verdict, same
  ambiguous-settlement family as the authority docs above).
- `docs/reference/modules/contracts.md` L137, `docs/reference/modules/data.md` L149,
  `docs/reference/modules/scripts.md` L108, `docs/reference/modules/state.md` L96,159 —
  all use "quarantine" as a generic rollback/side-effect-containment verb in packet
  rollback guidance ("revert or quarantine those artifacts", "quarantine by
  data_version/source tag", "quarantine the new write family"). Not the lifecycle
  mechanism — this is packet-rollback methodology language. Needs a precise verb swap
  (e.g. "cordon"/"isolate"/"tag-and-exclude") per the total-extermination word law even
  though the underlying advice is sound and orthogonal to T1-T8. B4 text-only, no owning
  T — flag for a dedicated small sweep, not blocked on any T-wave.
- `docs/reference/modules/ingest.md` L30 — "appends a quarantining/reversal lot" —
  describes a FAILED-after-MATCHED reversal-lot append. Ambiguous: could be the
  legitimate audit-move pattern (B2, rename to "reversal lot") — cross-check against
  T4/fill_tracker during src census; not conclusively mapped from docs alone.
- `docs/reference/legacy/legacy_reference_settlement_source_provenance.md` L130,134 —
  dated 2026-03-08 historical incident note (`authority='QUARANTINED'` on 6 WU cities).
  Filename says "legacy" — this is itself a historical/frozen reference of a past
  incident, not current law. Borderline EXEMPT; leaving in REFERENCE only because the
  directory is nominally `docs/reference/**` per scope, but treat as read-only history.

---

## OPERATIONS

### Current-fact (`docs/operations/current/**`) — live pointer vs expired

`docs/operations/current/index.md` confirms this whole tree is `role:
pointer_and_evidence (not architecture law, not runtime truth)` — none of it is
authority regardless of live/expired status, but distinguishing matters for which
files still describe an in-flight decision vs closed history.

**EXPIRED (packet landed — receipt.json present, status=landed):**
- `docs/operations/current/quarantine_chain_freshness/{PLAN.md,receipt.json,scope.yaml}`
  — packet literally named for the disease; closed 2026-07-11T11:43. Its own name is
  the strongest live artifact of the term but it is dead history, not current fact.
- `docs/operations/current/pending_exit_restart_redecision/receipt.json` — closed
  2026-07-11T11:49 (this session's own branch packet); 2 hits are commit-log lines
  referencing the prior packet's title, not new law.
- `docs/operations/current_state.md` L39 — single hit is a changelog line `packet
  \`current/quarantine_chain_freshness\` landed`, i.e. an append-only history entry in
  the live control pointer file, not live-governing text. No edit needed even post-T5;
  historical changelog entries stay verbatim per append-first convention.

**LIVE / IN-FLIGHT (no receipt.json, still open):**
- `docs/operations/current/live_entry_health_repair/PLAN.md` (2 hits, no receipt.json
  → in-flight) — L187 forbids "hide quarantined chain risk from a surface that
  currently owns it"; L190 notes review found "quarantined chain-risk ownership
  remains intact". This plan treats quarantine as a live risk-surface concept it must
  not regress. Flag for the executing packet: once T2/T5 land, this plan's forbidden-
  actions list needs a pass to retarget "quarantined chain risk" language at the
  ChainOnlyFact/exposure-cap surface it will become. Not a doc law fix — an in-flight
  packet coordination note.
- `docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md`
  L762,767 — "40 quarantined positions ($293) batch-stamped", "quarantined capital"
  draining — describes an already-executed remediation batch (past tense, dated
  2026-07-02). Read as closed sub-step inside a still-open plan file; no live law to
  amend, just a historical operational note inside it.
- `docs/operations/current/plans/gate_stack_simplification_2026-07-06.md` L104 —
  "actionable-certificate re-verify against the *current* quarantine state" describing
  executor.py's existing trust-boundary re-check. Will need a rename pass once T4's
  fact-validity re-implementation lands (re-verify against validity/revocation state
  instead). Owning T: T4.
- `docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md` L198,205
  — two findings ABOUT quarantine-adjacent artifacts (a YAML parse break in a commit
  message mentioning "grade quarantine drain", and a schema-status violation in history
  card `CWA_STATION_IS_LEGACY_QUARANTINE_PATH_NOT_DEAD_CODE`). These are meta-findings
  about doc/registry hygiene, not quarantine-law text themselves — no rewrite needed
  from this excision, though the history-lore card name itself is in scope for a T8
  card-rename pass (calibration/CWA cluster).
- `docs/operations/current/reports/runtime_db_lock_refactor_design_2026-06-26.md` L503
  — "Fix or quarantine `tail_stress_scenarios` registry mismatch" — generic verb usage
  (registry-hygiene "isolate"), not the lifecycle mechanism. B4 text-only, no owning T.
- `docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md` — 6 hits.
  Corroborating evidence FOR the T5 direction: L289 explicitly states "review = work
  item (`chain_reconciliation.py` already emits `ChainOnlyFact`, no synthetic Position
  — finish it)" and L34 "there is NO on-chain 'position status' — a position is a
  balance; 'closed/quarantined/settled' are Zeus inventions". L110 also references
  `test_no_new_scar_state.py` as already-landed (matches T5's ratchet-test claim).
  Treat as supporting analysis, not law; no edit needed but worth citing in the T5
  commit as prior-art confirmation.
- `docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md` —
  **15 hits, IMPORTANT CONFLICT FLAG.** This report is a PRIOR competing design that
  proposes a DIFFERENT target shape than T1-T8: rename `QUARANTINED`→`REVIEW_REQUIRED`
  (not delete/ChainOnlyFact) and introduce a new `ReviewWorkItem` entity as "the
  quarantine owner" (L277: "Introduce `ReviewWorkItem` as the quarantine owner; keep
  `phase=quarantined` only as projection in transit"). This directly contradicts the
  operator's 2026-07-11 "total extermination" verdict and T5's ChainOnlyFact target —
  it is a **superseded design**, not current law, but it is sitting in `current/reports/`
  looking authoritative and a future reader could implement the wrong shape from it.
  Recommend: when T5/T8 land, either delete this report or prepend a supersession
  banner pointing at `quarantine_excision_2026-07-11.md` as the controlling design.
  Not authority itself (role: pointer_and_evidence) so no separate T-owned law fix, but
  flagging because it is the single doc most likely to mislead an implementer.

### Historical packets (dated, closed — EXEMPT, no edits planned)

`docs/operations/edli_v1/{PR328_DEEP_SEMANTIC_WIRING_REVIEW.md,
PR328_REDEMPTION_PACKAGE.md, PR332_DEEP_REDEMPTION_REVIEW.md,
PR332_DEPLOY_READY_REVIEW.md, PR332_FULL_SWEEP_BASELINE_WAIVER.md,
FULL_SWEEP_PR332_HEAD.log, FULL_SWEEP_PR332_MAIN_BASELINE.log}` (PR review records),
`docs/operations/sd3_validation_evidence/audit_extraction.md`,
`docs/operations/tribunal_verification_2026-05-29/{CRITIC_asymmetry.md,
CRITIC_consistency.md}` (dated 2026-05-29, >6 weeks old). All closed review/validation
records referencing quarantine as contemporary fact at time of writing. Cold history —
no edits planned.

---

## EXEMPT (cold history, `docs/evidence/**` — 31 files, no edits planned)

`anchor_channels/2026-06-11_bucket_downscaling_49city_parity.md`,
`coarse_global_removal/resolve_settlement_snapshot.md`,
`fresh_start_20260704/baseline.md`, `hardcode_sweep/2026-06-13_round2.md`,
`investigation_2026-06-13/{full_lens_analysis.md,post_peak_harvester_build.md}`,
`live_order_pathology/{2026-06-21_forward_chain_diagnosis.md,
2026-06-22_governor_scope_lattice_decision.md,
2026-06-23_selection_curse_design_and_impl.md}`,
`lock_storm/2026-06-13_lock_storm_regression_archaeology.md`,
`mx2t3_decouple/consumer_classification.md`,
`per_city_source/residual_legacy_sources.md`,
`planning_2026-06-14/{IMPLEMENTATION_PLAN.md,P2_W-EDGE-LOCATE.md,
P2_W-KEEP-SIMPLIFY.md,P3_architecture.md}`,
`plans/2026-06-13_fill_bridge_retry_storm.md`,
`pr408_review/{chatgpt_deep_review_2026-06-14.md,
chatgpt_review_C2_bayesian_2026-06-14.md}`,
`settlement_guard/{riskguard_loader_quarantine_2026-06-16.md,
verify_chain_confirmed_absorber_2026-06-17.md}`,
`timing_audit/{fallback_outcome_quality_2026-06-16.md,freshness_fallback_map_2026-06-16.md,
impl_M1_harvester_test_fix_2026-06-16.md,impl_M1_invariant_test_2026-06-16.md,
impl_remaining_regressions_2026-06-16.md,IMPLEMENTATION_DONE_2026-06-16.md,
MASTER_TIMING_FIX_PLAN_2026-06-16.md,shadow_validation_method_2026-06-16.md,
timestamp_provenance_ledger_2026-06-16.md,ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md}`.

Also EXEMPT (advisory consult transcripts — inputs already synthesized into
`quarantine_excision_2026-07-11.md` itself, no independent edit value):
`docs/rebuild/consult_answers/{loop_design_review.txt,representation_layer_design.txt,
scar_audit_round2.txt,whole_system_round1.txt}`, `docs/rebuild/r2c_pass_map.md` (historical
commit-log record, references `_quarantine_confirmed_chain_absence` as a past commit
title only).

---

## Coverage diff (2026-07-11, second pass — requested by team lead)

Re-ran `rg -l -i quarantin docs/ AGENTS.md REVIEW.md .github/` (156 hits vs. 82 on the
first pass' `docs/`-only scope). Diffed against every file named in this ledger. Two
gap classes, both resolved above / here — no third gap found:

1. **`docs/archive/**` (63 files)** — absent from the first pass because it is
   gitignored (`.gitignore:254`) and untracked (`git ls-files docs/archive` = 0); the
   first `rg` invocation ran with default ignore behavior and silently skipped it,
   the second explicit invocation still surfaced it because gitignore-skip is a
   ripgrep default that a caller can defeat by naming the path directly enough times
   in one session — the safer read is "always assume untracked archive dirs need an
   explicit re-check", not that anything changed on disk. Classified above as EXEMPT
   under the mission doc's own "archives exempt as cold history" carve-out, and
   doubly out of scope of the T8 completion gate (which is scoped to "420 tracked,
   non-archive files"). No further action.
2. **Sibling investigator ledger files (9 files)**, created after this ledger's first
   pass by parallel investigators working the src/tests side of the same census:
   `docs/rebuild/quarantine_ledger_src_2026-07-11.md`,
   `docs/rebuild/quarantine_ledger_src_part_{KMA,KMB,KMC,KMD,TAIL2,TAIL3,TAIL4}.md`,
   `docs/rebuild/quarantine_ledger_tests_law_2026-07-11.md`. These are OTHER
   investigators' output artifacts, not docs law/reference/operations surfaces — they
   contain the word "quarantine" as their own generated analysis content, not as a
   surface this excision needs to rewrite. **SELF, not a census subject.** Same
   treatment as `docs/rebuild/quarantine_excision_2026-07-11.md` itself (the mission
   doc — already excluded from earlier passes as self-referential) and this ledger's
   own file. 11 files total in the SELF bucket (mission doc + this ledger + 9 sibling
   ledgers).

**Coverage confirmed**: every file in the current 156-file `rg` hit list is now
accounted for in exactly one bucket above (AUTHORITY / REFERENCE / OPERATIONS /
EXEMPT / REVIEW-DOCTRINE / OUT-OF-SCOPE / SELF). No unclassified residue.

**Also EXEMPT — `docs/archive/**` (63 files, found on coverage re-check, see below):**
`docs/archive/2026-Q2/{findings_historical,operations_historical,plans_historical,
task_2026-05-17_post_karachi_remediation}/**` and
`docs/archive/legacy_archives/{omc_planning_20260707,packets}/**`. `.gitignore:254`
ignores `docs/archive/`; `git ls-files docs/archive` returns 0 — untracked, so these
sit outside the T8 completion gate's own "420 tracked, non-archive files" scope by
construction. The mission doc's own line 12 says "archives exempt as cold history" —
this directory is the literal referent. No edits planned, not individually
enumerated beyond this pointer.

---

## docs/rebuild/** — EXECUTION_MASTER and chain_mirror_state_model stale-line flags

### `docs/rebuild/EXECUTION_MASTER_2026-07-07.md` — 3 hits, mixed domains

- L65: append-only packet ledger entry (R1-b already-landed, `decision_integrity_quarantine
  EXCLUDED` note) — historical record, append-first convention, no edit.
- L113, L128: **OUT-OF-SCOPE — different domain.** These describe the dev-ops
  `loop_guard.py` file-revert safety mechanism ("quarantine 硬还原" = hard-revert
  out-of-scope writes back to a safe quarantine state; "任何 tick 的脚本改动一律
  quarantine 到 PREPARE"). This is an AI-loop governance concept, unrelated to the
  trading position/data quarantine disease. Still in scope of the literal word-zero
  gate (`rg -i quarantin` must hit zero) but NOT owned by any T1-T7; needs its own
  small B2 rename (e.g. "hard-revert"/"safe-hold") as a T8 tail item, separate cluster
  from trading.

### `docs/rebuild/chain_mirror_state_model_2026-07-04.md` — 32 hits, WILL GO STALE ON T5 LANDING

This document is not passive prose — it IS the design for the reconciler that T5 step
(b) explicitly depends on ("existing 4 rows drain through the chain-mirror reconciler
grading (chain_mirror_state_model_2026-07-04 already folds quarantined → settled/voided)").
Every line below describes `quarantined` as a live enum member / live phase / live
`LEGAL_LIFECYCLE_FOLDS` entry — all of it goes stale the moment T5 removes the enum
member, but the drain LOGIC it specifies must run first:
- L18,34,36,38-41,50: root-cause narrative — `quarantined` has no automated exit path
  today (the disease this whole excision fixes).
- L68-71: reconciler grading table — `quarantined`/`entry_authority_quarantined` rows
  → `CLOSED_REDEEMED`/`CLOSED_WORTHLESS`/size-correction. **This table is the T5(b)
  drain spec — keep it live until the 4 rows are drained, THEN rewrite past tense.**
- L125-146, L154-157, L166-170, L202-213: `LEGAL_LIFECYCLE_FOLDS[QUARANTINED]`
  widening, `TERMINAL_STATES` exclusion, `is_terminal_state("quarantined")` guard
  changes — all describe the INTERIM state (quarantined still exists but is no longer
  terminal) that T5 sequences through before deleting the enum member outright.
- L238,249-250: "no row stays quarantined past one reconcile cycle" — the operator
  requirement this doc satisfies; becomes moot (vacuously true) once T5 deletes the
  phase entirely.

Recommend: do NOT rewrite this doc in the same packet as T5. Sequence: (1) T5 runs the
drain using this doc's grading table as spec: (2) once 0 `phase='quarantined'` rows
remain and the enum member is deleted, THIS document is superseded — either archive it
under `docs/evidence/` (cold history of how the drain was designed) or rewrite past
tense as a closeout record. It is temporarily load-bearing, not dead weight, right now.

### Other `docs/rebuild/**` files — light hits, quick classification

- `docs/rebuild/forecast_center_diagnosis.md` L88,114 — "refit/quarantine the stale
  EDLI row" — generic verb (isolate a bad calibration bias row from use), calibration
  domain not lifecycle. OUT-OF-SCOPE of T1-T8, same family as the A6 calibration-label
  rename above if it needs touching at all.
- `docs/rebuild/red_baseline_2026-07-08.md` L97-98 — `test_untracked_top_level_quarantine.py`,
  `UNTRACKED_QUARANTINE_CANDIDATE`. **OUT-OF-SCOPE — third distinct domain**: this is the
  repo's task-packet/file-topology guard (untracked files outside any packet scope get
  flagged), same family as loop_guard.py above, not trading. Needs its own B2 rename,
  no T1-T7 owner.
- `docs/rebuild/representation_contract_2026-07-08.md` L116 — "quarantine 名单外全仓"
  (a docs/representation-checker EXEMPT-list, i.e. files excused from a blocking
  checker). **OUT-OF-SCOPE — fourth distinct domain** (doc-checker allowlist), matches
  `consult_answers/representation_layer_design.txt` L543 "explicitly quarantined legacy
  paths" — same concept, same non-trading domain.
- `docs/rebuild/obs_infra_locate.md` — 7 hits (L14,45,51,61,111,318,321). Describes the
  METAR plausibility-quarantine mechanism (`filter_plausible_values()`,
  `note_metar_quarantine()`, `observations.authority='QUARANTINED'`) — same data-
  authority-flag family as `zeus_vendor_change_response_registry.md` /
  `zeus_data_and_replay_reference.md` above. Legitimate boundary-reject, B2, T8 tail
  (day0/observation ingest cluster), not T1-T7 named.
- `docs/rebuild/source_truth_upgrade/spec_live_drift.md` L33 — "plausibility
  quarantine" mention inline in a KEEP-verdict table row for `day0_fast_obs.py`. Same
  T8-tail dependency as above.

---

## REVIEW-DOCTRINE — no protective guidance found

`REVIEW.md` (root): 0 hits. `docs/review/code_review.md`: 0 hits.
`docs/review/AGENTS.md`: 0 hits. `docs/review/review_scope_map.md`: 0 hits.
`.github/**`: 0 hits (dir exists, no matching files).
**No quarantine-protective review guidance exists anywhere in the review doctrine to
fight the excision.** Clean.

---

## Out-of-scope note: `docs/methodology/adversarial_debate_for_project_evaluation.md`

L459 "revert+quarantine NOW" — generic English verb (isolate contaminated session
state) inside a methodology case-study about session-contamination remediation. Not the
Zeus trading mechanism. Recommend leaving untouched; flagging only per the literal
`rg -i quarantin` zero-hit gate if that gate is ever applied to `docs/methodology/**`
(the excision doc's completion gate as written only covers `src/ tests/ scripts/
architecture/ maintenance_worker/ bindings/` for code and "docs/** zero outside archive
registry" for docs — this file would need the same generic-verb swap as the other
out-of-scope clusters above if the docs gate is taken literally).
