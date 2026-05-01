# Polymarket CLOB V2 Migration — Execution Plan

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: `v2_system_impact_report.md` (this packet) + `zeus_touchpoint_inventory.md` (this packet)
Receipt-bound source: this file
Critic owner: critic-opus (zeus-midstream-critic team) OR surrogate code-reviewer@opus

---

## 1. Charter

### Goal

Status: **V2_ACTIVE_P0 under R3** (2026-04-27 Z0). Migrate Zeus's Polymarket CLOB integration from V1 (`clob.polymarket.com`, `py-clob-client`, USDC.e collateral) to V2 (`clob-v2.polymarket.com`, `py-clob-client-v2`, pUSD collateral) without losing typed-contract, fail-closure, provenance, reconciliation, and cycle-architecture investments. The exact heartbeat cadence is evidence-gated by Q-HB; R3 still treats heartbeat supervision as mandatory for live resting-order risk correctness.

### Out of scope (anti-goals)

- Rewriting strategy logic that incidentally touches CLOB (e.g. WS-driven reactive monitor_refresh). Tracked here as optional strategic slices, gated on independent critic review.
- Polymarket account / KYC / pUSD funding operations. Tracked as Phase 0 prerequisites; executed by operator outside this packet.
- Any non-Polymarket venue work.
- Changes to settlement schema, INV-14 spine, observation_instants_v2, calibration tables. The CLOB V2 boundary is upstream of all of those — this packet does not alter them.

### R3 source-of-truth pointer

This original packet remains the CLOB V2 migration evidence packet, but implementation authority now routes through the R3 lifecycle plan:

- R3 entry: `docs/operations/task_2026-04-26_ultimate_plan/r3/R3_README.md`
- R3 implementation plan: `docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md`
- First active phase: `docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z0.yaml`
- Corrected impact report: `v2_system_impact_report.md`
- Packet-local live-money contract: `polymarket_live_money_contract.md`

The older Phase 1-4 sections below are retained as historical packet context. Where they conflict with R3 phase cards, R3 wins.

### Allowed files (broad envelope; phase scopes refine)

- `src/data/polymarket_client.py`
- `src/contracts/clob_protocol.py` (NEW)
- `src/contracts/clob_heartbeat.py` (NEW)
- `src/contracts/realized_fill.py`, `slippage_bps.py`, `tick_size.py`, `execution_price.py` (only minor metadata edits if needed)
- `src/execution/fill_tracker.py` (state-set widening only)
- `src/main.py` (heartbeat coroutine integration only)
- `src/execution/harvester.py` (pUSD redemption only)
- `tests/test_v2_sdk_contract.py` (NEW), `tests/test_clob_protocol.py` (NEW), `tests/test_clob_heartbeat.py` (NEW), `tests/test_fill_tracker_delayed_status.py` (NEW), `tests/test_pusd_collateral_boundary.py` (NEW), `tests/test_neg_risk_passthrough_v2.py` (NEW)
- `tests/test_neg_risk_passthrough.py`, `tests/test_polymarket_error_matrix.py` (extend or mirror, not replace)
- `requirements.txt`
- `architecture/source_rationale.yaml`, `architecture/test_topology.yaml`
- `docs/reference/polymarket_clob_v2_reference.md` (NEW)
- This packet directory only — no other `docs/operations/` directories.

### Freeze point

No source-code change is authorized by this original packet body. Z0 is doc/test-only. R3 implementation phases authorize later source changes one phase at a time after drift check, topology navigation, operator-gate review, acceptance tests, and critic/verifier review. Q1-zeus-egress, Q-HB, Q-FX-1, CLOB V2 cutover, calibration retrain, TIGGE ingest, and G1 live deploy remain fail-closed operator gates.

### Critic owner

critic-opus on zeus-midstream-critic team is the durable critic. If unavailable on session resume, surrogate `Agent(subagent_type=code-reviewer, model=opus)` is the operator-authorized fallback. Critic gate applies to every slice — no slice ships without at least one critic verdict.

---

## Legacy Phase 1-4 plan body — superseded by R3

The sections below are retained as historical packet context only. They do not authorize implementation when they conflict with R3 phase cards or the corrected `v2_system_impact_report.md`. In particular, exact heartbeat cadence and exact `delayed` status spellings remain evidence-gated; R3 owns implementation through Z/U/M/R/T/F/A/G phase cards.

## 2. Decision framework

### Three paradigm shifts (K << N)

| Paradigm | V1 | V2 | Phase touchpoint |
|---|---|---|---|
| A. Transport | request/response, no liveness contract | supervised resting-order health; exact heartbeat cadence is Q-HB evidence-gated | Superseded by R3 Z1/Z2/Z3 |
| B. Collateral | USDC.e | pUSD | Phase 0 (bridge inquiry) + Phase 2 (redemption swap) |
| C. State machine | live → matched/cancelled | expanded/unknown venue states must be typed and fail-closed; exact transitional spellings require current SDK/API citation | Superseded by R3 U2/M1-M5 |

### Five-layer model (matches `v2_system_impact_report.md` §6 grouping)

| Layer | What | When | Risk |
|---|---|---|---|
| 0 | Operator investigation | now | zero |
| 1 | Protocol abstraction + antibody | after Phase 0 Q1-Q3 | low |
| 2 | Infrastructure (heartbeat / state machine / pUSD / SDK swap) | after Phase 1 + Q5/Q6 | medium |
| 3 | Dual-run + cutover | after Phase 2 | high |
| 4 | Cleanup | post-cutover | low |

---

## 3. Phase 0 — Operator investigation

Zero code change. All output goes into `evidence/`.

### 3.1 Phase-level entry/exit

- Entry: this packet exists, registered in `docs/operations/AGENTS.md`.
- Exit: `evidence/` contains files for Q1-Q4 (Q5-Q7 may lag, gating Phase 2 only). `open_questions.md` reflects answered/unanswered status.

### 3.2 Slices

#### 0.A — V2 host reachability probe

- Owner: operator
- Action: from Zeus's egress, run `curl -I https://clob-v2.polymarket.com/version` and `curl https://clob-v2.polymarket.com/version`
- Evidence file: `evidence/q1_v2_host_probe_2026-04-26.txt` (raw HTTP response + timestamp + egress IP)
- Acceptance: HTTP 200 with JSON body containing protocol identifier
- Resolves: Q1 in `open_questions.md`
- Failure path: if blocked, document blocker (geofence? TLS? DNS?) and escalate to Polymarket support via 0.D

#### 0.B — py-clob-client-v2 SDK API diff

- Owner: operator (read-only, no install required if Phase 1 not yet started)
- Action: read `py-clob-client-v2` README + `OrderArgs` source + `cancel` / `get_order_status` / `getHeartbeat` signatures from GitHub
- Evidence file: `evidence/q2_sdk_api_diff_2026-04-26.md` containing:
  - OrderArgs field list (V1 vs V2 side by side)
  - Method signature comparison: `create_order`, `post_order`, `cancel_order` / `cancel`, `get_order_status`, `get_orderbook`, `get_fee_rate` (or its replacement), `getHeartbeat`, `get_neg_risk`, `getClobMarketInfo`
  - Exception class diffs (`PolyApiException` vs V2 equivalent)
- Acceptance: every method Zeus currently calls (per `zeus_touchpoint_inventory.md` §1-2) has a documented V2 counterpart (or a noted gap)
- Resolves: Q2

#### 0.C — getClobMarketInfo capability check

- Owner: operator
- Action: identify whether `getClobMarketInfo(conditionID)` returns fee_rate + tick_size + neg_risk in one call (per `v2_system_impact_report.md` §2.4)
- Evidence file: `evidence/q3_getclobmarketinfo_capability_2026-04-26.md` containing the V2 SDK source signature + a sample response shape if obtainable
- Acceptance: confirmed yes/no with source citation
- Resolves: Q3 — affects deletability of `polymarket_client.get_fee_rate` (`zeus_touchpoint_inventory.md` §1, line 116)

#### 0.D — Polymarket support inquiry

- Owner: operator
- Action: contact Polymarket support / Discord channel for:
  - Q5: USDC.e → pUSD bridge path for existing Gnosis Safe holders
  - Q6: V1 EOL date or planned sunset window
  - Q7: Builder code registration requirement for V2 fee-share program
- Evidence file: `evidence/q5_q6_q7_polymarket_support_inquiry_2026-04-26.md` (inquiry text + responses received, redacted as needed)
- Acceptance: at minimum the inquiry sent and dated; responses populated as they arrive
- Resolves: Q5, Q6, Q7

#### 0.E — Live V1 fee/tick observation snapshot

- Owner: operator (optional but valuable)
- Action: run `polymarket_client.get_fee_rate` against current weather token IDs; capture observed feeRate values
- Evidence file: `evidence/q4_live_v1_fee_snapshot_2026-04-26.json`
- Acceptance: at least 3 weather tokens sampled
- Resolves: Q4 — provides V1 baseline for V2 fee comparison after migration

#### 0.F — Critic review of Phase 0 evidence package

- Owner: critic-opus
- Action: critic reads all evidence files + `v2_system_impact_report.md`
- Verdict file: `evidence/phase0_critic_verdict.md`
- Acceptance: critic confirms or rejects readiness for Phase 1 entry
- Resolves: Phase 0 → Phase 1 gate

### 3.3 Phase 0 estimated effort

≤4 hours operator time excluding 0.D response wait. 0.D may take days or weeks for Polymarket support reply.

---

## 4. Phase 1 — Protocol abstraction and antibody layer

Low-risk, parallel-safe with any other workstream. No runtime behavior change. All outputs are abstractions, antibodies, and documentation.

### 4.1 Phase-level entry/exit

- Entry: Phase 0 slice 0.F passes (critic accepts evidence package)
- Exit: protocol abstraction module + heartbeat typed atom + V2 SDK contract antibody + reference doc all merged with critic approval; `requirements.txt` carries dual pin

### 4.2 Slices

#### 1.A — Reference document

- Allowed files: `docs/reference/polymarket_clob_v2_reference.md` (NEW)
- Action: distill `v2_system_impact_report.md` §1, §2, §8 into a reference-class document for operators. Reference class means authority-neutral, evergreen, no packet binding.
- Acceptance: doc exists, has `Created` / `Last reused/audited` / `Authority basis` headers, registered in `docs/reference/AGENTS.md`
- Risk: very low (doc-only)
- Critic gate: standard

#### 1.B — `clob_protocol` typed atom

- Allowed files: `src/contracts/clob_protocol.py` (NEW), `tests/test_clob_protocol.py` (NEW)
- Action: define `ClobProtocol` frozen dataclass with fields: `version: Literal["v1", "v2"]`, `host: str`, `sdk_module: str`, `eip712_domain_version: str`, `collateral_asset: str`, `mandatory_heartbeat: bool`, `heartbeat_interval_seconds: int | None`. Provide `CLOB_V1` and `CLOB_V2` constants. Pure data; no side effects.
- Acceptance: contract dataclass passes its own antibody tests (≥6 cases: V1 invariants, V2 invariants, version field exhaustive, heartbeat field consistency, collateral field consistency, host shape validation)
- Risk: low
- Critic gate: standard

#### 1.C — `clob_heartbeat` typed atom

- Allowed files: `src/contracts/clob_heartbeat.py` (NEW), `tests/test_clob_heartbeat.py` (NEW)
- Action: define `Heartbeat` frozen dataclass: `last_success_at: datetime`, `interval: timedelta`, `failure_action: Literal["tombstone", "log_only", "no_op"]`. `is_stale(now) -> bool`. `compute_failure_action(now)` returns either `"tombstone"` or None. Pure logic; no I/O.
- Acceptance: ≥8 antibody cases including: fresh heartbeat passes, stale heartbeat over interval triggers failure_action, failure_action="no_op" returns None for stale, datetime tz-aware enforcement, interval positive enforcement
- Risk: low
- Critic gate: standard

#### 1.D — V2 SDK contract antibody

- Allowed files: `tests/test_v2_sdk_contract.py` (NEW)
- Action: pytest module that runs only when `py_clob_client_v2` is importable (skip otherwise). Asserts: `OrderArgs` has fields `metadata`, `builder_code`, `defer_exec`, `timestamp`; `OrderArgs` does NOT have field `nonce`; `ClobClient` has `getHeartbeat` method; `ClobClient.create_order` callable; `ClobClient.cancel` (or equivalent) callable. Fails loudly on V2 SDK shape drift.
- Acceptance: when V2 SDK absent, test skips; when present, asserts pass against current V2 SDK
- Risk: low
- Critic gate: standard

#### 1.E — Mirror `test_neg_risk_passthrough.py` for V2

- Allowed files: `tests/test_neg_risk_passthrough_v2.py` (NEW)
- Action: clone the V1 antibody pattern from `tests/test_neg_risk_passthrough.py:66-83` against `py_clob_client_v2`. Skip if SDK not installed. Asserts `ClobClient.get_neg_risk(token_id)` exists in V2 SDK with the same signature.
- Acceptance: skip-on-absence behavior; asserts pass when V2 SDK present
- Risk: low
- Critic gate: standard

#### 1.F — Mirror `test_polymarket_error_matrix.py` for V2

- Allowed files: extend existing `tests/test_polymarket_error_matrix.py` with conditional V2 branch, OR create `tests/test_polymarket_error_matrix_v2.py` (operator decision)
- Action: catalog V2 exception classes (likely renamed from `PolyApiException` to V2 equivalent). Build error-shape matrix.
- Acceptance: every V1 error class has a V2 equivalent or a documented gap
- Risk: low
- Critic gate: standard

#### 1.G — `requirements.txt` dual pin

- Allowed files: `requirements.txt`
- Action: add `py-clob-client-v2>=1.0.0` alongside existing `py-clob-client>=0.25`. V1 stays hot, V2 cold (loaded by tests only until Phase 2).
- Acceptance: `pip install -r requirements.txt` succeeds; both packages importable
- Risk: low (V2 SDK is GA but young; dependency conflicts possible)
- Critic gate: standard, plus operator confirmation that pip resolution does not produce dependency-conflict warnings

#### 1.H — Architecture registry update

- Allowed files: `architecture/source_rationale.yaml`, `architecture/test_topology.yaml`
- Action: update `polymarket_client.py` entry with `protocol_version: v1` field; register new contracts (`clob_protocol.py`, `clob_heartbeat.py`) and tests (1.D, 1.E, optional 1.F)
- Acceptance: `topology_doctor.py --planning-lock --json` returns `{ok: true, issues: []}`
- Risk: low (declarative)
- Critic gate: standard

#### 1.I — Phase 1 critic close-out

- Owner: critic-opus
- Action: run regression against pre-Phase-1 baseline; verify no new failures attributable to Phase 1 work
- Verdict file: append to `work_log.md` plus `evidence/phase1_critic_verdict.md`
- Acceptance: clean delta; phase exit gate

### 4.3 Phase 1 estimated effort

3-5 working days, parallel-safe. No runtime change. Slices are independently committable.

---

## 5. Phase 2 — Infrastructure (heartbeat, state machine, pUSD, SDK swap)

Medium-risk, sequential, requires Phase 1 + Phase 0 Q5/Q6 answers. **This is where the daemon supervisor changes shape.**

### 5.1 Phase-level entry/exit

- Entry: Phase 1 closed AND `evidence/q5_q6_q7_polymarket_support_inquiry_2026-04-26.md` contains pUSD bridge path + V1 EOL date (or operator decision to proceed without EOL date)
- Exit: V2 path is functional behind `ZEUS_CLOB_PROTOCOL=v2` env var, V1 remains default; all M1-M5 + R1-R3 + A1-A3 from `v2_system_impact_report.md` §6 are landed and tested

### 5.2 Slices

#### 2.A — Legacy transitional-status branch in fill_tracker (superseded by R3 M2/M3)

- Allowed files: `src/execution/fill_tracker.py`, `tests/test_fill_tracker_delayed_status.py` (NEW)
- Action:
  - Legacy placeholder: define a transitional-status set only from source-cited venue payloads; do not hard-code historical candidate strings without fresh evidence.
  - R3 replacement: unknown or transitional venue states must be journaled as typed facts and reconciled; no exact status string is active without source evidence.
  - R3 replacement: `_normalize_status` changes require source-cited payload examples and M2/M3 acceptance tests.
- Acceptance: superseded by R3 M2/M3 antibodies; no test may assert historical candidate spellings without current SDK/API citation.
- Risk: medium — capital-leak vector if mishandled
- Critic gate: full review + heartbeat-failure-injection test (A2 from §6.4)

#### 2.B — Heartbeat coroutine implementation (V1-compatible)

- Allowed files: `src/main.py` (only the supervisor / daemon entry), new `src/engine/heartbeat_supervisor.py` (NEW), `tests/test_heartbeat_supervisor.py` (NEW)
- Action:
  - Implement a coroutine that calls `clob.heartbeat()` (no-op when protocol is V1) every `Heartbeat.interval` seconds
  - On failure: write `state/auto_pause_failclosed.tombstone` with reason "heartbeat_failure_<utc_ts>"
  - Add a `_cycle_lock`-aware design: heartbeat coroutine runs in its own thread, not inside `run_cycle` (this is Zeus's first cross-cycle concurrent component)
  - V1 mode: coroutine logs "no-op (V1)" and does not call CLOB; V2 mode: real heartbeat
- Acceptance: heartbeat-failure-injection test (A2): kill the coroutine, observe tombstone file written, observe subsequent `run_cycle` invocation refuses to submit new orders
- Risk: medium-high — first concurrency in daemon
- Critic gate: deep review including thread-safety on `_cycle_lock`, tombstone write atomicity, failure-action correctness

#### 2.C — pUSD redemption / balance path

- Allowed files: `src/data/polymarket_client.py:266-275` region, `src/execution/harvester.py:1244-1264` region, `tests/test_pusd_collateral_boundary.py` (NEW)
- Action:
  - Branch on `ClobProtocol.collateral_asset`: V1 path keeps USDC.e ABI; V2 path uses pUSD ABI per Phase 0 Q5 evidence
  - Update `Startup wallet check` log copy to include collateral asset name
  - A3 boundary test: verify V1 balance is reported in USDC, V2 balance in pUSD, no contamination
- Acceptance: V1 baseline test still passes; V2 test asserts pUSD path is wired (mock-level — real funding is operator side)
- Risk: medium — operator-blocked on Phase 0 Q5
- Critic gate: standard + operator sign-off on pUSD ABI choice

#### 2.D — V2 SDK swap in polymarket_client.py (env-gated)

- Allowed files: `src/data/polymarket_client.py` (broad — but bounded to imports + `_ensure_client` + OrderArgs construction)
- Action:
  - Add `ZEUS_CLOB_PROTOCOL=v1|v2` env var, default `v1`
  - Branch import: `from py_clob_client.client import ClobClient` (V1) vs `from py_clob_client_v2.client import ClobClient` (V2)
  - Branch host: `CLOB_BASE` becomes `_resolve_host(protocol)` returning V1 or V2 host
  - `OrderArgs` construction: V2 path supplies `metadata`, `builder_code` (from config), `defer_exec`, `timestamp`; drops nothing because `nonce/taker/fee_rate_bps` were never explicitly set on V1 anyway
  - Direct httpx `/book` and `/fee-rate` calls: V2 path uses SDK methods (per Phase 0 Q3 evidence) or skips with documented rationale
- Acceptance: V1 default behavior unchanged; V2 path activates under env var; cross-protocol antibody test (A1) confirms both signatures valid for the same logical OrderArgs
- Risk: medium — touches the chokepoint module
- Critic gate: deep review + cross-protocol antibody (A1)

#### 2.E — Architecture registry refresh for V2

- Allowed files: `architecture/source_rationale.yaml`, `architecture/test_topology.yaml`
- Action: register new tests (2.A, 2.B, 2.C antibodies); update `polymarket_client.py` entry with `protocol_version: v1|v2` allowed field; add `clob_v2_boundary` authority_role
- Acceptance: `topology_doctor.py --planning-lock` returns `{ok: true}`
- Risk: low
- Critic gate: standard

#### 2.F — `getClobMarketInfo` cache (R2)

- Allowed files: `src/data/polymarket_client.py` (extend), `tests/test_clob_market_info_cache.py` (NEW)
- Action: replace direct `/fee-rate` httpx call with cached `getClobMarketInfo(conditionID)` lookup. Cache TTL config-driven, default 10 minutes.
- Acceptance: same fee value returned within TTL; new SDK call after expiry
- Risk: low
- Critic gate: standard

#### 2.G — `/version` probe at startup (R1)

- Allowed files: `src/data/polymarket_client.py`, `tests/test_clob_version_probe.py` (NEW)
- Action: at `_ensure_client()` first call, fetch `/version` and assert it matches `ClobProtocol.version`. Mismatch → log + write tombstone.
- Acceptance: V1 probe returns "v1", V2 probe returns "v2"; mismatch triggers tombstone path
- Risk: low
- Critic gate: standard

#### 2.H — Builder code native field (R4)

- Allowed files: `src/data/polymarket_client.py` (OrderArgs construction)
- Action: read `BUILDER_CODE` env var or config; if set, attach to `OrderArgs.builder_code` (V2 path only). Per Phase 0 Q7 — only run if Polymarket confirms Zeus needs to register a builder code.
- Acceptance: builder_code present in V2 OrderArgs when env var set; absent when not set
- Risk: low
- Critic gate: standard

#### 2.I — Phase 2 critic close-out

- Owner: critic-opus
- Action: full regression + heartbeat-failure-injection (A2) + cross-protocol antibody (A1) + pUSD boundary (A3)
- Verdict file: `evidence/phase2_critic_verdict.md`
- Acceptance: all three antibodies pass; no regressions; phase exit gate

### 5.3 Phase 2 estimated effort

8-12 working days. Slices 2.A-2.D are sequential (state machine → heartbeat → pUSD → SDK swap). Slices 2.E-2.H are parallel-safe after 2.D lands.

---

## 6. Phase 3 — Dual-run and cutover

High-risk. Real V2 traffic. Requires Phase 2 closed AND real fundable account.

### 6.1 Phase-level entry/exit

- Entry: Phase 2 closed; operator confirms pUSD-funded Gnosis Safe ready; V1 positions all redeemed (per `v2_system_impact_report.md` §4.5.c)
- Exit: V2 is the default protocol (`ZEUS_CLOB_PROTOCOL=v2` is hot); V1 fallback retained but unused

### 6.2 Slices

#### 3.A — Pre-cutover redemption sweep

- Owner: operator + Zeus daemon (no code change required if T2-G redemption already wired correctly)
- Action: verify all settled-but-unredeemed V1 positions are redeemed before cutover (V2 cannot redeem V1 positions)
- Acceptance: query `position_current` filtered for `status='settled' AND redeemed=0`; result is empty
- Risk: low (pre-existing capability)
- Critic gate: operator sign-off

#### 3.B — Telemetry for dual-run observation

- Allowed files: `src/data/polymarket_client.py` (add metric emit), `state/clob_protocol_telemetry.json` (NEW operational artifact)
- Action: emit per-call metrics: protocol version, latency, error class, unknown-or-transitional-status-rate. Daily rollup file written to state/.
- Acceptance: file populated after one cycle; visible to `verify_truth_surfaces.py` if registered
- Risk: low
- Critic gate: standard

#### 3.C — Cutover runbook

- Allowed files: `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/cutover_runbook.md` (NEW within this packet)
- Action: step-by-step runbook for operator: pre-flight checks, env var flip, monitoring window, rollback procedure
- Acceptance: runbook covers pre-flight + flip + 7-day monitoring + rollback; includes rollback test at the end
- Risk: low (doc-only)
- Critic gate: standard

#### 3.D — Cutover execution (operator-driven)

- Owner: operator
- Action: follow runbook. Flip `ZEUS_CLOB_PROTOCOL=v2`. Restart daemon. Observe.
- Evidence: `evidence/cutover_log_<date>.md` capturing pre-flip state, flip timestamp, first 24h observations, first 7d summary
- Risk: high — V2 SDK is young; bugs likely
- Critic gate: critic-opus sign-off after 7-day window OR rollback decision

#### 3.E — Post-cutover stability review

- Owner: critic-opus
- Action: read `evidence/cutover_log_<date>.md`; assess heartbeat stability, fill rate, fee-vs-prediction match, unknown/transitional-status incidence
- Verdict file: `evidence/phase3_critic_verdict.md`
- Acceptance: critic confirms stable (or recommends rollback)

### 6.3 Phase 3 estimated effort

1 week active monitoring; calendar weeks of preparation depending on operator availability.

---

## 7. Phase 4 — Cleanup

Low-risk; removes V1 dead code. Optional — Zeus could keep dual-protocol abstraction indefinitely if operationally desirable (e.g. for disaster recovery).

### 7.1 Phase-level entry/exit

- Entry: Phase 3 closed; ≥4 weeks of V2-only operation with no V1 fallback engagement
- Exit: V1 SDK pin removed; V1 branches in protocol abstraction simplified to V2-only

### 7.2 Slices

#### 4.A — V1 SDK pin removal

- Allowed files: `requirements.txt`
- Action: remove `py-clob-client>=0.25` line; keep `py-clob-client-v2>=1.0.0`
- Acceptance: clean install; tests gated on V1 SDK presence skip cleanly
- Risk: low
- Critic gate: standard

#### 4.B — V1 fallback branch removal

- Allowed files: `src/data/polymarket_client.py`, `src/contracts/clob_protocol.py` (collapse to V2-only or keep abstraction empty)
- Action: remove V1 import branches and host string; either delete `ClobProtocol` abstraction entirely or keep it as future-V3 placeholder
- Acceptance: V2-only path; V1 tests deleted or marked obsolete
- Risk: low (V1 has been off for 4+ weeks)
- Critic gate: full review (deletion is irreversible without revert)

#### 4.C — V1 antibody test deletion

- Allowed files: `tests/test_neg_risk_passthrough.py`, `tests/test_polymarket_error_matrix.py`
- Action: delete V1-shaped tests OR demote to skip-on-V2 mode
- Acceptance: test suite green
- Risk: low
- Critic gate: standard

### 7.3 Phase 4 estimated effort

1-2 days.

---

## 8. Per-slice discipline

Every slice in every phase follows the protocol below (anchored in memory rules L20, L22, L28, L30, plus packet conventions).

1. `git pull --rebase origin data-improve` to sync with co-tenant work
2. Read scoped `AGENTS.md` for every module touched
3. **Grep-verify every plan file:line citation within 10 minutes of edit** (memory L20)
4. Implement minimal change; do not exceed `Allowed files` envelope
5. Run targeted pytest + broader regression; cite delta-direction not absolute counts (L28)
6. Dispatch slice to critic-opus via SendMessage AND parallel surrogate `Agent(subagent_type=code-reviewer, model=opus)` (handoff redundancy)
7. Address every HIGH severity finding inline before commit; document deferred LOW findings in `receipt.json`
8. Update `work_log.md` + `receipt.json` FIRST (memory: phase commit protocol)
9. `python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <this plan> --json` → require `{ok: true}`
10. `git reset HEAD` then `git add <specific files>` — NEVER `git add -A` (co-tenant scope-bleed risk per L22)
11. HEREDOC commit message: `<phase>.<slice>: <one-line summary>` + Co-Authored-By trailer
12. `git push origin data-improve`; retry on 5xx
13. Verify with `git log -1` that the commit landed unmodified by hooks
14. Report commit hash + critic verdict + context usage to operator

---

## 9. Verification strategy

### Per-slice gates

- `topology_doctor.py --planning-lock --json` → `{ok: true}`
- pytest targeted on modified files: zero new failures
- At least one critic verdict (critic-opus OR surrogate)

### Phase-level gates

- Phase 0: `evidence/phase0_critic_verdict.md` written and approving
- Phase 1: `evidence/phase1_critic_verdict.md` written and approving; `requirements.txt` dual pin installs cleanly; V2 SDK contract antibody test passes
- Phase 2: `evidence/phase2_critic_verdict.md` written and approving; A1+A2+A3 antibody tests pass; full regression delta non-positive
- Phase 3: `evidence/phase3_critic_verdict.md` written and approving; ≥7 days V2 stable
- Phase 4: V1 SDK uninstall does not break test suite

### End-of-packet verification

1. `requirements.txt` has only `py-clob-client-v2>=1.0.0` (no V1)
2. `grep -r 'py_clob_client' src/ tests/` returns only V2 imports
3. `grep -r 'clob.polymarket.com' src/` returns zero hits (only V2 host)
4. `architecture/source_rationale.yaml` `polymarket_client.py` entry shows `protocol_version: v2`
5. `state/auto_pause_failclosed.tombstone` not present (no false-positive heartbeat failures during steady-state)
6. ≥4 weeks V2 stable per `evidence/cutover_log_*.md`

---

## 10. Risk register

| Risk | Phase | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| V1 EOL announced before Phase 2 ready | 0/1/2 | medium | high | Phase 0 Q6 inquiry; if EOL within 30d, accelerate Phase 1+2 |
| pUSD bridge path remains undocumented | 0/2 | medium | high | escalate via 0.D; if blocked >4 weeks, file with Polymarket BD contact |
| V2 SDK has bugs (GA only 2 weeks old at packet creation) | 2/3 | medium | medium | dual-pin (1.G), test in V2 antibody (1.D) before runtime use; rollback path in 3.C |
| Heartbeat coroutine introduces race with `_cycle_lock` | 2 | medium | high | thread-safe design in 2.B; A2 antibody injection test |
| Unknown/transitional venue state misclassification leaks capital | R3 M2/M3 | medium until R3 lands | high | typed unknown-side-effect handling, venue facts, and reconciliation sweep |
| pUSD ↔ USDC.e FX accounting surprise | 3 | medium | medium | document FX classification decision in 3.C runbook |
| Co-tenant work touches `polymarket_client.py` during Phase 2 | 2 | low | medium | rebase per slice; isolate-and-restore pattern if needed |
| V2 host probe (Q1) shows geofence | 0 | low | high | escalate to Polymarket support; pause packet |
| V2 SDK package conflicts with existing deps | 1 | low | medium | resolve in 1.G with operator; pin precise versions if needed |
| Operator never runs Phase 0 because no urgency | 0 | medium | low | this packet remains valid indefinitely; revisit on V1 EOL announcement |

---

## 11. Open questions (live tracker)

See `open_questions.md`. Phase advancement gates reference question IDs.

---

## 12. References

Within this packet:
- `v2_system_impact_report.md` — corrected V2 capability + Zeus impact analysis; R3 source-of-truth supersedes legacy Phase 1-4 body
- `zeus_touchpoint_inventory.md` — grep-verified file:line registry
- `open_questions.md` — operator decision tracker
- `work_log.md` — per-slice closure log
- `evidence/` — operator-collected evidence files

External:
- `docs/operations/AGENTS.md` — packet registry
- `docs/operations/known_gaps.md` — operations gap register
- `docs/reference/AGENTS.md` — reference doc registry
- `docs/operations/task_2026-04-23_midstream_remediation/plan.md` — slice-discipline pattern source
- `architecture/source_rationale.yaml`, `test_topology.yaml` — registry update targets
