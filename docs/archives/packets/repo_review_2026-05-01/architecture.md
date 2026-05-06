# Architecture Review — Zeus repo @ ultrareview25-remediation-2026-05-01 (HEAD 355bcfcb)

Authored by `architect` agent (opus, READ-ONLY) on 2026-05-01.
Saved by team-lead because the agent's prompt blocked Write/Edit; original returned inline.

## Boot evidence
Read in this order: `AGENTS.md`, `architecture/AGENTS.md`, `architecture/invariants.yaml` (full), `architecture/fatal_misreads.yaml`, `architecture/module_manifest.yaml`, `architecture/negative_constraints.yaml`, `architecture/map_maintenance.yaml`, `docs/operations/current_state.md`, `docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md`, plus targeted reads of `src/config.py`, `src/contracts/settlement_semantics.py`, `src/state/portfolio.py`, `src/state/truth_files.py`, `src/state/db.py` (515/1559/1581), `src/execution/executor.py` (1525/2126/2209), `src/data/polymarket_client.py`, `src/main.py`, `architecture/ast_rules/semgrep_zeus.yml`, `.claude/settings.json`, `.claude/hooks/*`, `.git/hooks/`. INV citations spot-grepped within last 10 min against HEAD.

## K-decisions (4)

### K1 — Wire `settle_market()` as the single rounding gate
`src/contracts/settlement_semantics.py:263-287` defines `settle_market(city, raw, policy)` whose TypeError makes "wrong rounding for wrong city" unconstructable. Production callers (`src/calibration/store.py:98,171`, `src/ingest/harvester_truth_writer.py:280`) still call the legacy string-dispatch path `SettlementSemantics.for_city(...).round_values`. The file's own header comment (line 194) admits: "This block APPENDS a parallel type-encoded settlement-rounding policy. It does NOT replace the existing... that migration is Tier 3 P8 territory." `architecture/fatal_misreads.yaml:141` cites `type_encoded_at: src/contracts/settlement_semantics.py:HKO_Truncation` as the active antibody for the Hong Kong rounding misread — but `grep -rn "settle_market\b" src/` returns zero call sites in `src/`. **The antibody is dormant.** Decision: route every settlement DB write through `settle_market(city, raw, policy)` (or wrap `assert_settlement_value` to construct the policy and dispatch via it), then delete `oracle_truncate` from the string path so legacy cannot drift.

### K2 — Runtime call-stack guard on `place_limit_order` (single-ring → double-ring)
INV-24 / NC-16 says `place_limit_order` is gateway-only. The semgrep rule (`architecture/ast_rules/semgrep_zeus.yml:162-177`) is the only structural enforcement, with `src/execution/executor.py`, `src/data/polymarket_client.py`, `scripts/live_smoke_test.py` allowlisted. But (a) semgrep is lint-time, not import-time, (b) the test `tests/test_p0_hardening.py:262` is a single AST-grep, (c) Bash-channel agent commits enforce hooks (`.claude/settings.json:14`) but operator-direct commits do not. Decision: encode at runtime — at `src/data/polymarket_client.py:253` `place_limit_order` entry, walk `inspect.stack()` and refuse unless caller frame's filename ends with one of the three allowlisted paths.

### K3 — Closed enum + mandatory consumer enumeration for authority labels
`src/state/portfolio.py:65-69` builds `_TRUTH_AUTHORITY_MAP = {"canonical_db": "VERIFIED", "degraded": "DEGRADED_PROJECTION", "unverified": "UNVERIFIED"}`. The comment at lines 62-64 says "grep confirms no `DEGRADED_PROJECTION` consumer in src/" — i.e. INV-23 ("degraded export must not be VERIFIED") is satisfied at the producer, but **no consumer differentiates `DEGRADED_PROJECTION` from `UNVERIFIED` or `VERIFIED`**. Authority is half-encoded: producers stamp, consumers ignore. Decision: turn the string into a closed `enum.StrEnum` `TruthAuthority {VERIFIED, UNVERIFIED, DEGRADED_PROJECTION, QUARANTINED}`, then require every truth-file reader to switch over all members.

### K4 — `DEFAULT 'high'` violates INV-14 in three more places, not one
The remediation plan (`docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md:31`) cites only `architecture/2026_04_02_architecture_kernel.sql:129`. Re-grep found **two more live runtime sites**: `src/state/db.py:515` (initial schema), `src/state/db.py:1559` (ALTER TABLE migration on `position_current`), `src/state/db.py:1581` (`ensemble_snapshots`). Decision: drop `DEFAULT 'high'` everywhere AND apply same audit to `physical_quantity`, `observation_field`, `data_version`.

## Top architecture / live-risk findings (ranked by blast radius)

| # | Severity | Finding | K | Evidence | Antibody |
|---|---|---|---|---|---|
| 1 | P0 | INV-05 ("Risk must change behavior") cites `tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema` — **this test does not exist anywhere in `tests/`** | K3-adj/immune | `architecture/invariants.yaml:54-56`; `grep -rn "test_risk_actions" tests/` → zero | Add the test or rewrite enforcement clause |
| 2 | P0 | `.git/hooks/` contains only `*.sample` files; `core.hookspath` points there. `.claude/hooks/*.sh` only fire when an agent runs `git commit` via Bash | immune | `ls .git/hooks/` confirms; `.claude/settings.json:14-29` agent-only | Symlink or `git config core.hooksPath .claude/hooks` |
| 3 | P0 | Settlement type-guard (HKO/WMO) is built and untouched by the production path; legacy string `oracle_truncate` runs | K1 | `src/contracts/settlement_semantics.py:194`; zero `settle_market` calls in `src/` | Wire `settle_market()` into `assert_settlement_value`; delete `oracle_truncate` |
| 4 | P1 | `temperature_metric NOT NULL DEFAULT 'high'` lives at THREE sites; plan cites one | K4 | `src/state/db.py:515,1559,1581` + `architecture/2026_04_02_architecture_kernel.sql:129` | Remove DEFAULT at all four sites; CI grep-gate |
| 5 | P1 | `DEGRADED_PROJECTION` has no consumer that distinguishes it from `UNVERIFIED` | K3 | `src/state/portfolio.py:60-67` | Closed enum + exhaustive match |
| 6 | P1 | F12 (INV-23 ↔ NC-17 anchor) operator-deferred since 2026-04-26 | K3/governance | `PLAN.md:79`, `architecture/invariants.yaml:233-241` | Operator ruling needed |
| 7 | P1 | `make_family_id` deprecated wrapper still exists alongside `make_edge_family_id` + `make_hypothesis_family_id` (R3 split). INV-22 says one canonical helper | immune/drift | `tests/test_fdr_family_scope.py:9-13,170` | Cut over and delete wrapper, or rewrite INV-22 |
| 8 | P2 | Forbidden patterns doc lists FM-08 ("immediate forbidden") with **no semgrep rule named `fm-08`** | immune | `PLAN.md:30` (F16); zero `fm-08` in semgrep rules | Add the rule, or delete the FM-08 row |
| 9 | P2 | `architecture/inv_prototype.py:73,247` — `validate()` mutates state; `all_drift_findings()` duplicates | immune | `PLAN.md:18,23` | 5-line idempotency fix + regression test |
| 10 | P2 | `src/contracts/` dataclasses have bare `source: str` / `verification_source: str` fields, not `ExternalParameter[T]` (cf. global epistemic_scaffold rule) | K3 | `src/contracts/executable_market_snapshot_v2.py:303,377`; `expiring_assumption.py:22`; `execution_intent.py:286,690,696,719`; `semantic_types.py:135` | Wrap in typed envelope |

## INV-## drift sample (7 picked, 1 doc-only — 14% drift)

| INV | Stated test | Real? | Notes |
|---|---|---|---|
| INV-01 | `test_negative_constraints_include_no_local_close` | ✅ `tests/test_architecture_contracts.py:52` | Real |
| INV-04 | `test_strategy_key_manifest_is_frozen` | ✅ `:40` | Real |
| **INV-05** | `test_risk_actions_exist_in_schema` | ❌ **DOES NOT EXIST** | **Doc-only — Top-finding #1** |
| INV-19 | `test_red_triggers_active_position_sweep` | ✅ `tests/test_dual_track_law_stubs.py:248`, `test_runtime_guards.py:4796`, `test_riskguard_red_durable_cmd.py:111-142` | Real, multi-anchored |
| INV-22 | `test_fdr_family_key_is_canonical` | ✅ `tests/test_dual_track_law_stubs.py:210` | Real but mid-migration |
| INV-24 | `test_place_limit_order_gateway_only` | ✅ `tests/test_p0_hardening.py:262` | Lint-time only; runtime guard absent (K2) |
| INV-26 | `test_cycle_runner_posture_gate_blocks_with_reason` | ✅ `tests/test_p0_hardening.py:574` + `:518` | Real |

## Provenance survey

- `src/config.py:48` `get_mode() -> "live"` — obsolete non-live runtime/shadow modes structurally removed; `ACTIVE_MODES = ("live",)`. **Strong antibody**.
- `src/state/truth_files.py:43-74` `build_truth_metadata` defaults `authority="UNVERIFIED"`. Producer-side discipline is good.
- **Consumer side is weak**: `_TRUTH_AUTHORITY_MAP` (portfolio.py:65) admits no downstream consumer reads `DEGRADED_PROJECTION` differently. INV-23 is producer-validated, consumer-blind.
- `src/calibration/store.py:116-135` `_resolve_training_allowed` whitelist-by-prefix on `data_version` (INV-15). Proper provenance gating.
- `src/contracts/*.py` — `source: str`, `verification_source: str`, `fee_source: str`, `depth_proof_source: str`, `imputation_source: str`, `bin_source` — all bare `str`. Per global epistemic gate, must be `ExternalParameter[T]`. Provenance evaporates at `dataclass(frozen=True)` field boundaries.

**Net**: provenance authority lives in column-name discipline and producer-side stamping, NOT in the type system. K3 closes this.

## Recommended hardening (concrete)

1. Wire `settle_market()` as the single rounding gate. Replace `SettlementSemantics.round_single` body (`settlement_semantics.py:89-95`) with dispatch through `settle_market(city_name, Decimal(value), policy)`. Construct via `policy_for_city(city) -> SettlementRoundingPolicy`. Delete `oracle_truncate`. **Test**: `tests/test_settlement_semantics.py::test_settle_market_called_on_every_settlement_write`.
2. Runtime call-stack guard on `place_limit_order`. At `src/data/polymarket_client.py:253` walk `inspect.stack()`; raise `RuntimeError("INV-24: place_limit_order called outside gateway")`.
3. Closed enum for authority labels. `class TruthAuthority(StrEnum): VERIFIED, UNVERIFIED, DEGRADED_PROJECTION, QUARANTINED`. AST-grep all `truth["authority"]` reads.
4. Drop `DEFAULT 'high'` from all four sites + audit siblings. CI grep-gate in `scripts/check_kernel_manifests.py`.
5. Add the missing INV-05 test. Parse kernel SQL, assert `risk_actions` table exists with non-advisory columns.
6. Real git hooks. `git config core.hooksPath .claude/hooks` after renaming files; or symlink each into `.git/hooks/`.
7. Cross-ref consistency check (already in remediation plan K3) — extend `architecture/inv_prototype.py` to validate every `tests:` cite resolves to a real `def test_*`. This would have caught Top-finding #1 in CI.
8. Type-wrap `source: str` fields in `src/contracts/`. Replace with `ExternalParameter[SourceFamily] | None` (closed enum).

## Drift summary

The K-pattern across all four K-decisions: **producers are disciplined; consumers and runtime gates are weaker than the YAML claims**. K1, K3, and the missing INV-05 test all share this shape — the rule exists in the documentation plane and partly in the producer plane, but the consumer/runtime/test plane has the actual hole.
