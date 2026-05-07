# Wave 15 Object-Meaning Invariance: outcome_fact -> replay diagnostic trade history

Status: in progress
Scope: diagnostic replay/report boundary only; not live unlock, not promotion-grade economics, not DB mutation.

## Route Evidence

- Root `AGENTS.md`: read from prompt/context.
- Scoped reads: `src/engine/AGENTS.md`, `tests/AGENTS.md`, `docs/operations/AGENTS.md`, `docs/reference/zeus_data_and_replay_reference.md`.
- Semantic boot: `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- First route, broad wording:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "object-meaning replay learning settlement authority Wave15 outcome_fact settlement result eligibility to replay learning calibration report consumers" --write-intent edit --files src/engine/replay.py src/backtest/economics.py tests/test_replay_skill_eligibility_filter.py docs/operations/task_2026-05-05_object_invariance_wave15/PLAN.md docs/operations/AGENTS.md`
  - Result: `navigation ok: False`; misselected `phase 5 promotion grade economics readiness implementation`; admitted only `src/backtest/economics.py`; rejected replay/tests/new packet.
- Replay-fidelity route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "edit replay fidelity" --write-intent edit --files src/engine/replay.py tests/test_backtest_outcome_comparison.py tests/test_backtest_trade_subject_identity.py`
  - Result: `navigation ok: False`; admitted `src/engine/replay.py` but rejected relevant trade-history tests.
- Admitted route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "object-meaning settlement authority cutover Wave15 replay/report diagnostic non-promotion outcome_fact settlement-result eligibility" --write-intent edit --files src/engine/replay.py tests/test_run_replay_cli.py docs/operations/task_2026-05-05_object_invariance_wave15/PLAN.md docs/operations/AGENTS.md`
  - Result: `navigation ok: True`; admitted `src/engine/replay.py`, `tests/test_run_replay_cli.py`, this plan, and `docs/operations/AGENTS.md`.
- Adjacent test-fixture route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "edit replay fidelity Wave15 verified settlement authority test fixture update" --write-intent edit --files tests/test_backtest_settlement_value_outcome.py`
  - Result: `navigation ok: True`; admitted `tests/test_backtest_settlement_value_outcome.py` for fixture alignment with VERIFIED settlement authority.
- Economics readiness route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "Phase 5B forward substrate DSA-19 Wave15 legacy outcome_fact lacks settlement authority fail closed" --write-intent edit --files src/backtest/economics.py tests/test_backtest_skill_economics.py`
  - Result: `navigation ok: True`; admitted `src/backtest/economics.py` and `tests/test_backtest_skill_economics.py`.
- Critic REVISE follow-up route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "object-meaning settlement authority cutover Wave15 replay/report diagnostic non-promotion outcome_fact decision_snapshot identity repair" --write-intent edit --files src/engine/replay.py tests/test_run_replay_cli.py tests/test_backtest_outcome_comparison.py docs/operations/task_2026-05-05_object_invariance_wave15/PLAN.md`
  - Result: `navigation ok: False`; admitted `src/engine/replay.py`, `tests/test_run_replay_cli.py`, and this plan; rejected `tests/test_backtest_outcome_comparison.py`.
  - Topology compatibility repair route: `python3 scripts/topology_doctor.py --navigation --task "object-meaning settlement authority cutover Wave15 topology admits direct trade-history outcome comparison relationship antibody" --write-intent edit --files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py` -> admitted. Added `tests/test_backtest_outcome_comparison.py` to the object settlement profile and regenerated `architecture/digest_profiles.py`.
  - After route repair: `python3 scripts/topology_doctor.py --navigation --task "object-meaning settlement authority cutover Wave15 replay/report diagnostic non-promotion outcome_fact decision_snapshot identity repair" --write-intent edit --files tests/test_backtest_outcome_comparison.py` -> admitted.

Topology compatibility notes recorded for system improvement:
- Generic object-meaning replay/learning wording was captured by an economics-readiness profile because `outcome_fact` is a trigger there. That route is semantically adjacent but too narrow for replay/report diagnostic boundary repair.
- `edit replay fidelity` admits source but not the most direct trade-history diagnostic test files; verification had to use the admitted `tests/test_run_replay_cli.py` surface.
- Missing plan packet paths are accepted by the object settlement profile wildcard, but rejected by other profiles that also detect `persistence_target: plan_packet`.
- `tests/test_backtest_outcome_comparison.py` was registered in `architecture/test_topology.yaml` but absent from the object settlement profile, even though it is a direct trade-history replay relationship antibody. This was repaired in topology rather than bypassed.

## Phase 0 Map Delta

Relevant money-path segment:

`verified world.settlements + position_current + legacy outcome_fact -> run_trade_history_audit -> backtest_outcome_comparison -> CLI/report/diagnostic consumers`

Authority surfaces:
- `world.settlements`: settlement source/result authority only when `authority='VERIFIED'` and `temperature_metric` matches the position.
- `position_current`: canonical position identity/read model.
- `outcome_fact`: legacy lifecycle projection of trade outcome/PnL; schema has no settlement authority, source authority, evidence class, or learning eligibility columns.
- `backtest_outcome_comparison`: derived diagnostic output with `authority_scope='diagnostic_non_promotion'`.

Canonical hierarchy for this wave:

`world.settlements VERIFIED` and `position_current` outrank `outcome_fact`; `backtest_outcome_comparison` is diagnostic non-promotion output and must not feed live truth or learning authority.

## Phase 1 Boundary Selection

Candidate boundaries:

| Boundary | Live-money relevance | Material values | Downstream consumers | Stale/legacy bypass | Scoped repair |
|---|---|---|---|---|---|
| `outcome_fact` -> trade-history audit actual fields | Can corrupt replay/report/learning interpretation of trade outcome/PnL | `outcome`, `pnl`, `settled_at`, `decision_snapshot_id` | `backtest_outcome_comparison`, CLI/report readers | `truth_source="trade_history"` hid the legacy source | yes, admitted |
| `outcome_fact` -> economics readiness | Future promotion-grade economics could treat legacy outcome rows as substrate | `decision_snapshot_id`, `outcome` | `src/backtest/economics.py` | always blocked by tombstone today | yes, separately admitted |
| `outcome_fact` -> diagnostic scripts | Operator reports can show table count as health | row counts | `audit_replay_fidelity.py`, truth-surface scripts | counts may look like authority | inspect/record only |

Selected boundary: `outcome_fact` -> trade-history diagnostic replay/report output. It is the highest-risk admitted boundary because it materializes actual trade outcome/PnL in a derived reporting DB.

## Phase 2 Material Value Lineage

| Value | Real object denoted | Origin | Authority/evidence | Unit/side | Time basis | Transform | Persistence | Consumers | State |
|---|---|---|---|---|---|---|---|---|---|
| `position_current.position_id` | canonical position identity | `position_current` | canonical DB read model | position id | lifecycle projection time | subject resolution | trade DB | replay audit | preserved |
| `position_current.temperature_metric` | high/low physical quantity | `position_current` | canonical DB read model | high/low | position lifecycle | used to filter settlement | none | settlement match | preserved |
| `world.settlements.settlement_value` | verified WU/HKO/CWA settlement observation value | `world.settlements` | `authority='VERIFIED'` | city unit | settlement time/date | bin scoring via `SettlementSemantics` | backtest diagnostic row | replay/report | preserved |
| `outcome_fact.outcome` | legacy recorded trade win/loss projection | `outcome_fact` | legacy lifecycle projection, no settlement authority | boolean outcome | legacy settled_at if present | diagnostic comparison only | backtest diagnostic row | replay/report | repaired |
| `outcome_fact.pnl` | legacy realized PnL projection | `outcome_fact` | legacy lifecycle projection, no fill/settlement provenance | USD | legacy settled_at if present | diagnostic comparison only | backtest diagnostic row | replay/report | repaired |
| `outcome_fact.decision_snapshot_id` | link to decision-time hypothesis | `outcome_fact` | legacy link field | id | decision time | eligibility gate | evidence JSON | replay/report | repaired |
| `backtest_outcome_comparison.actual_*` | diagnostic actual-trade comparison fields | `run_trade_history_audit` | `diagnostic_non_promotion` | outcome/USD | replay run time | explicit legacy source tagging | `zeus_backtest.db` | CLI/report readers | repaired |

UNKNOWN: `outcome_fact` rows in legacy live DBs may have mixed writer provenance. This wave does not relabel or backfill them.

## Phase 3 Findings

W15-F1 (S1): `run_trade_history_audit()` wrote `actual_trade_outcome` and `actual_pnl` from `outcome_fact` while labeling the row `truth_source="trade_history"`. The actual fields changed from legacy lifecycle projection to apparent trade-history fact at the replay/report boundary. This can corrupt diagnostic reports or any learning/report reader that keys off the backtest row rather than the `authority_scope`.

W15-F2 (S1): `outcome_fact` rows with no `decision_snapshot_id` or no `settled_at` could still supply actual outcome/PnL. That lets a row without decision-time linkage or settlement-time basis become an actual trade comparison value.

W15-F3 (S1): the trade-history audit summary did not declare the source/evidence class/learning eligibility of its actual-trade fields. The table had `diagnostic_non_promotion`, but field-level actual outcome/PnL semantics were implicit.

W15-F4 (S2): `check_economics_readiness()` treated legacy `outcome_fact` rows with `outcome` and `decision_snapshot_id` as sufficient resolution-matched outcome substrate. The engine is tombstoned today, but the readiness function is a future promotion gate and must fail closed until field-level settlement authority/evidence/learning eligibility exists.

W15-F5 (S1, critic): `outcome_fact.decision_snapshot_id` was initially only checked for presence. A stale row for the same position with a different non-empty decision snapshot could still become actual trade outcome/PnL. This attaches a legacy lifecycle projection to the wrong decision-time hypothesis.

W15-F6 (S3, critic/topology compatibility): the direct `tests/test_backtest_outcome_comparison.py` relationship antibody was stale after VERIFIED settlement filtering and was not initially admitted by the relevant topology profile.

W15-F7 (S2, topology compatibility): the relevant test files for trade-history subject identity/comparison are registered in test topology but not admitted by the replay-fidelity profile. This forced verification into `tests/test_run_replay_cli.py` until the object settlement profile was repaired.

## Phase 4 Repair

Restored invariant: a diagnostic replay row may compare verified settlement outcome against legacy `outcome_fact`, but it must preserve the field-level source, evidence class, authority scope, and learning/promotion ineligibility; unlinked legacy rows must fail closed to `trade_unresolved` rather than becoming actual trade evidence.

Code repair:
- Added explicit constants for legacy outcome fact diagnostic source/evidence class.
- Added `_diagnostic_outcome_fact_projection()` to gate `outcome_fact` actual fields on both `decision_snapshot_id` and `settled_at`.
- Changed trade-history output `truth_source` to `verified_settlement_vs_legacy_outcome_fact`.
- Added field-level evidence JSON: source, evidence class, authority scope, learning eligibility, promotion eligibility, linkage status.
- Outcome projection now requires `outcome_fact.decision_snapshot_id` to equal `position_current.decision_snapshot_id`; mismatch fails closed with `outcome_fact_decision_snapshot_mismatch`.
- Count `n_actual_traded` only when actual outcome passes the diagnostic eligibility gate.
- `check_economics_readiness()` now rejects legacy `outcome_fact` as promotion substrate unless authority columns exist and rows explicitly declare verified settlement authority, settlement evidence class, and learning eligibility.
- Topology repair: object settlement authority cutover profile now admits `tests/test_backtest_outcome_comparison.py` as a direct relationship antibody.

Relationship tests:
- `test_trade_history_audit_labels_outcome_fact_as_legacy_non_promotion`
- `test_trade_history_audit_rejects_unlinked_outcome_fact_as_actual_trade_evidence`
- `test_trade_history_audit_rejects_snapshot_mismatched_outcome_fact`
- Adjacent fixture repair: `tests/test_backtest_settlement_value_outcome.py` now marks settlement fixtures as VERIFIED only when the row can satisfy the authority trigger; the null-winning-bin case now proves fail-closed exclusion rather than invalid VERIFIED mutation.
- Direct relationship fixture repair: `tests/test_backtest_outcome_comparison.py` now seeds intended settlement comparison rows as VERIFIED and carries freshness headers.
- Economics readiness test: `test_economics_readiness_does_not_accept_legacy_outcome_fact_as_resolution_authority`.

## Phase 5 Verification

Initial focused checks:
- `python3 -m py_compile src/engine/replay.py tests/test_run_replay_cli.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_run_replay_cli.py -k 'trade_history_audit'` -> `3 passed, 19 deselected`.
- Process-local sklearn stub: full `pytest -q -p no:cacheprovider tests/test_run_replay_cli.py` -> `22 passed`.
- `pytest -q -p no:cacheprovider tests/test_replay_time_provenance.py -k 'diagnostic or authority_scope or snapshot_only or replay_context'` -> `10 passed`.
- `pytest -q -p no:cacheprovider tests/test_backtest_settlement_value_outcome.py -k 'trade_history_audit or wu_sweep or outcome'` -> `8 passed`.
- `python3 -m py_compile src/backtest/economics.py tests/test_backtest_skill_economics.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_backtest_skill_economics.py -k 'economics_readiness or economics_tombstone'` -> `8 passed, 12 deselected`.
- Full `pytest -q -p no:cacheprovider tests/test_backtest_skill_economics.py` -> `20 passed`.
- `python3 -m py_compile src/engine/replay.py src/backtest/economics.py tests/test_run_replay_cli.py tests/test_backtest_settlement_value_outcome.py tests/test_backtest_skill_economics.py` -> pass.
- Static grep for forbidden trade-history truth promotion found no remaining `truth_source="trade_history"` or direct `actual_* = outcome_fact` assignment in `src/engine/replay.py`; remaining `FROM outcome_fact` reads are subject inventory and gated diagnostic projection.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave15/PLAN.md` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --schema` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- `python3 scripts/digest_profiles_export.py --check` -> `OK: architecture/digest_profiles.py matches YAML`.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_settlement_authority_cutover_routes_to_wave5_profile tests/test_digest_profile_matching.py::test_phase5_economics_readiness_routes_to_phase5_profile` -> `2 passed`.
- `git diff --check` -> pass.
- Critic first review (`019dfb0e-ac09-75c1-82a9-ec9b8bffd2bf`) -> `REVISE`.
  - Finding S1: `outcome_fact.decision_snapshot_id` only checked for presence, not equality with `position_current.decision_snapshot_id`.
  - Finding S3: direct trade-history relationship test fixture lacked VERIFIED settlement authority.
- Post-REVISE verification:
  - `python3 -m py_compile src/engine/replay.py tests/test_run_replay_cli.py tests/test_backtest_outcome_comparison.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` -> pass.
  - `pytest -q -p no:cacheprovider tests/test_run_replay_cli.py -k 'trade_history_audit'` -> `4 passed, 19 deselected`.
  - `pytest -q -p no:cacheprovider tests/test_backtest_outcome_comparison.py` -> `7 passed`.
  - `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_settlement_authority_cutover_routes_to_wave5_profile` -> `1 passed`.
  - Process-local sklearn stub full `tests/test_run_replay_cli.py` -> `23 passed`.
  - Full `tests/test_backtest_skill_economics.py` -> `20 passed`.
  - `tests/test_backtest_settlement_value_outcome.py -k 'trade_history_audit or wu_sweep or outcome'` -> `8 passed`.
  - `tests/test_replay_time_provenance.py -k 'diagnostic or authority_scope or snapshot_only or replay_context'` -> `10 passed`.
  - Digest profile pair tests -> `2 passed`.
  - Freshness metadata initially failed for `tests/test_backtest_outcome_comparison.py`; after adding Lifecycle/Purpose/Reuse headers, `--freshness-metadata` -> `topology check ok`.
- Critic second review (`019dfb0e-ac09-75c1-82a9-ec9b8bffd2bf`) -> `APPROVE`.
  - Confirmed prior S1 fixed: `outcome_fact` actual fields require matching `position_current.decision_snapshot_id`.
  - Confirmed prior S3 fixed: direct relationship fixture now seeds VERIFIED settlement authority.
  - Critic reran `tests/test_run_replay_cli.py -k 'trade_history_audit'` -> `4 passed`; `tests/test_backtest_outcome_comparison.py` -> `7 passed`.

Pending before Wave15 close: none.
