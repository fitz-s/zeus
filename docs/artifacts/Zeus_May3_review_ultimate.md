## 0. Package README / How to use this output

This package is an implementation tribunal, not a prose summary. It is organized so three audiences can use it without rediscovering the system:

**Human reviewers** should read Sections 1, 6, 7, 9, 10, 18, 19, and 21 first. Those sections state the live-money verdict, cross-validated claim statuses, official Polymarket venue facts, staged severity taxonomy, critical blocker repairs, hidden branches, roadmap, and acceptance gates.

**Codex/local agents** should use Section 20. Each P0–P17 prompt is paste-ready and includes read-first files, allowed files, forbidden files, invariants, tasks, tests, commands, closeout evidence, rollback, and not-now constraints. The prompts intentionally forbid opportunistic refactors.

**Maintainers/operators** should use Sections 16, 17, and 21 before any live-money promotion. Those sections define migration sequencing, test/CI gates, cohort rules, and the conditions for corrected shadow, corrected live entry, automated exit, corrected P&L, strategy promotion, authoritative docs, and cleanup.

Evidence basis:

* I ingested both uploaded dossiers as full review dossiers:

  * **Review A:** `ZEUS REALITY-SEMANTICS ASYMMETRY AUDIT`.
  * **Review B:** `ZEUS PLAN-PRE5 ULTRA REVIEW AND REALITY-SEMANTICS REPAIR PACKET`.
* I validated against the current GitHub branch surfaced by PR #37. The GitHub branch view identifies the active tree as `live-unblock-ws-snapshot-2026-05-01`, and the README/runtime map shows the engine pipeline and entry points for the Zeus trading system. ([GitHub][1])
* Direct local `git clone`/pytest were not completed because the container DNS could not resolve GitHub. Repo validation here is therefore from GitHub web/raw file inspection plus official Polymarket/CLOB documentation. Any item that needs local grep across the full repo, migration dry-run, DB introspection, or pytest execution is marked `REVIEW_REQUIRED`.
* I did not rely on commit messages as evidence. Code, contracts, docs, schemas, tests, and official venue documentation are the evidence surfaces.
* Official Polymarket/CLOB venue facts are cited in Section 7 and used as the external reality constraint throughout.

The governing target remains:

> Zeus must prove that a trade refers to the same real-world economic object across selection → statistical/FDR hypothesis → posterior belief → market-prior use → executable quote/cost basis → Kelly sizing → order policy → immutable final intent → Polymarket token/order submission → command journal → fill/cancel lifecycle → position lot → held-token monitor → economic exit quote → settlement/redeem → persistence → replay → report → promotion.

A type-correct field is not enough. A scalar that changes physical meaning across modules is semantically false even if the code runs.

---

## 1. Final executive verdict

### Live-entry verdict

**New corrected live entry is not safe.** The current code contains strong contract seeds, but corrected live money is still bypassable through legacy or compatibility surfaces. The highest-risk confirmed chain is:

`MarketAnalysis/BinEdge scalar` → `evaluator raw entry_price wrapped as ExecutionPrice(implied_probability)` → `.with_taker_fee()` changes it into `fee_adjusted` → Kelly accepts it → executor/cycle can still construct or reprice a legacy `ExecutionIntent` → compatibility/legacy venue paths remain live-reachable unless every route is explicitly sealed.

Repo evidence:

* `BinEdge` still carries `p_market`, `entry_price`, `vwmp`, `p_posterior`, `edge`, and related fields in one object. ([GitHub][2])
* `MarketAnalysis.find_edges()` still populates `entry_price` and `vwmp` from market scalars; the main `buy_no` path has improved to native NO quote when available, but the carrier remains semantically overloaded. ([GitHub][3])
* `ExecutionPrice.assert_kelly_safe()` correctly rejects `implied_probability`, but `with_taker_fee()` converts an implied-probability price into `price_type="fee_adjusted"`; evaluator calls that path before Kelly, making a raw scalar Kelly-safe by type mutation. ([GitHub][4])
* `cycle_runtime` builds corrected cost/hypothesis/final intent only for specific marketable immediate cases; passive/GTC paths become shadow/unsupported, and the object is attached after selection rather than being a universal pre-submit authority. ([GitHub][5])
* `executor.py` still contains legacy `create_execution_intent` / `execute_intent` surfaces and imports `compute_native_limit_price`; `execute_final_intent` exists but is not proven universal. ([GitHub][6])

**Classification:** `LIVE_BLOCKER`.

### Corrected-shadow verdict

**Corrected shadow can be permitted only as non-submitting diagnostic/shadow after P0/P1 gates.** The repo now has meaningful contracts:

* `MarketPriorDistribution` separates `model_only` and corrected prior modes from legacy raw VWMP quote prior. ([GitHub][7])
* `ExecutableMarketSnapshotV2` pins condition/question/token/tick/min/fee/neg-risk/depth/hash/freshness facts. ([GitHub][8])
* `ExecutableCostBasis`, `ExecutableTradeHypothesis`, and `FinalExecutionIntent` exist and validate snapshot/cost hashes, order policy, final limit, depth, fee math, tick/min, and slippage. ([GitHub][9])

But shadow must be explicitly segregated from corrected live economics and promotion evidence. It must not write corrected P&L.

**Classification:** `PROMOTION_BLOCKER` until cohort rules and report gates are universal.

### Existing open-position verdict

**All existing open positions require a state census before automated action.** Current `Position` has added fields for `entry_cost_basis_id/hash`, `entry_economics_authority`, `fill_authority`, `pricing_semantics_version`, submitted/fill shares, and corrected eligibility, but legacy fallback properties still derive effective shares and cost basis from `shares`, `entry_price`, `size_usd`, or `cost_basis_usd` when fill authority is absent. ([GitHub][10])

Open positions must be classified:

* `legacy_price_probability_conflated`;
* `corrected_shadow_no_submit`;
* `corrected_submit_unknown_fill`;
* `corrected_fill_authoritative`;
* `chain_only_quarantined`;
* `exit_in_flight`;
* `settlement/redeem REVIEW_REQUIRED`.

**Classification:** `LIVE_BLOCKER` for automated exit unless held token, fresh SELL quote, fill authority, and lifecycle state are proven.

### Reporting/promotion verdict

**Reporting is partially safer than entry, but not promotion-complete.** There are real improvements:

* `profit_validation_replay.py` declares itself diagnostic-only and hard-fails mixed pricing cohorts for its replay surface. ([GitHub][11])
* `equity_curve.py` hard-fails mixed cohorts and marks `promotion_grade` only for corrected executable semantics. ([GitHub][12])
* `test_backtest_skill_economics.py` includes hard-fail tests for mixed cohorts, incomplete corrected rows, economics tombstone, and skill/economics separation. ([GitHub][13])

However, universal report/export/promotion policy is not proven across every report, DB view, migration, and dashboard. Historical rows cannot be backfilled into corrected executable economics without point-in-time executable snapshot/depth/fill facts.

**Classification:** `REPORT_BLOCKER` and `PROMOTION_BLOCKER`.

### Docs/authority verdict

**Docs and AGENTS are stale or partially contradictory relative to corrected semantics.** Examples:

* Root authority docs still describe α-weighted market fusion as part of the probability chain. ([GitHub][14])
* README still states `P_posterior = α × P_cal + (1 - α) × P_market`, which is unsafe unless explicitly legacy or estimator-scoped. ([GitHub][1])
* `src/execution/AGENTS.md` says “jump to ask for guaranteed fill,” which conflicts with immutable executable cost-basis semantics and venue order-type reality. ([GitHub][15])

**Classification:** `DOCS_AUTHORITY_BLOCKER` until runtime/tests/gates back the docs.

### Highest-risk unresolved unknowns

| Unknown                                                                                            | Why it matters                                                                                            | Status                             |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| Full `cycle_runner` → `cycle_runtime` → `executor` live dispatch reachability                      | Determines whether legacy `execute_intent` can still submit under corrected/live flags                    | `REVIEW_REQUIRED`                  |
| Whether adapter `submit_limit_order()` compatibility helper can reach live submit in active config | Helper fabricates `legacy:{token_id}`, collapsed YES/NO identity, and condition/question placeholders     | `LIVE_BLOCKER` / `REVIEW_REQUIRED` |
| Current production DB state and open positions                                                     | Old rows may be legacy, corrected-shadow, partial-fill, unknown-fill, or chain-only quarantine            | `LIVE_BLOCKER`                     |
| Full migration set and schema drift                                                                | `Position` has fields, but trade_decisions/probability/selection/report views need universal cohort law   | `REVIEW_REQUIRED`                  |
| CI actually runs semantic grep gates                                                               | Existing tests include some antibodies, but local CI execution was not verified                           | `REVIEW_REQUIRED`                  |
| Settlement/redeem payout lifecycle                                                                 | Harvester has fill-derived economics checks, but redeem and payout confirmation are not proven end-to-end | `REVIEW_REQUIRED`                  |

### One final decision sentence

**Freeze all new corrected live entries, allow only explicitly non-submitting corrected shadow after P0/P1, census open state before automated exit, and implement P0–P17 in order before any strategy promotion or corrected P&L claim.**

---

## 2. Full dossier preservation map

### Review A section map — `ZEUS REALITY-SEMANTICS ASYMMETRY AUDIT`

| Review A section                                | Preserved content                                                                                                                                                     |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Executive verdict                            | Live-blocking semantic risk; corrected contracts exist but end-to-end proof missing; raw scalar paths remain.                                                         |
| 2. Reconstructed real problem                   | Same-object economic identity across selection, belief, prior, quote, sizing, order, submission, lifecycle, exit, settlement, persistence, replay, report, promotion. |
| 3. Authority order / truth surfaces             | Venue reality > runtime code > persistence/schema > tests/CI > docs/AGENTS > unresolved uncertainty.                                                                  |
| 4. Top 10 hidden asymmetry findings             | ZRSA-01 through ZRSA-10 preserved below.                                                                                                                              |
| 5. Semantic aliasing table                      | `p_market`, `entry_price`, `vwmp`, `current_market_price`, `price`, `edge`, `BinEdge` multi-meaning risk.                                                             |
| 6. False symmetry register                      | `P_no = 1 - P_yes` valid in payoff/belief math, invalid as executable NO token cost; entry/exit and buy/sell asymmetries.                                             |
| 7. Time-state/lifecycle drift register          | Decision snapshot vs executable snapshot; submitted vs filled; settlement recorded vs redeemed.                                                                       |
| 8. Venue/API mismatch register                  | condition ID vs token ID; order types; BUY ask vs SELL bid; fees; tick/min/negative risk.                                                                             |
| 9. Monitor/exit symmetry audit                  | Corrected entry cannot use legacy exit; held-token SELL quote required.                                                                                               |
| 10. Backtest/reporting evidence integrity audit | Historical rows cannot become corrected executable economics without snapshot/depth/fill facts.                                                                       |
| 11. Required invariant test suite               | Entry/quote/prior/Kelly/executor/exit/report/backtest tests preserved in Section 17.                                                                                  |
| 12. Minimal repair packet                       | Live freeze, corrected contracts, final intent, native NO quote, exit quote, persistence and report gates.                                                            |
| 13. Codex handoff packet                        | Implementation-first prompts preserved and expanded into P0–P17.                                                                                                      |
| 14. Not-now list                                | Preserved and merged in Section 22.                                                                                                                                   |
| 15. Known truth vs unresolved uncertainty       | Preserved in Sections 5, 6, and 18.                                                                                                                                   |
| 16. Main-thread self-check                      | Preserved in Section 23.                                                                                                                                              |

### Review B section map — `ZEUS PLAN-PRE5 ULTRA REVIEW AND REALITY-SEMANTICS REPAIR PACKET`

| Review B section                                                | Preserved content                                                                          |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 1. Real problem reconstruction                                  | Same-object identity; four-plane separation; scalar aliasing.                              |
| 2. Authority order / truth surfaces                             | Same authority hierarchy as A, with code/schema/tests/docs split.                          |
| 3. Known truth vs unresolved uncertainty                        | Current PR#37 partial fixes; contracts exist but bypassable; local clone/tests unresolved. |
| 4. Required final deliverable sections                          | This final package uses the user-requested 0–23 structure.                                 |
| 5. Not-now list                                                 | Preserved and merged in Section 22.                                                        |
| 6. Executive architecture verdict                               | Not live-safe; shadow/report partial; live freeze; additive-first migration.               |
| 7. Ultra Review findings by 10 dimensions                       | U1–U10 claims preserved in Section 3.                                                      |
| 8. Cross-dimensional root cause map                             | Preserved in Section 8.                                                                    |
| 9. Impact radius map                                            | Preserved through stage taxonomy and claim matrix.                                         |
| 10. Four-plane reality model                                    | Preserved in Sections 8 and 11.                                                            |
| 11. Target object model                                         | Preserved and expanded in Section 11.                                                      |
| 12. ADR decisions                                               | Preserved in Sections 9, 11, 12, 16, 17, 21.                                               |
| 13. Phased repair roadmap                                       | Preserved and expanded into P0–P17.                                                        |
| 14. Migration and persistence plan                              | Preserved in Section 16.                                                                   |
| 15. Test and CI gate plan                                       | Preserved in Section 17.                                                                   |
| 16. Live safety and operator policy                             | Preserved in Sections 9, 10, 18, 21.                                                       |
| 17. Open-position / monitor / exit policy                       | Preserved in Sections 10, 11, 16, 18, 21.                                                  |
| 18. Settlement / reconciliation / city_id / timezone policy     | Preserved in Sections 13 and 16.                                                           |
| 19. Reporting / backtest / promotion policy                     | Preserved in Section 12.                                                                   |
| 20. Orphaned / over-engineering / branch-explosion cleanup plan | Preserved in Section 15.                                                                   |
| 21. Performance and staleness risk plan                         | Preserved in Section 14.                                                                   |
| 22. Hidden branch register                                      | Preserved and expanded in Section 18.                                                      |
| 23. Blast radius / rollback / monitor points                    | Preserved in Section 19.                                                                   |
| 24. Codex execution packet                                      | Preserved and expanded in Section 20.                                                      |
| 25. Final verification loop                                     | Preserved in Section 23.                                                                   |

### Review A findings preserved

| ID      | Finding                                                                      |
| ------- | ---------------------------------------------------------------------------- |
| ZRSA-01 | Legacy entry path `BinEdge` scalar feeds cost/Kelly/limit.                   |
| ZRSA-02 | FDR-selected hypothesis identity omits executable token/snapshot/cost basis. |
| ZRSA-03 | `LIMIT_MAY_TAKE_CONSERVATIVE` policy contradicts venue order types.          |
| ZRSA-04 | Corrected entry cannot prove corrected exit symmetry.                        |
| ZRSA-05 | `market_analysis_family_scan` can revive YES-complement NO pricing fallback. |
| ZRSA-06 | Persistence venue facts cannot prove decision-row semantic cohort.           |
| ZRSA-07 | Corrected contracts exist but live path usage not proven end-to-end.         |
| ZRSA-08 | Fee semantics not consistently bound to order policy / maker-taker reality.  |
| ZRSA-09 | Decision-time and submit-time snapshots drift under same selection identity. |
| ZRSA-10 | Authority docs contain stale venue/economic semantics.                       |

### Review B findings preserved

| Group                         | IDs preserved            |
| ----------------------------- | ------------------------ |
| Original blocker set          | F-01 through F-10.       |
| U1 Economic pipeline          | U1-A01 through U1-A05.   |
| U2 Semantic naming            | U2-S01 through U2-S05.   |
| U3 Workflow/control           | U3-W01 through U3-W04.   |
| U4 Orphans/compatibility      | U4-O01 through U4-O04.   |
| U5 Over-engineering           | U5-E01 through U5-E03.   |
| U6 Architecture decomposition | U6-G01 through U6-G04.   |
| U7 Data ownership             | U7-D01 through U7-D04.   |
| U8 Error handling             | U8-F01 through U8-F04.   |
| U9 Performance/staleness      | U9-P01 through U9-P03.   |
| U10 Tests/verification        | U10-T01 through U10-T04. |

### Tests preserved from both dossiers

| Category             | Tests preserved                                                                                                                                                                                                                                                                                                                                            |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Four-plane entry     | `test_executable_quote_change_affects_cost_not_posterior`; `test_market_prior_change_affects_posterior_not_token_snapshot`; `test_corrected_mode_cannot_size_from_raw_entry_price`; `test_corrected_mode_cannot_size_from_bin_edge_entry_price`.                                                                                                           |
| Kelly                | `test_kelly_requires_executable_cost_basis`; `test_execution_price_fee_adjusted_name_not_deducted`; `test_implied_probability_with_fee_cannot_become_kelly_safe_in_corrected_mode`; rewrite of `test_evaluator_always_uses_fee_adjusted_size`.                                                                                                             |
| Executor             | `test_corrected_executor_rejects_missing_final_limit_or_cost_basis`; `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp`; `test_legacy_execution_intent_live_rejected_without_operator_opt_in`; `test_compatibility_envelope_rejected_in_live_submit`; `test_corrected_executor_requires_cost_basis_hash_snapshot_hash_envelope_hash`. |
| Buy-NO               | `test_buy_no_requires_native_no_quote_no_complement_fallback`; `test_family_scan_buy_no_without_native_quote_is_diagnostic_only`; `test_buy_no_live_path_cannot_use_one_minus_yes_quote`.                                                                                                                                                                  |
| FDR/identity         | `test_fdr_hypothesis_id_changes_when_executable_snapshot_or_cost_basis_changes`; `test_snapshot_change_after_selection_requires_reject_recompute_or_amendment`; `test_order_policy_change_changes_cost_basis_identity`.                                                                                                                                    |
| Order policy         | `test_order_policy_order_type_mapping_is_explicit`; `test_limit_may_take_not_collapsed_with_post_only_or_fok`; `test_post_only_market_cross_rejected_before_submit`.                                                                                                                                                                                       |
| Exit                 | `test_exit_requires_fresh_held_token_best_bid_unless_derisk_override`; `test_corrected_entry_cannot_use_legacy_exit_fallback`; `test_buy_no_exit_uses_best_bid_not_vwmp`; `test_exit_context_missing_best_bid_fails_closed`; `test_manual_force_exit_tagged_excluded_from_corrected_evidence`.                                                             |
| Lifecycle/fills      | `test_partial_fill_then_cancel_keeps_remaining_reviewable`; `test_unknown_fill_status_blocks_corrected_pnl`; `test_submit_unknown_side_effect_state_not_reported_as_loss_or_fill`; `test_position_lot_authority_required_for_corrected_pnl`.                                                                                                               |
| Venue identity       | `test_venue_command_market_id_not_token_id_for_live_entry`; `test_venue_command_market_id_not_token_id_for_live_exit`; `test_live_envelope_requires_condition_question_yes_no_token_identity`; `test_negative_risk_metadata_present_or_policy_blocks_live`.                                                                                                |
| Reporting/backtest   | `test_reports_hard_fail_mixed_pricing_semantics_cohorts`; `test_backtest_without_depth_snapshot_excluded_from_corrected_economics`; `test_profit_replay_is_diagnostic_only`; `test_skill_backtest_cannot_promote_economics`; `test_no_historical_corrected_backfill_without_depth_snapshot_fill`.                                                          |
| Settlement/city/time | `test_is_settled_split_into_statuses`; `test_city_id_roundtrip_no_duplicate_transform`; `test_high_low_metric_identity_required`; `test_target_local_date_not_utc_day_guess`; `test_redeem_confirmed_distinct_from_settlement_recorded`.                                                                                                                   |
| Static gates         | Full list preserved in Section 17.                                                                                                                                                                                                                                                                                                                         |

### Hidden branches preserved

All hidden branches requested by the user are preserved in Section 18, including legacy open positions, corrected entry with legacy exit, complement executable price, raw quote as market prior, partial/unknown fills, command/submit/materialization crashes, stale quote, executor repricing, FDR drift, market/token collapse, missing condition/question, compatibility envelope reachability, mixed cohorts, diagnostic replay complement, order-policy enum conflation, settlement/redeem conflation, docs-only repair, and CI/static gate gaps.

### Migration/reporting policies preserved

| Policy                       | Preserved disposition                                                                                                                  |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Additive-first migration     | Required. No destructive migration before census.                                                                                      |
| No fake corrected backfill   | Required. Old rows remain legacy/model-only unless snapshot/depth/fill facts exist.                                                    |
| Cohort hard-fail             | Required for every promotion/report surface. Warning-only is forbidden.                                                                |
| Corrected row eligibility    | Requires executable snapshot, cost basis hash, final intent/envelope, fill authority, settlement/exit authority as applicable.         |
| Report segregation           | Legacy diagnostic, model-only diagnostic, corrected shadow, corrected live executable must not be aggregated silently.                 |
| Backtest economics tombstone | Keep tombstoned until point-in-time venue data, executable depth, fee/tick/min/negative-risk, fills, and settlement facts are present. |
| Promotion evidence           | Corrected live executable cohort only; model skill and diagnostic replay are not sufficient.                                           |

### Not-now constraints preserved

Merged fully in Section 22. The most important not-now constraints are:

* no full fill-probability/queue-priority/adverse-selection model first;
* no negative-risk optimizer first;
* no `yes_family_devig_v1` live market-prior promotion;
* no corrected historical economics without depth/snapshot/fill;
* no large parallel venue model;
* no ontology rewrite before money-path invariants;
* no automatic promotion from backtest ROI or model skill;
* no docs-only repair;
* no complement as executable NO price;
* no post-only/may-take/FOK/FAK policy collapse.

---

## 3. Material claim ledger

Status before repo validation for every row: `CLAIMED`. Duplicates are intentionally preserved here.

| Claim ID  | Source review | Source section                   | Original severity              | Claim summary                                                                   | Affected files/modules                                                                                           | Semantic plane                                          | Failure mode                                                                                      | Proposed fix                                                                                      | Required test/gate                                                                                     |
| --------- | ------------- | -------------------------------- | ------------------------------ | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| A-ZRSA-01 | Review A      | Top 10 hidden asymmetry findings | Critical / live-blocking       | Legacy entry path carries `BinEdge` scalar into cost/Kelly/limit.               | `src/types/market.py`, `src/strategy/market_analysis.py`, `src/engine/evaluator.py`, `src/execution/executor.py` | Belief/prior/quote/cost/execution                       | `entry_price`, `p_market`, `vwmp` become executable cost authority without executable snapshot.   | Kelly consumes `ExecutableEntryCostBasis`; executor consumes `FinalExecutionIntent`.              | Quote-change affects cost/limit/Kelly, not posterior; corrected executor rejects missing final intent. |
| A-ZRSA-02 | Review A      | Top 10                           | Critical                       | FDR hypothesis identity omits executable token/snapshot/cost basis.             | `src/strategy/selection_family.py`, `src/engine/cycle_runtime.py`, contracts                                     | Statistical/FDR identity → executable economic identity | Selected hypothesis can materialize as different token/quote/order policy.                        | Bind executable snapshot/cost/order policy to FDR materialization or create amended hypothesis.   | Snapshot/cost/order-policy changes after FDR require reject/recompute/amend.                           |
| A-ZRSA-03 | Review A      | Top 10                           | Critical                       | `LIMIT_MAY_TAKE_CONSERVATIVE` policy contradicts venue order types.             | `src/contracts/execution_intent.py`, executor, adapter                                                           | Order policy / venue                                    | Policy says may-rest bounded limit but contract forces FOK/FAK or conflates with post-only.       | Split order policy names and mapping.                                                             | Explicit order-policy/order-type mapping test.                                                         |
| A-ZRSA-04 | Review A      | Top 10                           | Critical                       | Corrected entry cannot prove corrected exit symmetry.                           | `src/engine/monitor_refresh.py`, `src/execution/exit_lifecycle.py`, `src/execution/exit_triggers.py`, portfolio  | Exit quote/economics                                    | Exit can use `current_market_price`, posterior, VWMP, or fallback instead of held-token SELL bid. | `ExitExecutableQuote` required for corrected economic exit.                                       | Exit requires fresh held-token best bid/depth; corrected entry cannot use legacy exit.                 |
| A-ZRSA-05 | Review A      | Top 10                           | High / live blocker for buy-NO | `market_analysis_family_scan` can revive YES-complement NO executable price.    | `src/strategy/market_analysis_family_scan.py`                                                                    | Quote/cost                                              | `1 - YES quote` returns as buy-NO market price in full-family scan.                               | Native NO quote required; complement diagnostic only.                                             | Buy-NO missing native quote fails closed.                                                              |
| A-ZRSA-06 | Review A      | Top 10                           | High / report blocker          | Persistence venue facts cannot prove decision-row semantic cohort.              | `src/state/db.py`, reports, replay, migrations                                                                   | Persistence/report                                      | Venue facts exist but row-level semantic version/cost/snapshot lineage not universal.             | Add semantic cohort fields and report hard-fails.                                                 | Mixed cohorts hard-fail; corrected rows require snapshot/cost/fill.                                    |
| A-ZRSA-07 | Review A      | Top 10                           | Critical                       | Corrected contracts exist but live path usage not proven end-to-end.            | Contracts, cycle runtime, executor, adapter                                                                      | Whole money path                                        | Downstream class existence mistaken for full path proof.                                          | Router rejects legacy live path unless explicitly legacy; corrected submit only via final intent. | Legacy `ExecutionIntent` live rejected; final-intent route required.                                   |
| A-ZRSA-08 | Review A      | Top 10                           | High                           | Fee semantics not consistently bound to order policy / maker-taker reality.     | `execution_price.py`, evaluator, cost basis, reports                                                             | Cost/fee                                                | Fee-adjusted price not tied to order type/fill maker/taker reality.                               | Cost basis stores fee source; realized fill stores maker/taker fee separately.                    | Fee policy changes cost basis, not belief.                                                             |
| A-ZRSA-09 | Review A      | Top 10                           | Critical                       | Decision-time and submit-time snapshots drift under same selection identity.    | `selection_family.py`, `cycle_runtime.py`, snapshot repo                                                         | FDR/executable identity                                 | Repricing after selection changes the economic object without identity change.                    | Executable snapshot hash and cost basis hash become identity fields.                              | Snapshot change after selection creates amended hypothesis or rejects.                                 |
| A-ZRSA-10 | Review A      | Top 10                           | Medium / docs blocker          | Authority docs stale venue/economic semantics.                                  | `README.md`, `AGENTS.md`, `src/*/AGENTS.md`, docs authority                                                      | Docs/agents                                             | Future agents reintroduce invalid semantics through docs.                                         | Docs rewritten only after tests/gates.                                                            | Static docs scan for “guaranteed fill” and raw α-weighted `P_market` language.                         |
| B-F01     | Review B      | Original F-01 disposition        | Critical                       | Kelly raw price laundering.                                                     | evaluator, `ExecutionPrice`, Kelly                                                                               | Cost/Kelly                                              | Raw `entry_price` converted to fee-adjusted EP and accepted.                                      | Corrected mode cannot size from raw `entry_price`.                                                | `test_corrected_mode_cannot_size_from_raw_entry_price`.                                                |
| B-F02     | Review B      | Original F-02                    | Critical                       | Executor recomputes limit from posterior/VWMP.                                  | executor, cycle runtime                                                                                          | Executor price authority                                | Executor/cycle can produce limit authority after selection.                                       | Executor validates final immutable intent only.                                                   | `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp`.                               |
| B-F03     | Review B      | Original F-03                    | High                           | Exit EV uses wrong economic value.                                              | monitor, exit triggers, portfolio                                                                                | Exit quote                                              | Sell value may use current price/VWMP/probability instead of held-token bid.                      | Held-token SELL bid required.                                                                     | `test_buy_no_exit_uses_best_bid_not_vwmp`.                                                             |
| B-F04     | Review B      | Original F-04                    | High                           | Monitor model-only/posterior refresh may degrade into legacy market-price use.  | monitor refresh, market fusion                                                                                   | Belief/quote                                            | Quote can feed posterior or native probability incorrectly.                                       | Model-only posterior and held quote separation.                                                   | Quote change not posterior change.                                                                     |
| B-F05     | Review B      | Original F-05                    | High / review-required         | Market identity drift: condition ID, Gamma ID, token ID collapse.               | data clients, venue command, DB, adapter                                                                         | Venue identity                                          | Token can masquerade as market/condition.                                                         | `MarketIdentity` contract; reject `market_id == token_id`.                                        | Live command market_id not token_id.                                                                   |
| B-F06     | Review B      | Original F-06                    | High                           | Compatibility envelope mitigated but not closed.                                | adapter, venue envelope, executor                                                                                | Venue submission                                        | Compatibility placeholder may reach submit.                                                       | `assert_live_submit_bound` enforced before live submit.                                           | Compatibility envelope rejected in live.                                                               |
| B-F07     | Review B      | Original F-07                    | High                           | Buy-NO complement fallback main/orphan split.                                   | market analysis, family scan                                                                                     | Quote/cost                                              | Main fixed, orphan fallback remains.                                                              | Quarantine family-scan complement.                                                                | Family-scan buy-NO missing native quote diagnostic only.                                               |
| B-F08     | Review B      | Original F-08                    | Critical                       | Order policy applied after sizing / not in cost identity.                       | cost basis, final intent, order policy                                                                           | Cost/order                                              | Order type changes cost but not selection identity.                                               | OrderPolicy in cost basis and hypothesis identity.                                                | Policy change changes cost_basis_id/hash.                                                              |
| B-F09     | Review B      | Original F-09                    | High                           | Position fallback impact reduced but still present.                             | portfolio, chain reconciliation                                                                                  | Lifecycle/fill                                          | Position can fall back to target size/entry price.                                                | PositionLot/FillAuthority authoritative.                                                          | Unknown fill blocks corrected P&L.                                                                     |
| B-F10     | Review B      | Original F-10                    | High                           | Reporting/backtest partially fixed but universal policy missing.                | profit replay, equity curve, reports, backtest                                                                   | Report/promotion                                        | Some report surfaces hard-fail; others unknown.                                                   | Universal `ReportingCohort` gate.                                                                 | All report paths fail mixed cohorts.                                                                   |
| B-U1-A01  | Review B      | U1 economic pipeline             | Critical                       | Evaluator violates Kelly contract by making raw implied probability Kelly-safe. | `src/engine/evaluator.py`, `execution_price.py`                                                                  | Kelly/cost                                              | Semantic laundering through `with_taker_fee`.                                                     | `ExecutableEntryCostBasis` as only corrected Kelly input.                                         | Corrected sizing rejects raw float.                                                                    |
| B-U1-A02  | Review B      | U1                               | Critical                       | Executor/cycle runtime price authority asymmetry.                               | cycle runtime, executor                                                                                          | Executor                                                | Repricing after selection mutates economic object.                                                | Immutable final intent before submit.                                                             | Executor no repricing grep + counterfactual test.                                                      |
| B-U1-A03  | Review B      | U1                               | High                           | Entry/exit asymmetry.                                                           | monitor, exit lifecycle/triggers                                                                                 | Exit                                                    | Exit path not as strict as entry.                                                                 | `ExitExecutableQuote` + fail closed.                                                              | Buy-NO exit best_bid tests.                                                                            |
| B-U1-A04  | Review B      | U1                               | High                           | Settlement vs fill asymmetry.                                                   | harvester, portfolio, chain reconciliation                                                                       | Settlement/fill                                         | Settlement P&L may use non-fill facts; reconciliation mutates entry economics.                    | Settlement uses PositionLot/fill authority only.                                                  | Corrected settlement requires fill authority.                                                          |
| B-U1-A05  | Review B      | U1                               | High                           | Market identity asymmetry.                                                      | adapter, DB, venue commands, clients                                                                             | Venue identity                                          | condition/token/question/gamma ID confusion.                                                      | `MarketIdentity`; reject live identity collapse.                                                  | `market_id != token_id` and condition/question required.                                               |
| B-U2-S01  | Review B      | U2 semantic naming               | Critical                       | `entry_price` means observed market, submitted limit, avg fill, cost basis.     | portfolio, trade decisions, reports                                                                              | Naming/data ownership                                   | P&L and sizing can use wrong entry fact.                                                          | Split fields and authorities.                                                                     | Entry authority matrix tests.                                                                          |
| B-U2-S02  | Review B      | U2                               | High                           | `fee_deducted` naming drift.                                                    | `ExecutionPrice`                                                                                                 | Fee semantics                                           | BUY fee is added/included, not deducted.                                                          | Rename/alias to `fee_adjusted` or `fee_included`.                                                 | Fee naming antibody.                                                                                   |
| B-U2-S03  | Review B      | U2                               | High                           | Lifecycle vocabulary drift.                                                     | db, portfolio, lifecycle manager, docs                                                                           | Lifecycle                                               | `pending_entry/active/entered/holding/pending_tracked` ambiguity.                                 | Canonical `LifecycleState`.                                                                       | State transition grammar tests.                                                                        |
| B-U2-S04  | Review B      | U2                               | High                           | `is_settled` / settlement status conflation.                                    | harvester, settlement, reports                                                                                   | Settlement/redeem                                       | Recorded settlement, payout eligibility, redeem confirmation conflated.                           | Split statuses.                                                                                   | Settlement status tests.                                                                               |
| B-U2-S05  | Review B      | U2                               | Medium                         | Timeout unit risk.                                                              | executor/config/tests                                                                                            | Operations                                              | Numeric timeouts without units.                                                                   | Unit-suffixed fields.                                                                             | Static timeout literal scan.                                                                           |
| B-U3-W01  | Review B      | U3 workflow                      | Critical                       | `FinalExecutionIntent` is conditional, not universal.                           | cycle runtime, executor                                                                                          | Final intent                                            | Some corrected decisions lack final immutable intent.                                             | Universal corrected intent or fail closed.                                                        | Corrected live rejects no final intent.                                                                |
| B-U3-W02  | Review B      | U3                               | Critical potential             | Compatibility envelope live-bound assertion not proven wired.                   | adapter, envelope, executor                                                                                      | Venue submit                                            | Placeholder envelope can submit unless live-bound assertion reached.                              | Assert before every live adapter submit.                                                          | Compatibility helper cannot live submit.                                                               |
| B-U3-W03  | Review B      | U3                               | High                           | Monitor quote failure returns `None` and can degrade exit.                      | monitor refresh                                                                                                  | Exit/monitor                                            | Missing quote can leave stale price or no fail-closed reason.                                     | Missing quote yields `REVIEW_REQUIRED` for corrected economics.                                   | Exit context missing quote fails closed.                                                               |
| B-U3-W04  | Review B      | U3                               | High                           | Command journal strong but lacks same-object proof.                             | venue command repo, envelope repo, snapshots                                                                     | Persistence/lifecycle                                   | Command/event/order/fill facts may not prove same cost/snapshot/envelope.                         | Hash join: final_intent/cost_basis/snapshot/envelope/order/fill.                                  | Same-object command journal test.                                                                      |
| B-U4-O01  | Review B      | U4 orphans                       | High                           | Family-scan buy-NO complement fallback.                                         | family scan                                                                                                      | Orphan/quote                                            | Legacy fallback can reenter.                                                                      | Delete/quarantine diagnostic only.                                                                | Static complement gate.                                                                                |
| B-U4-O02  | Review B      | U4                               | Critical potential             | V2 compatibility submit helper fabricates identity.                             | adapter                                                                                                          | Venue                                                   | `legacy:{token_id}`, `legacy-compat`, yes=no token.                                               | Adapter live rejects placeholders.                                                                | Live submit-bound assertion reachability.                                                              |
| B-U4-O03  | Review B      | U4                               | High                           | Legacy `compute_native_limit_price` corrected-adjacent.                         | executor                                                                                                         | Executor price authority                                | Corrected path can reuse legacy price computation.                                                | Static gate forbids in corrected executor.                                                        | Grep gate.                                                                                             |
| B-U4-O04  | Review B      | U4                               | Medium                         | Diagnostic replay complement/path approximations survive.                       | profit replay/backtest                                                                                           | Report/promotion                                        | Diagnostic output mistaken for evidence.                                                          | Label diagnostic-only and block promotion.                                                        | Promotion excludes diagnostic replay.                                                                  |
| B-U5-E01  | Review B      | U5 over-engineering              | High                           | `BinEdge` too central.                                                          | `types/market.py`, strategy/evaluator                                                                            | Architecture                                            | One object owns selection, belief, quote, cost, report fields.                                    | Shrink to selection hypothesis only.                                                              | Static gate against cost authority fields.                                                             |
| B-U5-E02  | Review B      | U5                               | Medium                         | Docs strong but tests/gates insufficient.                                       | docs/AGENTS/tests                                                                                                | Docs/agents                                             | Future agents trust docs over runtime gates.                                                      | Docs only after gates.                                                                            | Docs static scan.                                                                                      |
| B-U5-E03  | Review B      | U5                               | Medium                         | `ExecutableCostBasis` adequate; avoid large parallel venue model.               | contracts                                                                                                        | Architecture                                            | Overbuilding delays blockers.                                                                     | Use current contract seeds.                                                                       | Not-now guard.                                                                                         |
| B-U6-G01  | Review B      | U6 giant functions               | High                           | Cycle runtime repricing function too mixed.                                     | cycle runtime                                                                                                    | Architecture/control                                    | Snapshot capture, repricing, intent construction, mutation mixed.                                 | Extract pure builders.                                                                            | Unit tests per builder.                                                                                |
| B-U6-G02  | Review B      | U6                               | High                           | Executor legacy path giant and live-capable.                                    | executor                                                                                                         | Architecture/executor                                   | Hard to prove corrected path sealed.                                                              | Split legacy vs corrected APIs.                                                                   | Corrected API only final intent.                                                                       |
| B-U6-G03  | Review B      | U6                               | High                           | Monitor refresh mixed topology/probability/quote/logging.                       | monitor_refresh                                                                                                  | Architecture/staleness                                  | Missing quote/probability failures blurred.                                                       | Split quote, probability, exit context.                                                           | Missing authority fails closed.                                                                        |
| B-U6-G04  | Review B      | U6                               | High                           | Harvester mixes Gamma poll/settlement/learning/portfolio/report.                | harvester                                                                                                        | Settlement/report                                       | Broad settlement side effects hard to audit.                                                      | Split settlement facts, learning, redeem, report.                                                 | Settlement/redeem status tests.                                                                        |
| B-U7-D01  | Review B      | U7 data ownership                | Critical                       | Decision/edge mutable after FDR.                                                | cycle runtime, decision objects                                                                                  | FDR identity                                            | Selected decision mutated with new price/snapshot.                                                | Immutable selected hypothesis; amendments explicit.                                               | Snapshot change after selection test.                                                                  |
| B-U7-D02  | Review B      | U7                               | High                           | `Position` owns too many planes.                                                | portfolio                                                                                                        | Data ownership                                          | Runtime position owns belief/cost/fill/settlement/report facts.                                   | `PositionLot`, `EntryEconomicsAuthority`, `FillAuthority`.                                        | Fill-authority tests.                                                                                  |
| B-U7-D03  | Review B      | U7                               | High                           | Chain reconciliation mutates entry economics.                                   | chain_reconciliation                                                                                             | Lifecycle/fill                                          | Chain avg/cost overwrites entry_price/cost_basis.                                                 | Chain facts separated from entry cost; reconcile can correct lot only.                            | Chain mutation test.                                                                                   |
| B-U7-D04  | Review B      | U7                               | High                           | Snapshot ownership split.                                                       | snapshot repo, cost basis, command journal                                                                       | Same-object proof                                       | Snapshot hash not mandatory everywhere.                                                           | Make hash mandatory through cost_basis/final_intent/envelope/command/fill.                        | Hash propagation test.                                                                                 |
| B-U8-F01  | Review B      | U8 errors                        | High                           | Harvester broad exceptions can hide settlement/P&L inconsistency.               | harvester                                                                                                        | Error handling                                          | Settlement defects become warnings.                                                               | Typed errors and REVIEW_REQUIRED rows.                                                            | Broad-except money-path scan.                                                                          |
| B-U8-F02  | Review B      | U8                               | High                           | Monitor broad exceptions degrade authority.                                     | monitor_refresh                                                                                                  | Error handling                                          | Quote/prob failures silently fallback.                                                            | Missing authority fields explicit.                                                                | Monitor exception-to-REVIEW_REQUIRED test.                                                             |
| B-U8-F03  | Review B      | U8                               | High                           | Compatibility submit rejection can still build placeholder envelope.            | adapter                                                                                                          | Error handling/venue                                    | Rejected placeholder facts may be mistaken as live identity.                                      | Placeholder envelope excluded from live evidence.                                                 | Placeholder never corrected evidence.                                                                  |
| B-U8-F04  | Review B      | U8                               | Medium                         | Tests monkeypatch away cutoff/risk/heartbeat/collateral gates.                  | tests                                                                                                            | Verification                                            | Tests pass without proving live gate order.                                                       | Integration tests with gates active.                                                              | No-gate-monkeypatch promotion test.                                                                    |
| B-U9-P01  | Review B      | U9 performance                   | High                           | Snapshot freshness invalidated by slow post-selection.                          | snapshot, cycle, executor                                                                                        | Staleness/correctness                                   | Fresh at selection but stale at submit.                                                           | Submit deadline/freshness deadline in final intent.                                               | Stale by submit rejects.                                                                               |
| B-U9-P02  | Review B      | U9                               | High                           | Monitor hot path can stale due per-position work.                               | monitor_refresh                                                                                                  | Performance/staleness                                   | Slow batching makes quote stale.                                                                  | Batch orderbook/topology; telemetry.                                                              | Monitor age SLO tests.                                                                                 |
| B-U9-P03  | Review B      | U9                               | Medium                         | Backtest/replay DB tick-loop diagnostic only.                                   | scripts/backtest                                                                                                 | Performance/report                                      | Slow diagnostic mistaken for promotion.                                                           | Diagnostic label and promotion exclusion.                                                         | Diagnostic-only gate.                                                                                  |
| B-U10-T01 | Review B      | U10 tests                        | Critical                       | Tests prove wrong thing: fee-adjusted implied probability Kelly-safe.           | tests/test_execution_price.py                                                                                    | Verification                                            | Test encodes false assumption.                                                                    | Rewrite around cost basis authority.                                                              | Existing test should fail before fix.                                                                  |
| B-U10-T02 | Review B      | U10                              | High                           | Grep tests not semantic counterfactuals.                                        | tests/CI                                                                                                         | Verification                                            | Static gates alone miss runtime drift.                                                            | Pair grep gates with semantic tests.                                                              | Counterfactual tests.                                                                                  |
| B-U10-T03 | Review B      | U10                              | High                           | Command split tests prove event order, not same-object identity.                | tests/test_executor_command_split.py                                                                             | Verification/lifecycle                                  | Persist-before-submit does not prove cost/snapshot/envelope identity.                             | Add hash-chain tests.                                                                             | Same-object command/fill test.                                                                         |
| B-U10-T04 | Review B      | U10                              | High                           | Backtest/report tests improved but limited.                                     | tests/backtest/reports                                                                                           | Promotion                                               | Some report surfaces guarded, universal gate unknown.                                             | All reports share `ReportingCohort`.                                                              | Report registry gate.                                                                                  |

---

## 4. Review A ↔ Review B crosswalk

| Review A claim    | Review B related claim                                                                 | Relationship                            | Final merged interpretation                                                                                                                                                                                                     | What must be preserved                                                                                          |
| ----------------- | -------------------------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| A-ZRSA-01         | B-F01, B-U1-A01, B-U2-S01, B-U10-T01                                                   | Duplicate + expansion                   | Both reviews identify the same core live blocker: scalar `entry_price/p_market/vwmp` laundering into Kelly/executable cost. Review B expands with the exact `ExecutionPrice.with_taker_fee()` loophole and test false-positive. | Keep both the architectural aliasing diagnosis and the concrete evaluator/Kelly repair.                         |
| A-ZRSA-02         | B-U1-A02, B-F08, B-U7-D01, B-U7-D04                                                    | Expansion                               | Review A frames missing executable identity in FDR. Review B expands to final intent, order policy, snapshot hash, cost_basis hash, and decision mutation.                                                                      | Statistical hypothesis identity and executable economic hypothesis identity must be separate but linked.        |
| A-ZRSA-03         | B-F08, B-U3-W01, B-U1-A02                                                              | Expansion                               | Review A flags order-policy contradiction. Review B ties policy to cost basis and final intent.                                                                                                                                 | Split may-rest, post-only, immediate-sweep/FOK/FAK; policy changes cost identity.                               |
| A-ZRSA-04         | B-F03, B-F04, B-U1-A03, B-U3-W03, B-U6-G03                                             | Expansion + partial supersession        | Both flag exit asymmetry. Current repo has held-token quote improvements and tests, but corrected exit is not fully universal.                                                                                                  | Exit quote must be as strict as entry cost basis.                                                               |
| A-ZRSA-05         | B-F07, B-U4-O01                                                                        | Duplicate + current-code refinement     | Main `MarketAnalysis` buy-NO path is partially superseded by native NO quote, but `market_analysis_family_scan` fallback remains confirmed.                                                                                     | Preserve the orphan/hidden branch, not just the main path fix.                                                  |
| A-ZRSA-06         | B-F10, B-U3-W04, B-U7-D04, B-U10-T04                                                   | Expansion                               | Review A says persistence cannot prove semantic cohort. Review B adds command/envelope hash-chain and report cohort law.                                                                                                        | Same-object proof must join decision, snapshot, cost_basis, final_intent, envelope, command, fill, lot, report. |
| A-ZRSA-07         | B-U3-W01, B-U3-W02, B-U4-O02, B-U6-G02                                                 | Duplicate + expansion                   | Contracts exist; not enough. Review B expands bypasses: legacy executor and compatibility envelope.                                                                                                                             | Downstream contract existence is not upstream live proof.                                                       |
| A-ZRSA-08         | B-U2-S02, B-F08, B-U1-A04                                                              | Expansion                               | Review A flags fee semantics; Review B identifies `fee_deducted` naming drift and fill-vs-submitted fee distinction.                                                                                                            | Fee source/order policy/fill maker-taker must bind to cost basis and realized fill, not belief.                 |
| A-ZRSA-09         | B-U9-P01, B-U7-D01, B-U1-A02                                                           | Duplicate + expansion                   | Decision-time vs submit-time drift is the same as stale quote/freshness and executor repricing.                                                                                                                                 | Snapshot freshness deadline and submit deadline must be part of final intent.                                   |
| A-ZRSA-10         | B-U5-E02, B-U10-T02, B hidden branch 28                                                | Duplicate                               | Docs are not runtime proof.                                                                                                                                                                                                     | Docs must be demoted until tests/static gates enforce semantics.                                                |
| Unique to B       | B-U1-A05, B-U2-S03, B-U2-S04, B-U2-S05, B-U6-G04, B-U7-D03, B-U8-F01–F04, B-U9-P02/P03 | Unique/expansion                        | Review B expands beyond entry into market identity, lifecycle vocabulary, settlement/redeem, timeout units, harvester, reconciliation, error handling, performance.                                                             | These are not secondary if they can corrupt live evidence or reports.                                           |
| Unique to A       | A false symmetry register, A semantic alias table, A authority order                   | A has framing detail Review B assumes   | Review A’s physical-semantics framing should remain the test oracle.                                                                                                                                                            | Preserve the explicit distinction: probability, prior, quote, cost, fill, settlement, redeem.                   |
| Possible conflict | A-ZRSA-04 vs B’s claim that monitor improved                                           | Partial conflict resolved by repo       | Monitor has `HeldTokenMonitorQuote`, best bid fields, and buy-NO best-bid tests, but fail-closed corrected exit is not universal.                                                                                               | Classify as `PARTIALLY_CONFIRMED`, not contradicted.                                                            |
| Possible conflict | A-ZRSA-06 vs B’s report hard-fail improvements                                         | Partial supersession                    | Profit replay/equity curve have cohort hard-fails, but universal reports/migrations are not proven.                                                                                                                             | Do not downgrade universal report blocker until every report/export path uses same cohort gate.                 |
| Possible conflict | A-ZRSA-05 “buy-NO complement fallback” vs current main analysis                        | Superseded in main, confirmed in orphan | Main `market_analysis.py` native NO path is improved; `family_scan` fallback remains.                                                                                                                                           | Preserve both: main-path `SUPERSEDED_BY_CURRENT_CODE`, orphan `CONFIRMED`.                                      |

---

## 5. Repo exploration map

### Inspected clusters

| Cluster                                     | Files inspected                                                                                                                        | Evidence found                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Authority/docs                              | `AGENTS.md`, `src/AGENTS.md`, `src/execution/AGENTS.md`, `src/strategy/AGENTS.md`, `src/state/AGENTS.md`, `README.md`                  | Root/README still contain α-weighted market fusion language; execution AGENT still says “jump to ask for guaranteed fill”; state AGENT clarifies chain > chronicler > portfolio and append-first semantics. ([GitHub][14])                                                                                                                                                                            |
| Strategy/belief/prior/selection             | `market_fusion.py`, `market_analysis.py`, `market_analysis_family_scan.py`, `selection_family.py`, `types/market.py`                   | `MarketPriorDistribution` exists; main buy-NO native quote exists; family scan complement fallback remains; selection family IDs omit executable token/snapshot/cost/order policy; `BinEdge` remains overloaded. ([GitHub][7])                                                                                                                                                                        |
| Evaluator/Kelly/sizing                      | `engine/evaluator.py`, `contracts/execution_price.py`, `strategy/kelly.py`, `tests/test_execution_price.py`                            | Kelly contract exists but evaluator wraps raw `entry_price` as implied_probability and fee-adjusts it; tests currently encode fee-adjusted implied-probability acceptance. ([GitHub][16])                                                                                                                                                                                                             |
| Runtime/cycle/entry                         | `engine/cycle_runtime.py`, executor surfaces                                                                                           | Corrected cost/hypothesis/final intent attached conditionally; final intent generated for immediate marketable FOK/FAK-like path, passive path shadow/unsupported. ([GitHub][5])                                                                                                                                                                                                                      |
| Contracts                                   | `execution_intent.py`, `executable_market_snapshot_v2.py`, `execution_price.py`, `venue_submission_envelope.py`                        | Strong contracts exist: snapshot hashes, fee/tick/min/depth/freshness, cost basis, final intent, envelope live-bound assertion. But existence does not prove universal live route. ([GitHub][8])                                                                                                                                                                                                      |
| Execution/venue                             | `executor.py`, `exit_lifecycle.py`, `exit_triggers.py`, `polymarket_v2_adapter.py`, `venue_submission_envelope.py`                     | Legacy executor path remains; adapter compatibility helper fabricates placeholder `legacy:{token_id}`, `legacy-compat`, yes=no token; envelope has `assert_live_submit_bound`; adapter `submit()` does not visibly call it before posting in inspected raw. ([GitHub][6])                                                                                                                             |
| State/persistence/lifecycle                 | `state/db.py`, `state/portfolio.py`, `state/venue_command_repo.py`, `state/chain_reconciliation.py`                                    | Append-only venue envelopes/order facts/trade facts/position_lots exist; command grammar models unknown/partial/cancel states; `Position` has corrected/fill fields but fallback properties remain; chain reconciliation can update `entry_price/cost_basis/size/shares`. ([GitHub][17])                                                                                                              |
| Monitor/exit                                | `monitor_refresh.py`, `exit_lifecycle.py`, `exit_triggers.py`, `test_hold_value_exit_costs.py`                                         | `HeldTokenMonitorQuote` exists; monitor stores best bid/ask and diagnostic market price; quote failure returns `None`; monitor probability refresh still passes legacy `current_p_market` parameter with entry price; exit lifecycle uses current_market_price and best_bid but requires only current price for execution. Tests prove buy-NO EV gate best_bid behavior in some paths. ([GitHub][18]) |
| Settlement/reconciliation/reports/backtests | `harvester.py`, `profit_validation_replay.py`, `equity_curve.py`, `test_backtest_skill_economics.py`                                   | Harvester refuses settlement P&L for non-fill corrected authorities; profit replay/equity curve hard-fail mixed cohorts; backtest economics is tombstoned and skill cannot promote economics. Universal report coverage not proven. ([GitHub][19])                                                                                                                                                    |
| Tests/CI                                    | `tests/test_execution_price.py`, `test_executor_command_split.py`, `test_hold_value_exit_costs.py`, `test_backtest_skill_economics.py` | Existing tests include some useful gates, but some encode false semantics; command tests prove event order but not same-object hash chain; local pytest/CI not run. ([GitHub][20])                                                                                                                                                                                                                    |

### Files not inspected and why

| Cluster/file                                                  | Why not fully inspected                                                                     | Required verification path                                                                              |                      |                         |                      |                                                              |                               |                                             |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | -------------------- | ----------------------- | -------------------- | ------------------------------------------------------------ | ----------------------------- | ------------------------------------------- |
| Full `src/engine/cycle_runner.py` and all live dispatch paths | Raw/web inspection targeted `cycle_runtime` and executor; full call graph needs local grep. | Local grep: `rg "execute_intent                                                                         | execute_final_intent | create_execution_intent | submit_limit_order   | final_execution_intent" src/engine src/execution src/venue`. |                               |                                             |
| Full migration directory                                      | Web target list not enumerated; current schema inferred from `db.py` and tests.             | Local: `find . -iname "*migration*" -o -path "*/migrations/*"`; dry-run migration script.               |                      |                         |                      |                                                              |                               |                                             |
| Full docs/authority tree                                      | Root and AGENTS inspected; not every `docs/authority/*`.                                    | Local: `rg "guaranteed fill                                                                             | α                    | alpha                   | P_market             | p_market                                                     | current_market_price          | entry_price" README.md AGENTS.md src docs`. |
| All reports/dashboards                                        | `profit_validation_replay.py` and `equity_curve.py` inspected; not every report/export.     | Local: `rg "recent_exits                                                                                | pnl                  | pricing_semantics       | corrected_executable | equity                                                       | promotion" scripts src docs`. |                                             |
| CI workflows                                                  | API open rejected by safety constraint; local `.github/workflows` not available.            | Local: inspect `.github/workflows/*`; verify semantic gates run in CI.                                  |                      |                         |                      |                                                              |                               |                                             |
| Full tests                                                    | Four key tests inspected; not full test tree.                                               | Local: `pytest -q`; targeted semantic tests; static gate script.                                        |                      |                         |                      |                                                              |                               |                                             |
| Live production DB/open state                                 | Not available.                                                                              | P0 state census against `state/zeus_trades.db`, `state/positions-*.json`, venue facts, chain positions. |                      |                         |                      |                                                              |                               |                                             |

### Claim-to-file map

| Claim family                    | Primary files                                                                                                                                           |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Kelly raw scalar laundering     | `src/engine/evaluator.py`, `src/contracts/execution_price.py`, `src/strategy/kelly.py`, `tests/test_execution_price.py`                                 |
| Executor repricing/final intent | `src/execution/executor.py`, `src/engine/cycle_runtime.py`, `src/contracts/execution_intent.py`                                                         |
| FDR executable identity         | `src/strategy/selection_family.py`, `src/engine/cycle_runtime.py`, `src/contracts/execution_intent.py`, `src/state/snapshot_repo.py`                    |
| Buy-NO complement fallback      | `src/strategy/market_analysis.py`, `src/strategy/market_analysis_family_scan.py`                                                                        |
| Market identity                 | `src/contracts/executable_market_snapshot_v2.py`, `src/contracts/venue_submission_envelope.py`, `src/venue/polymarket_v2_adapter.py`, `src/state/db.py` |
| Compatibility envelope          | `src/venue/polymarket_v2_adapter.py`, `src/contracts/venue_submission_envelope.py`, executor submit surfaces                                            |
| Exit symmetry                   | `src/engine/monitor_refresh.py`, `src/execution/exit_lifecycle.py`, `src/execution/exit_triggers.py`, `src/state/portfolio.py`                          |
| Fill/lot authority              | `src/state/db.py`, `src/state/venue_command_repo.py`, `src/state/portfolio.py`, `src/execution/exit_lifecycle.py`                                       |
| Chain reconciliation mutation   | `src/state/chain_reconciliation.py`, `src/state/portfolio.py`                                                                                           |
| Reporting/promotion             | `scripts/profit_validation_replay.py`, `scripts/equity_curve.py`, `src/backtest/*`, report scripts                                                      |
| Docs/agents                     | `README.md`, `AGENTS.md`, `src/*/AGENTS.md`, `docs/authority/*`                                                                                         |

### Likely call graph targets

```text
strategy discovery:
  MarketAnalysis.find_edges()
  market_analysis_family_scan.scan_full_hypothesis_family()
  selection_family.apply_familywise_fdr()

evaluator:
  evaluate_candidate()
  _size_at_execution_price_boundary()
  kelly_size()

runtime:
  cycle_runner live loop
  cycle_runtime._attach_corrected_pricing_authority()
  decision.final_execution_intent / corrected_pricing_shadow

entry execution:
  executor.create_execution_intent()
  executor.execute_intent()
  executor.execute_final_intent()
  executor._live_order()
  PolymarketV2Adapter.create_submission_envelope()
  PolymarketV2Adapter.submit()
  PolymarketV2Adapter.submit_limit_order()

venue persistence:
  VenueSubmissionEnvelope.assert_live_submit_bound()
  venue_command_repo.insert_command()
  venue_command_repo.append_event()
  venue_submission_envelopes / venue_order_facts / venue_trade_facts / position_lots

monitor/exit:
  monitor_quote_refresh()
  monitor_probability_refresh()
  refresh_position()
  Position.evaluate_exit()
  exit_triggers.evaluate_exit_triggers()
  exit_lifecycle.execute_exit()
  executor.create_exit_order_intent()
  executor.execute_exit_order()

settlement/report:
  harvester._settlement_economics_for_position()
  compute_settlement_close()
  profit_validation_replay.require_single_exit_economics_cohort()
  equity_curve._single_exit_economics_cohort()
```

### High-risk unknowns

| Unknown                                                                 | Risk                                             | Verification                                                 |
| ----------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------ |
| Whether corrected live flag can route to legacy `execute_intent`        | Wrong live order/price/token                     | Local call graph + integration test.                         |
| Whether adapter compatibility helper can reach live CLOB SDK            | Placeholder condition/token identity live submit | Local/pytest test with fake adapter; static gate.            |
| Whether `assert_live_submit_bound()` is called before every live submit | Envelope assertion may be dead code              | `rg "assert_live_submit_bound"` and integration submit test. |
| Whether DB has legacy/corrected mixed open state                        | Automated exit/report corruption                 | P0 census.                                                   |
| Whether reports beyond replay/equity curve hard-fail mixed cohorts      | Promotion contamination                          | Report registry scan.                                        |
| Whether all semantic grep gates run in CI                               | Agent regression                                 | CI workflow inspection.                                      |

---

## 6. Cross-validation matrix

Legend: `CONFIRMED`, `PARTIALLY_CONFIRMED`, `SUPERSEDED_BY_CURRENT_CODE`, `CONTRADICTED_BY_CURRENT_CODE`, `REVIEW_REQUIRED`, `DUPLICATE`, `OUT_OF_SCOPE_FOR_NOW`.

| Claim ID  | Status                                                              | Repo evidence                                                                                                                                                                  | Venue dependency                                                                                | Severity after validation                         | Stage | Action                                                          | Test/gate                                  | Unresolved verification path                      |             |                |
| --------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- | ------------------------------------------------- | ----- | --------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------- | ----------- | -------------- |
| A-ZRSA-01 | `CONFIRMED`                                                         | `BinEdge` overloaded; evaluator raw float → `ExecutionPrice.with_taker_fee()` → Kelly. ([GitHub][2])                                                                           | BUY executable cost is best ask, not arbitrary probability.                                     | `LIVE_BLOCKER`                                    | 1/2   | Replace corrected Kelly input with cost basis only.             | Corrected mode rejects raw `entry_price`.  | Full call graph into evaluator live route.        |             |                |
| A-ZRSA-02 | `PARTIALLY_CONFIRMED`                                               | `selection_family` IDs omit executable facts; contracts/cycle attach cost later. ([GitHub][21])                                                                                | token/condition/orderbook snapshot are venue facts.                                             | `LIVE_BLOCKER` / `PROMOTION_BLOCKER`              | 1/2   | Bind executable hypothesis to FDR or create amended hypothesis. | Snapshot/cost change changes hypothesis.   | Full decision materialization path.               |             |                |
| A-ZRSA-03 | `CONFIRMED`                                                         | `FinalExecutionIntent` has policy coherence and special `limit_may_take_conservative` FOK/FAK rule; policy wording still ambiguous. ([GitHub][9])                              | GTC/GTD rest; FOK/FAK immediate; post-only rejects marketable. ([Polymarket Documentation][22]) | `LIVE_BLOCKER`                                    | 1/2   | Normalize order policies.                                       | Explicit policy mapping.                   | Verify executor/adapter mapping for every policy. |             |                |
| A-ZRSA-04 | `PARTIALLY_CONFIRMED`                                               | `HeldTokenMonitorQuote` exists, best bid stored, but quote failure returns `None`, exit executes on `current_market_price` not universal `ExitExecutableQuote`. ([GitHub][18]) | SELL value is best bid. ([Polymarket Documentation][23])                                        | `LIVE_BLOCKER`                                    | 1/2   | Require `ExitExecutableQuote` for corrected exits.              | Missing best_bid blocks corrected exit.    | Full monitor→exit route.                          |             |                |
| A-ZRSA-05 | `CONFIRMED` for family scan; main path `SUPERSEDED_BY_CURRENT_CODE` | Main `MarketAnalysis` has native NO support; `family_scan` fallback still computes `1 - p_market`. ([GitHub][3])                                                               | NO token has native token ID/orderbook.                                                         | `LIVE_BLOCKER` if family scan live-reachable      | 1/6   | Quarantine fallback; diagnostic only.                           | Static complement gate.                    | Confirm family-scan live reachability.            |             |                |
| A-ZRSA-06 | `PARTIALLY_CONFIRMED`                                               | Venue tables and position lots exist; report cohort tests exist; universal decision-row semantics not proven. ([GitHub][17])                                                   | Venue facts required for corrected economics.                                                   | `REPORT_BLOCKER`                                  | 3     | Add universal semantic fields/gates.                            | All reports hard-fail mixed cohorts.       | Inspect all migrations/reports/views.             |             |                |
| A-ZRSA-07 | `CONFIRMED`                                                         | Strong contracts exist; legacy executor and compatibility helper remain. ([GitHub][9])                                                                                         | Live submission must use token/orderbook/final limit.                                           | `LIVE_BLOCKER`                                    | 1/2   | Final-intent-only corrected live route.                         | Legacy live rejected.                      | Live dispatch call graph.                         |             |                |
| A-ZRSA-08 | `CONFIRMED`                                                         | `fee_deducted=True` after fee addition; evaluator fee adjusts raw scalar; Polymarket maker/taker fees differ. ([GitHub][4])                                                    | Taker fees only; maker fee zero in fee table.                                                   | `LIVE_BLOCKER` / `REPORT_BLOCKER`                 | 1/2/3 | Fee source in cost basis; realized maker/taker fee in fills.    | Fee policy changes cost basis only.        | Verify fee endpoint/code path.                    |             |                |
| A-ZRSA-09 | `CONFIRMED`                                                         | Selection identity lacks executable snapshot; cycle attaches cost later; snapshot has freshness but not universal submit deadline. ([GitHub][21])                              | Orderbook hash/freshness define executable quote.                                               | `LIVE_BLOCKER`                                    | 1/2/5 | Submit deadline + amended hypothesis.                           | Stale by submit rejects.                   | Full submit deadline route.                       |             |                |
| A-ZRSA-10 | `CONFIRMED`                                                         | README/AGENTS stale language. ([GitHub][14])                                                                                                                                   | Venue docs contradict “guaranteed fill.”                                                        | `DOCS_AUTHORITY_BLOCKER`                          | 4/16  | Rewrite docs after gates.                                       | Docs static scan.                          | Full docs scan.                                   |             |                |
| B-F01     | `DUPLICATE` of A-ZRSA-01 / `CONFIRMED`                              | Same evidence.                                                                                                                                                                 | Same.                                                                                           | `LIVE_BLOCKER`                                    | 1     | Same as P3.                                                     | Same.                                      | Same.                                             |             |                |
| B-F02     | `CONFIRMED`                                                         | Executor legacy surfaces and compute limit import remain; final intent not universal. ([GitHub][6])                                                                            | Price authority must be executable quote.                                                       | `LIVE_BLOCKER`                                    | 1/2   | Remove executor price authority in corrected mode.              | No corrected `compute_native_limit_price`. | Full grep.                                        |             |                |
| B-F03     | `PARTIALLY_CONFIRMED`                                               | Exit best_bid improvements exist; fallback gaps remain. ([GitHub][18])                                                                                                         | SELL best bid.                                                                                  | `LIVE_BLOCKER`                                    | 1/2   | Exit quote contract.                                            | Best_bid required.                         | Full exit route.                                  |             |                |
| B-F04     | `PARTIALLY_CONFIRMED`                                               | `MODEL_ONLY_POSTERIOR_MODE` exists; monitor still passes legacy `current_p_market` parameter using entry price. ([GitHub][7])                                                  | Market prior estimator must be named/validated.                                                 | `LIVE_BLOCKER` / architecture                     | 2/4   | Remove legacy monitor price parameter from corrected.           | Quote changes not posterior.               | Monitor route tests.                              |             |                |
| B-F05     | `REVIEW_REQUIRED` / partial                                         | Snapshot/envelope have condition/question/tokens; adapter compat collapses IDs. ([GitHub][8])                                                                                  | condition ID ≠ token ID. ([Polymarket Documentation][23])                                       | `LIVE_BLOCKER`                                    | 1/2   | Central `MarketIdentity`.                                       | Reject `market_id == token_id`.            | DB/live command scan.                             |             |                |
| B-F06     | `PARTIALLY_CONFIRMED`                                               | `assert_live_submit_bound()` exists; adapter `submit()` raw inspection did not show invocation before posting. ([GitHub][24])                                                  | Placeholder identity invalid for live.                                                          | `LIVE_BLOCKER`                                    | 1/2   | Enforce assertion at adapter/executor boundary.                 | Compatibility helper cannot live submit.   | `rg assert_live_submit_bound`.                    |             |                |
| B-F07     | `CONFIRMED` orphan / `SUPERSEDED` main                              | Main native NO improved; family-scan fallback remains. ([GitHub][3])                                                                                                           | Native NO orderbook required.                                                                   | `LIVE_BLOCKER`                                    | 1/6   | Quarantine fallback.                                            | Complement grep gate.                      | Reachability.                                     |             |                |
| B-F08     | `CONFIRMED`                                                         | Policy/cost basis contract exists, but policy contradiction and conditional final intent remain. ([GitHub][9])                                                                 | Order type reality.                                                                             | `LIVE_BLOCKER`                                    | 1/2   | Normalize policy.                                               | Policy hash changes.                       | Adapter mapping.                                  |             |                |
| B-F09     | `PARTIALLY_CONFIRMED`                                               | Position fill authority fields exist; fallback effective cost/shares remain. ([GitHub][10])                                                                                    | Fill facts determine cost.                                                                      | `LIVE_BLOCKER` / `REPORT_BLOCKER`                 | 2/3   | PositionLot/FillAuthority dominance.                            | Unknown fill blocks P&L.                   | DB projections.                                   |             |                |
| B-F10     | `PARTIALLY_SUPERSEDED`                                              | Profit/equity curve hard-fails; universal reports unknown. ([GitHub][11])                                                                                                      | Venue evidence required.                                                                        | `PROMOTION_BLOCKER`                               | 3     | Universal reporting cohort.                                     | Report registry gate.                      | Full report scan.                                 |             |                |
| B-U1-A01  | `CONFIRMED`                                                         | Same as A-ZRSA-01.                                                                                                                                                             | BUY ask cost.                                                                                   | `LIVE_BLOCKER`                                    | 1     | P3.                                                             | Cost basis Kelly.                          | Full route.                                       |             |                |
| B-U1-A02  | `CONFIRMED`                                                         | Same as B-F02.                                                                                                                                                                 | Venue executable quote.                                                                         | `LIVE_BLOCKER`                                    | 1     | P4.                                                             | No executor repricing.                     | Full route.                                       |             |                |
| B-U1-A03  | `PARTIALLY_CONFIRMED`                                               | Held quote exists; incomplete fail-closed not universal.                                                                                                                       | SELL best bid.                                                                                  | `LIVE_BLOCKER`                                    | 1/2   | P9.                                                             | Exit quote required.                       | Full exit tests.                                  |             |                |
| B-U1-A04  | `PARTIALLY_SUPERSEDED`                                              | Harvester refuses corrected settlement P&L without fill authority; chain reconciliation can mutate economics. ([GitHub][19])                                                   | Settlement payout ≠ fill cost.                                                                  | `REPORT_BLOCKER`                                  | 3/4   | Settlement uses lots only; chain mutation separated.            | Settlement fill authority test.            | Full settlement route.                            |             |                |
| B-U1-A05  | `PARTIALLY_CONFIRMED`                                               | Snapshot/envelope identity strong; compatibility collapse remains.                                                                                                             | condition/token docs.                                                                           | `LIVE_BLOCKER`                                    | 1/2   | MarketIdentity.                                                 | Reject collapse.                           | Command DB scan.                                  |             |                |
| B-U2-S01  | `CONFIRMED`                                                         | Position has multiple entry fields and fallback. ([GitHub][10])                                                                                                                | Submitted/fill/cost separate.                                                                   | `LIVE_BLOCKER` / `REPORT_BLOCKER`                 | 2/3   | Authority enums dominate.                                       | Entry field authority tests.               | Projections.                                      |             |                |
| B-U2-S02  | `CONFIRMED`                                                         | `fee_deducted=True` after addition. ([GitHub][4])                                                                                                                              | Taker fee cost.                                                                                 | `ARCHITECTURE_IMPROVEMENT` with live implications | 4     | Rename/alias carefully.                                         | Fee naming test.                           | Compatibility migration.                          |             |                |
| B-U2-S03  | `PARTIALLY_CONFIRMED`                                               | Docs/db/portfolio show multiple lifecycle strings. ([GitHub][17])                                                                                                              | None direct.                                                                                    | `ARCHITECTURE_IMPROVEMENT`                        | 4     | Canonical vocabulary.                                           | State grammar tests.                       | Full lifecycle grep.                              |             |                |
| B-U2-S04  | `REVIEW_REQUIRED`                                                   | `portfolio` has economic close vs settlement close; `is_settled` not found in inspected file, but broader code unknown. ([GitHub][10])                                         | Resolution/redeem distinct.                                                                     | `REPORT_BLOCKER`                                  | 3/4   | SettlementStatus split.                                         | Status tests.                              | Full grep `is_settled`.                           |             |                |
| B-U2-S05  | `REVIEW_REQUIRED`                                                   | Not fully grepped.                                                                                                                                                             | None direct.                                                                                    | `ARCHITECTURE_IMPROVEMENT` / ops                  | 4/5   | Unit-suffixed fields.                                           | Timeout static gate.                       | Local grep.                                       |             |                |
| B-U3-W01  | `CONFIRMED`                                                         | Cycle attaches final intent conditionally; passive path shadow/unsupported. ([GitHub][5])                                                                                      | Submit requires final order type/limit.                                                         | `LIVE_BLOCKER`                                    | 1/2   | Universal final intent or fail closed.                          | Corrected live no final intent reject.     | Full runtime route.                               |             |                |
| B-U3-W02  | `PARTIALLY_CONFIRMED`                                               | Compatibility helper fabricates placeholder envelope; assertion exists but not proven called. ([GitHub][25])                                                                   | Live condition/token truth.                                                                     | `LIVE_BLOCKER`                                    | 1/2   | Enforce assertion.                                              | Compatibility helper live reject.          | Full adapter/executor call graph.                 |             |                |
| B-U3-W03  | `PARTIALLY_CONFIRMED`                                               | `monitor_quote_refresh` catches exception and returns `None`; refresh falls back to stored price. ([GitHub][18])                                                               | SELL quote required.                                                                            | `LIVE_BLOCKER`                                    | 1/2   | Corrected missing quote = REVIEW_REQUIRED.                      | Missing quote fails closed.                | Exit integration.                                 |             |                |
| B-U3-W04  | `PARTIALLY_CONFIRMED`                                               | Command journal exists, event order tests exist; tests lack cost_basis/same-object checks. ([GitHub][26])                                                                      | Venue facts chain.                                                                              | `LIVE_BLOCKER` / `REPORT_BLOCKER`                 | 2/3   | Hash-chain same-object proof.                                   | Same-object test.                          | Full schema/command scan.                         |             |                |
| B-U4-O01  | `CONFIRMED`                                                         | family scan complement fallback. ([GitHub][27])                                                                                                                                | Native NO.                                                                                      | `LIVE_BLOCKER` if reachable                       | 1/6   | Quarantine/delete.                                              | Static gate.                               | Reachability.                                     |             |                |
| B-U4-O02  | `CONFIRMED` placeholder; reachability `REVIEW_REQUIRED`             | Adapter helper fabricates `legacy:{token_id}` and yes=no token. ([GitHub][25])                                                                                                 | Invalid live identity.                                                                          | `LIVE_BLOCKER`                                    | 1/6   | Disable in live.                                                | Helper cannot submit live.                 | Route reachability.                               |             |                |
| B-U4-O03  | `CONFIRMED`                                                         | Executor imports `compute_native_limit_price`. ([GitHub][6])                                                                                                                   | Executor must not derive price.                                                                 | `LIVE_BLOCKER`                                    | 1/6   | Corrected grep gate.                                            | Static gate.                               | Full grep.                                        |             |                |
| B-U4-O04  | `PARTIALLY_CONFIRMED`                                               | Profit replay labels diagnostic-only but still uses tick/complement logic for buy_no trajectory. ([GitHub][11])                                                                | Diagnostic only.                                                                                | `PROMOTION_BLOCKER`                               | 3/6   | Keep diagnostic; no promotion.                                  | Diagnostic exclusion.                      | Full report scan.                                 |             |                |
| B-U5-E01  | `CONFIRMED`                                                         | `BinEdge` all-plane object. ([GitHub][2])                                                                                                                                      | None direct.                                                                                    | `ARCHITECTURE_IMPROVEMENT` with live risk         | 4     | Shrink/quarantine.                                              | Static gate.                               | Refactor plan.                                    |             |                |
| B-U5-E02  | `CONFIRMED`                                                         | Stale docs. ([GitHub][15])                                                                                                                                                     | Venue contradiction.                                                                            | `DOCS_AUTHORITY_BLOCKER`                          | 4/16  | Docs after gates.                                               | Docs scan.                                 | Full docs scan.                                   |             |                |
| B-U5-E03  | `CONFIRMED`                                                         | Adequate contract seeds exist. ([GitHub][8])                                                                                                                                   | Venue model already represented enough for now.                                                 | `NOT_NOW` for overbuild                           | 4     | Use existing contracts.                                         | No parallel venue model.                   | None.                                             |             |                |
| B-U6-G01  | `CONFIRMED`                                                         | Cycle function mixes snapshot/cost/hypothesis/final intent and decision mutation. ([GitHub][5])                                                                                | Snapshot freshness.                                                                             | `ARCHITECTURE_IMPROVEMENT` / live risk            | 4     | Extract builders.                                               | Builder tests.                             | Full file review.                                 |             |                |
| B-U6-G02  | `CONFIRMED`                                                         | Executor giant legacy route remains. ([GitHub][6])                                                                                                                             | Executor no authority.                                                                          | `LIVE_BLOCKER`                                    | 1/4   | Split corrected API.                                            | No legacy in corrected.                    | Full executor scan.                               |             |                |
| B-U6-G03  | `PARTIALLY_CONFIRMED`                                               | Monitor mixes quote/prob/topology/logging and exceptions. ([GitHub][18])                                                                                                       | Quote freshness.                                                                                | `ARCHITECTURE_IMPROVEMENT` / live risk            | 4/5   | Split monitor.                                                  | Authority field tests.                     | Full monitor tests.                               |             |                |
| B-U6-G04  | `PARTIALLY_CONFIRMED`                                               | Harvester mixes settlement/logging/learning/P&L; some guard exists. ([GitHub][19])                                                                                             | Settlement/redeem.                                                                              | `ARCHITECTURE_IMPROVEMENT` / report risk          | 4     | Split harvester.                                                | Settlement status tests.                   | Full harvester.                                   |             |                |
| B-U7-D01  | `PARTIALLY_CONFIRMED`                                               | Cycle attaches corrected objects to `decision`; exact edge mutation needs full grep. ([GitHub][5])                                                                             | Snapshot changes object.                                                                        | `LIVE_BLOCKER`                                    | 1/2   | Immutable hypothesis.                                           | Mutation test.                             | `rg "decision.edge                                | entry_price | vwmp" cycle*`. |
| B-U7-D02  | `CONFIRMED`                                                         | `Position` owns many planes and fallback properties. ([GitHub][10])                                                                                                            | Fill/lot authority.                                                                             | `ARCHITECTURE_IMPROVEMENT` / report risk          | 4     | Split projections.                                              | PositionLot tests.                         | Projection scan.                                  |             |                |
| B-U7-D03  | `CONFIRMED`                                                         | Chain reconciliation overwrites `entry_price`, `cost_basis_usd`, `size_usd`, `shares` from chain. ([GitHub][28])                                                               | Chain fill facts separate.                                                                      | `REPORT_BLOCKER`                                  | 3/4   | Chain facts cannot mutate entry economics.                      | Chain mutation gate.                       | Reconciliation tests.                             |             |                |
| B-U7-D04  | `PARTIALLY_CONFIRMED`                                               | Snapshot hash exists; command cost_basis hash link not universal. ([GitHub][8])                                                                                                | Same-object proof.                                                                              | `LIVE_BLOCKER` / `REPORT_BLOCKER`                 | 2/3   | Mandatory hash chain.                                           | Same-object gate.                          | DB schema scan.                                   |             |                |
| B-U8-F01  | `PARTIALLY_SUPERSEDED`                                              | Harvester uses some typed guards; broad exceptions still present by dossier and code patterns. ([GitHub][19])                                                                  | Settlement money path.                                                                          | `REPORT_BLOCKER`                                  | 3/4   | Typed REVIEW_REQUIRED.                                          | Broad-except gate.                         | Full grep.                                        |             |                |
| B-U8-F02  | `CONFIRMED`                                                         | Monitor catches broad exceptions and returns/falls back. ([GitHub][18])                                                                                                        | Missing quote/prob authority.                                                                   | `LIVE_BLOCKER`                                    | 1/5   | Fail-closed authority fields.                                   | Monitor exception tests.                   | Full route.                                       |             |                |
| B-U8-F03  | `CONFIRMED`                                                         | Rejected compatibility envelope still has placeholder identity. ([GitHub][25])                                                                                                 | Invalid live identity.                                                                          | `REPORT_BLOCKER` / cleanup                        | 6     | Exclude placeholder evidence.                                   | Placeholder evidence test.                 | DB report scan.                                   |             |                |
| B-U8-F04  | `REVIEW_REQUIRED`                                                   | `test_executor_command_split` monkeypatches cutover/heartbeat/collateral gates. ([GitHub][29])                                                                                 | Gate order.                                                                                     | `PROMOTION_BLOCKER`                               | 3     | Add full-gate integration.                                      | No monkeypatch promotion test.             | CI/local tests.                                   |             |                |
| B-U9-P01  | `CONFIRMED`                                                         | Snapshot freshness exists; submit deadline universal not proven; adapter validates snapshot freshness for envelope creation. ([GitHub][8])                                     | Quote stale by submit invalid.                                                                  | `LIVE_BLOCKER`                                    | 1/5   | Submit deadline in intent/envelope.                             | Stale-by-submit reject.                    | Full submit route.                                |             |                |
| B-U9-P02  | `REVIEW_REQUIRED`                                                   | Monitor per-position complexity evident; latency not measured. ([GitHub][18])                                                                                                  | Fresh quote deadlines.                                                                          | `LIVE_BLOCKER` in scale                           | 5     | Batching/telemetry.                                             | Quote age SLO.                             | Runtime metrics.                                  |             |                |
| B-U9-P03  | `PARTIALLY_CONFIRMED`                                               | Profit replay diagnostic-only but uses tick loop. ([GitHub][11])                                                                                                               | Diagnostic vs promotion.                                                                        | `PROMOTION_BLOCKER`                               | 3/5   | Keep diagnostic label.                                          | Promotion exclusion.                       | Full backtest scan.                               |             |                |
| B-U10-T01 | `CONFIRMED`                                                         | Existing test asserts fee-adjusted implied probability passes Kelly. ([GitHub][20])                                                                                            | Cost basis needed.                                                                              | `LIVE_BLOCKER` / verification                     | 1     | Rewrite tests.                                                  | Expected fail before fix.                  | Local pytest.                                     |             |                |
| B-U10-T02 | `REVIEW_REQUIRED`                                                   | Some tests are static/relationship only; full CI unknown.                                                                                                                      | None direct.                                                                                    | `PROMOTION_BLOCKER`                               | 1/3   | Semantic counterfactual tests.                                  | Counterfactual suite.                      | Full tests.                                       |             |                |
| B-U10-T03 | `CONFIRMED`                                                         | Command split tests lack cost_basis/same-object patterns. ([GitHub][29])                                                                                                       | Same-object proof.                                                                              | `REPORT_BLOCKER`                                  | 2/3   | Hash-chain tests.                                               | Same-object test.                          | Full tests.                                       |             |                |
| B-U10-T04 | `PARTIALLY_SUPERSEDED`                                              | Backtest/report tests improved; universal policy unknown. ([GitHub][13])                                                                                                       | Promotion evidence.                                                                             | `PROMOTION_BLOCKER`                               | 3     | Report registry gate.                                           | All reports cohort-gated.                  | Full report scan.                                 |             |                |

No major material claim was `CONTRADICTED_BY_CURRENT_CODE`. Several were **partially superseded** by improved code, especially main buy-NO native quote, held-token quote surfaces, fill-authority fields, harvester fill-derived settlement guard, and replay/equity cohort gates.

---

## 7. Venue reality validation table

Official venue facts are non-negotiable. Repo-local names cannot override these.

| Venue fact                                                                                                                                                                      | Official source                                                                                                                                                         | Zeus local abstraction affected                                                                                  | Architecture implication                                                                                                                         | Test/gate required                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| CLOB orderbook `market` is the **Condition ID** and `asset_id` is the **Token ID**. Orderbook also carries bids, asks, tick size, min order size, negative-risk flag, and hash. | Polymarket orderbook docs show `market` as Condition ID, `asset_id` as Token ID, bids/asks/tick/min/neg-risk/hash fields. ([Polymarket Documentation][23])              | `MarketIdentity`, `ExecutableMarketSnapshotV2`, `VenueSubmissionEnvelope`, `venue_commands`, `market_id` fields. | `market_id == token_id` is invalid for live venue identity. `condition_id`, `question_id`, YES token, NO token, selected token must be separate. | Static/live test rejecting `market_id == token_id` and `condition_id.startswith("legacy:")` in live. |
| Gamma/events expose condition ID, question ID, and separate YES/NO token IDs for each market.                                                                                   | Polymarket market/event docs list condition ID, question ID, token IDs for Yes and No, and orderbook enablement. ([Polymarket Documentation][30])                       | Snapshot capture, market scanner, selection identity, venue envelope.                                            | Selection must link to condition/question/token identity. Token-only identity cannot prove event/market.                                         | `MarketIdentity` roundtrip and envelope identity test.                                               |
| BUY executable price is best ask; SELL executable price is best bid.                                                                                                            | Polymarket price docs: `getPrice` with `BUY` returns best ask; `SELL` returns best bid. ([Polymarket Documentation][23])                                                | Entry cost basis, exit quote, Kelly, monitor, report P&L.                                                        | Buy-NO requires native NO best ask; exit value requires held-token best bid. VWMP/current/display price is not executable cost/value by default. | Buy entry ask / sell exit bid counterfactual tests.                                                  |
| Displayed midpoint can be shown as implied probability, but it is not necessarily executable.                                                                                   | Polymarket docs describe midpoint/displayed price as implied probability. ([Polymarket Documentation][23])                                                              | `p_market`, `vwmp`, displayed/current price, market prior.                                                       | Midpoint/VWMP/current price cannot serve as executable price without named estimator and conversion.                                             | Static gate forbidding raw quote/VWMP into corrected Kelly.                                          |
| GTC/GTD orders rest on the book; FOK/FAK are immediate execution types. BUY size is amount spent in USD; SELL size is shares sold.                                              | Polymarket order docs define GTC/GTD/FOK/FAK and BUY/SELL sizing semantics. ([Polymarket Documentation][22])                                                            | `OrderPolicy`, `FinalExecutionIntent`, adapter order args, submitted shares/notional.                            | `post_only`, may-rest, marketable sweep, FOK/FAK cannot be collapsed. BUY target notional and submitted shares differ.                           | OrderPolicy mapping test and buy/sell size semantics test.                                           |
| Post-only orders only rest; if they cross the spread they are rejected; post-only is GTC/GTD-compatible, not FOK/FAK.                                                           | Polymarket docs state post-only rejects marketable crosses and is supported only with GTC/GTD. ([Polymarket Documentation][22])                                         | Order policy, executor, adapter.                                                                                 | “Jump to ask for guaranteed fill” is not post-only; FOK/FAK is not may-rest.                                                                     | Static/docs gate and policy coherence test.                                                          |
| CLOB validates signature, balance, allowances, tick size, and market status before accepting/posting.                                                                           | Order lifecycle docs describe operator validation and resting/matching behavior. ([Polymarket Documentation][31])                                                       | Collateral preflight, cutover, heartbeat, command journal, unknown side effects.                                 | Submitted intent is not fill. Accepted order is not confirmed fill. Unknown submit requires REVIEW_REQUIRED.                                     | Persist-before-submit and unknown side-effect tests.                                                 |
| Websocket price changes include `asset_id`, price, size, side, hash, best bid, best ask; tick-size changes include asset and market.                                            | Market websocket docs list asset_id, side, best_bid, best_ask, hash, and tick_size_change. ([Polymarket Documentation][32])                                             | Snapshot hash, monitor quote freshness, market_price_history.                                                    | Snapshot/orderbook hash and quote age must be persisted for evidence.                                                                            | Quote snapshot hash and freshness tests.                                                             |
| Fees are taker-side; maker fees are zero in the fee table; weather taker fee appears as 0.05 in the docs table. Formula is `fee = C × feeRate × p × (1-p)`.                     | Polymarket fee docs show formula and maker/taker table. ([Polymarket Documentation][33])                                                                                | `ExecutionPrice`, `ExecutableCostBasis`, `FillAuthority`, realized fill fee, reports.                            | Fee source must be bound to order policy and realized fill side. A planned taker fee is not necessarily realized maker/taker fee.                | Fee source/fill fee distinction test.                                                                |
| Negative-risk metadata exists in CLOB market/orderbook docs.                                                                                                                    | Orderbook and orders docs include `neg_risk`/negative-risk fields. ([Polymarket Documentation][23])                                                                     | Snapshot, cost basis, order policy, market prior estimator.                                                      | Corrected live cannot ignore negative-risk metadata; either policy modeled or live blocked.                                                      | Negative-risk metadata present-or-policy-blocks-live test.                                           |
| Resolution/settlement/redeem are separate from order/fill execution.                                                                                                            | Polymarket market/event docs describe token payouts/redemption surfaces; adapter currently defers redeem to settlement command ledger. ([Polymarket Documentation][30]) | SettlementStatus, harvester, redemption command, reports.                                                        | `settled` is not `redeemed_confirmed`; settlement result is not payout received.                                                                 | Settlement/redeem status split tests.                                                                |

---

## 8. Root-cause synthesis

### RC-01 — Belief / prior / quote / cost not isolated

Manifestations:

* `BinEdge` still carries `p_model`, `p_market`, `p_posterior`, `entry_price`, `vwmp`, `edge`, `forward_edge`, p-value, and support metadata in one object. ([GitHub][2])
* Evaluator treats an `entry_price` float as `ExecutionPrice(price_type="implied_probability")`, then fee-adjusts it into a Kelly-safe type. ([GitHub][16])
* Monitor stores `diagnostic_market_price` separately from best bid/ask, but still routes legacy probability refresh through a `current_p_market` parameter using `entry_price`. ([GitHub][18])

Repair principle:

* Four planes must be separately typed:

  1. `SettlementProbability`;
  2. `MarketPriorDistribution`;
  3. `ExecutableEntryCostBasis` / `ExitExecutableQuote`;
  4. `ExecutableTradeHypothesis`.

### RC-02 — Executor price authority

Manifestations:

* Executor imports and uses legacy price construction surfaces such as `compute_native_limit_price`. ([GitHub][6])
* `execute_final_intent()` exists, but legacy `create_execution_intent()` and `execute_intent()` remain live-capable surfaces. ([GitHub][6])
* Cycle runtime builds final intent conditionally and after selection. ([GitHub][5])

Repair principle:

* Executor must validate only:

  * selected token;
  * final limit;
  * order policy/order type/post-only;
  * cost_basis hash;
  * snapshot hash/freshness;
  * envelope identity.
* Executor must not derive or mutate price authority from posterior, VWMP, `p_market`, `entry_price`, or `BinEdge`.

### RC-03 — Mutable `BinEdge` / decision mutation

Manifestations:

* `BinEdge` is an all-plane carrier.
* Cycle runtime attaches corrected cost/final intent to decision after FDR-style selection.
* Repricing after selection risks altering economic object identity.

Repair principle:

* `BinEdge` should shrink to selection-only evidence.
* Executable economics must be materialized as a new cost/hypothesis object, not written back into selection fields.

### RC-04 — FDR materialization drift

Manifestations:

* `selection_family.py` family IDs include cycle/city/date/metric/discovery/snapshot/strategy dimensions but not selected token, executable snapshot hash, cost basis hash, order policy, or venue identity. ([GitHub][21])
* Cycle runtime uses `legacy_selection_family:{decision_snapshot_id}` style lineage in the corrected object builder. ([GitHub][5])

Repair principle:

* Statistical hypothesis ID and executable hypothesis ID must be distinct.
* Corrected live must materialize a bound `ExecutableTradeHypothesis`.
* If executable cost/snapshot changes after FDR, the system must reject, recompute, or create an amended hypothesis.

### RC-05 — Legacy compatibility path survival

Manifestations:

* Main buy-NO path improved, but family-scan fallback still uses `1 - p_market`. ([GitHub][27])
* Adapter compatibility helper fabricates `condition_id=f"legacy:{token_id}"`, `question_id="legacy-compat"`, `yes_token_id=no_token_id=token_id`, and `outcome_label="YES"`. ([GitHub][25])
* `assert_live_submit_bound()` exists but live invocation is not proven. ([GitHub][24])

Repair principle:

* Compatibility code may remain temporarily for diagnostics/tests but must be unreachable for live submit and corrected evidence.

### RC-06 — Lifecycle/fill authority not dominant

Manifestations:

* DB has append-only `venue_order_facts`, `venue_trade_facts`, and `position_lots`, plus command state transitions for partial, unknown, cancel, fill. ([GitHub][17])
* `Position` still derives `effective_shares` and `effective_cost_basis_usd` from legacy fields when fill authority is missing. ([GitHub][10])
* Chain reconciliation can overwrite entry economics from chain average/cost/size. ([GitHub][28])

Repair principle:

* Corrected P&L must be fill-derived.
* Unknown fill status yields `REVIEW_REQUIRED`, not corrected P&L.
* Chain facts can create reconciliation facts, not mutate entry cost basis.

### RC-07 — Report/backtest cohort law incomplete

Manifestations:

* Profit replay and equity curve now hard-fail mixed cohorts. ([GitHub][11])
* Backtest economics is tombstoned and skill/economics separation has tests. ([GitHub][13])
* Universal report registry is not proven.

Repair principle:

* Every report/export/promotion path must use `ReportingCohort`.
* Diagnostic replay/backtest is not promotion evidence.
* Mixed legacy/corrected cohorts hard-fail.

### RC-08 — City/time/settlement identity not centralized

Manifestations:

* `Position` has `temperature_metric`, `target_date`, `unit`, city strings.
* `chain_reconciliation.resolve_position_metric()` centralizes a missing metric default but still permits legacy UNVERIFIED default to high. ([GitHub][28])
* Settlement/economic close/status split is partial.

Repair principle:

* `CityIdentity`, `TimeIdentity`, `SettlementStatus`, and `MetricIdentity` must be centralized and enforced at money/report boundaries.

### RC-09 — Performance staleness as correctness failure

Manifestations:

* Snapshot contract includes freshness, but submit-deadline propagation is not universal.
* Monitor quote/probability refresh is complex and per-position.
* Websocket/orderbook docs expose hashes and best bid/ask that should be freshness authorities. ([Polymarket Documentation][32])

Repair principle:

* Quote age, snapshot age, submit deadline, monitor cycle latency, and stale reasons are correctness fields, not telemetry-only ornaments.

---

## 9. Final stage taxonomy

### Stage 0 — Evidence lock / live freeze / state census

| Field                  | Content                                                                                                                                              |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Stop unsafe corrected live entry; inventory DB/open positions; establish current truth; avoid destructive migrations.                                |
| Included claims        | A-ZRSA-06/07; B-F06/F09/F10; B-U3-W02; B-U7-D02/D03; hidden branches 1, 6–10, 16, 23, 35.                                                            |
| Excluded work          | Contract refactors, migrations that rewrite data, docs rewrites, cleanup deletes.                                                                    |
| Prerequisites          | Access to repo, state DBs, positions JSON, config, CI, logs, current venue adapter config.                                                           |
| Exit criteria          | New live entry disabled by default; state census report classifies all open positions; no destructive migrations; unknowns marked `REVIEW_REQUIRED`. |
| Tests/gates            | `test_corrected_live_disabled_by_default`; `test_state_census_no_mutation`; config typo gate.                                                        |
| Rollback               | Revert config freeze only after Stage 1/2 gates pass; census is read-only.                                                                           |
| Hidden branches closed | Legacy open positions, config typo, unknown migrations, live compatibility reachability unknown inventory.                                           |
| Unresolved unknowns    | Actual production DB state and open orders remain unknown until run locally.                                                                         |

### Stage 1 — Critical live blockers

| Field                  | Content                                                                                                                                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Block failures that can cause wrong live order, token, side, limit, cost basis, exit, duplicate/ghost command, corrupted position, or false corrected P&L.                                                                      |
| Included claims        | A-ZRSA-01–09; B-F01–F09; B-U1-A01–A05; B-U3-W01–W04; B-U4-O01–O03; B-U8-F02/F03; B-U9-P01; B-U10-T01/T03.                                                                                                                       |
| Excluded work          | Aesthetic refactors, docs rewrite before gates, full negative-risk optimizer, full fill probability model.                                                                                                                      |
| Prerequisites          | Stage 0 freeze and census.                                                                                                                                                                                                      |
| Exit criteria          | Corrected live cannot submit without final intent, cost basis, native token quote, snapshot hash, cost hash, order policy, envelope live-bound assertion, command proof, fill authority; corrected exit cannot use legacy exit. |
| Tests/gates            | Full Stage 1 test packet in Section 10 and Section 17.                                                                                                                                                                          |
| Rollback               | Feature flag off; revert corrected live route; keep diagnostic shadow.                                                                                                                                                          |
| Hidden branches closed | Buy-NO complement, stale quote, executor repricing, compatibility submit, missing condition/question, partial/unknown fill, command insert/submit side-effect splits.                                                           |
| Unresolved unknowns    | None allowed for corrected live entry; unresolved means live remains frozen.                                                                                                                                                    |

### Stage 2 — Live-safe minimal spine

| Field                  | Content                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Build minimum coherent corrected live/shadow path with four-plane separation and same-object proof.                                                                                                                                                                                                                                                                         |
| Included claims        | A-ZRSA-02/06/07/08/09; B-F05/F08/F09; B-U2-S01; B-U3-W04; B-U7-D04.                                                                                                                                                                                                                                                                                                         |
| Excluded work          | Report promotion, docs authority rewrite, cleanup deletes, performance optimization beyond freshness gates.                                                                                                                                                                                                                                                                 |
| Prerequisites          | Stage 1 blockers fail closed.                                                                                                                                                                                                                                                                                                                                               |
| Exit criteria          | `MarketPriorDistribution`, `ExecutableEntryCostBasis`, `ExecutableTradeHypothesis`, `FinalExecutionIntent`, `OrderPolicy`, `VenueSubmissionEnvelope`, `PositionLot`, `FillAuthority`, `ExitExecutableQuote`, `PricingSemanticsVersion`, `ReportingCohort`, `MarketIdentity`, `CityIdentity`, `TimeIdentity`, `SettlementStatus` are implemented or confirmed and connected. |
| Tests/gates            | Contract unit tests; integration corrected buy_yes/buy_no shadow; no legacy corrected submit.                                                                                                                                                                                                                                                                               |
| Rollback               | Contract additions are additive; live flag stays off until acceptance.                                                                                                                                                                                                                                                                                                      |
| Hidden branches closed | FDR drift, market/token collapse, command hash mismatch, corrected entry with legacy exit.                                                                                                                                                                                                                                                                                  |
| Unresolved unknowns    | Production migration remains Stage 3/16.                                                                                                                                                                                                                                                                                                                                    |

### Stage 3 — Promotion / reporting / evidence integrity

| Field                  | Content                                                                                                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Purpose                | Trust reports only when semantic cohorts and fill/settlement evidence are valid.                                                           |
| Included claims        | A-ZRSA-06; B-F10; B-U1-A04; B-U7-D03; B-U10-T04; hidden branches 17, 18, 21, 29, 31, 39.                                                   |
| Excluded work          | New strategy promotion, docs as authority, historical corrected backfill.                                                                  |
| Prerequisites          | Stage 2 spine and fill authority.                                                                                                          |
| Exit criteria          | `PricingSemanticsVersion` and `ReportingCohort` hard-fail every report; old rows classified; no fake backfill; diagnostic outputs labeled. |
| Tests/gates            | Mixed cohort hard-fails; backtest economics tombstone; promotion rejects model-only/diagnostic.                                            |
| Rollback               | Reports can be disabled or restricted to diagnostic; no data rewrite needed.                                                               |
| Hidden branches closed | Mixed report cohorts, backtest lacks depth, diagnostic replay complement, report/backtest promotion evidence stale.                        |
| Unresolved unknowns    | Every script/report view must be scanned.                                                                                                  |

### Stage 4 — Architecture improvement / maintainability

| Field                  | Content                                                                                                                                         |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Reduce future regression risk after live blockers are sealed.                                                                                   |
| Included claims        | A-ZRSA-10; B-U2-S02–S05; B-U5-E01/E02; B-U6-G01–G04; B-U7-D02; settlement/city/time vocabulary.                                                 |
| Excluded work          | Live-money behavior changes without tests; ontology rewrite; docs-only repair.                                                                  |
| Prerequisites          | Stages 1–3 tests.                                                                                                                               |
| Exit criteria          | `BinEdge` shrunk/quarantined; lifecycle vocabulary canonical; settlement status split; city/time identity centralized; docs updated with gates. |
| Tests/gates            | Static authority scans, lifecycle grammar, city/time/metric tests.                                                                              |
| Rollback               | Refactor behind compatibility adapters; preserve DB projections.                                                                                |
| Hidden branches closed | Lifecycle vocabulary drift, `fee_deducted` naming, `is_settled` split, city/time/high-low mismatch, docs-only claims.                           |
| Unresolved unknowns    | Full codebase refactor scope.                                                                                                                   |

### Stage 5 — Performance / staleness / operational telemetry

| Field                  | Content                                                                                                                 |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Treat latency/staleness as correctness.                                                                                 |
| Included claims        | B-U9-P01–P03; monitor batching; quote/snapshot age; telemetry counters.                                                 |
| Excluded work          | Queue priority/adverse selection/full fill probability.                                                                 |
| Prerequisites          | Entry/exit quote contracts.                                                                                             |
| Exit criteria          | Freshness deadlines and submit deadlines enforced; monitor quote age tracked; staleness counters exported; fail-closed. |
| Tests/gates            | Stale snapshot by submit rejects; monitor quote age SLO; latency counters.                                              |
| Rollback               | Disable corrected live if staleness counters breach.                                                                    |
| Hidden branches closed | Stale quote, performance staleness, submit delay, monitor batching.                                                     |
| Unresolved unknowns    | Runtime latency distribution.                                                                                           |

### Stage 6 — Cleanup / orphan removal

| Field                  | Content                                                                                                                                                                              |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Purpose                | Delete/quarantine stale code after invariant tests prove no live reachability.                                                                                                       |
| Included claims        | A-ZRSA-05; B-U4-O01–O04; B-U5-E01; docs-only stale paths.                                                                                                                            |
| Excluded work          | Deleting uncertain live-reachable code before quarantine.                                                                                                                            |
| Prerequisites          | Static gates and live reachability tests.                                                                                                                                            |
| Exit criteria          | Complement fallback removed/quarantined; compatibility helper live-disabled; corrected paths cannot call legacy price helpers; diagnostic replay labeled; docs stale claims removed. |
| Tests/gates            | Static grep gates and integration reachability tests.                                                                                                                                |
| Rollback               | Quarantine before delete; keep legacy diagnostic adapters retired/diagnostic-only with no execution-mode selector.                                                                    |
| Hidden branches closed | Orphans, compatibility helpers, diagnostic complement, stale docs.                                                                                                                   |
| Unresolved unknowns    | Any code path whose reachability remains uncertain stays quarantined, not deleted.                                                                                                   |

---

## 10. Stage 1 critical live blocker packet

### 10.1 Kelly raw float / `ExecutionPrice` laundering

| Field                | Content                                                                                                                                                                                                                                                                                 |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Evaluator takes `entry_price` float, creates `ExecutionPrice(price_type="implied_probability")`, calls `.with_taker_fee()`, producing `price_type="fee_adjusted"`, so `assert_kelly_safe()` passes even though source was not executable cost.                                          |
| Current evidence     | `evaluator._size_at_execution_price_boundary` wraps raw entry price; `ExecutionPrice.with_taker_fee()` changes type to `fee_adjusted`; tests assert this behavior. ([GitHub][16])                                                                                                       |
| Required code change | In corrected mode, delete/raw-block `_size_at_execution_price_boundary(entry_price=float)` as Kelly input. Add `size_from_executable_cost_basis(cost_basis: ExecutableEntryCostBasis, posterior: SettlementProbability, bankroll, policy)` and make corrected evaluator call only that. |
| Required test        | `test_corrected_mode_cannot_size_from_raw_entry_price`; `test_implied_probability_with_fee_cannot_become_kelly_safe_in_corrected_mode`; rewrite `test_evaluator_always_uses_fee_adjusted_size`.                                                                                         |
| Acceptance criterion | Corrected path cannot construct Kelly input from `BinEdge.entry_price`, `p_market`, `vwmp`, or raw float. Legacy diagnostic path must be explicitly labeled.                                                                                                                            |
| Rollback             | Keep legacy helper for legacy diagnostic only behind `legacy_price_probability_conflated`; corrected flag remains off.                                                                                                                                                                  |

### 10.2 Executor repricing

| Field                | Content                                                                                                                                                    |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Executor/cycle can derive native limit from posterior/VWMP/best ask after FDR, changing economic object identity.                                          |
| Current evidence     | Executor imports `compute_native_limit_price` and legacy `create_execution_intent` remains. `execute_final_intent` exists but not universal. ([GitHub][6]) |
| Required code change | Corrected executor API accepts only `FinalExecutionIntent`; it validates hash/token/limit/order policy/freshness and submits. It must not compute price.   |
| Required test        | `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp`; static gate: corrected executor calls `compute_native_limit_price` → fail.        |
| Acceptance criterion | No corrected call stack from `execute_final_intent` reaches price recomputation.                                                                           |
| Rollback             | Disable corrected live; leave legacy executor only for legacy diagnostic/live if explicitly allowed by operator policy.                                    |

### 10.3 Missing universal final intent

| Field                | Content                                                                                                                                                                             |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Cycle runtime builds `FinalExecutionIntent` only for specific immediate marketable paths; passive paths become shadow/unsupported or can fall back.                                 |
| Current evidence     | `_attach_corrected_pricing_authority` builds final intent only when marketable and order type immediate; passive creates shadow cost/hypothesis without final intent. ([GitHub][5]) |
| Required code change | Corrected live requires `FinalExecutionIntent` for every submit. Corrected shadow may omit it only if `will_submit=False`.                                                          |
| Required test        | `test_corrected_live_rejects_decision_without_final_execution_intent`.                                                                                                              |
| Acceptance criterion | Any corrected live decision without final intent fails before risk reservation or command insertion.                                                                                |
| Rollback             | Shadow-only corrected path remains.                                                                                                                                                 |

### 10.4 FDR materialization identity drift

| Field                | Content                                                                                                                                               |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Statistical hypothesis selected under one snapshot/cost can be submitted under another without identity amendment.                                    |
| Current evidence     | Selection family ID omits token/snapshot/cost/order policy; cycle attaches cost later. ([GitHub][21])                                                 |
| Required code change | Add `ExecutableTradeHypothesis` binding: selected token, condition/question, executable snapshot id/hash, cost basis id/hash, order policy, venue id. |
| Required test        | `test_snapshot_or_cost_basis_change_after_fdr_rejects_or_amends_hypothesis`.                                                                          |
| Acceptance criterion | Snapshot/cost/order-policy drift produces reject/recompute/amended hypothesis, not silent mutation.                                                   |
| Rollback             | Corrected live remains frozen; shadow can emit `REVIEW_REQUIRED`.                                                                                     |

### 10.5 Order policy contradiction

| Field                | Content                                                                                                                                                                             |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | `limit_may_take_conservative` semantics are muddled with FOK/FAK and post-only.                                                                                                     |
| Current evidence     | Contract has explicit policy/order type coherence but also raises if `limit_may_take_conservative` is not FOK/FAK. ([GitHub][9])                                                    |
| Required code change | Define policies: `MAY_REST_LIMIT_CONSERVATIVE` → GTC/GTD non-post-only; `POST_ONLY_PASSIVE_LIMIT` → GTC/GTD post-only; `IMMEDIATE_LIMIT_SWEEP_DEPTH_BOUND` → FOK/FAK non-post-only. |
| Required test        | `test_order_policy_order_type_mapping_is_explicit`.                                                                                                                                 |
| Acceptance criterion | No enum/string can represent two venue behaviors.                                                                                                                                   |
| Rollback             | Unsupported policy rejects.                                                                                                                                                         |

### 10.6 Buy-NO native quote

| Field                | Content                                                                                                                                                                             |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Buy-NO can use complement as executable cost.                                                                                                                                       |
| Current evidence     | Main `market_analysis.py` native NO support exists; `market_analysis_family_scan.py` fallback still returns `1 - analysis.p_market[idx]`. ([GitHub][3])                             |
| Required code change | In any live/corrected path, buy-NO requires native NO token id and orderbook best ask/depth/hash. Complement only allowed in diagnostic model/probability math with explicit label. |
| Required test        | `test_buy_no_requires_native_no_quote_no_complement_fallback`; static complement gate.                                                                                              |
| Acceptance criterion | Missing NO quote makes corrected live entry fail closed.                                                                                                                            |
| Rollback             | Diagnostic family scan only.                                                                                                                                                        |

### 10.7 Compatibility envelope live path

| Field                | Content                                                                                                                                                                                                              |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Adapter compatibility helper fabricates `legacy:{token_id}` condition and collapsed token identity; could reach live submit if assertion is not called.                                                              |
| Current evidence     | `_create_compat_submission_envelope` creates placeholder identity; `VenueSubmissionEnvelope.assert_live_submit_bound()` detects placeholders; raw `submit()` did not visibly call it before SDK post. ([GitHub][25]) |
| Required code change | Call `envelope.assert_live_submit_bound()` at the first line of every live `submit()` path; `submit_limit_order()` compatibility helper rejects when live mode or corrected mode.                                    |
| Required test        | `test_compatibility_envelope_rejected_in_live_submit`; grep live adapter submit for assertion.                                                                                                                       |
| Acceptance criterion | Placeholder envelope cannot contact SDK in live mode.                                                                                                                                                                |
| Rollback             | Compatibility helper diagnostic only; no live flag.                                                                                                                                                                  |

### 10.8 `market_id` / `token_id` / `condition_id` collapse

| Field                | Content                                                                                                                                                                                                    |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | A token ID can masquerade as market/condition ID, making command journal and reports refer to wrong venue object.                                                                                          |
| Current evidence     | Snapshot/envelope contracts separate fields; compatibility helper collapses them. Official docs distinguish condition ID and token ID. ([GitHub][8])                                                       |
| Required code change | Add `MarketIdentity` and live assertions: condition_id not empty, question_id not empty, yes_token_id != no_token_id, selected token matches outcome, condition_id not `legacy:*`, market_id not token_id. |
| Required test        | `test_live_venue_command_rejects_market_id_equal_token_id`; `test_live_envelope_requires_condition_question_yes_no_token_identity`.                                                                        |
| Acceptance criterion | No live command/envelope can persist corrected evidence with placeholder identity.                                                                                                                         |
| Rollback             | Quarantine placeholder rows as legacy/diagnostic.                                                                                                                                                          |

### 10.9 Quote stale by submit

| Field                | Content                                                                                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Snapshot fresh at selection becomes stale by submit; executor submits same hypothesis with changed executable object.                                                     |
| Current evidence     | `ExecutableMarketSnapshotV2` has `freshness_deadline`; adapter checks snapshot freshness for envelope creation; final submit deadline not proven universal. ([GitHub][8]) |
| Required code change | Add `submit_deadline` to cost basis/final intent; revalidate at risk reservation, command insert, envelope creation, and submit.                                          |
| Required test        | `test_corrected_submit_rejects_snapshot_stale_between_risk_and_submit`.                                                                                                   |
| Acceptance criterion | Stale quote/snapshot before submit yields `REVIEW_REQUIRED` or reject, not repricing.                                                                                     |
| Rollback             | Live off; shadow record stale.                                                                                                                                            |

### 10.10 Corrected entry with legacy exit

| Field                | Content                                                                                                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Failure mode         | Corrected entry cost basis submits but exit uses old `current_market_price` or legacy EV gate.                                                                                       |
| Current evidence     | Exit has improvements but `ExitContext` still has optional best_bid/best_ask and `execute_exit` only blocks missing `current_market_price`. ([GitHub][10])                           |
| Required code change | Any position with corrected entry semantics must require `ExitExecutableQuote` for economic automated exit, except explicit manual/emergency with exclusion from corrected evidence. |
| Required test        | `test_corrected_entry_cannot_use_legacy_exit_fallback`.                                                                                                                              |
| Acceptance criterion | Corrected position + missing exit quote = no automated economic exit.                                                                                                                |
| Rollback             | Manual tagged override only.                                                                                                                                                         |

### 10.11 Held-token SELL bid exit

| Field                | Content                                                                                                                                           |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Exit proceeds use VWMP/current/diagnostic price rather than held-token best bid.                                                                  |
| Current evidence     | `HeldTokenMonitorQuote` records best_bid/best_ask/diagnostic_market_price; buy-NO tests already enforce best_bid in some EV gates. ([GitHub][18]) |
| Required code change | `ExitExecutableQuote.best_bid` and bid depth are the sole corrected sell value.                                                                   |
| Required test        | `test_buy_no_exit_uses_best_bid_not_vwmp`; `test_buy_yes_exit_uses_best_bid_not_current_market_price`.                                            |
| Acceptance criterion | Changing diagnostic market price without best_bid change does not change sell value.                                                              |
| Rollback             | Hold to settlement or manual excluded exit.                                                                                                       |

### 10.12 Partial fill / cancel / unknown fill

| Field                | Content                                                                                                                                              |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Submitted limit/target notional treated as filled cost; partial cancel or unknown side effect reported as corrected P&L.                             |
| Current evidence     | Command grammar and `venue_trade_facts`/`position_lots` model partial, unknown, cancel; `Position` still has fallback effective cost. ([GitHub][26]) |
| Required code change | Add `FillAuthority` dominance: corrected P&L only with confirmed partial/full/cancelled remainder facts and fill-derived cost.                       |
| Required test        | `test_partial_fill_then_cancel_remainder_not_full_fill`; `test_submit_unknown_side_effect_blocks_corrected_pnl`.                                     |
| Acceptance criterion | Unknown fill = `REVIEW_REQUIRED`, not loss/profit/fill.                                                                                              |
| Rollback             | Position quarantined pending reconciliation.                                                                                                         |

### 10.13 Command journal same-object proof

| Field                | Content                                                                                                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure mode         | Persist-before-submit proves ordering but not that command/order/fill corresponds to same cost basis/snapshot/final intent/envelope.                             |
| Current evidence     | Command journal strong; tests lack `cost_basis`/same-object patterns. ([GitHub][26])                                                                             |
| Required code change | Store `final_intent_id/hash`, `cost_basis_id/hash`, `snapshot_id/hash`, `envelope_id/hash`, `order_id`, `fill_fact_id`, `position_lot_id` in a verifiable chain. |
| Required test        | `test_command_journal_proves_cost_basis_snapshot_envelope_same_object`.                                                                                          |
| Acceptance criterion | Any mismatch prevents corrected P&L and live continuation.                                                                                                       |
| Rollback             | Command row to `REVIEW_REQUIRED`.                                                                                                                                |

### 10.14 Adapter live-bound assertion reachability

| Field                | Content                                                                                                                                                |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Failure mode         | Assertion exists but may not be called on real submit.                                                                                                 |
| Current evidence     | `VenueSubmissionEnvelope.assert_live_submit_bound()` exists; adapter `submit()` body inspected did not visibly call it before SDK post. ([GitHub][24]) |
| Required code change | Assertion called at adapter boundary and executor boundary; tests monkeypatch fake client to prove no SDK call on placeholder.                         |
| Required test        | `test_adapter_submit_calls_assert_live_submit_bound_before_sdk_contact`.                                                                               |
| Acceptance criterion | Fake SDK call count remains zero for placeholder envelope.                                                                                             |
| Rollback             | Disable adapter compatibility submit.                                                                                                                  |

---

## 11. Stage 2 live-safe minimal spine

### Contract: `MarketPriorDistribution`

| Field                  | Specification                                                                                                                                                                                                                                                    |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Separates named market-prior estimator from raw quote/display price.                                                                                                                                                                                             |
| Fields                 | `prior_id`, `estimator_version`, `probabilities`, `bin_labels`, `side_convention`, `source_quote_hashes`, `family_complete`, `vig_treatment`, `de_vig_method`, `freshness`, `liquidity`, `negative_risk_policy`, `validated_for_live`, `validation_evidence_id`. |
| Invariants             | Raw VWMP/current price is not a prior unless converted by named estimator; `model_only_v1` rejects market input; unvalidated estimators cannot promote live.                                                                                                     |
| Producer               | Strategy/market fusion prior builder.                                                                                                                                                                                                                            |
| Consumer               | Posterior fusion only, never executor or Kelly.                                                                                                                                                                                                                  |
| Persistence            | `market_prior_id`, `market_prior_version`, `prior_hash`, source quote hashes.                                                                                                                                                                                    |
| Tests                  | Market-prior change affects posterior, not token/snapshot/cost; raw quote as prior rejected in corrected mode.                                                                                                                                                   |
| Rejected legacy inputs | bare `p_market`, `vwmp`, `current_market_price`, raw quote vector without estimator.                                                                                                                                                                             |

### Contract: `ExecutableEntryCostBasis`

| Field                  | Specification                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Sole authority for corrected entry executable cost and Kelly price.                                                                                                                                                                                                                                                                                                                                                                                                 |
| Fields                 | `cost_basis_id`, `cost_basis_hash`, `condition_id`, `question_id`, `gamma_market_id`, `selected_token_id`, `outcome_label`, `direction`, `side=BUY`, `best_ask`, `ask_depth`, `limit_price`, `submitted_shares`, `target_notional_usd`, `fee_rate`, `fee_source`, `fee_adjusted_execution_price`, `tick_size`, `min_order_size`, `neg_risk`, `quote_snapshot_id/hash`, `orderbook_hash`, `captured_at`, `freshness_deadline`, `submit_deadline`, `order_policy_id`. |
| Invariants             | BUY cost is ask/limit/depth-derived; complement not allowed for NO; p/value bounded; hash covers venue identity, quote, fee, policy, size.                                                                                                                                                                                                                                                                                                                          |
| Producer               | Cost basis builder from `ExecutableMarketSnapshotV2`.                                                                                                                                                                                                                                                                                                                                                                                                               |
| Consumer               | Kelly, final intent, command journal, position lot, reports.                                                                                                                                                                                                                                                                                                                                                                                                        |
| Persistence            | `entry_cost_basis_id/hash`, `execution_cost_basis_version`, snapshot link.                                                                                                                                                                                                                                                                                                                                                                                          |
| Tests                  | Kelly rejects raw price; fee/order policy changes hash; stale cost basis rejects.                                                                                                                                                                                                                                                                                                                                                                                   |
| Rejected legacy inputs | `BinEdge.entry_price`, `p_market`, `vwmp`, posterior, display price.                                                                                                                                                                                                                                                                                                                                                                                                |

### Contract: `ExecutableTradeHypothesis`

| Field                  | Specification                                                                                                                                                                                                                                                                                                           |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Bridges statistical/FDR hypothesis to executable economic object.                                                                                                                                                                                                                                                       |
| Fields                 | `hypothesis_id`, `statistical_family_id`, `selected_hypothesis_id`, `condition_id`, `question_id`, `selected_token_id`, `outcome_label`, `direction`, `posterior_distribution_id`, `market_prior_id`, `executable_snapshot_id/hash`, `cost_basis_id/hash`, `order_policy_id`, `venue_id`, `amendment_of`, `created_at`. |
| Invariants             | Snapshot/cost/order policy changes require new or amended executable hypothesis.                                                                                                                                                                                                                                        |
| Producer               | FDR materialization after selection and cost basis creation.                                                                                                                                                                                                                                                            |
| Consumer               | Final intent, reports, promotion.                                                                                                                                                                                                                                                                                       |
| Persistence            | `execution_hypothesis_id`, `fdr_family_id`, cost/snapshot hashes.                                                                                                                                                                                                                                                       |
| Tests                  | Snapshot/cost change after FDR rejects/recomputes/amends.                                                                                                                                                                                                                                                               |
| Rejected legacy inputs | naked FDR p-value/hypothesis without executable identity.                                                                                                                                                                                                                                                               |

### Contract: `FinalExecutionIntent`

| Field                  | Specification                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Immutable command authority for live submit.                                                                                                                                                                                                                                                                                                                                                  |
| Fields                 | `intent_id/hash`, `trade_hypothesis_id`, `cost_basis_id/hash`, `snapshot_id/hash`, `condition_id`, `question_id`, `selected_token_id`, `outcome_label`, `side`, `final_limit_price`, `submitted_shares`, `target_notional_usd`, `order_policy_id`, `order_type`, `post_only`, `tick_size`, `min_order_size`, `neg_risk`, `fee_source`, `freshness_deadline`, `submit_deadline`, `created_at`. |
| Invariants             | Executor cannot mutate token/limit/side/size/policy; submit only before deadline; hash validates.                                                                                                                                                                                                                                                                                             |
| Producer               | Corrected entry planner.                                                                                                                                                                                                                                                                                                                                                                      |
| Consumer               | Executor, venue envelope, command journal.                                                                                                                                                                                                                                                                                                                                                    |
| Persistence            | command `intent_hash`, envelope link, final intent table or payload.                                                                                                                                                                                                                                                                                                                          |
| Tests                  | Executor rejects missing/mismatched cost/snapshot/hash; no repricing.                                                                                                                                                                                                                                                                                                                         |
| Rejected legacy inputs | legacy `ExecutionIntent` for corrected live.                                                                                                                                                                                                                                                                                                                                                  |

### Contract: `ExitExecutableQuote`

| Field                  | Specification                                                                                                                                                                                                                                                                                          |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Purpose                | Sole corrected automated economic exit value.                                                                                                                                                                                                                                                          |
| Fields                 | `exit_quote_id/hash`, `position_lot_id`, `held_token_id`, `condition_id`, `question_id`, `side=SELL`, `best_bid`, `bid_depth`, `fee_rate`, `fee_source`, `tick_size`, `min_order_size`, `neg_risk`, `quote_snapshot_id/hash`, `orderbook_hash`, `captured_at`, `freshness_deadline`, `exit_policy_id`. |
| Invariants             | SELL value is best bid; missing/stale best bid blocks corrected economic exit except manual tagged override excluded from corrected evidence.                                                                                                                                                          |
| Producer               | Monitor quote refresher / exit cost basis builder.                                                                                                                                                                                                                                                     |
| Consumer               | Exit EV gate, exit final intent, reports.                                                                                                                                                                                                                                                              |
| Persistence            | `exit_cost_basis_id/hash`, exit snapshot link.                                                                                                                                                                                                                                                         |
| Tests                  | Held-token best bid required; diagnostic/current price not sell value.                                                                                                                                                                                                                                 |
| Rejected legacy inputs | current market price, VWMP, midpoint, posterior, entry price.                                                                                                                                                                                                                                          |

### Contract: `OrderPolicy`

| Field                  | Specification                                                                                                                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Purpose                | Canonicalizes order behavior and cost identity.                                                                                                                                                                    |
| Fields                 | `order_policy_id`, `name`, `order_type`, `post_only`, `may_take`, `may_rest`, `cancel_after_seconds`, `max_slippage_bps`, `depth_required`, `marketable`, `risk_tags`.                                             |
| Invariants             | `POST_ONLY_PASSIVE_LIMIT` = GTC/GTD + post_only + no crossing; `MAY_REST_LIMIT_CONSERVATIVE` = GTC/GTD non-post-only; `IMMEDIATE_LIMIT_SWEEP_DEPTH_BOUND` = FOK/FAK non-post-only; policy changes cost basis hash. |
| Producer               | Entry policy selector.                                                                                                                                                                                             |
| Consumer               | Cost basis, final intent, adapter.                                                                                                                                                                                 |
| Persistence            | `order_policy_id`, `order_type`, `post_only`.                                                                                                                                                                      |
| Tests                  | Policy/order type mapping explicit; invalid combos reject.                                                                                                                                                         |
| Rejected legacy inputs | ambiguous `limit_may_take_conservative` without venue behavior.                                                                                                                                                    |

### Contract: `VenueSubmissionEnvelope`

| Field                  | Specification                                                                                                                |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Immutable provenance envelope around venue submit.                                                                           |
| Fields                 | Existing fields plus `final_intent_id/hash`, `cost_basis_id/hash`, `snapshot_id/hash`, `live_submit_bound_asserted_at`.      |
| Invariants             | No `legacy:*`, no `legacy-compat`, no yes=no token, selected token matches outcome, live-bound assertion before SDK contact. |
| Producer               | Adapter/envelope builder from final intent and snapshot.                                                                     |
| Consumer               | Adapter submit, command journal, reports.                                                                                    |
| Persistence            | `venue_submission_envelopes`.                                                                                                |
| Tests                  | Placeholder cannot live submit; envelope hash matches command.                                                               |
| Rejected legacy inputs | adapter `submit_limit_order()` compatibility envelope for live/corrected evidence.                                           |

### Contract: `PositionLot`

| Field                  | Specification                                                                                                                                                                                                                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Purpose                | Fill-derived economic position authority.                                                                                                                                                                                                                                      |
| Fields                 | `lot_id`, `position_id`, `source_command_id`, `order_id`, `trade_fact_ids`, `state`, `shares_submitted`, `shares_filled`, `shares_remaining`, `avg_fill_price`, `filled_cost_basis_usd`, `fees_paid`, `entry_cost_basis_id/hash`, `held_token_id`, `created_at`, `updated_at`. |
| Invariants             | Corrected P&L requires fill-derived lot; target notional/submitted shares are not filled cost.                                                                                                                                                                                 |
| Producer               | Fill tracker / venue trade facts.                                                                                                                                                                                                                                              |
| Consumer               | Portfolio projection, exit, settlement, report.                                                                                                                                                                                                                                |
| Persistence            | `position_lots` plus projection.                                                                                                                                                                                                                                               |
| Tests                  | Partial fill/cancel/unknown fill.                                                                                                                                                                                                                                              |
| Rejected legacy inputs | size_usd/entry_price as corrected fill cost.                                                                                                                                                                                                                                   |

### Contract: `EntryEconomicsAuthority`

| Field                  | Specification                                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------- |
| Purpose                | Labels the authority for entry economics.                                                                     |
| Values                 | `legacy_unknown`, `model_edge_price`, `submitted_limit`, `avg_fill_price`, `corrected_executable_cost_basis`. |
| Invariants             | Corrected P&L requires `avg_fill_price` or `corrected_executable_cost_basis` with valid fill authority.       |
| Producer               | Entry/fill materializer.                                                                                      |
| Consumer               | Reports, settlement, portfolio.                                                                               |
| Persistence            | `entry_economics_authority`.                                                                                  |
| Tests                  | Authority matrix.                                                                                             |
| Rejected legacy inputs | implicit assumptions from `entry_price`.                                                                      |

### Contract: `FillAuthority`

| Field                  | Specification                                                                                                                                   |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Labels fill status and whether cost is authoritative.                                                                                           |
| Values                 | `none`, `optimistic_submitted`, `venue_confirmed_partial`, `venue_confirmed_full`, `cancelled_remainder`, `settled`, `review_required_unknown`. |
| Invariants             | Unknown side effect blocks corrected P&L; partial fill explicitly represented.                                                                  |
| Producer               | Command/fill tracker.                                                                                                                           |
| Consumer               | Portfolio/report/settlement.                                                                                                                    |
| Persistence            | `fill_authority`, `shares_filled`, `filled_cost_basis_usd`.                                                                                     |
| Tests                  | Unknown fill REVIEW_REQUIRED; partial/cancel explicit.                                                                                          |
| Rejected legacy inputs | order accepted = fill; submitted shares = filled shares.                                                                                        |

### Contract: `PricingSemanticsVersion`

| Field                  | Specification                                                                                                                                                        |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Cohort identity for row economics.                                                                                                                                   |
| Values                 | `legacy_price_probability_conflated`, `model_only_diagnostic`, `corrected_executable_shadow`, `corrected_executable_live`, `legacy_unclassified`, `review_required`. |
| Invariants             | Mixed versions hard-fail in promotion-grade reports.                                                                                                                 |
| Producer               | Entry/report/migration classifier.                                                                                                                                   |
| Consumer               | Reports, promotion gates.                                                                                                                                            |
| Persistence            | `pricing_semantics_version`.                                                                                                                                         |
| Tests                  | Mixed cohort hard-fail.                                                                                                                                              |
| Rejected legacy inputs | absent semantic version treated as corrected.                                                                                                                        |

### Contract: `ReportingCohort`

| Field                  | Specification                                                                                                                                                            |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Purpose                | Defines report aggregation eligibility.                                                                                                                                  |
| Fields                 | `cohort_id`, `pricing_semantics_version`, `entry_authority`, `fill_authority`, `exit_authority`, `settlement_authority`, `promotion_eligible`, `diagnostic_only_reason`. |
| Invariants             | No warning-only mixed cohort; promotion only corrected live executable with fill/exit/settlement evidence.                                                               |
| Producer               | Report classifier.                                                                                                                                                       |
| Consumer               | Reports/dashboards/promotions.                                                                                                                                           |
| Persistence            | report metadata.                                                                                                                                                         |
| Tests                  | Every report path calls cohort gate.                                                                                                                                     |
| Rejected legacy inputs | model skill / diagnostic replay as promotion evidence.                                                                                                                   |

### Contract: `MarketIdentity`

| Field                  | Specification                                                                                                                                                                          |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Central venue identity.                                                                                                                                                                |
| Fields                 | `gamma_event_id`, `gamma_market_id`, `condition_id`, `question_id`, `yes_token_id`, `no_token_id`, `selected_token_id`, `outcome_label`, `event_slug`, `market_slug`, `negative_risk`. |
| Invariants             | condition/question/tokens non-empty; YES != NO; selected token matches outcome; market/condition/token not collapsed.                                                                  |
| Producer               | Market scanner/snapshot.                                                                                                                                                               |
| Consumer               | Cost basis, final intent, envelope, command, reports.                                                                                                                                  |
| Persistence            | market identity table or snapshot fields.                                                                                                                                              |
| Tests                  | Condition/token collapse rejected.                                                                                                                                                     |
| Rejected legacy inputs | token-only market identity.                                                                                                                                                            |

### Contract: `CityIdentity`

| Field                  | Specification                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------- |
| Purpose                | Central physical city and forecast identity.                                                      |
| Fields                 | `city_id`, `city_name`, `lat`, `lon`, `timezone`, `settlement_unit`, `station/source`, `cluster`. |
| Invariants             | No duplicated city_id transforms; city/timezone/source stable.                                    |
| Producer               | Config/city registry.                                                                             |
| Consumer               | Strategy, monitor, settlement, reports.                                                           |
| Persistence            | city_id/city_name/timezone.                                                                       |
| Tests                  | City_id roundtrip; no duplicate transform.                                                        |
| Rejected legacy inputs | string-only city without identity.                                                                |

### Contract: `TimeIdentity`

| Field                  | Specification                                                                                                                                                                                             |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Prevents UTC/local-day/forecast/settlement confusion.                                                                                                                                                     |
| Fields                 | `target_local_date`, `target_timezone`, `decision_utc`, `forecast_issue_utc`, `forecast_valid_utc`, `snapshot_captured_utc`, `submit_utc`, `fill_utc`, `settlement_recorded_utc`, `redeem_confirmed_utc`. |
| Invariants             | Target local date cannot be inferred from UTC timestamp alone.                                                                                                                                            |
| Producer               | Entry runtime/time context.                                                                                                                                                                               |
| Consumer               | Settlement, monitor, reports.                                                                                                                                                                             |
| Persistence            | role-specific timestamp fields.                                                                                                                                                                           |
| Tests                  | Local-day/UTC mismatch tests.                                                                                                                                                                             |
| Rejected legacy inputs | generic `timestamp` as universal time.                                                                                                                                                                    |

### Contract: `SettlementStatus`

| Field                  | Specification                                                                                                                                                         |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Purpose                | Splits resolution, DB recording, payout eligibility, redeem request, redeem confirmation.                                                                             |
| Values                 | `UNRESOLVED`, `VENUE_RESOLVED`, `SETTLEMENT_RECORDED`, `POSITION_SETTLEMENT_EVALUATED`, `PAYOUT_ELIGIBLE`, `REDEEM_REQUESTED`, `REDEEM_CONFIRMED`, `REVIEW_REQUIRED`. |
| Invariants             | Settlement recorded is not redeemed payout; settlement result is not fill-derived P&L.                                                                                |
| Producer               | Harvester/redeem command ledger.                                                                                                                                      |
| Consumer               | Reports, promotion, portfolio.                                                                                                                                        |
| Persistence            | settlement/redeem fact tables.                                                                                                                                        |
| Tests                  | Settlement/redeem status split.                                                                                                                                       |
| Rejected legacy inputs | broad `is_settled` boolean.                                                                                                                                           |

---

## 12. Stage 3 promotion/reporting/evidence integrity

### Cohort matrix

| Cohort                               | Entry evidence                                        | Fill evidence                       | Exit evidence                       | Settlement/redeem evidence | Reportable P&L                                                | Promotion eligible      |
| ------------------------------------ | ----------------------------------------------------- | ----------------------------------- | ----------------------------------- | -------------------------- | ------------------------------------------------------------- | ----------------------- |
| `legacy_price_probability_conflated` | `entry_price/p_market/vwmp` legacy scalar             | May be absent/optimistic            | Legacy/current price                | Often settlement-only      | Diagnostic only                                               | No                      |
| `model_only_diagnostic`              | model/posterior skill only                            | None                                | None or simulated                   | None                       | No                                                            | No                      |
| `corrected_executable_shadow`        | snapshot/cost_basis/final-intent candidate, no submit | None                                | Shadow only                         | None                       | No                                                            | No                      |
| `corrected_submit_unknown_fill`      | final intent/envelope/command                         | unknown side effect                 | none                                | none                       | No; `REVIEW_REQUIRED`                                         | No                      |
| `corrected_executable_live_partial`  | final intent/cost/snapshot                            | confirmed partial + remainder state | maybe                               | maybe                      | Yes only for confirmed filled lot slice                       | Conditional after gates |
| `corrected_executable_live_full`     | final intent/cost/snapshot                            | confirmed full fill                 | held-token SELL quote or settlement | status split               | Yes                                                           | Yes if all gates pass   |
| `manual_emergency_exit`              | any                                                   | may exist                           | manual/emergency tagged             | maybe                      | Excluded from corrected economic evidence unless criteria met | No by default           |
| `chain_only_quarantined`             | none                                                  | chain-only                          | none                                | none                       | No                                                            | No                      |

### Row classification

| Row condition                                         | Classification                                                        |
| ----------------------------------------------------- | --------------------------------------------------------------------- |
| No `pricing_semantics_version`                        | `legacy_unclassified`; not corrected.                                 |
| `entry_price` present but no cost_basis/snapshot/fill | `legacy_price_probability_conflated`.                                 |
| Snapshot/cost exists but no live submit               | `corrected_executable_shadow`.                                        |
| Submit command exists but fill unknown                | `corrected_submit_unknown_fill`; `REVIEW_REQUIRED`.                   |
| Venue confirmed partial fill + cancel remainder       | `corrected_executable_live_partial`, P&L only on filled slice.        |
| Venue confirmed full fill + lot                       | `corrected_executable_live_full`.                                     |
| Settlement recorded but no redeem confirmation        | Settlement status only; not payout confirmed.                         |
| Legacy open position with token known                 | Exit allowed only through fresh held-token SELL quote; report legacy. |
| Chain-only token no local decision                    | `chain_only_quarantined`; no strategy attribution.                    |

### Migration sequencing

1. Add fields only.
2. Run read-only census.
3. Classify rows.
4. Create report views that expose cohort and eligibility.
5. Refuse mixed report cohorts.
6. Only after tests pass, allow corrected-live rows to accumulate.
7. Never update legacy rows into corrected economics unless point-in-time executable snapshot/depth/fill facts exist.

### Report hard-fail rules

| Rule                                                           | Action                                           |
| -------------------------------------------------------------- | ------------------------------------------------ |
| Report contains more than one promotion-grade pricing cohort   | Raise error; no report.                          |
| Corrected row lacks cost_basis hash                            | Raise error.                                     |
| Corrected row lacks fill authority                             | Raise error.                                     |
| Corrected row has placeholder `condition_id` or token collapse | Raise error.                                     |
| Legacy rows requested in corrected P&L report                  | Raise error or require explicit diagnostic mode. |
| Diagnostic replay included in promotion packet                 | Raise error.                                     |
| Backtest ROI requested as live promotion evidence              | Raise error.                                     |
| Settlement recorded without redeem confirmation labeled payout | Raise error.                                     |

### Backtest restrictions

* `ECONOMICS` remains tombstoned until executable venue substrate exists.
* `SKILL` backtests may measure model skill, not executable P&L.
* Backtest output must declare `purpose`, `authority_scope`, and `promotion_authority=False` unless corrected live executable facts exist.
* Gamma/current/display price-only histories cannot become corrected economics.
* Depth/orderbook hash/fee/tick/min/negative-risk/fill/settlement identity are required for economics.

### Diagnostic-only labels

Required labels:

* `diagnostic_non_promotion`;
* `legacy_price_probability_conflated`;
* `model_only_skill`;
* `shadow_cost_no_fill`;
* `tick_replay_no_depth`;
* `gamma_price_only`;
* `complement_no_executable_no_quote`;
* `manual_emergency_exit_excluded`.

### Promotion eligibility

A strategy/version is promotion-eligible only when:

1. All live blockers closed.
2. Corrected live entries use native token executable cost basis.
3. Executor validates immutable final intent.
4. Command/envelope/fill/lot hash-chain proves same object.
5. Exit uses held-token SELL quote or settlement/redeem status with fill authority.
6. Reports hard-fail mixed cohorts.
7. Backtests/diagnostics are excluded or clearly non-promotion.
8. No open `REVIEW_REQUIRED` rows in promoted cohort.
9. CI gates pass.
10. Operator live runbook exists.

### No-backfill rule

Historical rows cannot be reclassified as corrected executable economics unless they already contain:

* point-in-time condition/question/token identity;
* executable orderbook snapshot/depth/hash;
* tick/min/fee/negative-risk facts;
* immutable final intent or equivalent order record;
* venue command/envelope hash;
* confirmed fill facts;
* position lot;
* settlement/redeem status where used.

Otherwise they remain legacy/diagnostic.

---

## 13. Stage 4 architecture improvement

### `BinEdge` shrink/quarantine

| Item               | Plan                                                                                                                                  |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| Problem            | `BinEdge` owns statistical, belief, quote, executable, and reporting facts.                                                           |
| Current evidence   | Fields include `p_market`, `entry_price`, `vwmp`, `p_posterior`, `edge`, p-value, and support metadata. ([GitHub][2])                 |
| Target             | `BinEdge` becomes selection hypothesis evidence only: bin, direction, p_model, posterior id, p-value, edge score, selection metadata. |
| Quarantined fields | `entry_price`, `vwmp`, `p_market` only allowed as legacy/diagnostic display fields, not cost authority.                               |
| Tests              | Static gate: `BinEdge.vwmp`/`entry_price` cannot feed corrected Kelly/executor/cost basis.                                            |

### Lifecycle vocabulary cleanup

| Item    | Plan                                                                                                                                                                                                          |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Problem | `pending_entry`, `pending_tracked`, `active`, `entered`, `holding`, `day0_window`, `pending_exit`, `economically_closed`, `settled`, `voided`, `quarantined`, `admin_closed` mix runtime and evidence states. |
| Target  | `LifecycleState` for runtime; `FillAuthority` for fill facts; `SettlementStatus` for resolution/redeem; `ReportingCohort` for reports.                                                                        |
| Tests   | State transition grammar; no code compares raw strings outside lifecycle manager.                                                                                                                             |

### `is_settled` split

| Item            | Plan                                                                                                                                 |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Problem         | Settlement/result/redeem can collapse into broad settled state.                                                                      |
| Target statuses | `VENUE_RESOLVED`, `SETTLEMENT_RECORDED`, `POSITION_SETTLEMENT_EVALUATED`, `PAYOUT_ELIGIBLE`, `REDEEM_REQUESTED`, `REDEEM_CONFIRMED`. |
| Tests           | Recorded settlement cannot be reported as redeemed payout.                                                                           |

### City/time/high-low centralization

| Item    | Plan                                                                                                                                                             |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Problem | Weather markets depend on city, timezone, target local date, high/low metric, unit. Missing metric can default to high with UNVERIFIED authority. ([GitHub][28]) |
| Target  | `CityIdentity`, `TimeIdentity`, `MetricIdentity` required at entry, monitor, settlement, report.                                                                 |
| Tests   | LOW position missing metric cannot silently use HIGH for corrected evidence; target local date explicit.                                                         |

### Docs/AGENTS rewrite

| Item    | Plan                                                                                                       |
| ------- | ---------------------------------------------------------------------------------------------------------- |
| Problem | Docs still contain α-weighted raw `P_market` and “guaranteed fill” language. ([GitHub][15])                |
| Target  | Docs state four-plane model, venue reality, live freeze gates, report cohort law, and no docs-only repair. |
| Tests   | Static scan for forbidden language without legacy/diagnostic qualifier.                                    |

### Giant function decomposition

| Function                                            | Target decomposition                                                                                             |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `cycle_runtime._attach_corrected_pricing_authority` | snapshot validator, cost basis builder, executable hypothesis binder, final intent builder, decision attachment. |
| `executor.create_execution_intent`                  | legacy-only builder; corrected final-intent executor separate.                                                   |
| `monitor_refresh.refresh_position`                  | quote refresh, probability refresh, topology refresh, edge context construction, telemetry.                      |
| `harvester.run_harvester`                           | settlement poll, outcome fact, learning pair, position settlement, redeem command, report export.                |

### Static authority scans

Required static scans:

```bash
rg "compute_native_limit_price" src/execution src/engine
rg "p_market|vwmp|entry_price" src/engine src/execution src/strategy
rg "guaranteed fill|P_market|alpha|α" README.md AGENTS.md src docs
rg "except Exception: pass|except Exception" src/engine src/execution src/state src/venue scripts
rg "market_id.*token_id|token_id.*market_id|legacy:" src tests scripts
```

---

## 14. Stage 5 performance/staleness/telemetry

| Item                     | Requirement                                                                                                                                                                                                                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Quote freshness deadline | Every executable quote/cost basis has `captured_at`, `freshness_deadline`, `quote_age_ms`, `orderbook_hash`.                                                                                                                                                                                |
| Submit deadline          | `FinalExecutionIntent.submit_deadline` checked before risk reservation, command insert, envelope creation, SDK submit.                                                                                                                                                                      |
| Snapshot age             | `ExecutableMarketSnapshotV2` hash and age logged in command/envelope.                                                                                                                                                                                                                       |
| Monitor batching         | Batch held-token orderbook reads by token; cache sibling topology per condition/event per cycle.                                                                                                                                                                                            |
| Topology caching         | Market sibling topology must have authority status and TTL; stale topology blocks corrected monitor evidence.                                                                                                                                                                               |
| Adapter latency          | Record `preflight_ms`, `envelope_build_ms`, `sdk_submit_ms`, `ack_ms`.                                                                                                                                                                                                                      |
| Telemetry counters       | `corrected_entry_rejected_missing_cost_basis`, `corrected_entry_rejected_stale_snapshot`, `compat_submit_rejected`, `buy_no_native_quote_missing`, `exit_quote_missing`, `exit_quote_stale`, `fill_unknown_review_required`, `mixed_cohort_report_blocked`, `docs_forbidden_language_hits`. |
| Fail-closed behavior     | Breaching quote/snapshot/submit deadline rejects or marks `REVIEW_REQUIRED`; executor never reprices as freshness fix.                                                                                                                                                                      |
| Tests                    | Artificial delayed submit rejects; monitor quote older than threshold blocks exit; telemetry counter increments.                                                                                                                                                                            |

---

## 15. Stage 6 orphan cleanup

| Orphan path                                                        | Risk                               | Current evidence                                                               | Decision                                                               | Prerequisite tests                          | Rollback                                        |
| ------------------------------------------------------------------ | ---------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------- | ------------------------------------------- | ----------------------------------------------- |
| `market_analysis_family_scan` buy-NO complement fallback           | Executable NO price from `1 - YES` | Confirmed fallback. ([GitHub][27])                                             | Quarantine diagnostic-only first, then delete if no live reachability. | Native NO required; complement static gate. | Restore as diagnostic-only helper with no execution-mode selector. |
| Adapter `submit_limit_order()` compatibility helper                | Placeholder identity may submit    | Helper fabricates `legacy:{token_id}` and yes=no token. ([GitHub][25])         | Live-disable; retain only for legacy tests if needed.                  | Placeholder no SDK call in live.            | Re-enable only in test/fake venue mode.         |
| Legacy `compute_native_limit_price` in corrected-adjacent executor | Executor price authority           | Import remains. ([GitHub][6])                                                  | Static-gate out of corrected path; later isolate legacy module.        | No corrected call stack.                    | Keep legacy diagnostic.                         |
| `BinEdge.entry_price/vwmp` cost authority                          | Scalar aliasing                    | Fields remain. ([GitHub][2])                                                   | Quarantine; remove from corrected cost/sizing.                         | Static gate.                                | Legacy display only.                            |
| Diagnostic replay complement/tick path                             | Promotion contamination            | Replay diagnostic-only but converts buy_no ticks by complement. ([GitHub][11]) | Keep diagnostic-only; block promotion.                                 | Promotion excludes diagnostic replay.       | N/A.                                            |
| Legacy report/replay approximations                                | False corrected P&L                | Some gates exist, universal unknown.                                           | Audit all reports; label or delete.                                    | Report registry cohort gate.                | Diagnostic mode only.                           |
| Position all-plane fields                                          | Mutability and fallback            | Position owns many planes. ([GitHub][10])                                      | Convert to projection; lots/fills authoritative.                       | Fill authority tests.                       | Keep projection compatibility.                  |
| Chain reconciliation entry mutation                                | Entry economics corruption         | Reconciliation updates entry_price/cost/size/shares. ([GitHub][28])            | Stop mutating entry economics; write reconciliation facts.             | Chain mutation test.                        | Quarantine corrected rows until fixed.          |
| Docs-only authority claims                                         | Future agent regression            | Stale README/AGENTS.                                                           | Rewrite after gates; static scan.                                      | Docs forbidden-language gate.               | Docs revert does not change runtime.            |
| Broad money-path exceptions                                        | Silent evidence loss               | Monitor broad exceptions visible; harvester partial.                           | Replace with typed REVIEW_REQUIRED.                                    | Broad-except scan.                          | Explicit operator alert.                        |

---

## 16. Migration and DB plan

### Additive fields

Add these columns/tables where absent. Do not destructively rewrite.

| Table/surface                | Additive fields                                                                                                                                                                                                                                                                                                                                                                                                           |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `trade_decisions`            | `pricing_semantics_version`, `market_prior_id`, `market_prior_version`, `execution_hypothesis_id`, `entry_cost_basis_id`, `entry_cost_basis_hash`, `entry_economics_authority`, `quote_snapshot_id`, `quote_snapshot_hash`, `order_policy_id`, `final_intent_id`, `final_intent_hash`, `condition_id`, `question_id`, `gamma_market_id`, `selected_token_id`, `held_token_id`, `corrected_executable_economics_eligible`. |
| `probability_trace_fact`     | `posterior_distribution_id`, `market_prior_id`, `prior_estimator_version`, `probability_semantics_version`, `raw_quote_excluded_from_prior`.                                                                                                                                                                                                                                                                              |
| `selection_family_fact`      | `statistical_family_id`, `decision_snapshot_id`, `city_id`, `time_identity_id`, `metric_identity`, `selection_semantics_version`.                                                                                                                                                                                                                                                                                         |
| `selection_hypothesis_fact`  | `statistical_hypothesis_id`, `executable_hypothesis_id`, `selected_token_id`, `executable_snapshot_id/hash`, `cost_basis_id/hash`, `order_policy_id`, `amendment_of`.                                                                                                                                                                                                                                                     |
| `executable_cost_basis`      | New or existing table with `cost_basis_id/hash`, snapshot link, token, side, final limit, fee/tick/min/depth/neg-risk, freshness, submit deadline.                                                                                                                                                                                                                                                                        |
| `venue_commands`             | `final_intent_id/hash`, `cost_basis_id/hash`, `snapshot_id/hash`, `envelope_id`, `pricing_semantics_version`, `intent_kind`, `submit_deadline`.                                                                                                                                                                                                                                                                           |
| `venue_submission_envelopes` | Existing identity fields plus `final_intent_id/hash`, `cost_basis_id/hash`, `snapshot_id/hash`, `live_submit_bound_asserted_at`.                                                                                                                                                                                                                                                                                          |
| `venue_trade_facts`          | `fill_authority`, `maker_taker`, `fee_paid`, `cost_basis_id/hash`, `snapshot_hash`.                                                                                                                                                                                                                                                                                                                                       |
| `position_lots`              | Existing lot table plus `entry_cost_basis_hash`, `final_intent_hash`, `envelope_id/hash`, `shares_submitted`, `shares_filled`, `shares_remaining`, `avg_fill_price`, `filled_cost_basis_usd`, `fill_authority`.                                                                                                                                                                                                           |
| `positions/projection`       | `pricing_semantics_version`, `entry_economics_authority`, `fill_authority`, `corrected_executable_economics_eligible`, `exit_cost_basis_id/hash`, `settlement_status`.                                                                                                                                                                                                                                                    |
| `settlement/redeem facts`    | `settlement_status`, `venue_resolved_at`, `settlement_recorded_at`, `payout_eligible`, `redeem_requested_at`, `redeem_confirmed_at`, `redeem_tx_hash`.                                                                                                                                                                                                                                                                    |
| report metadata              | `reporting_cohort`, `diagnostic_only`, `promotion_eligible`, `mixed_cohort_blocked`.                                                                                                                                                                                                                                                                                                                                      |

### Classification matrix

| Existing row facts                             | New classification                                        | Corrected economics eligible                  |
| ---------------------------------------------- | --------------------------------------------------------- | --------------------------------------------- |
| No snapshot/cost/fill fields                   | `legacy_price_probability_conflated`                      | No                                            |
| `p_market/entry_price/vwmp` only               | `legacy_price_probability_conflated`                      | No                                            |
| Posterior/model only, no venue cost            | `model_only_diagnostic`                                   | No                                            |
| Snapshot + cost basis, no submit               | `corrected_executable_shadow`                             | No                                            |
| Final intent + command + no fill               | `corrected_submit_unknown_fill` or `optimistic_submitted` | No                                            |
| Confirmed partial fill + remainder known       | `corrected_executable_live_partial`                       | Filled slice only                             |
| Confirmed full fill + lot                      | `corrected_executable_live_full`                          | Yes                                           |
| Manual/force exit without quote                | `manual_emergency_exit_excluded`                          | No                                            |
| Chain-only token                               | `chain_only_quarantined`                                  | No                                            |
| Settlement recorded but no redeem confirmation | `settlement_recorded_not_redeemed`                        | Depends on fill for P&L; payout not confirmed |

### Dry-run script

Required script:

```bash
python scripts/dry_run_pricing_semantics_migration.py \
  --db state/zeus_trades.db \
  --positions state/positions-live.json \
  --read-only \
  --emit-summary \
  --emit-row-sample 50
```

Output must include:

* row counts by table;
* counts by proposed `pricing_semantics_version`;
* rows with missing condition/question/token;
* rows with `market_id == token_id`;
* rows with placeholder `legacy:*`;
* open positions by fill authority;
* exits by exit quote authority;
* settlement rows by settlement/redeem status;
* report rows that would hard-fail mixed cohorts.

### Production risk

| Risk                                       | Mitigation                                                            |
| ------------------------------------------ | --------------------------------------------------------------------- |
| Misclassifying legacy rows as corrected    | Default absent/unknown to legacy or REVIEW_REQUIRED.                  |
| Breaking existing reports                  | Add new views; keep legacy diagnostic reports labeled.                |
| Destroying operator state                  | Additive-only migration; read-only census first.                      |
| Mixed JSON/DB truth                        | DB-first loader already exists; census must compare DB and JSON.      |
| Open positions corrupted by migration      | No field mutation on open positions until census and operator review. |
| Chain reconciliation overwriting economics | Stage 3/4 fix before corrected P&L.                                   |

### Rollback

* Additive fields can remain unused.
* Set `ZEUS_CORRECTED_EXECUTABLE_LIVE_ENABLED=0`.
* Switch reports to diagnostic-only mode.
* Revert projection views, not raw facts.
* Do not delete venue facts or command journal rows.
* Quarantine rows with uncertain classification.

### No fake corrected backfill

A migration may set `pricing_semantics_version`, but may not set `corrected_executable_economics_eligible=True` unless row-level evidence already proves:

* executable snapshot/depth/hash;
* native token quote;
* cost basis hash;
* order policy/final intent;
* venue envelope/command;
* confirmed fill facts or explicit partial/cancel remainder;
* held-token exit quote or settlement/redeem facts for exit/settlement reporting.

### Old-row policy

| Row type                      | Policy                                                                             |
| ----------------------------- | ---------------------------------------------------------------------------------- |
| Legacy entries                | Keep legacy; diagnostic reports only.                                              |
| Legacy open positions         | Automated economic exit only if held token and fresh SELL quote; report legacy.    |
| Legacy settlement rows        | Settlement result usable for calibration/diagnostic; not corrected executable P&L. |
| Historical backtests          | Skill/diagnostic only.                                                             |
| Rows with unknown side effect | `REVIEW_REQUIRED`; no P&L.                                                         |
| Chain-only positions          | Quarantine; no strategy attribution.                                               |

### Report view changes

* Every promotion-grade report view must expose:

  * `pricing_semantics_version`;
  * `reporting_cohort`;
  * `entry_economics_authority`;
  * `fill_authority`;
  * `exit_authority`;
  * `settlement_status`;
  * `promotion_eligible`;
  * `diagnostic_only_reason`.
* Every report query must call a shared cohort gate.
* Mixed cohorts fail, not warn.

---

## 17. Test and CI gate matrix

### Semantic/unit/integration tests

| Test name                                                                       | Type               | Target                     | Invariant                                   | Stage | Expected fail-before        | Expected pass-after | Promotion required     |
| ------------------------------------------------------------------------------- | ------------------ | -------------------------- | ------------------------------------------- | ----- | --------------------------- | ------------------- | ---------------------- |
| `test_corrected_mode_cannot_size_from_raw_entry_price`                          | Unit               | evaluator/Kelly            | Raw float cannot be corrected cost          | 1     | Yes                         | Yes                 | Yes                    |
| `test_implied_probability_with_fee_cannot_become_kelly_safe_in_corrected_mode`  | Unit               | `ExecutionPrice`/evaluator | Fee-adjusted implied probability not enough | 1     | Yes                         | Yes                 | Yes                    |
| `test_kelly_requires_executable_cost_basis`                                     | Unit               | Kelly                      | Corrected Kelly input is cost basis         | 1     | Yes                         | Yes                 | Yes                    |
| `test_executable_quote_change_affects_cost_not_posterior`                       | Unit               | strategy/evaluator         | Quote changes cost, not belief              | 1     | Yes                         | Yes                 | Yes                    |
| `test_market_prior_change_affects_posterior_not_token_snapshot`                 | Unit               | market fusion              | Prior changes posterior only                | 2     | Maybe                       | Yes                 | Yes                    |
| `test_corrected_executor_rejects_missing_final_limit_or_cost_basis`             | Unit               | executor                   | Final intent/cost required                  | 1     | Maybe                       | Yes                 | Yes                    |
| `test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp`         | Unit/static        | executor                   | No executor price authority                 | 1     | Yes                         | Yes                 | Yes                    |
| `test_legacy_execution_intent_live_rejected_without_operator_opt_in`            | Integration        | executor/runtime           | Legacy path not corrected live              | 1     | Likely                      | Yes                 | Yes                    |
| `test_order_policy_order_type_mapping_is_explicit`                              | Unit               | `OrderPolicy`              | No policy collapse                          | 1     | Maybe                       | Yes                 | Yes                    |
| `test_post_only_market_cross_rejected_before_submit`                            | Unit/integration   | executor/adapter           | Post-only cannot cross                      | 1     | Maybe                       | Yes                 | Yes                    |
| `test_buy_no_requires_native_no_quote_no_complement_fallback`                   | Unit/integration   | strategy/cost basis        | Native NO quote required                    | 1     | Yes for family scan         | Yes                 | Yes                    |
| `test_family_scan_buy_no_without_native_quote_is_diagnostic_only`               | Unit               | family scan                | Complement diagnostic only                  | 1/6   | Yes                         | Yes                 | Yes                    |
| `test_fdr_hypothesis_id_changes_when_executable_snapshot_or_cost_basis_changes` | Unit               | selection/runtime          | Executable identity changes                 | 1/2   | Yes                         | Yes                 | Yes                    |
| `test_snapshot_change_after_selection_requires_reject_recompute_or_amendment`   | Integration        | cycle runtime              | No silent drift                             | 1/2   | Yes                         | Yes                 | Yes                    |
| `test_compatibility_envelope_rejected_in_live_submit`                           | Integration        | adapter                    | Placeholder cannot live submit              | 1     | Likely                      | Yes                 | Yes                    |
| `test_adapter_submit_calls_assert_live_submit_bound_before_sdk_contact`         | Integration        | adapter                    | Assertion reachability                      | 1     | Likely                      | Yes                 | Yes                    |
| `test_live_envelope_requires_condition_question_yes_no_token_identity`          | Unit               | envelope                   | No placeholder/collapse                     | 1     | Maybe                       | Yes                 | Yes                    |
| `test_venue_command_market_id_not_token_id_for_live_entry`                      | Integration        | command/envelope           | Market/token distinct                       | 1     | Unknown                     | Yes                 | Yes                    |
| `test_corrected_submit_rejects_snapshot_stale_between_risk_and_submit`          | Integration        | runtime/executor           | Stale quote fails closed                    | 1/5   | Likely                      | Yes                 | Yes                    |
| `test_corrected_entry_cannot_use_legacy_exit_fallback`                          | Integration        | monitor/exit               | Exit symmetry                               | 1/2   | Likely                      | Yes                 | Yes                    |
| `test_exit_requires_fresh_held_token_best_bid_unless_derisk_override`           | Unit/integration   | exit                       | SELL bid authority                          | 1/2   | Maybe                       | Yes                 | Yes                    |
| `test_buy_no_exit_uses_best_bid_not_vwmp`                                       | Unit               | exit                       | Buy-NO sell value = best bid                | 1     | Current similar test exists | Yes                 | Yes                    |
| `test_manual_force_exit_tagged_excluded_from_corrected_evidence`                | Integration        | exit/report                | Emergency excluded                          | 2/3   | Unknown                     | Yes                 | Yes                    |
| `test_partial_fill_then_cancel_remainder_not_full_fill`                         | Integration        | command/fill/lot           | Partial/cancel explicit                     | 1/2   | Unknown                     | Yes                 | Yes                    |
| `test_submit_unknown_side_effect_blocks_corrected_pnl`                          | Integration        | command/report             | Unknown fill REVIEW_REQUIRED                | 1/3   | Unknown                     | Yes                 | Yes                    |
| `test_position_lot_authority_required_for_corrected_pnl`                        | Unit/report        | position/report            | Fill lot required                           | 2/3   | Maybe                       | Yes                 | Yes                    |
| `test_command_journal_proves_cost_basis_snapshot_envelope_same_object`          | Integration        | command/facts/report       | Same-object proof                           | 2/3   | Yes                         | Yes                 | Yes                    |
| `test_reports_hard_fail_mixed_pricing_semantics_cohorts`                        | Unit/report        | reports                    | No mixed cohort                             | 3     | Current partial pass        | Universal pass      | Yes                    |
| `test_all_report_paths_call_reporting_cohort_gate`                              | Static/integration | reports                    | Universal cohort law                        | 3     | Likely                      | Yes                 | Yes                    |
| `test_backtest_without_depth_snapshot_excluded_from_corrected_economics`        | Unit               | backtest                   | No fake economics                           | 3     | Current partial pass        | Yes                 | Yes                    |
| `test_profit_replay_is_diagnostic_only`                                         | Unit               | profit replay              | Diagnostic not promotion                    | 3     | Current likely pass         | Yes                 | Yes                    |
| `test_skill_backtest_cannot_promote_economics`                                  | Unit               | backtest                   | Skill ≠ economics                           | 3     | Current likely pass         | Yes                 | Yes                    |
| `test_no_historical_corrected_backfill_without_depth_snapshot_fill`             | Migration/report   | migration                  | No fake backfill                            | 3/16  | Unknown                     | Yes                 | Yes                    |
| `test_chain_reconciliation_does_not_mutate_entry_economics_for_corrected_lot`   | Unit/integration   | chain reconciliation       | Chain facts separate                        | 3/4   | Yes                         | Yes                 | Yes                    |
| `test_is_settled_split_into_statuses`                                           | Unit               | settlement                 | Status split                                | 4     | Unknown                     | Yes                 | Yes                    |
| `test_redeem_confirmed_distinct_from_settlement_recorded`                       | Integration        | settlement/redeem          | Payout distinct                             | 4     | Unknown                     | Yes                 | Yes                    |
| `test_city_id_roundtrip_no_duplicate_transform`                                 | Unit/static        | city identity              | Central city ID                             | 4     | Unknown                     | Yes                 | Yes                    |
| `test_high_low_metric_identity_required`                                        | Unit               | metric                     | No silent high/low                          | 4     | Maybe                       | Yes                 | Yes                    |
| `test_target_local_date_not_utc_day_guess`                                      | Unit               | time identity              | Local day explicit                          | 4     | Unknown                     | Yes                 | Yes                    |
| `test_timeout_literals_have_unit_names`                                         | Static             | code/config                | Unit clarity                                | 4/5   | Unknown                     | Yes                 | No                     |
| `test_monitor_quote_age_slo_blocks_corrected_exit`                              | Integration        | monitor/exit               | Staleness correctness                       | 5     | Unknown                     | Yes                 | Yes                    |
| `test_submit_deadline_metric_and_counter_increment`                             | Integration        | telemetry                  | Stale rejection visible                     | 5     | Unknown                     | Yes                 | Yes                    |
| `test_docs_forbidden_language_requires_legacy_or_diagnostic_qualifier`          | Static             | docs                       | Docs not stale                              | 4/16  | Yes                         | Yes                 | Yes for docs authority |
| `test_static_gates_run_in_ci`                                                   | CI meta            | workflows                  | CI enforces semantics                       | 17    | Unknown                     | Yes                 | Yes                    |

### Semantic grep gates

Each gate fails CI if matched in corrected/live money path without an explicit legacy/diagnostic qualifier.

| Gate                                                                                      | Pattern/meaning                                                | Stage |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ----- |
| Corrected executor calls `compute_native_limit_price`                                     | `compute_native_limit_price` reachable from corrected executor | 1     |
| Corrected mode passes `p_market` / `vwmp` / `entry_price` into Kelly executable cost      | Raw quote/cost alias                                           | 1     |
| Buy-NO live path uses complement as executable cost                                       | `1 - p_market` or `1.0 - ...` near buy_no live/cost            | 1     |
| Report aggregates legacy and corrected rows                                               | Report query lacks cohort gate                                 | 3     |
| Live envelope has `condition_id` like `legacy:{token_id}`                                 | Placeholder identity                                           | 1     |
| `market_id == token_id` in live venue command                                             | Identity collapse                                              | 1     |
| `BinEdge.vwmp` becomes required cost authority                                            | BinEdge cost authority                                         | 4     |
| Executable snapshot VWMP written back into permanent edge cost authority                  | Decision mutation                                              | 1/4   |
| Broad `except Exception: pass` in money path                                              | Silent loss                                                    | 1/4   |
| Timeout literals lack unit naming                                                         | Ops ambiguity                                                  | 4/5   |
| `city_id` transformation duplicated outside central contract                              | City drift                                                     | 4     |
| Compatibility envelope reaches adapter submit in live                                     | Live compatibility leak                                        | 1     |
| Docs contain “guaranteed fill” or α-weighted raw `P_market` language without legacy label | Stale docs                                                     | 4/16  |
| `yes_family_devig_v1` has `validated_for_live=True` without evidence                      | Premature market-prior promotion                               | 3     |
| Manual/force exits included in corrected P&L                                              | Evidence contamination                                         | 3     |
| `settled` used as redeemed payout                                                         | Settlement/redeem conflation                                   | 4     |
| Chain reconciliation mutates corrected entry cost basis                                   | Entry economics corruption                                     | 3/4   |

---

## 18. Hidden branch register

| Branch ID | Source   | Branch                                                               | Risk                             | Stage | Decision                             | Test/gate                                              | Rollback/escalation            | Validation status                                   |
| --------- | -------- | -------------------------------------------------------------------- | -------------------------------- | ----- | ------------------------------------ | ------------------------------------------------------ | ------------------------------ | --------------------------------------------------- |
| HB-01     | A/B/user | Legacy open positions                                                | Automated exit/report corruption | 0/1/3 | Census and classify                  | `state_census_no_mutation`                             | REVIEW_REQUIRED, manual review | `REVIEW_REQUIRED`                                   |
| HB-02     | A/B/user | Corrected entry with legacy exit                                     | Wrong exit value                 | 1/2   | Forbid                               | `test_corrected_entry_cannot_use_legacy_exit_fallback` | Disable auto exit              | `PARTIALLY_CONFIRMED`                               |
| HB-03     | A/B/user | Buy-NO without native NO quote                                       | Wrong token cost                 | 1     | Fail closed                          | Native NO quote test                                   | Shadow diagnostic only         | `CONFIRMED` for family scan                         |
| HB-04     | A/B/user | Complement executable price                                          | Wrong live cost                  | 1/6   | Static gate                          | Complement grep                                        | Quarantine helper              | `CONFIRMED`                                         |
| HB-05     | B/user   | Raw quote as market prior                                            | False posterior                  | 2/3   | Require named estimator              | Raw prior reject                                       | model-only                     | `PARTIALLY_SUPERSEDED` by `MarketPriorDistribution` |
| HB-06     | A/B/user | Partial fill/cancel                                                  | False full-fill P&L              | 1/2/3 | Represent explicitly                 | Partial/cancel tests                                   | REVIEW_REQUIRED                | `PARTIALLY_CONFIRMED` schema exists                 |
| HB-07     | B/user   | Delayed/unknown fill                                                 | False P&L                        | 1/2/3 | REVIEW_REQUIRED                      | Unknown fill gate                                      | Operator reconciliation        | `PARTIALLY_CONFIRMED` command grammar               |
| HB-08     | B/user   | Command insert succeeds but submit fails                             | Ghost command                    | 1/2   | State transition to rejected/unknown | Persist-before-submit + submit fail                    | REVIEW_REQUIRED                | `PARTIALLY_CONFIRMED`                               |
| HB-09     | B/user   | Submit succeeds but materialization fails                            | Live position not tracked        | 1/2   | Unknown side-effect state            | Submit side-effect test                                | Reconciliation                 | `REVIEW_REQUIRED`                                   |
| HB-10     | B/user   | Crash after risk before submit                                       | Reserved collateral/no order     | 1     | Atomic phases                        | Crash simulation                                       | Release/reserve rollback       | `REVIEW_REQUIRED`                                   |
| HB-11     | A/B/user | Stale quote by submit                                                | Wrong limit/cost                 | 1/5   | Submit deadline reject               | Stale submit test                                      | Live off                       | `CONFIRMED risk`                                    |
| HB-12     | A/B/user | Executor reprices after FDR                                          | Changed object                   | 1/2   | Forbid repricing                     | No repricing test/static gate                          | Reject/amend                   | `CONFIRMED`                                         |
| HB-13     | A/B/user | FDR hypothesis materializes as different token/snapshot              | Wrong object                     | 1/2   | Bind executable hypothesis           | FDR snapshot/cost hash tests                           | Recompute/amend                | `PARTIALLY_CONFIRMED`                               |
| HB-14     | B/user   | `market_id/token_id` collapse                                        | Wrong venue object               | 1/2   | `MarketIdentity`                     | Reject collapse                                        | Quarantine row                 | `PARTIALLY_CONFIRMED`                               |
| HB-15     | B/user   | Missing condition/question ID                                        | No venue identity                | 1/2   | Required fields                      | Envelope identity test                                 | Reject                         | `PARTIALLY_CONFIRMED`                               |
| HB-16     | B/user   | Compatibility envelope live reachability                             | Placeholder live submit          | 1/6   | Assert live-bound                    | Adapter no SDK call                                    | Disable helper                 | `PARTIALLY_CONFIRMED`                               |
| HB-17     | A/B/user | Mixed report cohorts                                                 | False promotion P&L              | 3     | Hard fail                            | Cohort tests                                           | Diagnostic only                | `PARTIALLY_SUPERSEDED`                              |
| HB-18     | A/B/user | Backtest lacks depth                                                 | Fake economics                   | 3     | Tombstone/exclude                    | Backtest depth tests                                   | Skill only                     | `PARTIALLY_SUPERSEDED`                              |
| HB-19     | B/user   | High/low conflation                                                  | Wrong weather object             | 4     | MetricIdentity                       | High/low test                                          | REVIEW_REQUIRED                | `PARTIALLY_CONFIRMED`                               |
| HB-20     | B/user   | City_id mismatch                                                     | Wrong city object                | 4     | CityIdentity                         | Roundtrip test                                         | Quarantine rows                | `REVIEW_REQUIRED`                                   |
| HB-21     | A/B/user | Settlement recorded vs paid/redeemed                                 | False payout                     | 3/4   | SettlementStatus                     | Redeem status test                                     | Report block                   | `REVIEW_REQUIRED`                                   |
| HB-22     | B/user   | Timezone/local-day/UTC mismatch                                      | Wrong target date                | 4     | TimeIdentity                         | Local date test                                        | Report block                   | `REVIEW_REQUIRED`                                   |
| HB-23     | B/user   | Unsafe config typo                                                   | Live unsafe enabled              | 0/1   | Freeze default; config validation    | Config typo gate                                       | Live off                       | `REVIEW_REQUIRED`                                   |
| HB-24     | B/user   | Orphan settlement/reconciliation path callable                       | State mutation                   | 3/4/6 | Quarantine                           | Reachability grep                                      | REVIEW_REQUIRED                | `PARTIALLY_CONFIRMED` chain mutation                |
| HB-25     | B/user   | Tests sharing false assumptions                                      | False confidence                 | 1/17  | Rewrite tests                        | Expected fail-before                                   | CI block                       | `CONFIRMED`                                         |
| HB-26     | B/user   | Performance staleness                                                | Correctness failure              | 5     | Telemetry/freshness gates            | Quote age tests                                        | Live off                       | `REVIEW_REQUIRED`                                   |
| HB-27     | B/user   | Agent patch regression                                               | Invariant regression             | 16/17 | Static gates/CI                      | CI meta test                                           | Revert patch                   | `REVIEW_REQUIRED`                                   |
| HB-28     | A/B/user | Docs updated without gates                                           | Docs-only repair                 | 16    | Docs after runtime gates             | Docs scan                                              | Docs demoted                   | `CONFIRMED stale`                                   |
| HB-29     | user/new | Adapter live-bound assertion reachability                            | Dead assertion                   | 1     | Assert before SDK                    | Fake SDK no call                                       | Disable adapter live           | `PARTIALLY_CONFIRMED`                               |
| HB-30     | B/user   | `fee_deducted` naming drift                                          | Fee semantics confusion          | 4     | Rename/alias                         | Fee naming test                                        | Compatibility alias            | `CONFIRMED`                                         |
| HB-31     | B/user   | Lifecycle vocabulary drift                                           | Wrong state/report               | 4     | Canonical states                     | State grammar                                          | Report block                   | `PARTIALLY_CONFIRMED`                               |
| HB-32     | B/user   | Chain reconciliation mutating entry economics                        | P&L corruption                   | 3/4   | Separate chain facts                 | Chain mutation test                                    | Quarantine corrected rows      | `CONFIRMED`                                         |
| HB-33     | B/user   | Diagnostic replay complement                                         | Fake promotion                   | 3/6   | Diagnostic only                      | Promotion exclusion                                    | Report block                   | `PARTIALLY_CONFIRMED`                               |
| HB-34     | B/user   | Premature market-prior promotion                                     | False live prior                 | 3     | Validation evidence required         | Prior live validation test                             | model-only/shadow              | `REVIEW_REQUIRED`                                   |
| HB-35     | B/user   | Order policy enum conflation                                         | Wrong order type                 | 1/2   | Normalize policies                   | Policy tests                                           | Reject unsupported             | `CONFIRMED`                                         |
| HB-36     | B/user   | Position object owning too many planes                               | Data mutation                    | 4     | Projection-only position             | PositionLot tests                                      | Compatibility projection       | `CONFIRMED`                                         |
| HB-37     | B/user   | Command journal not proving cost_basis/snapshot/envelope same object | False evidence                   | 2/3   | Hash chain                           | Same-object test                                       | REVIEW_REQUIRED                | `PARTIALLY_CONFIRMED`                               |
| HB-38     | B/user   | Report/backtest promotion evidence stale or diagnostic               | False promotion                  | 3     | Promotion gate                       | Report registry test                                   | Block promotion                | `PARTIALLY_SUPERSEDED`                              |
| HB-39     | user/new | Unknown migrations already present                                   | Schema drift                     | 0/16  | Migration census                     | Dry-run migration                                      | No destructive migration       | `REVIEW_REQUIRED`                                   |
| HB-40     | user/new | CI not running semantic gates                                        | Regression                       | 17    | CI workflow verify                   | CI meta test                                           | Manual gate before merge       | `REVIEW_REQUIRED`                                   |
| HB-41     | user/new | Negative-risk metadata present but policy not modeled                | Wrong economics                  | 2/5   | Include/require policy               | Negative-risk gate                                     | Live block market              | `PARTIALLY_CONFIRMED`                               |
| HB-42     | user/new | Settlement/redeem status conflation                                  | False payout                     | 3/4   | SettlementStatus                     | Redeem split test                                      | Report block                   | `REVIEW_REQUIRED`                                   |

---

## 19. Ordered implementation roadmap P0–P17

### P0 — Full repo evidence lock, live freeze, state census

| Field                        | Content                                                                                                                                                                               |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Objective                    | Freeze corrected live entry and inventory current truth without mutation.                                                                                                             |
| Dependency                   | None.                                                                                                                                                                                 |
| Allowed files                | `src/config.py`, `src/control/*`, `scripts/state_census.py`, `scripts/dry_run_pricing_semantics_migration.py`, tests.                                                                 |
| Forbidden files              | Executor price logic, strategy logic, destructive migrations.                                                                                                                         |
| Tasks                        | Add/verify `ZEUS_CORRECTED_EXECUTABLE_LIVE_ENABLED=0` default; add read-only state census; classify open positions; detect placeholder identities; detect mixed cohorts; emit report. |
| Tests                        | `test_corrected_live_disabled_by_default`; `test_state_census_no_mutation`; config typo test.                                                                                         |
| Validation commands          | `pytest -q tests/test_live_freeze.py tests/test_state_census.py`; `python scripts/state_census.py --read-only`.                                                                       |
| Expected failures before fix | Live flag may be absent; census script absent.                                                                                                                                        |
| Acceptance evidence          | Freeze flag default false; census output with row counts/classifications; no DB writes.                                                                                               |
| Rollback                     | Remove census script; leave freeze default false.                                                                                                                                     |
| Blast radius                 | Low; read-only/config.                                                                                                                                                                |
| Hidden branches closed       | HB-01, HB-23, HB-39.                                                                                                                                                                  |

### P1 — Full semantic test suite and static gates

| Field                        | Content                                                                                               |
| ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| Objective                    | Add failing tests/gates before implementation.                                                        |
| Dependency                   | P0.                                                                                                   |
| Allowed files                | `tests/*`, `scripts/semantic_static_gates.py`, CI workflow.                                           |
| Forbidden files              | Production code except test fixtures.                                                                 |
| Tasks                        | Add Section 17 tests/gates; mark expected fail-before; ensure CI runs static gates.                   |
| Tests                        | All Stage 1 tests.                                                                                    |
| Validation commands          | `pytest -q tests/test_reality_semantics_*`; `python scripts/semantic_static_gates.py`.                |
| Expected failures before fix | Kelly raw price, family-scan complement, compatibility submit, executor repricing, same-object proof. |
| Acceptance evidence          | Tests fail for known blockers and pass only after code changes.                                       |
| Rollback                     | Remove/xfail only with tribunal note; do not weaken.                                                  |
| Blast radius                 | Medium in CI.                                                                                         |
| Hidden branches closed       | HB-25, HB-27, HB-40.                                                                                  |

### P2 — Contract object package and semantic types

| Field                        | Content                                                                                                                                                                          |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Objective                    | Finalize minimal spine contracts.                                                                                                                                                |
| Dependency                   | P1.                                                                                                                                                                              |
| Allowed files                | `src/contracts/*`, tests.                                                                                                                                                        |
| Forbidden files              | Runtime wiring beyond inert producers/validators.                                                                                                                                |
| Tasks                        | Add/confirm `MarketIdentity`, `CityIdentity`, `TimeIdentity`, `SettlementStatus`, `ReportingCohort`, `ExitExecutableQuote`; harden existing cost/snapshot/final intent/envelope. |
| Tests                        | Contract invariant tests.                                                                                                                                                        |
| Validation commands          | `pytest -q tests/test_contracts_semantics.py`.                                                                                                                                   |
| Expected failures before fix | Missing contracts/fields.                                                                                                                                                        |
| Acceptance evidence          | Contracts reject legacy placeholders/raw inputs.                                                                                                                                 |
| Rollback                     | Leave additive contracts unused.                                                                                                                                                 |
| Blast radius                 | Low/medium.                                                                                                                                                                      |
| Hidden branches closed       | HB-14, HB-15, HB-21, HB-22, HB-41, HB-42.                                                                                                                                        |

### P3 — Entry cost basis + Kelly split

| Field                        | Content                                                                                                            |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Objective                    | Make corrected Kelly consume executable cost basis only.                                                           |
| Dependency                   | P2.                                                                                                                |
| Allowed files                | `src/engine/evaluator.py`, `src/strategy/kelly.py`, `src/contracts/execution_price.py`, cost basis builder, tests. |
| Forbidden files              | Executor submit logic, reports.                                                                                    |
| Tasks                        | Add `size_from_executable_cost_basis`; block raw `entry_price` in corrected; keep legacy diagnostic helper.        |
| Tests                        | Kelly raw rejection; quote changes cost not posterior.                                                             |
| Validation commands          | `pytest -q tests/test_reality_semantics_kelly.py tests/test_execution_price.py`.                                   |
| Expected failures before fix | Existing `test_evaluator_always_uses_fee_adjusted_size` encodes wrong assumption.                                  |
| Acceptance evidence          | Corrected evaluator cannot pass raw float.                                                                         |
| Rollback                     | Legacy sizing retained diagnostic-only.                                                                            |
| Blast radius                 | High for entry sizing.                                                                                             |
| Hidden branches closed       | HB-04, HB-05, HB-25.                                                                                               |

### P4 — Immutable final intent + executor no repricing

| Field                        | Content                                                                                                                           |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Objective                    | Seal corrected executor as validator/submitter only.                                                                              |
| Dependency                   | P3.                                                                                                                               |
| Allowed files                | `src/execution/executor.py`, `src/engine/cycle_runtime.py`, final intent tests.                                                   |
| Forbidden files              | Strategy selection changes except plumbing.                                                                                       |
| Tasks                        | Corrected path requires `FinalExecutionIntent`; remove/ban price recompute; executor validates hash/token/limit/policy/freshness. |
| Tests                        | Executor no repricing; missing final intent reject.                                                                               |
| Validation commands          | `pytest -q tests/test_reality_semantics_executor.py`; static gate.                                                                |
| Expected failures before fix | `compute_native_limit_price` reachable.                                                                                           |
| Acceptance evidence          | Corrected live can only call `execute_final_intent`.                                                                              |
| Rollback                     | Live flag off; legacy path unchanged.                                                                                             |
| Blast radius                 | High.                                                                                                                             |
| Hidden branches closed       | HB-11, HB-12, HB-29.                                                                                                              |

### P5 — Native NO quote / complement quarantine

| Field                        | Content                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------ |
| Objective                    | Remove executable NO complement risk.                                          |
| Dependency                   | P3.                                                                            |
| Allowed files                | `market_analysis_family_scan.py`, `market_analysis.py`, quote builders, tests. |
| Forbidden files              | Executor/report refactors.                                                     |
| Tasks                        | Native NO quote required for corrected; complement diagnostic-only.            |
| Tests                        | Buy-NO native quote tests; static complement gate.                             |
| Validation commands          | `pytest -q tests/test_buy_no_quote_semantics.py`; static gate.                 |
| Expected failures before fix | family-scan fallback.                                                          |
| Acceptance evidence          | Missing NO quote fails corrected path.                                         |
| Rollback                     | Diagnostic family scan retained.                                               |
| Blast radius                 | Medium.                                                                        |
| Hidden branches closed       | HB-03, HB-04.                                                                  |

### P6 — FDR materialization identity / executable hypothesis binding

| Field                        | Content                                                                                     |
| ---------------------------- | ------------------------------------------------------------------------------------------- |
| Objective                    | Bind selected statistical hypothesis to executable economic object.                         |
| Dependency                   | P2–P5.                                                                                      |
| Allowed files                | `selection_family.py`, `cycle_runtime.py`, executable hypothesis contracts, tests.          |
| Forbidden files              | Venue adapter.                                                                              |
| Tasks                        | Add executable hypothesis IDs/hashes; reject/recompute/amend on snapshot/cost/policy drift. |
| Tests                        | Snapshot/cost/order-policy change tests.                                                    |
| Validation commands          | `pytest -q tests/test_fdr_executable_identity.py`.                                          |
| Expected failures before fix | FDR ID unchanged under cost change.                                                         |
| Acceptance evidence          | Same-object identity trace.                                                                 |
| Rollback                     | Revert binding, keep live off.                                                              |
| Blast radius                 | High strategy/runtime.                                                                      |
| Hidden branches closed       | HB-13.                                                                                      |

### P7 — OrderPolicy normalization and venue order type mapping

| Field                        | Content                                                                           |
| ---------------------------- | --------------------------------------------------------------------------------- |
| Objective                    | Remove policy/order type contradictions.                                          |
| Dependency                   | P2/P4.                                                                            |
| Allowed files                | `src/contracts/execution_intent.py`, order policy config, executor mapping tests. |
| Forbidden files              | Strategy scoring.                                                                 |
| Tasks                        | Define canonical policies and mapping; reject invalid combos.                     |
| Tests                        | Policy mapping tests.                                                             |
| Validation commands          | `pytest -q tests/test_order_policy_semantics.py`.                                 |
| Expected failures before fix | ambiguous `limit_may_take_conservative`.                                          |
| Acceptance evidence          | Policies map one-to-one to venue behavior.                                        |
| Rollback                     | Unsupported policies reject.                                                      |
| Blast radius                 | Medium.                                                                           |
| Hidden branches closed       | HB-35.                                                                            |

### P8 — Venue envelope / command journal / cost_basis same-object proof

| Field                        | Content                                                                                                         |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Objective                    | Prove final intent, snapshot, cost basis, envelope, command, order, fill, lot are same object.                  |
| Dependency                   | P4/P6/P7.                                                                                                       |
| Allowed files                | `venue_submission_envelope.py`, `venue_command_repo.py`, `state/db.py` additive fields, executor command tests. |
| Forbidden files              | Destructive migrations.                                                                                         |
| Tasks                        | Add hash fields; enforce live-bound assertion; join tests; placeholder exclusion.                               |
| Tests                        | Same-object command journal; adapter assertion reachability.                                                    |
| Validation commands          | `pytest -q tests/test_command_same_object.py tests/test_venue_envelope_live_bound.py`.                          |
| Expected failures before fix | command tests lack cost_basis; adapter assertion dead.                                                          |
| Acceptance evidence          | Hash-chain mismatch blocks corrected evidence.                                                                  |
| Rollback                     | Additive fields remain; live off.                                                                               |
| Blast radius                 | High persistence.                                                                                               |
| Hidden branches closed       | HB-08, HB-09, HB-16, HB-37.                                                                                     |

### P9 — Monitor / held-token SELL quote / corrected exit symmetry

| Field                        | Content                                                                                               |
| ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| Objective                    | Make exit as strict as entry.                                                                         |
| Dependency                   | P2/P8.                                                                                                |
| Allowed files                | `monitor_refresh.py`, `exit_lifecycle.py`, `exit_triggers.py`, `portfolio.py` projection only, tests. |
| Forbidden files              | Entry selection/evaluator.                                                                            |
| Tasks                        | Build `ExitExecutableQuote`; require best bid/depth/freshness; manual override excluded.              |
| Tests                        | Exit quote required; corrected entry cannot legacy exit; buy_yes/buy_no best_bid tests.               |
| Validation commands          | `pytest -q tests/test_exit_executable_quote.py tests/test_hold_value_exit_costs.py`.                  |
| Expected failures before fix | missing quote fallback.                                                                               |
| Acceptance evidence          | Corrected exit blocks without fresh held-token SELL quote.                                            |
| Rollback                     | Hold position/manual override.                                                                        |
| Blast radius                 | High exit.                                                                                            |
| Hidden branches closed       | HB-02, HB-06, HB-07.                                                                                  |

### P10 — PositionLot / FillAuthority / partial fill / cancel remainder

| Field                        | Content                                                                                           |
| ---------------------------- | ------------------------------------------------------------------------------------------------- |
| Objective                    | Make fill authority dominant over submitted/target notional.                                      |
| Dependency                   | P8/P9.                                                                                            |
| Allowed files                | `state/db.py` additive, `venue_command_repo.py`, fill tracker, `portfolio.py` projections, tests. |
| Forbidden files              | Strategy/evaluator.                                                                               |
| Tasks                        | Represent partial fills, cancel remainder, unknown fill; corrected P&L only fill-derived.         |
| Tests                        | Partial/cancel/unknown fill tests.                                                                |
| Validation commands          | `pytest -q tests/test_fill_authority.py`.                                                         |
| Expected failures before fix | fallback `effective_*` used.                                                                      |
| Acceptance evidence          | Unknown fill no corrected P&L.                                                                    |
| Rollback                     | Quarantine positions.                                                                             |
| Blast radius                 | High portfolio/report.                                                                            |
| Hidden branches closed       | HB-06, HB-07, HB-10.                                                                              |

### P11 — DB migration / semantic cohort fields

| Field                        | Content                                                                                      |
| ---------------------------- | -------------------------------------------------------------------------------------------- |
| Objective                    | Add semantic version/cohort fields and dry-run migration.                                    |
| Dependency                   | P10.                                                                                         |
| Allowed files                | migrations, `db.py`, migration scripts/tests.                                                |
| Forbidden files              | Runtime trading behavior.                                                                    |
| Tasks                        | Add additive fields; dry-run classify; no fake corrected backfill.                           |
| Tests                        | Migration no-mutation, classification matrix.                                                |
| Validation commands          | `python scripts/dry_run_pricing_semantics_migration.py --read-only`; pytest migration tests. |
| Expected failures before fix | missing fields/classifier.                                                                   |
| Acceptance evidence          | Census + migration dry-run with no corrected fake rows.                                      |
| Rollback                     | Additive columns inert.                                                                      |
| Blast radius                 | High data.                                                                                   |
| Hidden branches closed       | HB-39.                                                                                       |

### P12 — Reporting / backtest / promotion gates

| Field                        | Content                                                                                                    |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Objective                    | Universal report cohort law.                                                                               |
| Dependency                   | P11.                                                                                                       |
| Allowed files                | reports, `scripts/profit_validation_replay.py`, `scripts/equity_curve.py`, `src/backtest/*`, report tests. |
| Forbidden files              | Entry/exit runtime.                                                                                        |
| Tasks                        | Shared `ReportingCohort` gate; every report calls it; diagnostic labels; economics tombstone remains.      |
| Tests                        | Mixed cohort all reports; no promotion from skill/diagnostic.                                              |
| Validation commands          | `pytest -q tests/test_reporting_cohorts.py tests/test_backtest_skill_economics.py`.                        |
| Expected failures before fix | unguarded report paths.                                                                                    |
| Acceptance evidence          | All report paths fail mixed cohorts.                                                                       |
| Rollback                     | Disable promotion reports.                                                                                 |
| Blast radius                 | Medium/high reports.                                                                                       |
| Hidden branches closed       | HB-17, HB-18, HB-33, HB-38.                                                                                |

### P13 — Settlement / redeem / city / timezone / high-low identity

| Field                        | Content                                                                                       |
| ---------------------------- | --------------------------------------------------------------------------------------------- |
| Objective                    | Centralize settlement, redeem, physical/weather identity.                                     |
| Dependency                   | P11/P12.                                                                                      |
| Allowed files                | settlement contracts, harvester split, city/time contracts, tests.                            |
| Forbidden files              | Entry executor.                                                                               |
| Tasks                        | SettlementStatus; redeem command facts; CityIdentity/TimeIdentity/MetricIdentity enforcement. |
| Tests                        | Redeem status split; high/low/timezone/city tests.                                            |
| Validation commands          | `pytest -q tests/test_settlement_identity.py tests/test_city_time_identity.py`.               |
| Expected failures before fix | broad settled/status assumptions.                                                             |
| Acceptance evidence          | Settlement recorded not payout; local date explicit.                                          |
| Rollback                     | Report settlement as REVIEW_REQUIRED.                                                         |
| Blast radius                 | Medium settlement/report.                                                                     |
| Hidden branches closed       | HB-19, HB-20, HB-21, HB-22, HB-42.                                                            |

### P14 — Performance / staleness / telemetry

| Field                        | Content                                                               |
| ---------------------------- | --------------------------------------------------------------------- |
| Objective                    | Enforce freshness and expose staleness telemetry.                     |
| Dependency                   | P4/P9.                                                                |
| Allowed files                | monitor, executor freshness checks, telemetry, tests.                 |
| Forbidden files              | Strategy scoring.                                                     |
| Tasks                        | Quote age, snapshot age, submit deadline, monitor batching, counters. |
| Tests                        | Stale by submit; monitor quote age; telemetry counters.               |
| Validation commands          | `pytest -q tests/test_freshness_telemetry.py`.                        |
| Expected failures before fix | missing counters/deadlines.                                           |
| Acceptance evidence          | Stale data fails closed with counter.                                 |
| Rollback                     | Disable corrected live on SLO breach.                                 |
| Blast radius                 | Medium.                                                               |
| Hidden branches closed       | HB-11, HB-26.                                                         |

### P15 — Orphan cleanup / branch reduction

| Field                        | Content                                                                                                                    |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Objective                    | Delete/quarantine stale code after gates pass.                                                                             |
| Dependency                   | P1–P14.                                                                                                                    |
| Allowed files                | orphan modules/helpers/docs labels/tests.                                                                                  |
| Forbidden files              | Deleting uncertain live-reachable code without quarantine.                                                                 |
| Tasks                        | Remove/completely quarantine family-scan complement; isolate legacy helpers; label diagnostics; remove stale report paths. |
| Tests                        | Static gates remain green; reachability tests.                                                                             |
| Validation commands          | static gates + full pytest.                                                                                                |
| Expected failures before fix | static hits.                                                                                                               |
| Acceptance evidence          | No live reachability.                                                                                                      |
| Rollback                     | Restore quarantined diagnostic helper.                                                                                     |
| Blast radius                 | Medium.                                                                                                                    |
| Hidden branches closed       | HB-03, HB-04, HB-16, HB-24.                                                                                                |

### P16 — Docs / AGENTS / authority rewrite

| Field                        | Content                                                                                                   |
| ---------------------------- | --------------------------------------------------------------------------------------------------------- |
| Objective                    | Make docs match runtime gates.                                                                            |
| Dependency                   | P1–P15 gates.                                                                                             |
| Allowed files                | `README.md`, `AGENTS.md`, `src/*/AGENTS.md`, `docs/authority/*`.                                          |
| Forbidden files              | Runtime behavior.                                                                                         |
| Tasks                        | Rewrite authority docs around four-plane model, venue facts, live gates, reporting cohorts, not-now list. |
| Tests                        | Docs static scan.                                                                                         |
| Validation commands          | `python scripts/semantic_static_gates.py --docs`; docs tests.                                             |
| Expected failures before fix | stale α/guaranteed-fill language.                                                                         |
| Acceptance evidence          | Docs scan clean and cite gate names.                                                                      |
| Rollback                     | Docs revert only.                                                                                         |
| Blast radius                 | Low docs/high agent behavior.                                                                             |
| Hidden branches closed       | HB-28, HB-30, HB-31.                                                                                      |

### P17 — Final promotion gate / live runbook

| Field                        | Content                                                                                               |
| ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| Objective                    | Decide corrected shadow/live/report/promotion readiness.                                              |
| Dependency                   | P0–P16.                                                                                               |
| Allowed files                | runbook, promotion checklist, CI gate aggregation.                                                    |
| Forbidden files              | New features/refactors.                                                                               |
| Tasks                        | Aggregate gates; run full tests; state census; dry-run; shadow soak; live canary plan; rollback plan. |
| Tests                        | Acceptance gates in Section 21.                                                                       |
| Validation commands          | `pytest -q`; static gates; census; migration dry-run; report cohort dry-run.                          |
| Expected failures before fix | Any unresolved blocker.                                                                               |
| Acceptance evidence          | All gates green; no unresolved live blockers; operator signoff.                                       |
| Rollback                     | Feature flag off, cancel canary, quarantine rows.                                                     |
| Blast radius                 | High operational.                                                                                     |
| Hidden branches closed       | All promotion blockers.                                                                               |

---

## 20. Codex/local-agent prompts P0–P17

### P0 prompt — Evidence lock, live freeze, state census

```text
Role: Zeus live-money safety agent.

Scope: Implement a read-only evidence lock and corrected-live freeze. Do not repair pricing logic in this packet.

Read first:
- README.md
- AGENTS.md
- src/AGENTS.md
- src/execution/AGENTS.md
- src/state/AGENTS.md
- src/config.py
- src/control/*
- src/state/db.py
- src/state/portfolio.py
- src/state/venue_command_repo.py

Allowed files:
- src/config.py
- src/control/*
- scripts/state_census.py
- scripts/dry_run_pricing_semantics_migration.py
- tests/test_live_freeze.py
- tests/test_state_census.py

Forbidden files:
- src/engine/evaluator.py
- src/execution/executor.py
- src/strategy/*
- venue adapter submit logic
- destructive migrations

Invariants:
- Corrected live entry defaults disabled.
- Census is read-only.
- Unknown state becomes REVIEW_REQUIRED, not corrected.
- Do not mutate DB, positions JSON, or venue facts.

Tasks:
1. Add or verify ZEUS_CORRECTED_EXECUTABLE_LIVE_ENABLED defaults false.
2. Add a config validation test that unsafe typo/unknown truthy values do not enable corrected live.
3. Implement scripts/state_census.py with --read-only.
4. Census must classify open positions by pricing_semantics_version, fill_authority, entry_economics_authority, condition/question/token identity, placeholder legacy:* identity, command state, fill state, settlement/redeem status.
5. Emit JSON and Markdown summaries.

Tests:
- test_corrected_live_disabled_by_default
- test_corrected_live_flag_rejects_unknown_truthy_typo
- test_state_census_no_mutation
- test_state_census_classifies_missing_semantics_as_legacy_or_review_required

Commands:
- pytest -q tests/test_live_freeze.py tests/test_state_census.py
- python scripts/state_census.py --read-only --emit-summary

Expected failures before fix:
- Missing freeze flag or no census script.

Closeout evidence:
- Test output.
- Census sample output.
- Statement that no DB writes occur.

Rollback:
- Leave freeze default false.
- Remove census script if needed; do not touch state.

Do not opportunistically refactor.
Do not change evaluator/executor/strategy.
Do not mark any row corrected by inference.
```

### P1 prompt — Semantic tests and static gates

```text
Role: Zeus invariant test author.

Scope: Add failing semantic tests and static gates. Production code changes are forbidden except harmless test fixtures.

Read first:
- tests/test_execution_price.py
- tests/test_executor_command_split.py
- tests/test_hold_value_exit_costs.py
- tests/test_backtest_skill_economics.py
- src/engine/evaluator.py
- src/execution/executor.py
- src/strategy/market_analysis_family_scan.py
- src/contracts/execution_intent.py
- src/contracts/venue_submission_envelope.py

Allowed files:
- tests/test_reality_semantics_*.py
- tests/test_static_semantic_gates.py
- scripts/semantic_static_gates.py
- .github/workflows/* only to run gates

Forbidden files:
- Production pricing/execution code, except test fixtures.

Invariants:
- Tests must encode physical semantics, not current implementation.
- Expected fail-before is acceptable.
- Static gates are not substitutes for counterfactual tests.

Tasks:
1. Add tests from the Stage 1 list: raw Kelly reject, executor no repricing, buy-NO native quote, compatibility envelope reject, same-object command proof, stale submit, corrected entry cannot use legacy exit.
2. Add static gate script with forbidden patterns.
3. Add CI workflow invocation if CI exists.
4. Ensure tests name semantic invariant in test docstring.

Tests:
- pytest -q tests/test_reality_semantics_*.py
- python scripts/semantic_static_gates.py

Expected failures before fix:
- Evaluator raw price/Kelly.
- Family scan complement.
- Compatibility submit.
- Executor repricing/static hit.
- Same-object command proof missing.

Closeout evidence:
- List failing tests and why each fails.
- Static gate output.

Rollback:
- Do not delete tests. If temporarily xfail, include strict reason and blocker ID.

Do not opportunistically refactor.
Do not weaken existing passing tests to hide blockers.
```

### P2 prompt — Contract object package and semantic types

```text
Role: Zeus contract architect.

Scope: Add/complete inert semantic contracts. Do not wire live execution yet.

Read first:
- src/contracts/execution_intent.py
- src/contracts/executable_market_snapshot_v2.py
- src/contracts/execution_price.py
- src/contracts/venue_submission_envelope.py
- src/state/portfolio.py
- src/state/db.py

Allowed files:
- src/contracts/*
- tests/test_contracts_semantics.py
- tests/test_market_identity.py
- tests/test_reporting_cohort.py

Forbidden files:
- src/execution/executor.py live submit logic
- src/engine/evaluator.py sizing logic
- DB destructive migrations

Invariants:
- Probability scalar is not a trade.
- Raw quote is not prior unless estimator contract says so.
- BUY ask cost and SELL bid value are separate.
- condition_id, question_id, YES token, NO token, selected token remain distinct.
- Settlement recorded is not redeem confirmed.

Tasks:
1. Add MarketIdentity.
2. Add CityIdentity.
3. Add TimeIdentity.
4. Add SettlementStatus.
5. Add ReportingCohort.
6. Add ExitExecutableQuote.
7. Harden/confirm ExecutableEntryCostBasis fields.
8. Harden/confirm VenueSubmissionEnvelope placeholder detection.

Tests:
- Contract rejects token/condition collapse.
- ExitExecutableQuote rejects missing/stale best_bid for corrected mode.
- ReportingCohort hard-fails mixed promotion cohorts.
- SettlementStatus cannot collapse recorded and redeemed.

Commands:
- pytest -q tests/test_contracts_semantics.py tests/test_market_identity.py tests/test_reporting_cohort.py

Expected failures before fix:
- Missing contract classes or invariant checks.

Closeout evidence:
- Contract tests pass.
- No runtime submit changes.

Rollback:
- Leave additive contracts unused.

Do not opportunistically refactor.
Do not build a large parallel venue ontology.
```

### P3 prompt — Entry cost basis + Kelly split

```text
Role: Zeus Kelly/cost-basis repair agent.

Scope: Corrected Kelly must consume executable cost basis only.

Read first:
- src/engine/evaluator.py
- src/contracts/execution_price.py
- src/contracts/execution_intent.py
- src/strategy/kelly.py
- tests/test_execution_price.py
- tests/test_reality_semantics_kelly.py

Allowed files:
- src/engine/evaluator.py
- src/strategy/kelly.py
- src/contracts/execution_price.py only if needed for clearer naming/guards
- tests/test_reality_semantics_kelly.py
- tests/test_execution_price.py

Forbidden files:
- src/execution/executor.py
- src/venue/*
- reporting scripts

Invariants:
- Corrected mode cannot size from entry_price, p_market, vwmp, current_market_price, or BinEdge.
- Fee adjustment cannot launder implied_probability into corrected executable cost.
- Legacy diagnostic helpers must be explicitly labeled.

Tasks:
1. Add size_from_executable_cost_basis() or equivalent.
2. Route corrected evaluator mode to cost_basis only.
3. Keep legacy _size_at_execution_price_boundary only for legacy/diagnostic or make it reject corrected.
4. Rewrite false-positive test that treats fee-adjusted implied probability as sufficient.

Tests:
- test_corrected_mode_cannot_size_from_raw_entry_price
- test_implied_probability_with_fee_cannot_become_kelly_safe_in_corrected_mode
- test_kelly_requires_executable_cost_basis
- test_executable_quote_change_affects_cost_not_posterior

Commands:
- pytest -q tests/test_reality_semantics_kelly.py tests/test_execution_price.py

Expected failures before fix:
- Existing evaluator accepts raw float.

Closeout evidence:
- Corrected tests pass.
- Grep shows corrected sizing path requires cost_basis.

Rollback:
- Restore legacy helper only under diagnostic label.
- Keep corrected live flag off.

Do not opportunistically refactor.
Do not change executor.
```

### P4 prompt — Immutable final intent + executor no repricing

```text
Role: Zeus executor boundary repair agent.

Scope: Make corrected executor validate immutable final intent only.

Read first:
- src/execution/executor.py
- src/engine/cycle_runtime.py
- src/contracts/execution_intent.py
- src/contracts/executable_market_snapshot_v2.py
- tests/test_executor_command_split.py
- tests/test_reality_semantics_executor.py

Allowed files:
- src/execution/executor.py
- src/engine/cycle_runtime.py only for corrected final-intent plumbing
- src/contracts/execution_intent.py if validation gaps exist
- tests/test_reality_semantics_executor.py

Forbidden files:
- Strategy selection logic
- Market prior logic
- Reporting

Invariants:
- Corrected executor must not compute limit from posterior, vwmp, p_market, entry_price, or BinEdge.
- Corrected live submit requires FinalExecutionIntent.
- Executor validates hashes/freshness/token/limit/order policy; it does not amend.

Tasks:
1. Add corrected executor entry point if absent.
2. Reject legacy ExecutionIntent in corrected live mode.
3. Ensure execute_final_intent validates cost_basis_hash, snapshot_hash, final_limit_price, token, order policy, submit deadline.
4. Add static gate forbidding compute_native_limit_price in corrected call stack.

Tests:
- test_corrected_executor_rejects_missing_final_limit_or_cost_basis
- test_corrected_executor_never_recomputes_limit_from_posterior_or_vwmp
- test_legacy_execution_intent_live_rejected_without_operator_opt_in
- static compute_native_limit_price corrected gate

Commands:
- pytest -q tests/test_reality_semantics_executor.py
- python scripts/semantic_static_gates.py --executor

Expected failures before fix:
- Legacy path live-capable.
- compute_native_limit_price static hit.

Closeout evidence:
- Tests pass.
- Corrected submit call graph documented.

Rollback:
- Disable corrected live flag.
- Preserve legacy executor only under explicit legacy path.

Do not opportunistically refactor.
Do not change pricing strategy.
```

### P5 prompt — Native NO quote / complement quarantine

```text
Role: Zeus buy-NO venue reality repair agent.

Scope: Ensure executable buy-NO economics require native NO token quote.

Read first:
- src/strategy/market_analysis.py
- src/strategy/market_analysis_family_scan.py
- src/data/polymarket_client.py
- src/contracts/executable_market_snapshot_v2.py
- tests/test_reality_semantics_buy_no.py

Allowed files:
- src/strategy/market_analysis.py
- src/strategy/market_analysis_family_scan.py
- native quote builder modules
- tests/test_reality_semantics_buy_no.py
- scripts/semantic_static_gates.py

Forbidden files:
- Executor submit logic
- Reports
- Kelly code except test fixture wiring

Invariants:
- P_no = 1 - P_yes may be belief/payoff math.
- NO executable cost must come from native NO token orderbook.
- Complement executable price is diagnostic only.

Tasks:
1. Remove or quarantine family_scan complement fallback for corrected/live.
2. Add explicit diagnostic label if complement remains for model analysis.
3. Ensure native NO token id and best ask/depth/hash are required for corrected buy_no cost basis.

Tests:
- test_buy_no_requires_native_no_quote_no_complement_fallback
- test_family_scan_buy_no_without_native_quote_is_diagnostic_only
- static gate for buy_no complement in live/cost paths

Commands:
- pytest -q tests/test_reality_semantics_buy_no.py
- python scripts/semantic_static_gates.py --buy-no

Expected failures before fix:
- family_scan fallback uses 1 - p_market.

Closeout evidence:
- Missing native NO quote fails corrected path.
- Complement path labeled diagnostic.

Rollback:
- Retain complement only as diagnostic-only evidence with no execution-mode selector.

Do not opportunistically refactor.
Do not alter posterior math beyond quote/cost separation.
```

### P6 prompt — FDR materialization identity

```text
Role: Zeus FDR/executable-hypothesis binding agent.

Scope: Bind selected statistical hypotheses to executable economic object identity.

Read first:
- src/strategy/selection_family.py
- src/engine/cycle_runtime.py
- src/contracts/execution_intent.py
- src/contracts/executable_market_snapshot_v2.py
- src/state/snapshot_repo.py

Allowed files:
- src/strategy/selection_family.py
- src/engine/cycle_runtime.py
- src/contracts/execution_intent.py
- tests/test_fdr_executable_identity.py

Forbidden files:
- Venue adapter
- Reports
- Executor submit mechanics beyond consuming bound IDs

Invariants:
- Statistical hypothesis identity is not executable hypothesis identity.
- Executable snapshot/cost/order-policy changes after FDR must reject, recompute, or amend.
- No silent mutation of selected economic object.

Tasks:
1. Add executable_hypothesis_id/hash or equivalent binding.
2. Include selected token, condition/question, executable_snapshot_id/hash, cost_basis_id/hash, order_policy_id, venue id.
3. Add amended-hypothesis representation.
4. Ensure FDR-selected decision cannot silently update cost/snapshot under same executable ID.

Tests:
- test_fdr_hypothesis_id_changes_when_executable_snapshot_or_cost_basis_changes
- test_snapshot_change_after_selection_requires_reject_recompute_or_amendment
- test_order_policy_change_changes_cost_basis_identity

Commands:
- pytest -q tests/test_fdr_executable_identity.py

Expected failures before fix:
- Family ID unchanged by executable cost/snapshot changes.

Closeout evidence:
- Hypothesis hash chain printed in test assertion output.

Rollback:
- Live remains off; shadow emits REVIEW_REQUIRED.

Do not opportunistically refactor.
Do not change FDR statistical math.
```

### P7 prompt — OrderPolicy normalization

```text
Role: Zeus order-policy semantics agent.

Scope: Normalize order policies to Polymarket CLOB order type reality.

Read first:
- src/contracts/execution_intent.py
- src/execution/executor.py
- src/venue/polymarket_v2_adapter.py
- tests/test_order_policy_semantics.py

Allowed files:
- src/contracts/execution_intent.py
- order policy config/constants
- executor validation mapping
- tests/test_order_policy_semantics.py

Forbidden files:
- Strategy scoring
- Kelly logic
- Reporting

Invariants:
- POST_ONLY_PASSIVE_LIMIT = GTC/GTD + post_only + must not cross.
- MAY_REST_LIMIT_CONSERVATIVE = GTC/GTD + non-post-only + may rest/may take.
- IMMEDIATE_LIMIT_SWEEP_DEPTH_BOUND = FOK/FAK + non-post-only.
- OrderPolicy is part of cost_basis identity.

Tasks:
1. Replace ambiguous limit_may_take_conservative semantics or alias it to a specific canonical policy.
2. Add order type/post-only coherence validators.
3. Ensure policy change changes cost_basis hash.

Tests:
- test_order_policy_order_type_mapping_is_explicit
- test_post_only_market_cross_rejected_before_submit
- test_order_policy_change_changes_cost_basis_identity

Commands:
- pytest -q tests/test_order_policy_semantics.py

Expected failures before fix:
- Ambiguous policy can map to contradictory behavior.

Closeout evidence:
- Policy mapping table in code or tests.

Rollback:
- Reject ambiguous policies.

Do not opportunistically refactor.
Do not add fill probability or queue model.
```

### P8 prompt — Venue envelope and command same-object proof

```text
Role: Zeus venue provenance repair agent.

Scope: Prove same-object identity across final intent, cost basis, snapshot, envelope, command, order, fill, and lot.

Read first:
- src/contracts/venue_submission_envelope.py
- src/venue/polymarket_v2_adapter.py
- src/state/venue_command_repo.py
- src/state/db.py
- tests/test_executor_command_split.py

Allowed files:
- src/contracts/venue_submission_envelope.py
- src/venue/polymarket_v2_adapter.py
- src/state/venue_command_repo.py
- src/state/db.py additive schema only
- tests/test_command_same_object.py
- tests/test_venue_envelope_live_bound.py

Forbidden files:
- Destructive migrations
- Strategy/evaluator changes
- Report promotion logic

Invariants:
- No live placeholder envelope.
- Same hashes must flow through final intent, cost basis, snapshot, envelope, command, fill, lot.
- Persist-before-submit is necessary but insufficient.

Tasks:
1. Add final_intent_id/hash, cost_basis_id/hash, snapshot_id/hash to envelope/command facts if absent.
2. Call envelope.assert_live_submit_bound before SDK contact.
3. Add fake SDK test proving placeholder envelope does not submit.
4. Add command/fill/lot same-object mismatch tests.

Tests:
- test_compatibility_envelope_rejected_in_live_submit
- test_adapter_submit_calls_assert_live_submit_bound_before_sdk_contact
- test_command_journal_proves_cost_basis_snapshot_envelope_same_object

Commands:
- pytest -q tests/test_command_same_object.py tests/test_venue_envelope_live_bound.py

Expected failures before fix:
- assert_live_submit_bound not reached.
- command tests do not require cost_basis hash.

Closeout evidence:
- Test fake SDK call count zero for placeholder.
- Same-object mismatch raises REVIEW_REQUIRED.

Rollback:
- Additive fields remain.
- Corrected live remains disabled.

Do not opportunistically refactor.
Do not delete compatibility helper until reachability is proven.
```

### P9 prompt — Monitor/exit held-token SELL quote symmetry

```text
Role: Zeus exit symmetry repair agent.

Scope: Corrected automated exit must use held-token SELL quote.

Read first:
- src/engine/monitor_refresh.py
- src/execution/exit_lifecycle.py
- src/execution/exit_triggers.py
- src/state/portfolio.py
- src/contracts/*
- tests/test_hold_value_exit_costs.py

Allowed files:
- src/contracts/exit_executable_quote.py or equivalent
- src/engine/monitor_refresh.py
- src/execution/exit_lifecycle.py
- src/execution/exit_triggers.py
- src/state/portfolio.py projection changes only
- tests/test_exit_executable_quote.py
- tests/test_hold_value_exit_costs.py

Forbidden files:
- Entry evaluator
- Strategy selection
- Venue adapter except quote fetch interface

Invariants:
- SELL value = held-token best bid with depth and freshness.
- current_market_price, VWMP, midpoint, posterior, entry_price are not corrected sell value.
- Manual/emergency exit must be tagged and excluded unless evidence criteria met.

Tasks:
1. Implement ExitExecutableQuote.
2. Build it in monitor/exit path.
3. Corrected positions require it for automated economic exit.
4. Quote failure returns REVIEW_REQUIRED/blocked, not stale fallback.
5. Ensure buy_yes and buy_no exit EV gates use best_bid.

Tests:
- test_exit_requires_fresh_held_token_best_bid_unless_derisk_override
- test_corrected_entry_cannot_use_legacy_exit_fallback
- test_buy_no_exit_uses_best_bid_not_vwmp
- test_manual_force_exit_tagged_excluded_from_corrected_evidence

Commands:
- pytest -q tests/test_exit_executable_quote.py tests/test_hold_value_exit_costs.py

Expected failures before fix:
- Missing quote can fall back to stale/current_market_price.

Closeout evidence:
- Corrected exit fails closed without best_bid.
- Manual override excluded from corrected evidence.

Rollback:
- Disable automated corrected exits; hold/manual only.

Do not opportunistically refactor.
Do not change entry logic.
```

### P10 prompt — PositionLot / FillAuthority

```text
Role: Zeus fill authority repair agent.

Scope: Make submitted, filled, cancelled, unknown, and settled facts distinct.

Read first:
- src/state/db.py
- src/state/venue_command_repo.py
- src/state/portfolio.py
- src/execution/exit_lifecycle.py
- src/execution/executor.py
- tests/test_executor_command_split.py

Allowed files:
- src/state/db.py additive schema
- src/state/venue_command_repo.py
- fill tracker modules
- src/state/portfolio.py projection/fallback guards
- tests/test_fill_authority.py

Forbidden files:
- Strategy/evaluator
- Reports except test fixtures

Invariants:
- Submitted limit is not average fill.
- Target notional is not filled cost.
- Partial fill and cancel remainder explicit.
- Unknown fill blocks corrected P&L.

Tasks:
1. Add/confirm FillAuthority enum.
2. Project position lots into Position without using fallback for corrected evidence.
3. Ensure unknown side effects mark REVIEW_REQUIRED.
4. Add partial fill/cancel remainder representation.

Tests:
- test_partial_fill_then_cancel_remainder_not_full_fill
- test_submit_unknown_side_effect_blocks_corrected_pnl
- test_position_lot_authority_required_for_corrected_pnl

Commands:
- pytest -q tests/test_fill_authority.py

Expected failures before fix:
- effective_shares/effective_cost_basis fallback can produce P&L.

Closeout evidence:
- Corrected P&L impossible without fill-grade authority.

Rollback:
- Quarantine uncertain positions.

Do not opportunistically refactor.
Do not rewrite historical rows.
```

### P11 prompt — DB migration / semantic cohort fields

```text
Role: Zeus migration safety agent.

Scope: Add semantic cohort fields and dry-run classifier. No destructive migration.

Read first:
- src/state/db.py
- src/state/portfolio.py
- scripts/profit_validation_replay.py
- scripts/equity_curve.py
- tests/test_backtest_skill_economics.py

Allowed files:
- migrations or schema init additions
- src/state/db.py additive only
- scripts/dry_run_pricing_semantics_migration.py
- tests/test_pricing_semantics_migration.py

Forbidden files:
- Destructive ALTER/DROP
- Runtime entry/execution behavior
- Marking rows corrected without evidence

Invariants:
- Missing semantic version defaults legacy/review_required.
- No fake corrected historical backfill.
- Migration is dry-run first.

Tasks:
1. Add additive fields listed in Section 16.
2. Implement dry-run classifier.
3. Emit classification matrix and risk counts.
4. Add tests for no mutation and no fake corrected eligibility.

Tests:
- test_migration_additive_only
- test_dry_run_classifies_legacy_rows_not_corrected
- test_no_historical_corrected_backfill_without_depth_snapshot_fill

Commands:
- pytest -q tests/test_pricing_semantics_migration.py
- python scripts/dry_run_pricing_semantics_migration.py --read-only --emit-summary

Expected failures before fix:
- Fields absent; classifier absent.

Closeout evidence:
- Dry-run output.
- No corrected eligibility without evidence.

Rollback:
- Leave additive fields inert.

Do not opportunistically refactor.
Do not mutate production DB.
```

### P12 prompt — Reporting / backtest / promotion gates

```text
Role: Zeus reporting and promotion evidence agent.

Scope: Make all report/backtest/promotion surfaces obey cohort law.

Read first:
- scripts/profit_validation_replay.py
- scripts/equity_curve.py
- src/backtest/*
- docs/reports/*
- tests/test_backtest_skill_economics.py

Allowed files:
- reporting scripts
- src/backtest/*
- shared ReportingCohort gate module
- tests/test_reporting_cohorts.py
- tests/test_backtest_skill_economics.py

Forbidden files:
- Entry/executor runtime
- Migrations except test fixtures

Invariants:
- Mixed cohorts hard-fail.
- Diagnostic replay/backtest is not promotion evidence.
- Model skill is not executable economics.
- Corrected historical economics requires depth/snapshot/fill facts.

Tasks:
1. Implement shared ReportingCohort gate.
2. Route every report script through it.
3. Keep economics backtest tombstoned until substrate ready.
4. Add promotion_eligible false for diagnostic outputs.
5. Add report registry/static test.

Tests:
- test_reports_hard_fail_mixed_pricing_semantics_cohorts
- test_all_report_paths_call_reporting_cohort_gate
- test_backtest_without_depth_snapshot_excluded_from_corrected_economics
- test_profit_replay_is_diagnostic_only
- test_skill_backtest_cannot_promote_economics

Commands:
- pytest -q tests/test_reporting_cohorts.py tests/test_backtest_skill_economics.py

Expected failures before fix:
- Unguarded reports or diagnostic promotion leaks.

Closeout evidence:
- Every report path listed with cohort gate.
- Mixed cohort fixture fails.

Rollback:
- Disable promotion-grade reports.

Do not opportunistically refactor.
Do not implement economics backtest engine in this packet.
```

### P13 prompt — Settlement / redeem / city / timezone / high-low identity

```text
Role: Zeus physical/settlement identity agent.

Scope: Separate settlement/redeem statuses and centralize city/time/metric identity.

Read first:
- src/execution/harvester.py
- src/state/chain_reconciliation.py
- src/state/portfolio.py
- src/contracts/settlement_semantics.py
- src/types/metric_identity.py
- src/config.py

Allowed files:
- src/contracts/settlement_status.py or equivalent
- src/contracts/city_identity.py
- src/contracts/time_identity.py
- src/execution/harvester.py split only as needed
- src/state/chain_reconciliation.py metric/identity guards
- tests/test_settlement_identity.py
- tests/test_city_time_identity.py

Forbidden files:
- Entry executor
- Strategy scoring
- Report promotion changes beyond status consumption

Invariants:
- Venue resolved != DB recorded != payout eligible != redeem requested != redeem confirmed.
- Target local date is explicit.
- high/low metric is explicit and authority-tagged.
- Missing metric cannot silently become corrected HIGH evidence.

Tasks:
1. Add SettlementStatus.
2. Add CityIdentity and TimeIdentity use at boundaries.
3. Add tests for high/low and local/UTC date.
4. Ensure redeem status separate from settlement recorded.

Tests:
- test_is_settled_split_into_statuses
- test_redeem_confirmed_distinct_from_settlement_recorded
- test_city_id_roundtrip_no_duplicate_transform
- test_high_low_metric_identity_required
- test_target_local_date_not_utc_day_guess

Commands:
- pytest -q tests/test_settlement_identity.py tests/test_city_time_identity.py

Expected failures before fix:
- Broad settled/status assumptions.

Closeout evidence:
- Status transition table.
- City/time/metric tests pass.

Rollback:
- Report settlement rows as REVIEW_REQUIRED.

Do not opportunistically refactor.
Do not add negative-risk optimizer.
```

### P14 prompt — Performance / staleness / telemetry

```text
Role: Zeus staleness correctness agent.

Scope: Enforce quote/snapshot/submit freshness and telemetry.

Read first:
- src/contracts/executable_market_snapshot_v2.py
- src/engine/cycle_runtime.py
- src/execution/executor.py
- src/engine/monitor_refresh.py
- src/venue/polymarket_v2_adapter.py

Allowed files:
- freshness/deadline validators
- monitor batching helpers
- telemetry counters
- tests/test_freshness_telemetry.py

Forbidden files:
- Strategy scoring
- Fill probability, queue, adverse selection models

Invariants:
- Freshness is correctness.
- Stale quote/snapshot rejects or REVIEW_REQUIRED.
- Executor must not reprice as freshness fix.
- Telemetry counters required for every fail-closed reason.

Tasks:
1. Add submit_deadline checks.
2. Add quote_age/snapshot_age telemetry.
3. Add monitor quote freshness blocking.
4. Add counters for rejected stale entry/exit.

Tests:
- test_corrected_submit_rejects_snapshot_stale_between_risk_and_submit
- test_monitor_quote_age_slo_blocks_corrected_exit
- test_submit_deadline_metric_and_counter_increment

Commands:
- pytest -q tests/test_freshness_telemetry.py

Expected failures before fix:
- Missing deadline/counter.

Closeout evidence:
- Counter names and test output.

Rollback:
- Live flag off on staleness breach.

Do not opportunistically refactor.
Do not add queue priority model.
```

### P15 prompt — Orphan cleanup / branch reduction

```text
Role: Zeus cleanup quarantine agent.

Scope: Delete or quarantine stale/orphan paths only after gates prove safety.

Read first:
- src/strategy/market_analysis_family_scan.py
- src/venue/polymarket_v2_adapter.py
- src/execution/executor.py
- scripts/profit_validation_replay.py
- scripts/semantic_static_gates.py

Allowed files:
- orphan modules
- diagnostic labels
- static gate tests
- docs notes for quarantined paths

Forbidden files:
- Deleting uncertain live-reachable code without quarantine
- Core entry/exit behavior changes not already tested

Invariants:
- Quarantine before delete.
- Diagnostic code cannot be live or promotion evidence.
- Static gates stay green.

Tasks:
1. Quarantine family-scan complement.
2. Live-disable compatibility submit helper.
3. Isolate legacy compute_native_limit_price.
4. Label replay diagnostic-only.
5. Remove stale report approximations after report gates pass.

Tests:
- static gates full suite
- reachability tests for compatibility helper
- diagnostic-only promotion exclusion

Commands:
- python scripts/semantic_static_gates.py
- pytest -q tests/test_reality_semantics_buy_no.py tests/test_venue_envelope_live_bound.py tests/test_reporting_cohorts.py

Expected failures before fix:
- Static hits for complement/compat/legacy helper.

Closeout evidence:
- Static gate clean.
- Quarantined path list.

Rollback:
- Restore quarantined diagnostic helper only as diagnostic-only evidence with no execution-mode selector.

Do not opportunistically refactor.
Do not delete live-reachable code until test proves non-reachability.
```

### P16 prompt — Docs / AGENTS / authority rewrite

```text
Role: Zeus authority documentation agent.

Scope: Rewrite docs to match runtime gates. No production code changes.

Read first:
- README.md
- AGENTS.md
- src/AGENTS.md
- src/execution/AGENTS.md
- src/strategy/AGENTS.md
- src/state/AGENTS.md
- docs/authority/* if present
- scripts/semantic_static_gates.py

Allowed files:
- README.md
- AGENTS.md
- src/*/AGENTS.md
- docs/authority/*
- tests/test_docs_authority.py

Forbidden files:
- Runtime code
- Tests that weaken semantic gates

Invariants:
- Docs are not runtime proof.
- Docs must describe four-plane model.
- Raw alpha-weighted P_market language must be legacy/diagnostic or removed.
- “Guaranteed fill” language forbidden.

Tasks:
1. Rewrite entry/prior/cost/exit/report docs.
2. Add live freeze and promotion gates.
3. Include not-now list.
4. Add docs static scan.

Tests:
- test_docs_forbidden_language_requires_legacy_or_diagnostic_qualifier
- test_docs_reference_runtime_gate_names

Commands:
- python scripts/semantic_static_gates.py --docs
- pytest -q tests/test_docs_authority.py

Expected failures before fix:
- README/AGENTS stale language.

Closeout evidence:
- Docs scan clean.
- Docs point to tests/gates.

Rollback:
- Revert docs only; runtime unaffected.

Do not opportunistically refactor.
Do not claim corrected semantics unless gates exist.
```

### P17 prompt — Final promotion gate / live runbook

```text
Role: Zeus promotion tribunal agent.

Scope: Aggregate all gates and produce final live/shadow/report runbook.

Read first:
- Section-equivalent docs/runbooks
- scripts/state_census.py
- scripts/dry_run_pricing_semantics_migration.py
- scripts/semantic_static_gates.py
- tests/*
- README.md
- AGENTS.md

Allowed files:
- docs/runbooks/corrected_live_runbook.md
- docs/reports/final_promotion_gate.md
- tests/test_acceptance_gates.py
- scripts/final_acceptance_gate.py

Forbidden files:
- New feature work
- Pricing/execution refactors
- Destructive migrations

Invariants:
- No unresolved LIVE_BLOCKER for corrected live.
- No promotion from diagnostic/backtest skill.
- Open positions classified.
- Rollback explicit.
- Acceptance gates are binary.

Tasks:
1. Implement final_acceptance_gate.py.
2. Aggregate pytest/static/census/migration/report results.
3. Produce live runbook: shadow, canary, rollback, operator checks.
4. Define stop conditions and telemetry thresholds.

Tests:
- test_corrected_shadow_gate
- test_corrected_live_entry_gate
- test_automated_exit_gate
- test_corrected_pnl_gate
- test_strategy_promotion_gate
- test_docs_authoritative_gate
- test_cleanup_allowed_gate

Commands:
- pytest -q
- python scripts/semantic_static_gates.py
- python scripts/state_census.py --read-only --emit-summary
- python scripts/dry_run_pricing_semantics_migration.py --read-only --emit-summary
- python scripts/final_acceptance_gate.py

Expected failures before fix:
- Any open blocker prevents live/promotion.

Closeout evidence:
- Final acceptance report with pass/fail per gate.
- Runbook with rollback.

Rollback:
- Corrected live flag off.
- Cancel canary.
- Quarantine ambiguous rows.

Do not opportunistically refactor.
Do not promote while any gate is REVIEW_REQUIRED.
```

---

## 21. Acceptance gates

### Gate: corrected shadow allowed

Allowed only if:

* corrected live submit disabled;
* shadow does not contact venue;
* `MarketPriorDistribution`, `ExecutableEntryCostBasis`, `ExecutableTradeHypothesis` may be built but marked `corrected_executable_shadow`;
* no P&L/promotion claim;
* report cohort labels diagnostic/shadow;
* missing native NO quote fails shadow cost basis or labels diagnostic only.

### Gate: corrected live entry allowed

Allowed only if all are true:

1. P0 census complete.
2. P1 tests/static gates pass.
3. Corrected Kelly requires `ExecutableEntryCostBasis`.
4. Buy-NO native NO quote required.
5. `ExecutableTradeHypothesis` binds selected token/snapshot/cost/policy.
6. `FinalExecutionIntent` present and immutable.
7. Executor does not reprice.
8. OrderPolicy mapping explicit.
9. Venue envelope live-bound assertion reaches adapter before SDK.
10. condition/question/YES/NO/selected token identity valid.
11. Snapshot and submit deadlines fresh.
12. Command journal persists same-object hash chain.
13. Unknown side effects become REVIEW_REQUIRED.
14. Corrected entry cannot auto-use legacy exit.
15. CI runs gates.

### Gate: automated economic exit allowed

Allowed only if:

* position classified and not `REVIEW_REQUIRED`;
* held_token_id known;
* fill/lot authority known for corrected economics;
* fresh `ExitExecutableQuote` with best bid/depth/hash exists;
* exit order policy explicit;
* stale/missing quote fails closed;
* manual/force exits tagged and excluded unless evidence criteria met.

### Gate: corrected P&L allowed

Allowed only if:

* pricing cohort = corrected executable live;
* entry cost basis hash present;
* final intent/envelope/command hash chain present;
* fill authority is confirmed partial/full/cancel remainder/settled as appropriate;
* filled cost basis and shares filled are positive;
* exit uses held-token SELL quote or settlement status with fill authority;
* unknown fill/submit/cancel/redeem status absent;
* report cohort gate passes.

### Gate: strategy promotion allowed

Allowed only if:

* corrected live entries and exits meet gates;
* promotion report contains a single corrected executable cohort;
* no diagnostic replay or skill-only result used as economics;
* no old-row corrected backfill;
* settlement/redeem statuses are separated;
* unresolved live/review-required rows excluded or block promotion;
* CI and final acceptance script pass.

### Gate: docs considered authoritative

Allowed only if:

* runtime/tests/gates already pass;
* docs static scan clean;
* docs do not claim guaranteed fills;
* raw α-weighted `P_market` language is removed or explicitly legacy/estimator-scoped;
* docs cite gate names and runbook;
* AGENTS files instruct future agents to fail closed and not use docs as runtime proof.

### Gate: cleanup allowed

Allowed only if:

* static gates prove no live reachability;
* tests cover branch behavior;
* compatibility/orphan path quarantined first;
* production state census confirms no dependency;
* rollback path documented;
* deleting code does not remove diagnostic evidence needed for open investigations.

---

## 22. Not-now list

| NOT_NOW item                                                        | Reason                                                              |
| ------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Full fill probability model                                         | Not needed to prevent wrong token/limit/cost/fill authority.        |
| Queue priority model                                                | Same; queue modeling comes after basic executable identity.         |
| Adverse-selection model                                             | Not first-order live blocker repair.                                |
| Negative-risk optimizer first                                       | Negative-risk metadata must be represented, but optimizer can wait. |
| `yes_family_devig_v1` live market-prior promotion                   | Requires validation evidence first.                                 |
| Corrected historical economics without depth/snapshot/fill          | Forbidden; fake backfill.                                           |
| Large parallel venue model                                          | Existing snapshot/cost/envelope contracts are adequate seeds.       |
| Overgeneralized ontology rewrite                                    | Would delay live blockers and create regression risk.               |
| Automatic live promotion from backtest ROI                          | Backtest ROI is not executable live evidence.                       |
| Model-skill-only promotion                                          | Skill is not execution economics.                                   |
| Hidden refactors without invariant tests                            | Future regression path.                                             |
| Warning-only mixed cohort reports                                   | Must hard-fail.                                                     |
| `BinEdge` god object                                                | Must shrink/quarantine.                                             |
| Docs-only repair                                                    | Docs are not runtime proof.                                         |
| Executor repricing as freshness fix                                 | Repricing changes economic object identity.                         |
| Deleting uncertain live-reachable code without quarantine           | Could break or hide live branches.                                  |
| Complement as executable NO price                                   | Venue reality requires native NO token quote.                       |
| Post-only/may-take/FOK/FAK policy collapse                          | Different venue behaviors.                                          |
| Manual/force exits as corrected economic evidence by default        | Must be tagged/excluded.                                            |
| Chain reconciliation mutating corrected entry cost basis            | Chain facts must not overwrite entry economics.                     |
| Settlement recorded as redeemed payout                              | Separate statuses required.                                         |
| Promotion while CI semantic gates absent                            | Agent regression risk.                                              |
| Large performance optimization before freshness gates               | Freshness correctness first.                                        |
| New strategy tuning before evidence spine                           | Strategy tuning cannot repair semantic identity.                    |
| Operator override to enable corrected live with unresolved blockers | Freeze remains until gates pass.                                    |

---

## 23. Final verification loop

| Question                                                                                                                                                                                  | Answer                                                                                                                                                                                                                                           |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1. Did you read and preserve both dossiers?                                                                                                                                               | Yes. Review A and Review B structures, findings, tests, hidden branches, migration/reporting policies, and not-now constraints are preserved in Sections 2, 3, 17, 18, and 22.                                                                   |
| 2. Did you build the full claim ledger before merging?                                                                                                                                    | Yes. Section 3 preserves A and B claims separately, including duplicates, before Section 4 crosswalks them.                                                                                                                                      |
| 3. Did you cross-validate against repo reality?                                                                                                                                           | Yes, using GitHub/web/raw inspection of the current branch tree and high-risk files. Local clone/pytest remains `REVIEW_REQUIRED` due DNS failure.                                                                                               |
| 4. Did you distinguish confirmed, partially confirmed, superseded, contradicted, and REVIEW_REQUIRED claims?                                                                              | Yes. Section 6 uses the requested classification labels. No material claim was fully contradicted by inspected current code; several are partially superseded.                                                                                   |
| 5. Did you avoid context-saving compression?                                                                                                                                              | Yes. The package preserves claims, hidden branches, stages, contracts, tests, migration, roadmap, and prompts rather than collapsing them into a short list.                                                                                     |
| 6. Did you preserve all hidden branches?                                                                                                                                                  | Yes. Section 18 includes all requested hidden branches plus newly inferred branches.                                                                                                                                                             |
| 7. Did you avoid downstream-contract-as-upstream-proof?                                                                                                                                   | Yes. Contracts are treated as seeds; live path universality remains a blocker where not proven.                                                                                                                                                  |
| 8. Did you preserve four-plane separation?                                                                                                                                                | Yes. Sections 8 and 11 enforce settlement probability, market prior, executable quote/cost, and executable trade hypothesis separation.                                                                                                          |
| 9. Did you separate critical/live/promotion/improvement/cleanup stages?                                                                                                                   | Yes. Sections 9 and 19 separate Stage 0–6 and P0–P17.                                                                                                                                                                                            |
| 10. Did you prevent executor price authority?                                                                                                                                             | Yes. Sections 10, 17, 19, and 20 require final-intent-only corrected executor and static gate against repricing.                                                                                                                                 |
| 11. Did you make exit symmetry as strong as entry?                                                                                                                                        | Yes. Sections 10 and 11 require `ExitExecutableQuote` with held-token SELL best bid/depth/hash/freshness.                                                                                                                                        |
| 12. Did you prevent report/backtest cohort contamination?                                                                                                                                 | Yes. Sections 12, 16, 17, and 21 require hard-fail cohort gates and diagnostic-only labels.                                                                                                                                                      |
| 13. Did you handle migration and old-row classification?                                                                                                                                  | Yes. Section 16 defines additive fields, dry-run, classification matrix, rollback, and no-backfill rule.                                                                                                                                         |
| 14. Did you include rollback/blast-radius/telemetry?                                                                                                                                      | Yes. Roadmap packets include rollback and blast radius; Section 14 defines telemetry.                                                                                                                                                            |
| 15. Did you include Codex-executable prompts?                                                                                                                                             | Yes. Section 20 contains P0–P17 paste-ready prompts.                                                                                                                                                                                             |
| 16. Did you define acceptance gates?                                                                                                                                                      | Yes. Section 21 defines corrected shadow, live entry, automated exit, corrected P&L, promotion, docs authority, and cleanup gates.                                                                                                               |
| 17. Did you state what must not be done now?                                                                                                                                              | Yes. Section 22 merges and expands the not-now list.                                                                                                                                                                                             |
| 18. Does the final plan satisfy the true target: same real-world economic object across selection, sizing, submit, monitor, exit, settlement, persistence, replay, report, and promotion? | Yes as an implementation plan and gate set. Actual satisfaction requires executing P0–P17, running local tests/static gates, census/migration dry-run, and acceptance gates. New corrected live entry remains frozen until that evidence exists. |

[1]: https://github.com/fitz-s/zeus/tree/live-unblock-ws-snapshot-2026-05-01 "https://github.com/fitz-s/zeus/tree/live-unblock-ws-snapshot-2026-05-01"
[2]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/types/market.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/types/market.py"
[3]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/market_analysis.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/market_analysis.py"
[4]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/contracts/execution_price.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/contracts/execution_price.py"
[5]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/engine/cycle_runtime.py "raw.githubusercontent.com"
[6]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/execution/executor.py "raw.githubusercontent.com"
[7]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/market_fusion.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/market_fusion.py"
[8]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/contracts/executable_market_snapshot_v2.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/contracts/executable_market_snapshot_v2.py"
[9]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/contracts/execution_intent.py "raw.githubusercontent.com"
[10]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/portfolio.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/portfolio.py"
[11]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/scripts/profit_validation_replay.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/scripts/profit_validation_replay.py"
[12]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/scripts/equity_curve.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/scripts/equity_curve.py"
[13]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/tests/test_backtest_skill_economics.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/tests/test_backtest_skill_economics.py"
[14]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/AGENTS.md "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/AGENTS.md"
[15]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/execution/AGENTS.md "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/execution/AGENTS.md"
[16]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/engine/evaluator.py "raw.githubusercontent.com"
[17]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/db.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/db.py"
[18]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/engine/monitor_refresh.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/engine/monitor_refresh.py"
[19]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/execution/harvester.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/execution/harvester.py"
[20]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/tests/test_execution_price.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/tests/test_execution_price.py"
[21]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/selection_family.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/selection_family.py"
[22]: https://docs.polymarket.com/trading/orders/overview "Overview - Polymarket Documentation"
[23]: https://docs.polymarket.com/trading/orderbook "Orderbook - Polymarket Documentation"
[24]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/contracts/venue_submission_envelope.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/contracts/venue_submission_envelope.py"
[25]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/venue/polymarket_v2_adapter.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/venue/polymarket_v2_adapter.py"
[26]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/venue_command_repo.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/venue_command_repo.py"
[27]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/market_analysis_family_scan.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/strategy/market_analysis_family_scan.py"
[28]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/chain_reconciliation.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/src/state/chain_reconciliation.py"
[29]: https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/tests/test_executor_command_split.py "https://raw.githubusercontent.com/fitz-s/zeus/live-unblock-ws-snapshot-2026-05-01/tests/test_executor_command_split.py"
[30]: https://docs.polymarket.com/concepts/markets-events "Markets & Events - Polymarket Documentation"
[31]: https://docs.polymarket.com/concepts/order-lifecycle "Order Lifecycle - Polymarket Documentation"
[32]: https://docs.polymarket.com/market-data/websocket/market-channel "Market Channel - Polymarket Documentation"
[33]: https://docs.polymarket.com/trading/fees "Fees - Polymarket Documentation"
