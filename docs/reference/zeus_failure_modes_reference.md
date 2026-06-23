# Zeus Failure Modes Reference

Status: canonical durable failure-mode reference  
Authority rank: reference. `architecture/fatal_misreads.yaml`, `architecture/negative_constraints.yaml`, code, tests, manifests, and authority docs win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 1. Settlement/Contract Failures

### 1.1 Continuous-weather shortcut

Failure: treating a weather market as a continuous value forecast instead of a discrete settlement-bin contract.

Prevention: build family Ω, settlement transform, bin topology, and q over Ω before edge or size.

### 1.2 UTC/local-day mismatch

Failure: using UTC date where the market settles on local calendar day.

Prevention: family identity must carry local target date and source role.

### 1.3 High/low identity leak

Failure: mixing high and low observations, calibration, settlement, replay, or market topology.

Prevention: metric identity is part of family/source/calibration/replay keys.

### 1.4 Bin topology misread

Failure: treating open shoulders as finite ranges or parsing labels instead of topology.

Prevention: use topology contracts/manifests and settlement-preimage integration.

---

## 2. Probability/q Failures

### 2.1 NO complement shortcut

Failure: pricing, sizing, or admitting NO from `1 - YES` or using `1 - q_lcb_yes` as a NO lower bound.

Prevention: native NO quote/depth for execution; conservative q law `q_lcb_no = 1 - q_ucb_yes` only inside q-band seam.

### 2.2 Lower-bound inversion

Failure: `q_lcb > q` for the same random variable.

Prevention: q-band certification and no-trade/reject on invalid lower bound. Do not paper over with clamps unless code proves a distinct transformed random variable.

### 2.3 Legacy probability resurrection

Failure: treating ENS/Platt/market_fusion, old q_lcb_5pct bootstrap, or dated replacement docs as current live authority.

Prevention: route through current code/config/manifests; legacy surfaces are diagnostics/history unless proven active.

### 2.4 Stale q/source cycle

Failure: using an old forecast cycle or stale posterior as live q.

Prevention: materializer/live eligibility gates and current data/source freshness checks.

---

## 3. Market/Execution Failures

### 3.1 Stale book or partial substrate

Failure: selecting from stale/incomplete sibling bins or old executable snapshots.

Prevention: family substrate freshness, targeted refresh, JIT pre-submit witness, fail-closed partial family behavior.

### 3.2 Duplicate submit after unknown side effect

Failure: retrying as if no order exists when venue side effect is unknown.

Prevention: command persistence, idempotency key, unknown-side-effect states, venue command repo.

### 3.3 Final-stage redecision/repricing

Failure: final submit recomputes probability/edge/size or chases price without typed abort/re-rank.

Prevention: final execution intent excludes posterior/edge/recompute inputs; recapture/redecision emits typed abort states.

### 3.4 Collateral/allowance blind submit

Failure: submitting without fresh balance/allowance/inventory proof.

Prevention: pre-submit witness and executor collateral preflight.

---

## 4. Lifecycle/Settlement Failures

### 4.1 Lifecycle phase hallucination

Failure: inventing states such as `holding`, `closed`, or `sold` as canonical phase.

Prevention: `src/state/lifecycle_manager.py` and `architecture/money_path_objects.yaml` own phase grammar.

### 4.2 Exit intent treated as close

Failure: marking position closed when an exit intent exists but no confirmed sell fill/economic close exists.

Prevention: exit lifecycle golden rule: sell fill creates economic close; settlement remains harvester-owned.

### 4.3 Chain/local mismatch normalized away

Failure: ignoring chain-only assets, voiding from unknown chain snapshot, or trusting stale local projection over chain/CLOB.

Prevention: chain/CLOB truth hierarchy, quarantine/review states, completeness classifier.

### 4.4 Settlement mistaken for exit

Failure: treating redeem/settlement truth as equivalent to exit order state.

Prevention: separate exit, economic close, settlement outcome, redeem command, and learning surfaces.

---

## 5. Risk/Data Failures

### 5.1 Advisory-only risk

Failure: risk is logged but entry/sizing/execution behavior remains unchanged.

Prevention: RiskGuard/risk allocator must actuate behavior. DATA_DEGRADED/YELLOW/ORANGE/RED semantics must block or protect.

### 5.2 Current fact treated as durable law

Failure: copying PID, loaded SHA, bankroll, active positions, source freshness, or packet diary into authority/reference.

Prevention: current facts live only in operations current pointers and runtime receipts with expiry.

### 5.3 Backtest promoted to live authority

Failure: deploying from replay/shadow result without settlement-market parity, executable orderbook, and no-hindsight proof.

Prevention: replay remains diagnostic until promoted through authority/change-control with evidence.

---

## 6. Docs Authority Failures

### 6.1 Packet/consult/report contamination

Failure: a zero-context agent reads a dated packet, consult raw, PR review, rebuild diary, or evidence folder as present-tense law.

Prevention: default-read route only to active authority/reference/current pointers; docs registry marks evidence/report/archive/rebuild non-default.

### 6.2 Old authority path survives

Failure: an outdated file remains under `docs/authority/**` or active registry route and competes with current law.

Prevention: demote source, promote surviving law, update `docs/archive_registry.md`, `docs/README.md`, AGENTS routers, and `architecture/docs_registry.yaml`.

---

## 7. Failure-Review Checklist

For any live-money incident or architecture review, ask:

1. Did contract/source/settlement truth precede probability?
2. Was Ω complete and high/low correct?
3. Were q/q_lcb/q_ucb coherent and fresh?
4. Was NO treated as native side?
5. Was executable cost side-specific and fresh?
6. Did direction law admit only legal sides?
7. Did risk change behavior?
8. Did command persistence and idempotency precede side effect?
9. Did lifecycle stay in grammar?
10. Did chain/CLOB truth outrank cache?
11. Did docs/evidence/history stay out of default authority?
