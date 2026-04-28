# Polymarket CLOB V2 Migration — Work Log

Created: 2026-04-26
Authority basis: this packet's `plan.md`

This file records per-slice closure events. Update FIRST before any commit (memory: phase commit protocol).

---

## Format

Each slice closure adds a row to the table below and a more detailed paragraph in the slice notes section. The table is the index; paragraphs are the audit trail.

| Date | Phase.Slice | Commit | Critic verdict | Notes |
|---|---|---|---|---|
| 2026-04-27 | R3.Z0 | pending | APPROVE | R3 Z0 source-of-truth correction closed locally: impact report rewrite, open-question correction, packet-local live-money contract, plan-lock tests; post-close third-party audit approved and Z1 is ready to start. |
| 2026-04-27 | R3.Z1 | pending | APPROVE | R3 Z1 CutoverGuard closed locally after pre-close critic+verifier approval: fail-closed state machine, HMAC-signed operator transitions, executor pre-side-effect submit gate, cycle summary surface, and regression tests; post-close third-party review passed and Z2 was unfrozen. |
| 2026-04-27 | R3.Z2 | pending | APPROVE | R3 Z2 V2 adapter closed locally after blocker-fix critic+verifier approval: strict `PolymarketV2Adapter`, frozen `VenueSubmissionEnvelope`, V2-only live dependency, preflight-before-submit compatibility wrapper, missing-order-id rejection, stale snapshot antibodies, and full R3 YAML/topology cleanup. Post-close third-party critic Confucius and verifier Wegener approved the closeout; Z3/Z4 are now unfrozen. |
| 2026-04-27 | R3.Z3 | pending | APPROVE | R3 Z3 HeartbeatSupervisor closed after pre-close critic+verifier and post-close critic+verifier approval: venue heartbeat health state, single fail-closed tombstone reuse, GTC/GTD submit gate, FOK/FAK immediate-only exemption, daemon scheduler hook, cycle summary surface, and scheduler-observability regression. Post-close critic blocker was fixed with the B047 wrapper; Z4 is now unfrozen. |
| 2026-04-27 | R3.Z4 | pending | APPROVE | R3 Z4 CollateralLedger re-closed after fresh pre-close critic+verifier approval and terminal-release atomicity repair: pUSD/CTF collateral ledger, allowance-aware reservations, quantized BUY notional reservation, repo-owned terminal release, DB-backed snapshot refresh, no-live-side-effect wrap/unwrap commands, and R1-deferred redemption. Second post-close critic Leibniz + verifier Herschel passed; U1 is unfrozen. |

---

## Slice notes

(Slice-by-slice closure narratives go here. Initial state: empty. Add a `### Phase X.Y — <slice name>` section when a slice closes.)

---

## Phase closure log

(Phase-level closure events go here. Format: `### Phase N closed YYYY-MM-DD` followed by critic verdict path + summary.)

### Phase R3.Z0 closed 2026-04-27

Pre-close critic + verifier approved the Z0 doc/test-only closeout after the
packet-local live-money contract route, impact-report path normalization,
legacy-plan stale-premise cleanup, plan-lock tests, topology gates, and explicit
`.code-review-graph/graph.db` exclusion were in place. Commit is still pending;
the required post-close third-party critic+verifier pass also approved after the
temporary `ready_to_start: []` freeze was cleared. Z1 is now ready to start, but
its live cutover remains operator-gated.

### Phase R3.Z1 closed 2026-04-27

Pre-close critic first required stronger operator authority and narrower
cancel/redemption claims. The revised closeout now requires
`ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET`-backed HMAC operator tokens, rejects
unsigned ambient tokens, stores only operator identity plus token fingerprint,
and documents that Z1 enforces entry/exit submit preflight while direct
cancel/redeem side-effect wiring remains future work for M4/R1. M5/T1 still
own cutover-wipe reconciliation and full fake-venue simulation. The second
pre-close critic+verifier pass approved the closure with focused tests at
`57 passed, 4 skipped`, drift check green, topology navigation green, and
planning-lock/map-maintenance green excluding the pre-existing dirty derived
`.code-review-graph/graph.db`. Z2 was kept frozen until the required
post-close third-party critic+verifier pass completed.

Post-close third-party critic+verifier then approved Z1 with no blockers:
critic confirmed Z1 does not overclaim cancel/redeem or reconciliation scope,
verifier re-ran the focused suite and metadata checks, and `ready_to_start`
was advanced to `[Z2]` at `2026-04-27T03:37:29Z`.

### Phase R3.Z2 closed 2026-04-27

The first pre-close critic and verifier blocked Z2 on live-money safety and
workspace hygiene: legacy `_clob_client.create_order/post_order` bypass,
missing centralized preflight for compatibility submits, entry ACK on missing
venue order id, post-hoc SELL envelope mutation, weak snapshot freshness, an
`UNKNOWN` outcome label allowance, malformed R3 slice-card YAML, and missing
full-diff routing. The revised Z2 closeout removes the legacy live bypass,
routes `PolymarketClient.place_limit_order()` through adapter preflight before
any submit, rejects missing order ids and `success=false` before `SUBMIT_ACKED`,
computes compatibility SELL hashes from final side/size, rejects stale snapshots
using `captured_at + freshness_window_seconds`, limits envelope outcomes to
`YES`/`NO`, repairs R3 YAML parsing, and adds package-closeout topology routing.
The post-close third-party critic then found one remaining public-boundary
variant: direct `PolymarketV2Adapter.submit()` / `submit_limit_order()` calls
could bypass Q1 if callers skipped the compatibility wrapper. The final adapter
now enforces preflight inside those methods and returns typed rejections without
SDK contact when Q1 evidence is absent.

The second pre-close critic+verifier pass approved the closure. Evidence:
focused tests at `93 passed, 4 skipped` locally and verifier-expanded tests at
`97 passed, 5 skipped`, later updated to `100 passed, 4 skipped` after public
adapter-submit fail-closed regressions, `py_compile` OK, all R3 package YAML
parse OK, R3 drift check `GREEN=18 YELLOW=0 RED=0`, map-maintenance/reference-replacement/
planning-lock OK, topology navigation OK for both Z2 subset and full R3 package
diff, no V1 `py_clob_client` live imports, and `py_clob_client_v2` confined to
`src/venue/`. Q1-zeus-egress remains open, so runtime V2 preflight remains
fail-closed and no live cutover is authorized. Post-close third-party critic
Confucius approved the adapter/live-money boundary, verifier Wegener reran the
focused tests and closeout gates, and `ready_to_start` was advanced to
`[Z3, Z4]`.

### Closeout metadata record — R3.Z2 2026-04-27

Date: 2026-04-27
Branch: plan-pre5
Task: R3 Z0/Z1/Z2 package implementation and Z2 CLOB V2 adapter closeout.
Changed files: R3 package docs/manifests, CLOB V2 adapter/envelope/control source, executor/data seams, and focused R3 regression tests; `.code-review-graph/graph.db` is excluded as a pre-existing derived local artifact.
Summary: Added Z0 plan-lock evidence, Z1 CutoverGuard, and Z2 Polymarket V2 adapter fail-closed boundary with compatibility routing through `PolymarketClient`, strict submission envelope provenance, stale snapshot rejection, V2-only live SDK dependency, and package topology/mesh maintenance.
Verification: Focused R3 suite reached `100 passed, 4 skipped`; `py_compile` OK; R3 YAML parse OK; Z2 drift check GREEN; map-maintenance, reference-replacement, planning-lock, topology navigation, import scan, and diff-check gates were run locally before post-close review.
Next: Start the next R3 phase (`Z3` HeartbeatSupervisor or `Z4` CollateralLedger) through topology navigation, phase boot, tests-first implementation, pre-close critic+verifier, and post-close third-party critic+verifier before any further unfreeze.

### Phase R3.Z3 pre-close record 2026-04-27

Date: 2026-04-27
Branch: plan-pre5
Task: R3 Z3 HeartbeatSupervisor mandatory live-resting-order gate.
Changed files: `src/control/heartbeat_supervisor.py`, executor/daemon/cycle wiring, heartbeat tests, R3 state docs, and topology/module/test registries.
Summary: Added a duck-typed venue heartbeat supervisor that transitions STARTING→HEALTHY/DEGRADED/LOST, writes the existing `auto_pause_failclosed.tombstone` with `heartbeat_cancel_suspected` after two misses, blocks GTC/GTD submit when health is not HEALTHY, keeps FOK/FAK immediate-only order types heartbeat-exempt, wires executor pre-persist gates, surfaces heartbeat health in cycle summary, and schedules venue heartbeat ticks in the daemon.
Verification: Initial focused R3 suite reached `107 passed, 8 skipped`; after post-close critic found the missing B047 scheduler-observability wrapper, the blocker fix reached `114 passed, 8 skipped` including `tests/test_bug100_k1_k2_structural.py`, and `tests/test_heartbeat_supervisor.py` reached `8 passed, 4 skipped`. `py_compile` OK; Z3 drift check GREEN; map-maintenance/reference-replacement/closeout checks pass with no blocking issues. Skips are explicit M5/T1-owned integration placeholders.
Next: Pre-close critic Nietzsche and verifier Lorentz approved. Post-close critic Bernoulli blocked once on scheduler observability; the venue heartbeat job now uses `@_scheduler_job("venue_heartbeat")` and reports post misses as FAILED. Bernoulli then APPROVED and verifier Cicero PASSED the post-fix gate. `ready_to_start` advanced to `[Z4]`; start Z4 only through topology navigation and its own pre/post close gates.

---

## Open process notes

(Standing notes on slice-discipline observations, co-tenant interactions, operator handoffs, etc. Use freely.)

- 2026-04-27 — R3 Z0 detected that the phase card requested `docs/architecture/polymarket_live_money_contract.md`, but `docs/architecture/` is not an active docs subroot. The contract is emitted packet-locally instead to avoid creating a parallel authority surface.
- 2026-04-27 — R3 Z0 closeout excludes `.code-review-graph/graph.db`: it is a pre-existing dirty derived binary artifact outside the Z0 doc/test scope, and resetting it would overwrite unrelated local state.
- 2026-04-27 — R3 Z1 keeps exchange cutover-wipe classification deferred to M5/T1. Z1 blocks venue side effects before command persistence; it does not invent reconciliation findings or choose the live cutover date.
- 2026-04-27 — R3 Z2 first pre-close review proved that compatibility seams can silently preserve unsafe live paths. Future live-boundary phases should test the compatibility wrapper itself, not only the new adapter class.
- 2026-04-27 — R3 Z3 post-close critic caught a live scheduler observability gap not covered by the initial focused suite. Future daemon-scheduler slices must include `tests/test_bug100_k1_k2_structural.py` or an equivalent scheduler-health antibody before local close.

---

## Packet creation event

2026-04-26 — Packet created with the following initial files:
- `AGENTS.md` (packet router)
- `v2_system_impact_report.md` (capability + impact analysis)
- `zeus_touchpoint_inventory.md` (grep-verified Zeus integration sites)
- `plan.md` (phased execution plan)
- `open_questions.md` (operator decision tracker)
- `work_log.md` (this file)

No code change accompanied packet creation. Phase 0 is the next gate; nothing in `src/`, `tests/`, `architecture/`, or `requirements.txt` was touched.

Packet registration: `docs/operations/AGENTS.md` registry entry added in the same commit as packet creation.

Date: 2026-04-27

Task: R3 Z4 CollateralLedger pUSD/CTF collateral and reservation gate.
Changed files: `src/state/collateral_ledger.py`, `src/execution/wrap_unwrap_commands.py`, `src/contracts/fx_classification.py`, executor/data/venue/harvester collateral seams, focused R3 regression tests, and topology/module/test registries; `.code-review-graph/graph.db` is excluded as a pre-existing derived local artifact.
Summary: Added a DB-backed CollateralLedger for pUSD buy collateral, CTF sell inventory, reservations, legacy USDC.e separation, and degraded-authority fail-closed snapshots; added durable wrap/unwrap command states without live chain side effects; gated executor entry/exit before command persistence/SDK contact; enforced pUSD and CTF allowances; converted CTF inventory to micro-share units to avoid fractional overcommit; persisted runtime snapshots/reservations through the trade DB; released reservations from terminal venue-command transitions atomically; kept V2 SDK imports confined to the venue adapter; and left Q-FX-1/R1 redemption side effects blocked.
Verification: After the terminal-release atomicity repair, `tests/test_collateral_ledger.py` reached `32 passed, 4 warnings`; the focused R3/Z4 suite reached `117 passed, 8 skipped, 4 warnings`; command journal suite reached `104 passed`; `py_compile` OK; Z4 drift check GREEN; topology navigation OK; map-maintenance OK; planning-lock OK; closeout `ok: true` with no blocking issues; and `git diff --check` OK before refreshed pre-close review.
Next: Start U1 through topology navigation and its own boot/pre-close/post-close gates. Live cutover remains blocked by Q1/cutover and heartbeat/collateral gates.

Z4 critic blocker repair: allowance checks, micro-share CTF accounting, DB-backed global ledger configuration, and command-repo terminal reservation release were added after the first pre-close critic BLOCK. A second critic then found two remaining blockers: runtime snapshots/reservations were not durably tied to the same command DB connection, and direct redeem compatibility paths could still imply live settlement side effects. That repair commits DB-backed balance snapshots, reserves buy/sell collateral with the same venue-command connection, releases terminal reservations in the same savepoint, and makes both `PolymarketClient.redeem()` and `PolymarketV2Adapter.redeem()` defer fail-closed to R1 without SDK contact. A fresh critic then found a BUY share-quantization blocker: submitted shares could round up above target notional while preflight/reservation only checked `target_size_usd`; the repair computes pUSD preflight/reservation from the actual submitted BUY notional (`ceil(shares * limit_price * 1e6)`) and adds antibodies for the `$10 @ 0.333 -> $10.003320` case. The next critic found an aggregate-allowance blocker: existing pUSD/CTF reservations were subtracted from balances but not allowances. That repair nets open reservations from pUSD and CTF allowance availability, removes the private BUY reservation fallback to target-size notional, and adds aggregate allowance over-reservation antibodies. Post-close third-party critic James then found a terminal-release atomicity blocker: executor fallback code could release reservations after a failed terminal `append_event()`. The latest repair removes those fallback releases and adds entry/exit antibodies proving reservations remain active when terminal append fails, so release occurs only through successful command-repo terminal transitions in the same savepoint. Evidence was refreshed before re-review.

### Phase R3.U1 pre-close record 2026-04-27

Date: 2026-04-27
Branch: plan-pre5
Task: R3 U1 ExecutableMarketSnapshotV2 table, repo, and venue-command freshness gate.
Changed files: `src/contracts/executable_market_snapshot_v2.py`, `src/state/snapshot_repo.py`, `src/state/db.py`, `src/state/venue_command_repo.py`, executor intent/submit seams, focused U1/command/executor/collateral tests, and topology/module/test registries.
Summary: Added immutable executable CLOB market snapshots with raw payload hashes, append-only SQLite triggers, repo round-trips, latest-fresh lookup, and a Python freshness/tradability gate at `venue_command_repo.insert_command()`. New venue commands must cite a fresh snapshot before insertion; stale/missing snapshots and disabled/inactive/closed/orderbook-disabled/token/tick/min-size/neg-risk mismatches fail closed before executor SDK contact. Entry and exit intent contracts now carry executable snapshot citation and comparison facts. Fresh DBs create `venue_commands.snapshot_id NOT NULL`; legacy DBs receive a nullable column but new writes are Python-enforced.
Verification: `tests/test_executable_market_snapshot_v2.py` reached `15 passed`; command journal suite reached `104 passed` then `118 passed` with U1 tests included; executor/collateral focused suite reached `79 passed, 2 skipped, 4 warnings`; combined U1/Z4 focused suite reached `131 passed, 8 skipped, 4 warnings`; `py_compile` OK; U1 drift check `GREEN=14 YELLOW=0 RED=0`; topology navigation OK; map-maintenance OK; planning-lock OK; `git diff --check` OK. A broader `tests/test_architecture_contracts.py tests/test_db.py` run surfaced two unrelated discovery-harness failures from `temperature_metric=None`; topology/verifier classified them outside U1 scope.
Pre-close review: critic Epicurus PASS and verifier Erdos PASS. Post-close critic Hypatia PASS. Post-close verifier Nash initially BLOCKED on missing post-close artifact/state evidence only; `r3/reviews/U1_post_close_2026-04-27.md` records the third-party review trail and state/receipt remediation. Verifier Boole then PASSED the rerun and confirmed U2 may be unfrozen by the leader.
Next: `ready_to_start` is advanced to `[U2]`. Start U2 only through topology navigation and its own boot/pre-close/post-close gates; live cutover remains blocked by Q1/cutover and downstream M/R/T gates.
