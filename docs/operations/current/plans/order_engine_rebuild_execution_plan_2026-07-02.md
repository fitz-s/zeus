# Order-Engine v2 — Execution Plan (2026-07-02)

**Authority chain:** design `docs/rebuild/order_engine_first_principles_design_2026-07-02.md` (v2)
→ architecture map `docs/rebuild/order_engine_implementation_architecture_2026-07-02.md` (§1 component
verdicts, §2 deletion radii, §3 migration DAG, §4 resolved operator decisions + §4b axiom deltas).
This doc adds only EXECUTION: packet cuts, gates, acceptance, resume protocol. On conflict, the
architecture doc wins on WHAT, this doc wins on HOW/WHEN.

**Operator axiom (binding):** decision is a continuous flow; win = persistently faster than the
market's motive; time and efficiency are first-class. Consequence already folded into the DAG:
measure first (W0), truth objects before frequency (W1 before W4).

**Resume protocol (context loss is expected):** a fresh session resumes from THIS doc + git alone.
After every packet: update `## NEXT ACTION` below, commit doc with the packet. Never rely on
conversation memory for state.

---

## P0 — Pre-flight (blocking, before any rebuild code)

1. **Land the in-flight dirty tree.** 21 files / +566−87 uncommitted on
   `hotfix/redecision-execution-seams-20260625`: deployment-freshness false-positive fix
   (new `src/control/runtime_code_plane.py`, gate_runtime, live_health, deployment-freshness tests)
   — coherent hotfix unit, commit after its tests pass. SEPARATELY attribute
   `src/decision/family_decision_engine.py` (+82) + `src/main.py` (+84) + reactor/command_recovery
   diffs — if same hotfix, same commit; if redecision-seams work, own commit. Transcript-replay
   memory available if attribution fails.
2. **Verify CWA/HKO serving-fix commit status.** Notepad says UNCOMMITTED but its files
   (`read_current_instrument_values`, materializer call site) are NOT dirty → either already
   committed (find SHA) or notepad stale. Resolve; never leave verified-live code unlanded.
3. **Baseline green:** full test suite on the landed tree; record command + result here.
4. **Branch strategy:** rebuild waves on dedicated branches off main after hotfix lands
   (`rebuild/w0-instrumentation`, `rebuild/w1-truth-objects`, ...). PR at milestone level only
   (batch the paid review per wave, not per packet).

## Wave packets

Every packet, no exceptions: planning-lock evidence before `src/state/**`/cross-zone edits ·
provenance-audit before reusing money-path code · TDD (tests in same commit) · registry rows
(`module_manifest.yaml`, `source_rationale.yaml`, scoped AGENTS.md, `test_topology.yaml`) in the
SAME packet · `db_table_ownership.yaml` + schema-fingerprint refresh for any new table · no
permanent flags · deploy interaction: live daemon is launchd `zeus-live-main` (LIVE_REPO footgun —
verify deployed path before claiming live behavior).

### W0 — instrumentation (additive, read-only, self-arming; packets parallel-safe)
| pkt | scope | acceptance |
|---|---|---|
| W0.1 | q_version formalization: alias `posterior_identity_hash`; read-time tripwire incorporated-HWM ≥ raw-input HWM | tripwire fires on synthetic stale-serve fixture; zero behavior change live |
| W0.2 | input→q_version latency metric + SLA + **blind-window metric** (time with no live book subscription; restart churn made visible) | both metrics emitted + persisted; dashboardable query documented |
| W0.3 | `SOURCE_RUN_ARRIVED` event type from source-clock probe | event rows appear on real cycle advance; idempotency key proven by replay test |
| W0.4 | registry backfill (src/decision sat unregistered — do not repeat) | topology_doctor clean |

### W1 — the two K0 truth objects (schema packets FIRST, code second)
Draft both `schema_packet`s per change-control constitution → principal_architect +
integration_reviewer sign-off → then implement. Serial after sign-off, parallel between themselves.
- **W1.1 CAS reservation ledger** (live TOCTOU bugfix TODAY, independent justification):
  aggregate-atomic reserve (compare-and-swap), convert-on-fill (kills ~180s phantom-cash window),
  partial-fill accounting, unsettled-proceeds bucket, A4 identity finding → EXISTING RiskGuard RED.
  Acceptance: concurrent reserve stress test (thread pool ≥ 20) shows zero over-reserve; identity
  `cash + reserved + unsettled = ledger` holds across a simulated fill/cancel/settle storm.
- **W1.2 order-state extension (AMENDED 2026-07-02 — Option B, derived predicates):**
  `venue_commands.q_version` stamp column + stamp plumbing at command creation; staleness and
  `delayed` are DERIVED predicates (no stored states, no CHECK edit, no CommandState expansion,
  no reducer change); predicate must define the no-servable-q case as INDETERMINATE (never
  mass-cancel a blind family); + `test_venue_order_facts_ddl_copies_identical` guard (trade copy
  is CI-blind today). Orthogonal to position LifecyclePhase — do NOT fold.
  Acceptance: stamp present on every new command; predicate truth table proven on fixtures incl.
  INDETERMINATE; old rows byte-identical.
  DEFERRED (recorded here, out of packet): `exchange_reconcile._OPEN_ORDER_FACT_STATES` vs
  `canonical_projections.OPEN_ORDER_FACT_STATES` pre-existing drift — standalone hygiene fix.

### W2 — venue capabilities (inert until consumed; parallel-safe; can overlap W3 build)
W2.1 batch submit/cancel wrapper (≤15 chunking, safe-prefix decomposition; SDK primitives exist,
zero call sites) · W2.2 self-trade guard · W2.3 rate-limit budget + cancel-priority ·
W2.4 CTF convert/split/merge adapter methods + `venue_commands` intent kinds (flips `negrisk_routes`
conversion legs executable; HARD — schedule first inside W2). Acceptance each: unit + venue-sandbox
integration proof; nothing consumes them yet.

### W3 — the SOLVE (new module `src/solve/`, single seam, biggest packet — sub-slices)
1. Interfaces first: `ScenarioService` (transitional impl = per-family independent product measure),
   menu adapter over `negrisk_routes`, endowment/wealth-by-outcome state.
2. Math core: joint multi-asset ΔU generalizing `payoff_vector.optimize_vector_stake`, κ fractional
   shading, discrete repair pass. Property tests: solver ≥ top-1 picker utility on every fixture;
   zero-edge → zero-stake; monotone in q.
3. Exits-as-same-solve (C5 marginal rule `b·Σq_j/W_j > q_i/W_i`); `ExitContext` plumbing reused.
4. Swap at `qkernel_spine_bridge.py:1332` behind TIME-BOXED promotion flag: G3 byte-identical-OFF
   proof → settlement-graded evidence gate → ARM flip → **flag deleted**.
   Coherence veto NOT carried over (§4 decision 1); `assess_market_coherence` rewired as divergence
   event + re-decision priority key. Receipts stamp `correlation_rail=caps` (§4 decision 2).
   **C4 joint scenario service runs W3-parallel** (rehome Ledoit-Wolf under probability authority),
   own evidence gate, drops in as ScenarioService swap.

### W4 — event-driven triggers (correctness objects now in place → raise frequency)
Universalize book-move bridge (any family with resting capital or q-coverage) · C3 staleness path
(`SOURCE_RUN_ARRIVED`/obs-tick → `STALE_PENDING_CANCEL` → batch cancel-set → reconciled re-solve) ·
REST_ELIGIBLE from `release_calendar` + measured cancel/submit p99 · demote 60s scan to liveness
backstop · **same packet:** DELETE `maker_rest_escalation` (only GTC TTL owner — C3 replaces) +
`maker_fill_calibration` (§3.4 anti-pattern, 9 call sites). Acceptance: A2 latency metric shows
event-path detection ≪ scan floor; no orphaned GTC (TTL ownership handover proven by test).

### W5 — deletions (strict order; each with tests + registry rows, same commit)
1. **ARM rewire FIRST** — decouple `ArmGateVerification` from coverage verdict (TRAP: settings says
   ARM reads it unconditionally; deleting first silently disarms).
2. Then: coverage/licensing lanes → selection machinery (curse bound + calibrator) → Kelly haircut
   stack → κ · `monitor_refresh.py` dissolution (4,043 lines; belief-re-read content survives in
   solve) · strategy dispatch (`cycle_runtime.py:63,98`; KEEP `edge_source` receipt field) ·
   regret-ledger decision inputs · Day0 decision-time re-masks (`evaluator.py:2458`, reactor
   `:21191`) after authority-side conditioning trusted · retire tests WITH components.

## Agent strategy — orchestration classification (BINDING; operator-directed 2026-07-02)

**Execution base:** worktree `/Users/leofitz/zeus-worktrees/rebuild-w0`, branch
`rebuild/w0-instrumentation`, cut from HEAD `0380fe3f` (main checkout's dirty hotfix tree stays
untouched there — P0.1 attribution deferred, NOT abandoned). Per-packet implementer branches cut
from this base; main thread merges serially onto the wave branch (registry YAML appends are the
only expected cross-packet overlap).

| work shape | vehicle | model | why |
|---|---|---|---|
| locate/enumerate (file:line, verbatim, chains) | **Workflow** fan-out | haiku (simple), sonnet (cross-module chains) | parallel, read-only, context stays out of main thread |
| packet implementation (TDD, own branch/worktree) | **subagent** executor, one per packet | sonnet | full brief (born knowing nothing); done-claim carries commit SHA |
| verification (reproduce acceptance, fresh context) | **Workflow** verify stage or verifier subagent | sonnet | independence from implementer |
| K0 schema packets (draft + adversarial review) | draft: main thread; review: **critic subagent** | opus | canonical truth — outcome decided here |
| W3 math core + evidence-gate judgments | **subagent** / judge panel via Workflow | opus | hardest reasoning, money-deciding |
| deletion sweeps (W4/W5 mechanical hits) | **Workflow** pipeline (locate haiku → edit sonnet → verify sonnet) | haiku+sonnet | high fan-out, mechanical |
| git landing (merge, conflict, history) | **git-master subagent** | sonnet | serial, plumbing |
| seam surgery (`qkernel_spine_bridge.py:1332` swap), integration decisions, K0 final wording | **main thread** (Fable) | — | single-point irreversible edits stay with orchestrator |
| second opinion on W3 solver design / promotion evidence | **chatgpt-consult** background | — | free parallel depth, wakes on completion |

Cross-packet integration lands only through the wave branch after packet + verify green.

## Evidence gates (per P2_sequence 2026-06-14 precedent)
Each wave: G0 tests green → G3 byte-identical-OFF (where a flag exists) → settlement-graded evidence
→ ARM flip → flag deleted. No wave starts while the prior wave's live regression window (≥ a full
settlement cycle) shows unexplained deltas.

## NEXT ACTION (updated 2026-07-02, execution running)
IN FLIGHT: Phase A locate briefs DONE (wf_883d26c1-637, 6/6; extracted to scratchpad briefs/).
W0.1/W0.2/W0.3 implementers running on branches rebuild/w0.{1,2,3}-* (worktrees
/Users/leofitz/zeus-worktrees/w0-{1,2,3}); fork drafting the two W1 K0 schema packets into
rebuild-w0/docs/rebuild/schema_packets/ (uncommitted, for main-thread review).
KEY PHASE-A FINDING (raises W0.1 stakes): hook_factory live q-serving path
(replacement_forecast_hook_factory.py:545-556 → event_reactor_adapter.py:2924) has NO raw-input
HWM check — live decisions can consume a posterior while newer raw input sits unincorporated.
Design: opt-in enforce_raw_input_hwm on the shared bundle reader; shared module
src/data/replacement_input_hwm.py; position_belief untouched (annotate-contract, monitor must not raise).
PACKET STATUS:
- W0.3 DONE — commit 5b146458, branch rebuild/w0.3-source-run-event. Idempotency key is
  run-derived (entity_key=model|source_cycle_time, available_at from run availability; wall-clock
  received_at deliberately outside the key) because live callers use advance_cursor=False — cursor
  does not dedupe; replay test proves it. EMIT ONLY: no production call site wired (W4 wires).
  Baseline diff: 35 pre-existing failures identical, +3 new tests, zero regressions.
  ⚠ MERGE/DEPLOY FLAGS: (a) CHECK migration triggers a full opportunity_events table rebuild on
  next live-daemon boot (real I/O/lock-duration event on a large live table — surface to operator
  before live deploy); (b) acceptance "rows appear on real cycle advance" proven at probe level,
  production wiring intentionally deferred to W4.
- W1 packets: CRITIC VERDICT (opus) — both PASS-WITH-REQUIRED-FIXES; CAS load-bearing question
  PASSES (second writer's snapshot established after write-lock acquisition; live path further
  protected by insert_command holding the write lock before the CAS on the same conn; worst case
  loud BUSY_SNAPSHOT, never silent over-reserve). Required fixes ruled + sent to drafter fork
  (single edit pass, uncommitted): W1.1 — idempotent remaining=original−MAX(matched_size) (facts
  are cumulative + replays double-fire an incremental decrement), convert takes no notional,
  type-aware A4 identity (CTF_SELL proceeds are incoming, not subtraction), consistent-snapshot +
  tolerance + auto-resolve on clean check (no sticky false halt), append_event-centrality
  invariant + carve-out guard for the reconcile direct-write, live-topology concurrency test
  (insert_command→CAS same conn), BUSY retry + trigger-exception mapping + cite fix (:2914-2999).
  W1.2 — normalized (not byte) DDL comparison covering BOTH pairs (venue_order_facts + collateral;
  _TRADE_CLASS_DDL wholly outside fingerprint), rest_deadline_exceeded predicate over ALL open
  rests regardless of q_version (closes NULL-q rest-forever leak; W4 may delete
  maker_rest_escalation ONLY in the packet landing this deadline wired to cancel), stamping
  precedent → insert_command (venue_command_repo.py:812-888), INV-CL-1 cite dropped (not a
  registered invariant), path fixes.
  CITATION-VERIFY PASS (critic's child agent, all W1.1 citations confirmed live) — three
  load-bearing facts: (a) executor reserve calls BYPASS the global db_path singleton — they wrap
  the caller-owned conn (CollateralLedger(conn), executor.py:2685,2694) in the SAME txn as the
  venue-command insert; CAS must hold in this mode (statement-level guard inside caller txn — it
  does). (b) RED = trading-pause + Discord alert ONLY (discord_alerts.py:190-200); LEVEL_ACTIONS
  "exit all positions" is a label, not executed code — false-positive RED is costly, not
  catastrophic. (c) risk_level.overall_level is VARIADIC — 7th component addition trivial.
  ⚠ NEW FINDING for W1.1 packet: exchange_reconcile.py:1660-1667
  (_book_external_operator_close_exit_fact) INSERTs venue_commands directly with terminal
  state='FILLED', never passing append_event's terminal dispatch (venue_command_repo.py:1179-1185)
  — today harmless (synthetic external-close id never carries a reservation; shared-wallet
  journal-only correction) but under convert-on-fill this pattern would silently orphan any
  reservation. Packet must add: write-gate on direct terminal inserts (assert no live reservation
  for the command_id) + A4 sweep covering orphaned reservations vs terminal commands. Inject into
  packet after critic verdict (single edit pass, no moving target under review).
  W1.2 CITATION-VERIFY: amendments queued for the same edit pass —
  (a) DESIGN: only 1 of 3 venue_commands INSERT sites (insert_command,
  venue_command_repo.py:888-916 — also the REAL stamping site; packet's executor.py:1216 cite was
  a validation guard, not stamping) has live decision context; the 2 exchange_reconcile synthetic
  rows (:1152, :1662) borrow old-entry snapshot_id → q_version stays NULL there BY RULE (= "not
  Zeus's decision basis"), and the staleness predicate maps NULL q_version → INDETERMINATE (no
  cancel churn on externally-originated/recovery orders) — composes with the existing
  no-servable-q rule. (b) Pre-existing reducer gap CONFIRMED: 4 CHECK-legal states
  (CANCEL_REQUESTED/CANCEL_UNKNOWN/CANCEL_FAILED/HEARTBEAT_CANCEL_SUSPECTED) silently fall through
  to UNKNOWN fallback; VenueOrderStatus enum missing 5 legal states — validates Option B; record
  in packet risk table, fix NOT in scope. (c) Fingerprint blind spot ALSO covers collateral DDL
  (_TRADE_CLASS_DDL entirely un-fingerprinted) → W1.1's DDL edits are CI-blind too — extend the
  lockstep-guard approach to the collateral DDL pair (db.py:5513-5538 vs collateral_ledger.py:53-79).
  (d) Citation fixes: INV-CL-1 is a code-comment label, NOT in invariants.yaml (don't cite as
  registered); INV-28 at :561; family_exclusive_dedup lives in src/strategy/;
  OPEN_ORDER_FACT_STATES at canonical_projections.py:40-44.
- W0.2 DONE — commit 8dcae361, branch rebuild/w0.2-latency-metrics. Latency: NO mirror table
  (derivable from forecast_posteriors' source_cycle_time/computed_at — consistent with the
  derive-don't-store doctrine); structured-log emit + LATENCY_QUERY documented. Blind-window: new
  trade-DB table market_channel_connectivity_events via the established feasibility-evidence
  routing; connect/disconnect/reconnect all covered. Registries + ownership tests green; 9+2
  pre-existing failures verbatim-confirmed on stashed baseline.
  ⚠ VERIFIER CHECKLIST: (a) latency START point — if LATENCY_QUERY computes computed_at −
  source_cycle_time it measures issue→posterior (includes provider delay we don't control); A2
  wants raw-input ARRIVAL→posterior (join raw fetched/written ts via dependency_source_run_ids_json)
  — verify which it is; if issue-based only, require the arrival-based variant as follow-up.
  (b) blind-window misses hard kills (SIGKILL/OOM never reaches on_disconnect) — documented gap,
  future join vs heartbeat; acceptable W0.
- W0.1 DONE — commit 7fbcb60b, branch rebuild/w0.1-qversion-tripwire. Shared module
  src/data/replacement_input_hwm.py (4 HWM fns incl. combine glue); adapter fns → byte-identical
  delegating wrappers; bundle reader gains enforce_raw_input_hwm=False opt-in; hook_factory opts
  in (BLOCKED via existing not-ok contract, reason prefix REPLACEMENT_RAW_INPUT_HWM:). Anti-tautology
  mutation check done (flag reverted → guard test fails). 2+16 pre-existing failures stash-verified
  byte-identical. position_belief untouched (2 impls remain, full collapse = W5).
  NOTE for fresh worktrees: config/settings.json is gitignored — copy locally to run tests, never commit.
MERGED: W0.3 → 417eacb2, W0.1 → dc552b7b on rebuild/w0-instrumentation (auto-merge, no row drops
verified; combined sanity green: 57 passed, exactly the 2 known pre-existing failures, fingerprint
+ manifest audit OK). W0.4 implementer dispatched off dc552b7b (re-verifies brief's MISSING list
post-merge; condemned modules' rationale rows must state their W3/W5 fate).
K0 SIGN-OFF: OPERATOR SIGNED BOTH PACKETS 2026-07-02 (rev-2, commit d766d138; critic verdict
PASS-WITH-REQUIRED-FIXES with all fixes applied; orchestrator rulings: original_amount column
dropped [amount immutable = original], capital-efficiency double-count window accepted
[conservative direction], rest_deadline_exceeded bootstrap = incumbent
MAKER_REST_ESCALATION_DEADLINE_MINUTES value until W0.2 p99s exist).
W1 IMPLEMENTATION RUNNING: w11-implementer (rebuild/w1.1-cas-ledger) + w12-implementer
(rebuild/w1.2-order-state) in parallel — disjoint hunks verified (W1.1: collateral DDL ~:5513 +
terminal dispatch :1179 + append_order_fact :2914; W1.2: venue_commands DDL + insert_command
:812-888); W1.2's normalized-DDL guard written self-consistent so W1.1's lockstep edits pass.
Merge order on completion: W1.1 first.
- W0.4 DONE — commit d84ce29e (registries + src/decision/AGENTS.md only). Brief's downstream
  guesses corrected by grep; 10 source_rationale rows + 10 test_topology rows + manifest decision:
  entry; condemned modules' rows carry their W3/W5 fate; city_skill_gate/decision_receipt honestly
  marked disposition-unknown. Merge dispatched to w0-merger.
  MERGED → d72056fc (clean, sanity green: topology grep 0, manifest audit 0, YAML OK,
  fingerprint 1 passed). NOTE: W1.1/W1.2 branches were cut at d766d138 (pre-W0.4) — expect
  additive YAML merge at their landing, keep-both policy.
  DEFERRED (recorded): src/decision_kernel/ (23 files) + scattered decision_* files remain
  unregistered — future hygiene packet; topology_doctor --strict crashes on missing
  state/assumptions.json in fresh worktrees (pre-existing).
- W0.2 arrival variant DONE — a4a2c00e. Better than the join proposal: reuses the persisted
  source_available_at column (C1-AVAIL-CLOCK, materializer :172-228, max fetch_finished_at over
  roles) instead of duplicating possession logic at read time. Both variants exposed:
  latency_from_issue + latency_from_arrival (emit + query + rationale row). Disclosed limit:
  openmeteo leg arrival = preflight-hint fallback until that leg writes source_run rows
  (auto-upgrades, documented). 8+88 tests green, no stray refs to renamed fn.
══ W0 WAVE CLOSED 2026-07-02 ══ — final merge 8e1fb748; wave branch carries all four packets
(W0.1 tripwire, W0.2 dual latency + blind-window, W0.3 SOURCE_RUN_ARRIVED, W0.4 registry
backfill). Closing sanity: 143 + 1 + 14 tests green, fingerprint pin unchanged, manifest audit 0.
NOT yet deployed to live daemon — deploy decision is the operator's, and W0.3's CHECK migration
triggers a full opportunity_events table rebuild on first boot (flagged above).
- W1.2 DONE — b845bd6c. q_version sourced from the VALIDATED certificate payload
  (executor.py:5378→5699→insert_command :5715; hash originates event_reactor_adapter.py:11564,
  validated decision_kernel/verifier.py:1393-1425) — additive kwargs only, STOP condition not
  triggered. EXIT (:4394) + cancel-proxy paths NULL by design (no posterior cert on today's exit
  lane; those rests governed by rest_deadline_exceeded — W3 exits-as-solve will bring certificates
  to exits). Predicates 25-case truth table green incl. INDETERMINATE/NULL; normalized DDL guard
  covers both mandated pairs; both venue_commands DDL copies stamped in lockstep; fingerprint
  repinned (venue_commands world copy IS hashed — expected per packet CI gate). 35 pre-existing
  failures stash-verified; money-path invariants 41/41.
  NEW PRE-EXISTING GAP (deferred): venue_commands world copy carries
  idx_venue_commands_envelope/_snapshot indexes the trade copy lacks — index drift predates this
  packet; recorded in test_ddl_copies_normalized_identical.py comment; standalone hygiene fix.
- W1.1 DONE — c7e095ee (+1760/−16). Six acceptance tests green (25-thread live-topology stress:
  20/25 succeed, zero over-reserve, contended failures all CollateralInsufficient; storm; replay;
  auto-resolve; carve-out guard both directions; orphan sweep) + convert-on-fill/type-aware/
  clearing/trigger-mapping/3-conn-modes. Migration via formal scripts/migrations framework,
  live-tested on simulated legacy DB (preserved rows, no-op second run).
  REAL FINDING (deviation 1): the packet's "three lockstep DDL copies" needed a FOURTH —
  db.py:2666 embeds exchange_reconcile_findings DDL inside world init_schema; proven by stash
  bisection, widened + repinned. DEFERRED: extend W1.2's normalized-DDL guard to the
  exchange_reconcile_findings pair (4th copy class). A4 comparator design-completed as three
  signals under one finding kind (orphan sweep / stuck-unsettled tolerance / spendable-negative
  defense) — verifier to sanity. CTF_SELL proceeds valued at submit-time price (only price at the
  no-notional seam) — trued up by balance refresh; acceptable.
VERIFIER VERDICTS: W1.1 VERIFIED (zero findings — all 16 acceptance tests rerun independently,
migration self-executed on synthetic legacy DB, all FOUR finding-kind vocabulary copies lockstep,
riskguard gating structural not advisory, baseline 13 pre-existing failures identical on base).
W1.2 VERIFIED-WITH-FINDINGS, both resolved: (a) packet's negative-fingerprint premise was FALSE
(venue_commands world DDL copy IS fingerprint-covered) — implementation was right, packet ERRATUM
committed 40cd035cb; (b) cross-packet merge NOT unattended-clean: 2 known conflicts
(_schema_fingerprint.txt pin both-changed → re-run --write-pin on merged tree;
test_exchange_reconcile.py same-region appends → accept both). db.py + venue_command_repo.py
confirmed disjoint via merge-tree simulation.
══ W1 WAVE CLOSED 2026-07-02 ══ — merges 1945ff995 (W1.1) + e26b9b18f (W1.2); both known
conflicts resolved per playbook (union pin 5eacf215 via --write-pin on merged tree;
test_exchange_reconcile.py accept-both). Closing sanity: only the known pre-existing failure set
(6 collateral test_executor_* + 1 TestCancelPendingInRecoveryFilter + 7 riskguard trailing-loss),
cross-wave regression 22/22, fingerprint + manifest audit + all YAML green.
Wave branch now carries: W0 instrumentation + CAS ledger (live TOCTOU closed, phantom-cash window
structurally gone, A4→RED) + q_version stamping + derived predicates. NOT deployed to live.
══ W2 WAVE OPEN ══ — locate briefs in flight (wf_0b421e04-732): W2.1 batch wrapper, W2.2
self-trade guard (shared-wallet law: foreign orders EXPECTED, guard only vs Zeus's own),
W2.3 rate budget + cancel-priority, W2.4 CTF convert/split/merge (long pole; redeem stays
third-party per operator law). All land INERT; W3/W4 consume.
BRIEFS DONE (4/4, extracted to scratchpad briefs/W2_*.json). KEY FINDINGS: SDK has NO batch-size
limit — ≤15 is our architecture decision; batch response→command mapping UNVERIFIED vs live API
(fail-closed rule: prefer echoed orderID, else index+length-assert, unmappable → SUBMIT_UNKNOWN);
batch gateway needs INV-24 allowlist + semgrep rule + NC-16 additions; safe-prefix has ZERO code
precedent (build from design doc def); order path has ZERO 429 handling (only data-fetch loops).
DISPATCHED (parallel, file-ownership partitioned): W2.4 CTF primitives (adapter file OWNER;
IntentKind closed-grammar route decision delegated with STOP conditions) · W2.2 self-trade guard
(pure module; shared-wallet law binding: foreign orders never blocked) · W2.3 rate budget
(standalone module, cancel-priority, injectable clock, inert).
W2.1 batch wrapper DEFERRED until W2.4 lands (same adapter file — one owner at a time); its
governance surfaces (INV-24/semgrep/NC-16) noted in brief W2_1.json.
- W2.2 DONE — 673a83cf. Pure check_self_trade (CLEAR/WOULD_SELF_CROSS/INDETERMINATE) + thin
  loader (latest-fact-per-command via MAX(local_sequence), canonical OPEN_ORDER_FACT_STATES,
  tables-missing → None → INDETERMINATE). 23-case truth table + 7 loader tests green; fail-closed
  everywhere (None set, bad inputs → INDETERMINATE; malformed row skipped, not fatal). Inert.
  Uses CANONICAL open-states set only (reconcile's divergent local set stays a deferred hygiene item).
- W2.3 DONE — d92e51286. SINGLE shared bucket + cancel_reserve_tokens floor = the priority
  mechanism (independent per-class buckets would nullify priority — correct first-principles read).
  Ceiling venue-published (Polymarket docs: 5000/10s burst, 200/s sustained); operating point 20
  tok/s = conservative 10%, flagged for sizing once W3/W4 wire a consumer. Injected clock, 27 tests
  incl. 200-thread exact-grant race (5x rerun, zero flakes). Config via optional .get() (protects
  gitignored settings copies). Inert. WIRING NOTES for W3/W4: budget pressure must never pre-empt
  the CutoverPending cancel gate; coordinate with CAS ledger timing.
  W2.2 MERGED → 9ba356a22; W2.3 MERGED → 15f16248f (both clean, pin 5eacf215 unmoved,
  50+69 tests green, manifest audit 0).
- W2.4 DONE — 5ffe4097, deep verifier IN FLIGHT (K0-level compensating scrutiny: the packet
  created a NEW trade-DB truth table ctf_conversion_commands WITHOUT a schema packet, justified by
  redeem-symmetry precedent — settlement_commands-style own ledger, own closed state enum;
  IntentKind grammar deliberately NOT extended, order-shaped columns are a schema-fit mismatch for
  quantity-only conversions). On-chain determination evidence-backed (SDK lacks primitives; 5
  selectors locally keccak-computed, cross-validated vs pinned redeem selectors + third-party Go
  bindings; NEGRISK_SPLIT single-source 0xa3d7da1d → OPERATOR DRY-RUN REQUIRED before first live
  use). Fail-closed: ambiguous broadcast → UNKNOWN non-terminal; kill-switch default-off;
  negrisk_routes untouched, conversion_routes stays () (W3 flips). Fingerprint pin unmoved.
  W2.4 VERIFIER VERDICT: VERIFIED-WITH-FINDINGS — 7/7 selectors reproduced under independent
  keccak recomputation; routing tests decode real Safe calldata; persist-before-side-effect
  structurally enforced (execute requires prior INTENT_CREATED row); UNKNOWN non-terminal proven;
  scope fences hold (3-way falsifiable grep test, no tautology); dry-run→FAILED-clean RULED CORRECT
  (by construction cannot have touched chain). ONE REQUIRED FIX in flight (w24): both new tables
  missing from domains.py CANONICAL_OWNER (precedent registers them; owner_routed_write guards
  silently no-op for unregistered tables — must land before any writer wires).
  ⚠ DEPLOY GATE (recorded): NEGRISK_SPLIT_POSITION_SELECTOR (0xa3d7da1d) single-source — operator
  dry-run / bytecode verification REQUIRED before first live split on a neg-risk market.
  W2.4 fix b10f6883 landed; MERGED → baf2ea0f2 (one predicted registry conflict keep-both;
  db.py + domains.py auto-clean, both wirings + alphabetized CANONICAL_OWNER verified; 88+109
  sanity green, pin unmoved).
  W2.1 DISPATCHED (w21-implementer, branch rebuild/w2.1-batch-wrapper off baf2ea0f2) with binding
  rulings: fail-closed response→command mapping (echo-id > index+length-assert > SUBMIT_UNKNOWN);
  N-persists-before-1-SDK-call journaling reusing single-order sequence; gateway two-ring
  governance same commit (INV-24 allowlist + semgrep + NC-16); ≤15 self-imposed chunking;
  safe-prefix as pure function with INJECTED acceptability predicate (W3 supplies real one);
  FC-03 via per-envelope create_submission_envelope loop; optional rate_budget/self-trade hooks
  with None defaults; INERT.
- W2.1 DONE — 5f765d677 (+2240, 60 tests). Mapping stricter than ruled (partial echo untrusted —
  indistinguishable from dropped item); per-chunk persist semantics (later chunks NEVER persisted
  on halt → zero reconcile debt, fresh retry); governance three rings same commit (sibling semgrep
  rule — existing rule pattern-matches literal name). ⚠ ONE NON-INERT EDIT: drive-by fix of latent
  Decimal→float canonicalization bug in SHARED create_submission_envelope (live single-order
  path) — deep verifier tasked to rule live-crash-prevented vs behavior-change with constructed
  case on old-vs-new code. DEFERRED: FakePolymarketVenue lacks submit_batch/cancel_batch (its
  protocol test is baseline-broken anyway — add when fixing that).
  W2.1 VERIFIER VERDICT: VERIFIED-WITH-FINDINGS — "shared-path edit" premise REFUTED
  (create_submission_envelope zero diff; sole caller in repo = the new orchestrator; Decimal fix
  lives inside brand-new dead code — packet fully inert). Semgrep new rule PROVEN to fire (real
  run vs scratch violation). Findings: (a) persist-before-call test not load-bearing
  (same-connection read sees uncommitted writes; implementation correct at :295) — FIX IN FLIGHT
  (w21: separate ro connection + mutation-check proof); (b) pre-existing count 22 not 14, same
  set both branches, zero regressions — substance holds.
  w21 fix f5649fee4 (mutation-check proven load-bearing); W2.1 MERGED → 8ad6aed7f (zero
  conflicts incl. adapter file — both method sets disjoint; 132+122 sanity green, pin unmoved).
══ W2 WAVE CLOSED 2026-07-03 ══ — wave branch @ 8ad6aed7f carries W0+W1+W2 complete. All venue
capabilities INERT until W3/W4 consume. Standing deploy gates: opportunity_events table rebuild
on first boot (W0.3); NEGRISK_SPLIT selector dry-run before first live neg-risk split (W2.4).
══ W3 WAVE OPEN ══ — the SOLVE. Briefs 5/5 done (scratchpad briefs/W3_*.json). LOAD-BEARING
SEAM FINDINGS: (a) exact swap contract — qkernel_spine_bridge.py:1332 ctor + :1379 decide()
kwargs + FamilyDecision return shape + :1684 proof-overlay fields (q_posterior/q_lcb_5pct/
trade_score/qkernel_execution_economics/...) must stay shape-identical; (b) TODAY'S decide() IS
UNCONSTRAINED FULL-KELLY — argmax robust_delta_u with max_stake_usd not passed by the reactor
(defaults None → full book notional); kelly_multiplier haircut is a DOWNSTREAM submit-boundary
layer (event_reactor_adapter.py:5657-5819) — new solver internalizing κ is a correctness fix,
and ON-mode double-shading (κ + downstream haircut both active until W5 deletes the haircut)
needs an explicit ruling; (c) _record_qkernel_selection_family_facts reads decision fields via
getattr-with-default — FIELD RENAMES FAIL SOFT (shim must assert field presence); (d) NO existing
G3 byte-identical harness for this flag — newly authored per P2 gates table
(docs/evidence/planning_2026-06-14/P2_sequence_and_critical_path.md:120-142).
IN FLIGHT: fork drafting src/solve/ interface skeleton (types/scenario_service/menu_adapter/
solver-shim/kappa/exits) + W3 design packet (uncommitted). THEN: main-thread review →
chatgpt-consult on concrete interfaces → opus math core → G3 harness → seam swap.
THEN: verify W1 packets (fresh verifier incl. ≥20-thread stress rerun) → merge W1.1 → W1.2 →
W1 wave CLOSED → W2 venue capabilities dispatch (batch wrapper, self-trade guard, rate budget,
CTF convert/split/merge — CTF first, it is the long pole).
DEFERRED, NOT ABANDONED: P0.1 dirty-tree attribution in main checkout; P0.2 CWA commit-status check.
