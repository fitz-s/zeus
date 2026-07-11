# Quarantine Excision — 病灶摘除设计(2026-07-11)

Operator verdict (direct instruction, 2026-07-11, superseding the first draft's
"keep legitimate validation" carve-out): **ALL quarantine is removed — total
extermination.** The operator's listed findings were a starting point, not the scope.
Every mechanism, state, table, column literal, disposition, doc term, and comment
carrying "quarantine" either (a) dies with its disease, or (b) — where the underlying
function is genuinely needed (input validation, scoped source blocks, file audit
moves) — is re-implemented under its own precise semantic name with correct shape:
rejection is named rejection, a scoped source block is named a source block, an audit
move is named an audit move. The word and the pattern both go to zero across
src/tests/scripts/architecture/docs (archives exempt as cold history).
T1-T7 below cover the pathological core; §T8 (extermination ledger) covers the rest.

Chain position: this work sits on `execution -> monitoring -> settlement` (position truth
and re-decision). On re-decision: every excision replaces a frozen scar with a lane that
re-evaluates against fresh chain/venue truth each cycle.

## Disease definition (what qualifies for excision)

```
upstream bug/data error
  -> authoritative fact NOT fixed
  -> quarantine state / side-table minted
  -> readers exclude
  -> global gate blocks operation
  -> tests/invariants freeze the exclusion
```

A mechanism is DISEASE if it (a) makes an error permanent without a truth-resolution path,
(b) widens a local error into a global stop, or (c) hides a real fact from an authority
view while reporting healthy. A mechanism is LEGITIMATE if it rejects known-bad INPUT at
a boundary with scoped effect and an evidence-backed release path.

RESHAPE-AND-RENAME (function survives under its true name; the word and the
quarantine shape die):
- `ensemble_snapshot_provenance`: known-bad data-version rejection → boundary
  validation named as such (e.g. `rejected_data_versions` / `is_admissible`);
  reject-at-ingest semantics, no "quarantine" state.
- `market_scanner` source-contract mismatch: city-scoped source block with release
  evidence → rename to source-contract block (`source_block`, release path kept).
- materialization-queue orphaned-lock move + maintenance-worker moves: file audit
  rename → `orphaned_lock_archive` / audit-move naming.
- invalid-certificate submit rejection: fail-closed rejection is law (FC-03); the
  REJECTION survives, renamed to certificate invalidation/revocation semantics.
- `decision_integrity_quarantine` side-table: R1-b erratum #8 found live callers
  (executor.py:1163 pre-submit gate; command_recovery.py:3111 — an INDEPENDENT
  duplicate implementation, both re-pointed same packet; evidence_report.py:251
  learning filter), so it cannot be deleted blind — but under total extermination it
  does not wait for R7 either. Re-implement as first-class fact invalidation.
  Verified constraints (2026-07-11): the side-table is a genuine second existence-
  authority (row-existence = invalidation), keyed (table_name, row_id, reason_code)
  — multiple coexisting reason codes per row + meta_json audit payload; it tags 7
  canonical tables spanning ALL THREE physical DBs. Therefore the reshape is NOT a
  boolean column: it is a precisely-named **revocation record** per owning DB
  (preserves reason multiplicity + audit payload), with validity consulted through
  the owning table's read path. This is a 3-DB schema migration with data backfill,
  not a rename; module docstring's DB map is stale vs domains.py (opportunity_fact
  is TRADE-class) — migration follows domains.py.

## Live blast radius (measured 2026-07-11, read-only)

- `edli_fill_bridge_dispositions` (zeus-world.db): 8 `QUARANTINED_BRIDGE_FAILURE`
  (permanently excluded from scan — potential unmaterialized real fills = money),
  7 NULL-disposition accumulating, 6 `SETTLED_MARKET_FILL_BOOKED` (legitimate).
- `settlements.authority='QUARANTINED'`: 92 rows per in-code probe comment
  (db.py:3195-3197) — re-count live at T2b packet start.
- `position_lots.state='QUARANTINED'`: 0 live rows (census TAIL4); enum literal +
  minting site (venue_command_repo.py:3291-3329) still migrate in T5.
- `position_current` (zeus_trades.db): 4 `phase='quarantined'`, all
  `chain_state='entry_authority_quarantined'` (already redecision-eligible via
  `_quarantined_position_can_redecision`).
- Global gate live: `cycle_runner.py:400-402` (`not has_quarantine` in
  `_discovery_gates_allow_entries`), fed by `_has_quarantined_positions` (:129).

## Excision targets

### T1 — EDLI bridge permanent quarantine disposition [Tier 0, self-contained]

Files: `src/events/edli_position_bridge.py` (:1201-1345 disposition machinery),
`src/ingest/price_channel_ingest.py` (:1256-1345 skip + quarantine call),
`src/state/schema/edli_fill_bridge_dispositions_schema.py`,
`tests/events/test_fill_bridge_settled_routing_quarantine.py`,
`tests/test_fill_bridge_dispositions_migration.py`.

Disease: broad `except Exception` counted to 10 → terminal
`QUARANTINED_BRIDGE_FAILURE` → aggregate excluded from all future scans. A decoder
bug or transient error becomes a permanent confirmed-fill data gap. No release path.

Target form (first principles): a confirmed fill on chain is truth that MUST
materialize; the only terminal disposition is an accounting truth
(`SETTLED_MARKET_FILL_BOOKED` — keep). Failure to materialize is a code/venue problem:
- Delete `DISPOSITION_QUARANTINED` / `_quarantine_aggregate` / threshold.
- Keep the accumulating row (attempt_count, last_error) purely as evidence +
  backoff input: retry cadence decays with attempt_count (bounded per-cycle cost),
  eligibility never terminates. A fixed decoder self-heals on next scan.
- ERROR log every failed attempt (loud), no exclusion.
- Drain: migration clears the 8 QUARANTINED rows' disposition to NULL so the fixed
  scanner re-drives them; report what materializes (operator-visible receipt).
- Schema CHECK drops the quarantine literal.

### T2 — Global discovery gate → scoped + existing risk law [Tier 0]

Files: `src/engine/cycle_runner.py` (:129-155, :400-402), `src/engine/evaluator.py`
(candidate filter seam), `architecture/invariants.yaml` INV-27,
`tests/test_p0_hardening.py` (INV-27 tests), gate tests.

Disease: any one quarantine fact freezes ALL new entries portfolio-wide.

Target form: quarantine facts lose their bespoke global gate; effects route through
scoped blocks + exposure accounting + existing risk law.
Verified premises (2026-07-11 investigation, file:line in evidence):
- The gate IS portfolio-wide today: one boolean over all facts/positions
  (`cycle_runner.py:129-155`), no condition_id parameter, consumed at `:402`.
- A per-condition scoped block consulted by the candidate filter DOES NOT exist —
  net-new seam. `entry_block_scope`/`token_suppression` only modulate the global
  boolean or reconciliation resurrection; evaluator/market_scanner never read them.
- Exposure accounting (`portfolio.total_exposure_usd`) sums typed Positions only;
  `ChainOnlyFact.size/cost_basis` exist but are counted NOWHERE.
- Zeus's negRisk model: each mutually-exclusive bin is its OWN condition_id; the
  correlated family is a group of sibling condition_ids. `family_exclusive_dedup.py`
  treats the family as one partition — for local positions only, never ChainOnlyFact.
- DATA_DEGRADED confirmed YELLOW-equivalent: only GREEN admits entries.

1. **Scoped block, FAMILY-scoped not condition-scoped**: an unknown-asset fact blocks
   new entries for its whole weather family (city, target_date, metric) via
   `WeatherFamilyKey` — the same partition `family_exclusive_dedup` already uses —
   because sibling bins are not independent. New seam: candidate filter consults
   blocked family keys derived from ChainOnlyFacts. Everything outside the family
   trades.
2. **Exposure conservatism**: extend exposure/heat accounting to include
   ChainOnlyFact worst case. Per-token payout bound shares × $1 is sound (CTF
   collateralization); cost side uses `cost_basis`. Family-level worst case follows
   the existing `_family_portfolio_max_loss_usd` shape extended to unknown assets.
3. **DATA_DEGRADED for unbounded unknowns**: a fact with no usable size/cost figure
   is missing truth input → existing DATA_DEGRADED lane (blocks entries by risk law,
   monitor/exit continue), heals when truth arrives.
INV-27 rewritten: quarantine is no longer "the canonical entry blocker"; the invariant
becomes "unknown-exposure facts must reach the risk view (family-scoped block +
worst-case exposure or DATA_DEGRADED); silent exclusion forbidden".

### T2b — settlements/observations authority tier QUARANTINED [own packet; census "T9"]

`settlements.authority` / `observations.authority` third tier `QUARANTINED` (92 live
settlement rows; minted by harvester.py + harvester_truth_writer.py duplicate writer
pair sharing one CHECK + monotonic trigger requiring evidence-backed release; ALSO
actively minted by scripts/backfill_settlements_via_gamma_2026.py, drained by the
repeatable scripts/drain_settlement_quarantine.py — ~30 hit-groups across
architecture/scripts/tests). Rename target: **DISPUTED** — CONFIRMED live vocabulary, not a proposal:
`contracts/settlement_axes.py:179-182` already maps QUARANTINED →
SettlementResolutionState.DISPUTED one layer up (root enum:
`types/truth_authority.py`), and scripts/backfill_settlement_outcome_type.py maps
the same. Packet scope: 7 coordinated files (census-src ledger row), not 3.
Shape verdict: the per-row scoping + DB-enforced evidence release (`reactivated_by`)
is sound and survives; the DISEASE part is drain-by-manual-script — the packet also
wires drain into the normal settlement re-discovery cycle (re-resolution attempts on
DISPUTED rows each harvester pass, bounded) so rows heal through the same lane that
minted them. Both writer copies + backfill/drain scripts +
`bayes_precision_fusion_history_provider` filter + calibration law §A6 + law
surfaces (settlement_dual_source_truth yaml, fatal_misreads, history_lore,
preflight_overrides, paris_station_resolution — historical signed directives stay
verbatim as history) rename in ONE packet with the table-rebuild migration.
Statistical treatment law (exclude-or-model, never down-weight) carries over
verbatim under the new name.

### T3 — RiskGuard row exclusion reporting `consistency_lock=pass` [Tier 0, self-contained]

Files: `src/riskguard/riskguard.py` (:352-454; the `consistency_lock="pass"`
computation is at :449), `tests/test_riskguard.py` (baseline test defs at :1344,
:1414).

Disease: unparseable canonical row → excluded from `PortfolioState.positions`,
excluded rows added back into the count so `consistency_lock="pass"` — real exposure
vanishes from risk math while the system reports healthy. (B052's anti-crash intent
was right; the "pass" verdict is the lie.)

Target form: keep the no-crash behavior, fix the verdict:
- Any excluded row → `consistency_lock="degraded"` (never "pass") and the truth dict
  carries the excluded rows (already does).
- `degraded` feeds risk level as DATA_DEGRADED (YELLOW-equivalent: no new entries,
  monitor/exit continue) — an unparseable exposure row IS missing truth input under
  §2 law. Not RED: crash-the-tick was the original bug.
- Root fix direction (R5): single-writer projection + schema CHECKs make unparseable
  canonical rows structurally impossible; this packet makes the interim state honest.
- Tests asserting pass-with-exclusion are rewritten to assert degraded-with-exclusion.

### T4 — fill_tracker conflated quarantine minting [Tier 0, depends on R2 reconcile lane]

Files: `src/execution/fill_tracker.py` (7 `_mark_entry_quarantined` sites: 881, 905,
988, 1050, 1114, 1165, 1240, 1287 vicinity), `src/state/lifecycle_manager.py`
(`enter_chain_quarantined_runtime_state`), associated tests.

Disease: missing venue fill economics, missing authority fields, ledger write failure,
canonical write failure, pending timeout — programmer error, schema error, and venue
truth gaps all folded into one lifecycle scar.

Verified premises (2026-07-11 investigation): all 8 call sites are private to
fill_tracker.py. Classification: sites 881/905 = VENUE-truth gaps (already
logger.error); sites 988/1050/1165/1240 = LOCAL ledger-write failure (four of them
silent at call site — several semantic-conflict early-returns inside
`_maybe_append_venue_fill_observation` produce NO Python ERROR log); site 1114 =
LOCAL canonical-write failure; site 1287 = CONFLATED (timeout/cancel routes into
`_mark_entry_voided`, whose OWN lifecycle write failing quarantines — timeout never
reaches quarantine directly today). There is NO in-module retry: `check_pending_entries`
scans only `pending_tracked`, so quarantine permanently exits the polling loop.
`src/reconcile/chain_truth.py` exists but is NOT directly consumable for per-position
timeout arbitration: it cannot distinguish "confirmed absent" from "not yet observed"
(no confirmation-count logic) and is whole-book, not per-command. The production
absent-vs-unobserved authority is `chain_mirror_reconciler.classify_local_position`
(two-consecutive-mirror-run force-resolve, ~10min cadence).

Target form: pending-entry uncertainty resolves through CHAIN truth, not a scar phase:
- venue payload incomplete / missing economics → stay `pending_entry` with a typed
  blocker reason on the position (stays in the `pending_tracked` scan set, re-polled
  every cycle; venue or chain eventually answers). No phase change.
- local write failure (ledger/canonical) → loud ERROR **at every site including the
  four currently-silent semantic-conflict paths** + stay in scan set, retry next
  cycle; a local bug must not relabel venue truth.
- pending timeout → keep the existing void lane for venue-confirmed cancels; for
  ambiguous timeouts hand to the chain-mirror reconciler's absent-vs-unobserved
  protocol (NOT chain_truth.py — wrong tool). Chain is the arbiter (reconciliation
  order law); void only on CONFIRMED absence, never on unobserved.
- void-write-failure (site 1287): loud ERROR + stay pending + retry — the void
  re-derives from the same durable inputs next cycle.
- `_mark_entry_quarantined` deleted when all callers are re-routed. Downstream: every
  consumer of the minted state is enumerated in T5/T8 ledgers (gate, dedup carve-out,
  harvester settlement block, exit scan exclusion, monitor cadence cohort, portfolio
  filters) — each is re-pointed in the same wave, none silently starved.

### T5 — QUARANTINED lifecycle phase retirement [after T1-T4 stop minting]

Files: `src/state/lifecycle_manager.py`, `src/contracts/canonical_lifecycle.py`,
`src/contracts/semantic_types.py`, `position_current` CHECK constraint (schema
migration), `tests/test_no_new_scar_state.py` (ratchet baseline SHRINKS — the test
was built for exactly this), `docs/authority/zeus_current_architecture.md` §8.2.

REPLACEMENT PHASE LAW (critic I-1): confirmed-fill-conflict / terminal-restore
positions hold REAL venue-confirmed exposure — they are a different class from
chain-only unknowns and must stay Position rows. Replacement shape: the position
keeps its TRUE lifecycle phase (`active`, or `pending_exit` per exposure state) and
the dispute moves to a typed **ReviewFact** record (position-linked, reason-coded,
operator-clearable — the ReviewWorkItem shape the scar-state atlas §7C/§7D already
prescribes). Consumers re-key: `cycle_runtime.py:3505`
(_family_monitor_position_has_live_risk), `:3940` (consolidated redecision
predicate), and the exit path key on "has open ReviewFact", never on a phase string.
Entry blocking for the affected family rides T2's family-scoped block fed by open
ReviewFacts exactly as by ChainOnlyFacts.

THREE-ENUM LAW (census-src TAIL1): T5 is NOT a single-enum migration. Three parallel
enums carry the state — `contracts/canonical_lifecycle.py::PositionPhase`,
`contracts/semantic_types.py::LifecycleState` + `::ChainState` — plus shared root
constants in `contracts/position_truth.py`; a migration touching only PositionPhase
silently misses the other two. All three + root constants retire in ONE coordinated
step, with `test_no_new_scar_state.py`'s three baselines ratcheting together.

Sequence: (a) T1-T4 remove all minting writers — census found minting sites BEYOND
fill_tracker/chain_reconciliation that must close in the same wave:
`venue_command_repo.py:3291-3329` mints `position_lots.state='QUARANTINED'` (read by
polymarket_user_channel M5 gate), `lifecycle_events.py` review-required canonical
writes via chain_reconciliation:920/928, and two command_recovery passes that WRITE
`phase='quarantined'` as their repair target (`repair_confirmed_phantom_voids`
:8419-8451, `repair_confirmed_chain_absence_positive_projections` :8639-8642) —
these two repairs are chain-truth-driven "needs review" promotions and get the T4
replacement state (typed review/blocker fact), not deletion.
(b) existing 4 rows drain through the chain-mirror reconciler grading
(chain_mirror_state_model_2026-07-04 already folds quarantined → settled/voided);
(c) enum member + CHECK literal removed via table-rebuild migrations (SQLite cannot
ALTER CHECK; template exists in db.py's REOPEN-2/authority rebuilds) across ALL
carrying tables: position_current.phase, position_events.phase_before/phase_after +
event_type 'CHAIN_QUARANTINED' + occurred_at 'QUARANTINE' sentinel,
position_lots.state, token_suppression(+history).suppression_reason,
settlements/observations.authority 'QUARANTINED' (92 live settlement rows),
market_topology_state + source_contract_audit_events source_contract_status.
Historical position_events rows are REWRITTEN by the same migrations (event strings
are data, not enum-parsed at rest) — replay/rebuild paths read post-migration values;
T8 census verifies no reader parses the retired literals from history.
(d) scar baseline ratcheted, `LEGAL_LIFECYCLE_FOLDS` cleaned. Chain-only unknown
assets live as `ChainOnlyFact` typed facts (already exist), never as Position rows.
Resolves the authority contradiction (T7 does the doc side).

### T6 — control-plane quarantine ack machinery [rides T2/T5 — SEQUENCING TRAP]

`has_acknowledged_quarantine_clear` / `acknowledged_quarantine_clear_tokens` and the
operator ack command lane die when nothing mints the state they acknowledge.
Census finding: this is currently the ONLY operator release valve for stuck
quarantined/disputed positions. Order is binding: the replacement release path (an
operator command that resolves a typed review/blocker fact) must be wired and tested
BEFORE T5 retires the state — never a window where operators cannot clear a disputed
position.

### T-consolidations — shared predicates BEFORE the migration waves

Census cross-validated duplications that must consolidate FIRST (else silent behavior
loss during T4/T5 rewiring):
1. `_decision_certificate_is_quarantined` — two independent implementations
   (executor.py:1384, command_recovery.py:3042). Consolidate to one shared
   `is_certificate_revoked()` in the decision_integrity replacement module; re-point
   both callers in that packet.
2. Redecision-eligibility predicate for quarantined positions — FOUR implementations:
   `cycle_runtime._quarantined_position_can_redecision` (canonical),
   `cycle_runtime._canonical_monitor_position_rows` inline,
   `portfolio._is_runtime_open_position`, price_channel_ingest exposure-clause SQL.
   Consolidate to the canonical predicate before T5 touches the state vocabulary —
   this is also the live functional core of branch p2-pending-exit-restart-redecision.
3. `chain_reconciliation._materialize_chain_only_position_if_resolvable` (:1531-1591)
   mints a fake Position with state=QUARANTINED for chain-only tokens — directly
   contradicts the target model (chain-only assets = ChainOnlyFact only). DELETE
   outright in T5, never rename.
4. (critic I-2) Two MORE live QUARANTINED minters in chain_reconciliation.py:
   `_preserve_confirmed_fill_chain_absence_conflict:1281` and
   `_restore_terminal_chain_exposure_if_available:1406` — both set QUARANTINED on
   REAL venue-confirmed/terminal-restore exposure (live from reconcile()
   :1719/:2067/:2256). NOT delete, NOT ChainOnlyFact: these take the I-1
   ReviewFact replacement (true phase + typed review record). Loader coercion is
   strict (`portfolio.py:728`), so any surviving minter after enum removal is a
   load-crash — T5 closure list must include both sites.

### T7 — semantic contamination cleanup [Tier 3, anytime]

- TERMINAL-LIST accuracy bug (fix-now, live-wrong since P0c 2026-07-04): root
  AGENTS.md:170, src/state/AGENTS.md:52, docs/reference/zeus_domain_model.md:181 all
  list `quarantined` as terminal; live TERMINAL_STATES excludes it
  (lifecycle_manager.py:82-126). In the T7 implementation packet. Root AGENTS §2
  replacement language (census-docs draft, adopted): L170 terminals =
  voided/admin_closed + chain-only unknown assets never enter the Position lifecycle
  (typed ChainOnlyFact, scoped entry block, worst-case exposure); L172 "Chain exists,
  not local -> materialize scoped ChainOnlyFact (family-scoped entry block +
  worst-case exposure in risk caps) and evaluate forced exit" — L172 mandate rewrite
  lands with T5, L170 list fix with T7.
- `docs/authority/zeus_current_architecture.md` §8.2: `quarantined` listed as terminal —
  contradicts `zeus_execution_lifecycle_reference.md` and the chain-mirror model. Fix
  authority doc now (it is wrong today, independent of T5). Also §9 (:272 "fail,
  quarantine, or mark degraded") and §351 (`acknowledge_quarantine_clear` in the
  frozen command vocabulary) — the law audit found these outside §8.2.
- LAW surfaces beyond the original list (2026-07-11 audit — each amended by its
  owning packet): AGENTS.md:170+:172 (lifecycle terminals + reconciliation mandate),
  invariants.yaml INV-27 (rewrite) + INV-09 test citation, money_path_objects.yaml
  (lifecycle_phase/position_event/decision_integrity reason codes/LateArrivalPolicy),
  kernel_manifest.yaml frozen enums, kernel SQL 6 CHECK clauses,
  db_table_ownership.yaml (settlement_commands_era_quarantine +
  decision_integrity_quarantine rows), source_rationale.yaml
  (ensemble_snapshot_quarantine_contract authority_role), script_manifest.yaml
  (promotion barriers ×2, quarantine scripts), test_topology.yaml antibody rows,
  fatal_misreads.yaml:181, history_lore.yaml (CWA legacy-quarantine card — rewrite to
  new name, keep the do-not-delete lesson), naming_conventions.yaml:106,
  topology.yaml:989 + digest_profiles.py:195 (source_contract_quarantine.json runtime
  file — renamed with its mechanism), semgrep_zeus.yml:96 message text,
  settlement_dual_source_truth (quarantine_reason key),
  preflight_overrides_2026-04-28 + paris_station_resolution_2026-05-01 (SIGNED
  operator directives: historical resolutions stay verbatim as history; only
  live-consumed keys rename with the settlement-authority migration),
  statistical_calibration_addendum §A6/D2 + consult2_crossvalidation (binding math
  law for QUARANTINED settlement rows → renamed with the authority enum, rule
  semantics preserved: exclude-or-model, never down-weight),
  .claude/skills provenance-audit + zeus-methodology-bootstrap wording.
- Distinct-mechanism inventory (law audit): ≥8 textually-independent "quarantine"
  senses (lifecycle scar, chain-reconcile action, settlement/observation authority
  tier, calibration treatment law, decision-integrity side-table, source-contract
  city block, ensemble snapshot refuse-list, LateArrivalPolicy value, doc-artifact
  disposition). Each renames with ITS OWN mechanism's packet — no global word-swap.
- `architecture/2026_04_02_architecture_kernel.sql:33`: `occurred_at` accepting literal
  `'QUARANTINE'` — a state word inside a timestamp type. Fix the shape.
- `architecture/artifact_authority_status.yaml` QUARANTINE disposition: rename to a
  docs-native word (e.g. `SUPERSEDED_UNREVIEWED`) so lexical search stops conflating
  doc lifecycle with position lifecycle.

### T8 — Extermination ledger (everything outside T1-T7)

Total lexical census (tracked, non-archive): 420 files. src/** per-file hit counts
measured 2026-07-11; top masses: decision_integrity_quarantine.py (124),
chain_reconciliation.py (85), db.py (59), cycle_runtime.py (57), portfolio.py (47),
market_scanner.py (43), lifecycle_manager.py (40), fill_tracker.py (39),
command_recovery.py (33), ensemble_snapshot_provenance.py (31), plus a ~70-file tail
(day0_fast_obs, day0_oracle_anomaly, harvester, canonical_projections,
lifecycle_events, position_truth, truth_authority, evidence_report, harvester_truth_writer,
replacement_forecast_calibration_quarantine, calibration/*, maintenance_worker/**, …).

Census COMPLETE (2026-07-11): authoritative ledgers —
`quarantine_ledger_src_2026-07-11.md` (+8 part files),
`quarantine_ledger_tests_law_2026-07-11.md` (246/246 files, 0 gaps:
131 B1 / 54 B2 / 2 B3 / 46 B4 / 11 B5-HISTORICAL),
`quarantine_ledger_docs_2026-07-11.md`. The 11-surface LAW amendment list and
B5 exemption detail live in the tests/law ledger; packets cite ledger rows, this
plan does not duplicate them. The settlement-authority mechanism census called
"T9-candidate" is formally owned by **T2b** above.

Method: every T8 file gets a semantic classification pass (fan-out investigators),
each hit assigned to exactly one bucket:
- **B1 DIES-WITH-DISEASE**: consumer/producer of a T1-T7 mechanism → removed by that
  packet's rewiring (no separate work; ledger records the mapping).
- **B2 RESHAPE-AND-RENAME**: real function, wrong name/shape → re-implemented under
  precise semantics in the same packet as its owning module (rejection/validation/
  block/audit-move/revocation naming; state words never enter timestamp or
  disposition columns of unrelated types).
- **B3 DEAD-CODE**: quarantine-only helpers, tests of removed behavior, schema shims
  → deleted, registries updated.
- **B4 TEXT-ONLY**: comments/docstrings/docs referencing the concept → rewritten to
  describe the new semantics (not merely word-swapped).
Census-identified B2 families beyond T1-T7 (each renames within its owning module's
packet): CALIB (Platt bucket rejection), FORECAST-INGEST-BOUNDARY (TIGGE ensemble
majority-threshold rejection), LOOP-GUARD (repo-loop-runner kill-switch → same
HALT/violation naming as its wrapper), MAINTENANCE split (SELF_QUARANTINE kill-switch
→ self-halt; 4 file-audit-move rules → audit-move naming; bindings/zeus/config.yaml +
safety_overrides.yaml `quarantine_dir` keys edit SAME packet — runtime-read config),
STRATEGY-LOCALIZATION (unrelated word collision in test_riskguard.py — rename only).
Coordination-risk files (multiple targets converge; edit order per removal sequence):
tests/test_command_recovery.py (T5+T6+DIQ), scripts/check_live_restart_preflight.py
(T5+DIQ — center of live branch p2-pending-exit-restart-redecision), kernel SQL
(T5+T6+T7 converge), kernel_manifest.yaml lockstep with kernel SQL.
DB artifacts: quarantine columns/tables/CHECK literals get migrations (drop or rename
to the new semantic name); historical event strings handled per T5 replay analysis.
B5-HISTORICAL: 11 one-shot migrations already run stay for lineage — exempt from the
zero-grep gate alongside archives (gate exemption list: docs archives, B5 migration
scripts, this excision doc's history).
Completion gate: `rg -i quarantin src/ tests/ scripts/ architecture/ maintenance_worker/
bindings/ config/ .claude/ AGENTS.md REVIEW.md CLAUDE.md .github/` returns ZERO
(critic M-1: root law files + skills in the gate set); docs/** zero outside archive
registry, B5-HISTORICAL migrations, census ledgers, + this excision doc's own
history section.

## Execution status (conductor log)

- T3: IMPLEMENTED + VERIFIED MERGE-READY — branch `claude/agent-a94804f30a13a5dd3`
  (8933b7ced + ab4ef5198). Verifier confirmed DATA_DEGRADED wiring reaches persisted
  risk_state.level; M-2 duplicate split tested end-to-end; 103 tests green. Awareness
  note: `tick_with_portfolio` (:3048) uses `portfolio_loader_degraded`, separate
  mechanism, out of T3 scope.
- T7: IMPLEMENTED, one doc defect being fixed (src/state/AGENTS.md:52 +
  zeus_domain_model.md:181 missing `settled` in terminal list) — branch
  `claude/agent-a794538c86262be51`. occurred_at='QUARANTINE' sentinel proven dead
  (0 live rows) and removed; artifact disposition renamed SUPERSEDED_UNREVIEWED.
- T1: IMPLEMENTED + M-3 amendment in progress — branch `claude/agent-a8b279c707d88ebbc`.
  8 frozen rows diagnosed read-only: 5× transient `database is locked` (retry heals),
  1× EDLI_BRIDGE_STRATEGY_MISSING (structurally unrecoverable → operator terminal),
  1× F109 parallel-row guard (human review), 1× link-refuses-overwrite (latent code
  bug, flagged). UNRECOVERABLE_MANUAL_REVIEW operator-only terminal added.
- Verifier evidence: evidence/verifier/verify_quarantine_excision_t3_t7_2026-07-11.md.

## Consult adjudication (2026-07-11, GPT-5.6 Pro deep review — answer at
## /tmp/cgc/answer_REQ-20260711-140149-0f9584.txt; verdict adopted)

Verdict: direction correct; RQ-2/RQ-3 NO-GO as previously drafted. Two defenses may
not be removed before their accidental safety invariants are replaced:

BLOCKER-1 (largest live-money hole): an unresolved/partially-persisted entry can
carry ZERO effective Portfolio exposure while RiskGuard's unprojected-fill
compensation misses it (venue fact never persisted). The global gate is overbroad
but currently fail-safe against this undercount. LAW: every durable command that may
have caused venue/chain exposure has exactly one of {authoritative settled
economics | conservative bounded **EntryExposureObligation** | unbounded obligation
→ DATA_DEGRADED}, created ATOMICALLY on the failure path (before return), not on a
later load/reconcile. Final admission = ONE candidate-aware fold (evolution of
`_discovery_gates_allow_entries`) that persists an **EntryRiskReservation** in the
same transaction before network post; evaluator filtering stays a prefilter, never
an enforcement authority.

BLOCKER-2 (T5): three-DB table rebuilds are NOT crash-atomic under WAL (SQLite:
attached-DB commit is atomic per file under WAL). T5 executes as an offline RED
cutover: full writer-plane fence (all daemons/scripts, not just cycle_runner) →
WAL checkpoint+truncate → synchronized 3-DB backup set → dedicated NON-WAL
rollback-journal connection (never `_connect()`, it re-enables WAL) → ONE attached
transaction (create targets → backfill+parity(counts+payload hashes) → chronicler
provenance → rebuild carrying tables → cross-DB invariant validation → stamp
identical schema_epoch ×3 → commit) → target binary refuses ANY mixed epoch at
startup. Kill-point crash matrix (SIGKILL at every DDL/copy/validate/stamp/commit
boundary) is the acceptance gate. Rollback = all three backups together or forward
fix; never one file alone; never old binary after any target write.

BLOCKER-3 (T4): "two mirror misses" ≠ confirmed absence without an observation
contract. Chain observations feeding force-void carry a typed
**ChainObservationEnvelope**: account/network scope, completeness (not
paginated/truncated/rate-limited), freshness bound, post-command watermark,
independence interval, finality, uncontradicted by venue trade/balance/open-order.
Stale/incomplete observation = DATA_DEGRADED, never an absence vote; positive chain
observation always overrides local absence evidence.

Adopted target shape (supersedes scattered typed facts; refines critic I-1):
**one ReviewWorkItem PROTOCOL, physically owner-local** — one schema/reason
vocabulary/scheduler/operator contract instantiated in EACH fact's owning DB
(same-DB transaction with its fact; read-only union = operator atlas). Typed domain
facts (ChainOnlyFact, SettlementDispute/DISPUTED, CertificateRevocation, source
block, ingestion rejection, EntryExposureObligation) remain the authorities; work
items schedule re-observation/retry/operator resolution and are rebuildable from
facts. Work item carries: subject identity, reason_code, authority_revision,
evidence refs, family key, exposure bound | unbounded, attempt/next_attempt
(indexed due-work scheduler: `status='OPEN' AND next_attempt_at<=now ORDER BY
priority,next_attempt_at LIMIT N`), OPEN/RESOLVED/SUPERSEDED with CAS resolution on
authority_revision, partial unique index on OPEN. Eligibility permanent; execution
frequency and ACTIVE CARDINALITY bounded (per-cycle budgets segregate fresh fills /
pending polls / old retries / settlement rediscovery / EDLI; active-work high-water
→ DATA_DEGRADED). Family risk reducer dedups by canonical token/asset identity
FIRST (Position + ChainOnlyFact for same token = ONE exposure), then maps to
WeatherFamilyKey; unmappable → DATA_DEGRADED, never skipped. shares×$1 bound valid
long-only-CTF only (documented assumption).

Wave gating (adopted):
- T3 GO (done, merged). T7 GO (done, merged). Census GO (done, committed).
- T1 CONDITIONAL: + idempotent drain (verify aggregate hasn't already materialized
  canonical records before re-drive), receipt categories {materialized,
  already-materialized, settled-accounting, bounded-retry, unbounded/DATA_DEGRADED}.
  Indexed scheduler optional at current row counts (~21), mandatory if table grows.
- T2 GATED on: canonical asset dedup reducer + ChainOnlyFact exposure + family
  mapping + unbounded handling + EntryRiskReservation + single final fold, ONE
  packet. Acceptance test: command admitted → venue submit maybe-succeeded → fill
  observation write FAILS pre-RiskGuard-fact → position pending → obligation enters
  family+total risk → next candidate rejected/reduced → monitor/exit/reconcile
  continue.
- T2b CONDITIONAL GO, EXCLUSIVE owner of settlements/observations rebuild, HARD
  predecessor of T5 (T5 only asserts the invariant already holds). + bounded
  rediscovery (next_attempt_at), new-evidence wake, writer fence on rebuild.
- T4 GATED on ReviewWorkItem machinery + BLOCKER-3 envelope + idempotency keys.
- DIQ CONDITIONAL: shared `is_fact_revoked(owner_domain, table, row_id)` API;
  migrate under RED: create → backfill → per-reason count + payload-hash parity →
  switch readers → assert parity → drop old; executor + command_recovery move same
  packet.
- T5+T6 HARD GATED: all minters closed, T2b done, replacement operator command
  live, current rows safely mapped (ambiguous rows ABORT, never forced void),
  crash-matrix passed.
- T8 zero-grep LAST: conformance check after semantic tests+migrations, never a
  safety release criterion.

Provenance note: consult reviewed the pinned public tree; docs/rebuild plan +
ledgers + EXECUTION_MASTER were not pushed at that SHA and were supplied as
premises in the prompt context; PR #431 is unrelated (riskguard freshness). Its
code-level citations were independently spot-checked locally where load-bearing.

## Ordering and packet plan

Wave RQ-1 (parallel, self-contained): T1, T3, T7, T8-census (classification ledger
produced by fan-out investigators; no code edits).
Wave RQ-2: T2 (needs the ChainOnlyFact scoped-block seam + exposure worst-case);
T8 reshape packets for self-contained modules (ensemble_snapshot_provenance,
market_scanner source block, file-audit renames, calibration/day0 tail).
Wave RQ-3: T4 (uses R2 `src/reconcile/` chain_truth lane); decision_integrity
re-implementation as fact-validity semantics (callers re-pointed same packet);
then T5+T6 (drain + retire) and T8 residue sweep to the zero-grep gate.

Interaction with EXECUTION_MASTER: T4/T5 overlap R2-c/R5 territory (command_recovery
passes still fire on quarantine states) — RQ packets must not delete recovery passes;
they remove MINTING and GATING. Recovery-pass deletion stays R5-gated per R2-c map.

Constitution classification (critic I-3, §E2.1): this whole excision is operator-
ordered hemostasis — the same R0 class as EXECUTION_MASTER's own stop-the-bleed lane.
The pathological machinery is live-money-wrong TODAY (frozen fills, global freeze,
lying consistency verdict), and its removal cannot wait for target-namespace rebuild
waves. Net-new REPLACEMENT logic stays minimal and lives by placement rule:
- typed facts/records (ReviewFact, revocation records) are CONTRACTS → new modules
  under src/contracts/ / src/state/ (target-shape, not god-file growth);
- consumer re-keying (evaluator family-block read, riskguard verdict, fill_tracker
  blocker reasons) = single-seam edits to legacy files — permitted class ②;
- anything larger discovered mid-packet escalates back to the conductor, not into a
  god-file.

Each packet: provenance-audit on touched files first; tests red→green with the frozen
scar/gate baselines rewritten (not appeased); no destructive git; registries updated
(`source_rationale.yaml`, `test_topology.yaml`, `invariants.yaml`); commit per packet.

## Verification bar

- T1: the 8 drained aggregates re-scanned; each either materializes a position or
  fails loudly with a named error class (receipt).
- T2: unit — one quarantine fact + N healthy candidates → N-1 markets still pass the
  gate; exposure math shows worst-case add.
- T3: injected bad row → consistency_lock=degraded → risk level YELLOW-equivalent →
  entries blocked BY RISK LAW, monitor/exit alive, tick completes.
- T5: `test_no_new_scar_state.py` baseline ratchets (its stale-baseline test forces it);
  grep of lifecycle enums shows no quarantine member; 0 `phase='quarantined'` rows.
- T8/global: `rg -i quarantin` over src/ tests/ scripts/ architecture/
  maintenance_worker/ bindings/ = ZERO hits; every reshape survives its module's
  tests under the new name; full suite no new failures vs baseline.
