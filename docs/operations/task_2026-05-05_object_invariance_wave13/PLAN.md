# Object-Meaning Invariance Wave 13 Plan

## Scope

Boundary selected: canonical DB portfolio loader fill-authority economics -> RiskGuard protective `PortfolioState` positions.

This wave is protective/read-model provenance repair only. It does not authorize live unlock, live venue side effects, production DB mutation, schema migration, risk-policy changes, allocation formula changes, settlement harvest, backfill, redemption, report publication, or legacy relabeling.

## Route Evidence

- Root `AGENTS.md`, `src/riskguard/AGENTS.md`, `src/state/AGENTS.md`, and `docs/reference/modules/riskguard.md` were read.
- Initial route: `python3 scripts/topology_doctor.py --navigation --task "object-meaning invariance wave 13: RiskGuard portfolio loader fill-authority current-open economics provenance preservation" --write-intent edit --files src/riskguard/riskguard.py tests/test_riskguard.py docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md architecture/improvement_backlog.yaml` returned `profile: modify risk or strategy`, `admission_status: scope_expansion_required`; it admitted only `tests/test_riskguard.py` and rejected `src/riskguard/riskguard.py`, this new plan path, and `architecture/improvement_backlog.yaml`.
- Semantic bootstrap attempt: `python3 scripts/topology_doctor.py semantic-bootstrap --task-class risk --task "RiskGuard portfolio loader fill-authority current-open economics provenance preservation" --files src/riskguard/riskguard.py tests/test_riskguard.py --json` failed because `risk` is not a recognized semantic-bootstrap task class.
- Planning packet route: `python3 scripts/topology_doctor.py --navigation --task "operation planning packet for object-meaning invariance Wave13 RiskGuard portfolio loader provenance" --intent "operation planning packet" --write-intent add --files docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md` admitted this plan only.
- Topology-kernel route for adding a new narrow profile admitted `architecture/topology.yaml`, `architecture/digest_profiles.py`, and `tests/test_digest_profile_matching.py`, but rejected `docs/operations/AGENTS.md`, this plan, and `architecture/improvement_backlog.yaml`. Those companion surfaces need separate admitted routes.

## Candidate Boundaries

| Candidate | Live-money relevance | Values crossing | Downstream consumers | Stale/bypass risk | Repair scope |
| --- | --- | --- | --- | --- | --- |
| DB loader fill economics -> RiskGuard positions | RiskGuard can block entries, emit gates, flag RED/DATA_DEGRADED, and report protective exposure/PnL | `size_usd`, `cost_basis_usd`, `shares`, `entry_price`, `entry_price_avg_fill`, `shares_filled`, `filled_cost_basis_usd`, `entry_economics_authority`, `fill_authority`, `entry_fill_verified` | RiskGuard `portfolio.positions`, unrealized PnL fallback, protective details, strategy health refresh inputs | `_portfolio_position_from_loader_row()` preserves adjusted numeric values but drops fill authority/provenance, making `Position.has_fill_economics_authority` false | Safely scoped if new profile admits only mapper/test/doc/profile changes |
| RiskGuard bankroll details -> status reports | Already repaired in Wave12 | `effective_bankroll`, `total_pnl` | status/equity reports | critic approved residual fixed | Done |
| RiskGuard strategy health refresh -> status/report stale rows | Can affect operator status interpretation | strategy health snapshot rows | status_summary/report surfaces | Wave11 noted stale status route residual | Deferred; likely separate status/report wave |
| RiskGuard durable actions -> engine/control consumption | Directly affects entry gating | risk action rows and `RiskLevel` | cycle runner/evaluator/executor | high blast radius policy grammar path | Out of scope; policy changes forbidden |

Selected: DB loader fill economics -> RiskGuard protective `PortfolioState` positions.

## Lineage Table

| Value | Real object denoted | Origin | Authority/evidence | Unit/side | Time basis | Transform | Persistence | Consumers | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `query_portfolio_loader_view().positions[].size_usd` | Current open exposure cost basis for the position | `src/state/db.py::query_portfolio_loader_view` | canonical DB read model; fill-authority adjusted when execution fact is filled | USD held side | loader query time, current projection time, fill time when available | `fill_economics["effective_cost_basis_usd"]` | in-memory loader view | RiskGuard mapper | numerically preserved |
| `cost_basis_usd` | PnL cost basis for current open position | `src/state/db.py::query_portfolio_loader_view` | canonical DB read model; fill-authority adjusted when filled | USD held side | loader query time/fill time | `fill_economics["pnl_cost_basis_usd"]` | in-memory loader view | RiskGuard mapper -> `Position.effective_cost_basis_usd` | numerically preserved |
| `shares` | Current open held-side shares | `src/state/db.py::query_portfolio_loader_view` | canonical DB read model; min(current projection, filled shares) when filled | market shares, held side | loader query time/fill time | `fill_economics["effective_shares"]` | in-memory loader view | RiskGuard mapper -> `Position.effective_shares` | numerically preserved |
| `entry_price` | Effective held-side entry price | `src/state/db.py::query_portfolio_loader_view` | avg fill price when filled, projection entry price otherwise | probability/price in held-side native space | fill time or projection time | `fill_economics["effective_entry_price"]` | in-memory loader view | RiskGuard mapper | numerically preserved |
| `entry_price_avg_fill` | Venue-confirmed average fill price | `_query_entry_execution_fill_hints()` | canonical `execution_fact` filled entry evidence | probability/price in held-side native space | filled_at | copied into loader view | in-memory loader view | RiskGuard mapper | broken: dropped |
| `shares_filled` | Venue-confirmed filled shares | `_query_entry_execution_fill_hints()` | canonical `execution_fact` filled entry evidence | market shares | filled_at | copied into loader view | in-memory loader view | RiskGuard mapper | broken: dropped |
| `filled_cost_basis_usd` | Venue-confirmed fill cost | `_query_entry_execution_fill_hints()` | canonical `execution_fact` filled entry evidence | USD held side | filled_at | `fill_price * shares` | in-memory loader view | RiskGuard mapper | broken: dropped |
| `entry_economics_authority` | Authority class for executable entry economics | `_position_current_effective_entry_economics()` | `avg_fill_price` for filled execution fact; `legacy_unknown` otherwise | enum/string authority | loader query/fill evidence time | explicit classification | in-memory loader view | RiskGuard mapper -> `Position.has_fill_economics_authority` | broken: dropped |
| `fill_authority` | Authority class for fill finality | `_position_current_effective_entry_economics()` | `venue_confirmed_full` for filled execution fact; `none` otherwise | enum/string authority | loader query/fill evidence time | explicit classification | in-memory loader view | RiskGuard mapper -> `Position.has_fill_economics_authority` | broken: dropped |
| `entry_fill_verified` | Boolean fill-finality evidence flag | loader hints plus transitional hints | canonical execution fact/transitional hints | boolean evidence flag | fill or lifecycle hint time | OR of hints | in-memory loader view | RiskGuard mapper | preserved but insufficient alone |
| `temperature_metric` | Physical/market family identity for high vs low contract | `position_current.temperature_metric` via `query_portfolio_loader_view` | canonical DB current-position read model | enum: high/low | loader query/current projection time | copied into `Position.temperature_metric` | RiskGuard in-memory portfolio | protective position identity | repaired after critic |
| `token_id`, `no_token_id`, `condition_id` | Venue/token market identity | `position_current` via `query_portfolio_loader_view` | canonical DB current-position read model | CLOB token/condition IDs | loader query/current projection time | copied into `Position` token fields | RiskGuard in-memory portfolio | protective position identity | repaired after critic |
| `entry_economics_source`, `execution_fact_intent_id`, `execution_fact_filled_at` | Source and fill-time provenance for fill-grade economics | `_query_entry_execution_fill_hints()` | canonical `execution_fact` source/time evidence | string source, intent id, ISO-ish fill time | fill time | validated before authority is accepted, then intentionally terminates at Position authority fields | no `Position` persistence field in this route | RiskGuard mapper boundary | explicit transform |

UNKNOWN: whether every future RiskGuard protective calculation will remain numerically compatible if provenance stays dropped. Because `Position.effective_cost_basis_usd`, `effective_shares`, and `unrealized_pnl` branch on `has_fill_economics_authority`, provenance loss is economically material even where current numeric fields happen to match.

## Findings

### W13-F1 - S1 Active

Object meaning changed: canonical loader rows with venue-confirmed fill economics become RiskGuard `Position` objects with legacy/unknown entry economics authority.

Boundary: `src/state/db.py::query_portfolio_loader_view` -> `src/riskguard/riskguard.py::_portfolio_position_from_loader_row`.

Code path: the loader emits `entry_price_avg_fill`, `shares_filled`, `filled_cost_basis_usd`, `entry_economics_authority`, `fill_authority`, and `entry_economics_source`, but the RiskGuard mapper passes only adjusted `size_usd`, `shares`, `cost_basis_usd`, `entry_price`, and `entry_fill_verified`. `Position.has_fill_economics_authority` therefore remains false.

Economic impact: RiskGuard protective PnL/exposure fallbacks can become numerically or semantically legacy if `Position.effective_cost_basis_usd`, `effective_shares`, or `unrealized_pnl` are consumed after the authority bit is lost. The system may still treat the object as the same current-open economic position while discarding the venue evidence class that justifies the numbers.

Reachability: active protective path. `tick()` loads `query_portfolio_loader_view()` and builds `PortfolioState` before calculating unrealized PnL fallback and writing risk details.

Repair invariant: RiskGuard positions created from canonical loader rows must preserve fill-authority provenance fields whenever the loader provides them. If a row claims fill-grade economics but lacks positive filled shares/cost, the position must not silently become fill-authority; the existing `Position.has_fill_economics_authority` predicate remains the gate.

### W13-F2 - S3 Active Topology Compatibility

The existing `modify risk or strategy` profile recognizes `src/riskguard/riskguard.py` but does not admit it, while `semantic-bootstrap --task-class risk` is unavailable. Object-meaning repairs on RiskGuard read-model mapper seams need a narrow profile distinct from risk policy grammar changes.

### W13-F3 - S2 Active

Object meaning changed: loader row market identity did not survive into RiskGuard `Position`.

Boundary: `src/state/db.py::query_portfolio_loader_view` -> `src/riskguard/riskguard.py::_portfolio_position_from_loader_row`.

Code path: the loader emits `temperature_metric`, `token_id`, `no_token_id`, and `condition_id`, but the mapper initially omitted them. `Position.temperature_metric` therefore defaulted to `high`, and token/condition IDs became empty strings.

Economic impact: aggregate exposure/PnL can remain numerically correct while the active protective object loses high/low physical identity and venue-token identity. Future protective or diagnostic consumers could treat a low contract as high or lose held-token identity.

Reachability: active protective path. Found by first critic pass.

Repair invariant: RiskGuard positions preserve `temperature_metric`, `token_id`, `no_token_id`, and `condition_id` from canonical loader rows; relationship test uses a low contract and non-empty IDs.

### W13-F4 - S3 Active Diagnostic

Object meaning changed: source/time provenance from the execution fact can be dropped after converting to authority fields.

Boundary: `src/state/db.py::query_portfolio_loader_view` -> RiskGuard `Position` construction.

Code path: the loader emits `entry_economics_source`, `execution_fact_intent_id`, and `execution_fact_filled_at`; `Position` has no admitted destination fields for these without changing `src/state/**`, which this wave forbids.

Economic impact: currently diagnostic because RiskGuard does not publish per-position provenance, but accepting fill-grade authority without validating source/time evidence would make provenance ambiguous.

Repair invariant: RiskGuard validates `entry_economics_source == "execution_fact"`, non-empty `execution_fact_intent_id`, and non-empty `execution_fact_filled_at` before accepting any fill-grade entry/fill authority. The source/time evidence intentionally terminates at the mapper after validation; future durable retention needs a separate state/Position route.

## Repair Plan

1. Add a narrow topology profile `object meaning riskguard loader provenance semantics` that admits only this mapper/read-model repair surface plus plan/test/topology companion files, and forbids DB mutation, schema, policy grammar, allocation formulas, venue side effects, settlement, engine/execution, and live unlock.
2. Add a digest-profile test proving the Wave13 phrase admits `src/riskguard/riskguard.py`, `tests/test_riskguard.py`, the plan, docs registry, topology files, and backlog only under the narrow profile.
3. Update `_portfolio_position_from_loader_row()` to preserve loader-provided fill economics/provenance into `Position`.
4. Add a relationship test proving a filled `execution_fact` travels through `query_portfolio_loader_view()` into RiskGuard `Position` with `has_fill_economics_authority == True`, correct effective exposure/cost basis, correct unrealized PnL, and explicit authority fields.
5. Run focused tests, py_compile, digest export, schema, planning-lock, map-maintenance/freshness checks, and downstream contamination grep.
6. Run critic with instructions to inspect cross-module preservation from DB loader to RiskGuard details and to challenge legacy/fallback bypasses before advancing.

## Verification Plan

- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_riskguard_loader_provenance_semantics_routes_to_wave13_profile`
- `pytest -q -p no:cacheprovider tests/test_riskguard.py -k 'portfolio_loader_fill_authority or position_current_for_portfolio_truth'`
- `python3 -m py_compile src/riskguard/riskguard.py tests/test_riskguard.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
- `python3 scripts/digest_profiles_export.py --check`
- `python3 scripts/topology_doctor.py --schema`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md`
- Static sweep for RiskGuard loader consumers that still drop `entry_economics_authority`, `fill_authority`, `shares_filled`, `filled_cost_basis_usd`, or `entry_price_avg_fill`.

## Verification Results

- `pytest -q -p no:cacheprovider tests/test_riskguard.py::TestRiskGuardSettlementSource::test_portfolio_loader_fill_authority_preserved_into_riskguard_position tests/test_riskguard.py::TestRiskGuardSettlementSource::test_portfolio_loader_fill_authority_requires_source_time_provenance` passed: 2 passed.
- `pytest -q -p no:cacheprovider tests/test_riskguard.py -k 'portfolio_loader_fill_authority or position_current_for_portfolio_truth'` passed: 3 passed, 47 deselected.
- `pytest -q -p no:cacheprovider tests/test_riskguard.py` passed: 50 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_riskguard_loader_provenance_semantics_routes_to_wave13_profile` passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py` passed: 159 passed.
- `python3 -m py_compile src/riskguard/riskguard.py tests/test_riskguard.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` passed.
- `python3 scripts/digest_profiles_export.py --check` passed.
- `python3 scripts/topology_doctor.py --schema` passed.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files src/riskguard/riskguard.py tests/test_riskguard.py tests/test_digest_profile_matching.py` passed.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md docs/operations/AGENTS.md src/riskguard/riskguard.py tests/test_riskguard.py architecture/topology.yaml architecture/digest_profiles.py architecture/improvement_backlog.yaml tests/test_digest_profile_matching.py --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md` passed.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md docs/operations/AGENTS.md src/riskguard/riskguard.py tests/test_riskguard.py architecture/topology.yaml architecture/digest_profiles.py architecture/improvement_backlog.yaml tests/test_digest_profile_matching.py` passed.
- `python3 scripts/topology_doctor.py --navigation --task "object-meaning invariance wave 13: RiskGuard portfolio loader fill-authority current-open economics provenance preservation" --write-intent edit --files docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md docs/operations/AGENTS.md src/riskguard/riskguard.py tests/test_riskguard.py architecture/topology.yaml architecture/digest_profiles.py architecture/improvement_backlog.yaml tests/test_digest_profile_matching.py` passed and admitted only the Wave13 route surface.
- Forbidden-surface navigation with `src/riskguard/policy.py`, `src/risk_allocator/governor.py`, `src/state/db.py`, and `state/zeus-world.db` was blocked by the Wave13 profile.
- `git diff --check` passed.
- Static sweep for fill-authority and market-identity fields found RiskGuard's mapper preservation, the new relationship tests, canonical DB loader producers, and `Position` properties. No remaining RiskGuard mapper path was found that drops loader fill-authority or market-identity fields.

## Critic Verdict

- First critic pass: REVISE. It found W13-F3 (market identity fields dropped: `temperature_metric`, `token_id`, `no_token_id`, `condition_id`) and W13-F4 (source/time provenance ambiguity after authority conversion).
- Repair after critic: RiskGuard now preserves market identity fields into `Position`; the relationship test uses a low-temperature contract and non-empty token/condition IDs. RiskGuard also validates execution-fact source/time provenance before accepting fill-grade authority, with a negative test proving missing `execution_fact_filled_at` fails closed.
- Second critic pass: APPROVE. No remaining active S0/S1/S2 path was found where canonical DB loader fill-authority/current-open economics or low/token market identity become legacy/unknown or the wrong physical/market object inside RiskGuard. The critic confirmed the source/time provenance validation is a safe explicit boundary termination under this route.

## Compatibility Notes

- Topology profile granularity is lagging object-meaning audit needs: broad `riskguard` file evidence selects a profile that blocks its own primary source file.
- Semantic bootstrap has no `risk` task class despite RiskGuard being a K1 protective live-money boundary.
- Companion surface routing is fragmented: topology profile edits, planning packet files, operations registry updates, and improvement backlog entries each require separate routes even for one bounded wave. That is workable but creates high ceremony for semantic repair waves.
