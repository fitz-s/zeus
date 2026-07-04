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
SKELETON LANDED — 14eda27bb (7 interface files import-clean + design packet with 5 orchestrator
decisions RESOLVED: two-phase ON-mode confirmed; conversion routes in phase-2 behind the W2.4
dry-run gate; κ=1.0 until W5 haircut deletion [single-owner, like-for-like promotion evidence];
coherence lockstep [shim emits allows=True, guard retires at flag-ON]; legacy max_stake_usd NOT
threaded [would break G3 OFF byte-identity; new solver budget-aware by construction + CAS rail]).
REGISTRY DEBT: skeleton landed unregistered — assigned as first item of the math-core packet.
BRANCH PUSHED to public origin (repo is PUBLIC — github.com/fitz-s/zeus) for consult link
delivery. CHATGPT-CONSULT IN FLIGHT (REQ-20260702-212900-e935c9, Pro Extended, conversation
6a471ed8…, answer → /tmp/cgc/answer_REQ-20260702-212900-e935c9.txt; detached waiter armed).
Infra note for future consults: HTTP_PROXY=127.0.0.1:20128 intercepts loopback — ALWAYS export
NO_PROXY=127.0.0.1,localhost before cdp_launch.sh/cdp_consult.py (launcher's liveness curl is
fooled by the proxy's 404 page otherwise).
CONSULT VERDICT (Pro Extended, deep review): NO-GO on current interfaces — six blockers, ALL
ACCEPTED with rulings (answer: /tmp/cgc/answer_REQ-20260702-212900-e935c9.txt): ① JOINT ATOM
AXIS — JointOutcomeScenarioSet(atoms, q_draws, weights, semantics enum) + WealthStateByAtom incl.
reservations/resting/unsettled/ledger-snapshot-id (concatenated marginal bins cannot express a
joint distribution; C4 swap would force solver rewrite); ② index-paired product measure invalid
on sorted draws (comonotone!) + TIGHTENED RULING: transitional service = single-family degenerate
case ONLY, multi-family fails closed until C4 (caps = loss limiter, never a sizing license —
numeric proof: identical q=.60 f=.20 pair: independent +.039, comonotone −.0024 expected log);
③ RepairCertificate typed artifact (repaired ΔU>0 required for non-empty plan); ④
LegacyDecisionProjection — phase-1 evidence grades the STANDALONE post-haircut primary leg
(standalone ΔU≤0 → no-trade; full-plan grading would bias the A/B toward W3); ⑤ exits stay
interface-only until ledger-aligned wealth state exists; ⑥ ON-mode sentinel tests (getattr
soft-fail) beyond G3 OFF byte-identity. Plus: CVaR/worst-case objective (quantile-of-concave is
NOT unimodal — legacy payoff_vector assertion unsafe to inherit); same-feasible-set dominance
baseline; per-leg quantization; kappa typed; max_stake_usd out of core solve.
W3.2 DONE — 3 commits: 56c410b35 (registry debt) + 74377574f (interface revision, all 6 blockers
addressed + REV-2 packet appendix) + db624ee96 (math core). Objective = lower-tail CVaR_alpha of
per-draw expected Δlog-wealth over joint atoms — CVaR-of-concave IS concave → global optimum by
construction, concavity TEST-GUARDED (random rays + 4000-pt brute-force match); deterministic
coordinate ascent + explicit max(joint, top1) dominance floor. 57 tests: 71 dominance evals,
identical-marginals/different-joint fixture (comonotone strictly worse — atom axis earns its
cost), min-size sign-flip → NO_IMPROVING_DISCRETE_PLAN, VaR non-unimodality counterexample,
sentinel facts-writer, zero-wealth typed errors. Multi-family FAILS CLOSED (stronger than
consult's envelope alternative — implementer's call, ratified). Zero regressions (identical 4
pre-existing). DECLARED DEFERRALS (sub-slice 3): SolveEngineShim.decide() body,
build_wealth_by_atom ledger derivation, exits economic bodies, per-level worst-price model v2.
VERIFIER: VERIFIED-WITH-FINDINGS — dominance HONEST (top-1 baseline through identical
repair/worst-price pipeline, verified by code trace), certificate structurally inescapable,
sentinel two-sided, import graph clean; 2 gaps (1-D-only brute force; row-sum only for
POSTERIOR_Q_DRAWS). CONSULT FOLLOW-UP: interface foundation CLEARED, CVaR RATIFIED (correct
interpretation: robustification over served posterior draws, not adversarial band set), GO for
shim sub-slice, NO-GO for phase-1 evidence until fixes — 2 NEW BLOCKERS: ① no executable-budget
constraint (W_end>0 does not imply affordability — mutually exclusive claims can all pay while
upfront outlay exceeds cash) ② route-cost depth cap (avg_cost priced at route.shares, max_units
was full ladder → stale cheap average beyond priced depth). FIX ROUND 2 CONSOLIDATED →
w3-math-core (15 items: budget constraint in _feasible_hi/_repair/certificate; max_units=
min(max_shares, route.shares); phase-1 menu = direct native ONLY [basket/pair payoff projection
via single instrument was WRONG — riskfree basket is constant-across-atoms]; chosen_source
certificate parent; prefix positivity; CVaR zero-weight NaN; multi-dim brute force + gap
diagnostics; row-sum all semantics; projector full-coverage; MIN_TAIL_DRAWS stamp; NO-ladder
quantization; price assignment stays with existing submit path in phase 1; PlannedOrder
validation; real-consumer AST sentinel; packet rev-2 hygiene + coordinate-ascent-over-
Rockafellar-Uryasev ruling recorded [RU = future hardening if gap diagnostics show stall]).
RE-SCOPED: build_wealth_by_atom minimal entry-side body now IN sub-slice 3 (not deferrable —
evidence on underivable wealth is not evidence). α-sensitivity replay (α∈{.01,.05,.10}
decision-stability) recorded as promotion-evidence-gate item.
FIX ROUND 2 LANDED — 2c6c70f46 (all 15 items; tests/solve 75 green; baseline unchanged).
KEY EVENT: the multi-dim brute-force guard PAID IMMEDIATELY — pure coordinate ascent was NOT
globally optimal once the budget constraint landed (stalls: budget face / origin under
superadditive CVaR diversification directions / balanced-growth rays); implementer did NOT widen
tolerances — closed with three deterministic moves (budget-neutral pairwise exchange, diversified
multi-start, radial step) until grids match. Residual asymmetric-coupling stall risk ON RECORD as
a phase-2 promotion-gate item (RU convex program = the recorded hardening path).
SPOT-CHECK: MERGE-CLEARED (budget three-layer chain traced + fixture arithmetic independently
verified [wealth bound 220 vs cash bound 20]; grid tolerances byte-identical across commits —
the only test change was the NECESSARY budget filter on grid combos; AST sentinel parses the
real event_reactor_adapter source with genuine positive-detection proof).
W3.2 MERGED → 2188471b4 (zero conflicts, 75+191+68 sanity green, pin unmoved) and PUSHED to
origin (wave branch public @ 2188471b4).
IN FLIGHT: sub-slice 3 (w3-math-core, same branch): SolveEngineShim.decide() body with phase-1
invariants (phase1_tradeable direct-executable primary; standalone post-haircut ΔU≤0 → no-trade;
coherence lockstep; projection-graded evidence fields) + minimal entry-side build_wealth_by_atom
(CAS spendable net of reservations + ledger_snapshot_id) + haircut re-scoring arithmetic
(replicated, not wired). STOP conditions armed (kwargs insufficiency; ledger API gap).
W3.3 DONE — c7880b68c (86 tests). Shim composes inner engine for scaffolding, REPLACES selection
with the joint solver; projection ΔU/size stamped into selected.optimal_delta_u/optimal_stake_usd
(overlay grades PROJECTION, never joint-plan ΔU); phase-1 gate two-layered (solver
UNSAFE_PREFIX_DECOMPOSITION upstream + shim PHASE1_PRIMARY_LEG_NOT_TRADEABLE); wealth builder
injected-inputs (no ledger reach; no-provider default floors spendable at min endowment — never
fabricates unconfirmed cash). APPROVED judgment: haircut = config-factor at decide() + dual
pre/post stamps; settlement receipts authoritative at grading (recorded as phase-1
evidence-grading contract in packet appendix). Injection points ready for seam swap
(engine=/spendable_cash_provider=/ledger_snapshot_id_provider=; self.last_projection audit hook).
NOTED for seam-swap review: inner engine's q_lcb/calibrator guards (W5-deletion targets) still
run in composition — like-for-like preserved in phase 1.
W3.3 SPOT-CHECK: MERGE-CLEARED (verifier computed joint-vs-projection ΔU empirically: 26x apart
under real kelly_multiplier=0.02 — distinction substantive; one test-completeness note fixed in
25e858587 with < and != assertions + pinned 0.5 haircut). MERGED → 63ba4d283, pushed to origin.
FINAL W3 PACKET IN FLIGHT (w3-math-core drafts, ORCHESTRATOR reviews the bridge diff line-by-line
before merge): w3_solve_enabled flag (absent=OFF, registered + deletion deadline, read per-call);
MINIMAL seam edit at bridge :1332 (ON→SolveEngineShim with real CAS-ledger injections or
documented None-fallback; OFF→byte-identical, lazy import inside ON branch); G3 harness
(absent-vs-OFF byte-identity over fixture corpus + single-consumer proof + ON-mode
contract/sentinel integration); import-isolation extended to flag-OFF decide run. STOP conditions
armed (unprovable byte-identity; threading beyond construction site → orchestrator hand-work).
W3.4 DONE — f0ae4d4d1: bridge diff = 2 additive helpers (w3_solve_enabled per-call fail-closed
accessor + _wrap_engine_with_solve_shim lazy-import wrapper) + 2-line guard before the UNCHANGED
decide() call; ORCHESTRATOR LINE-BY-LINE REVIEW PASSED (verified against real commit, not the
report) + personal G3 rerun (5 G3 + 6+1 routing green). G3 harness: absent-vs-OFF byte-identity,
single-consumer AST proof, ON-mode contract-valid divergence, subprocess import isolation.
LEDGER THREADING (STOP-reported, sanctioned fallback): shim runs with conservative endowment
floor until the by-hand promotion-time threading (exact shape recorded: spendable_usd_provider
kwarg through decide_family_via_spine + its one caller at event_reactor_adapter.py:5022).
HYGIENE (non-blocking): numpy RuntimeWarning in G3 fixtures (−inf into percentile path) — silence
or root-cause later.
══ W3 WAVE CLOSED (code-complete, flag OFF) 2026-07-03 ══ — final merge cb641594d, pushed
(97+191 sanity green, import isolation holds, pin unmoved). Wave branch = W0+W1+W2+W3 complete.
W3 PROMOTION GATE (OPERATOR NODE, whenever ready — not blocking W4 build): deploy wave branch
decision [2 standing deploy gates: opportunity_events rebuild window + neg-risk selector dry-run]
→ ledger-provider hand-threading (exact shape in W3.4 report: spendable_usd_provider kwarg
through decide_family_via_spine + caller :5022) → flag ON → settlement-graded projection evidence
window → α-sensitivity replay → ARM flip → flag deletion.
══ W4 WAVE OPEN ══ — locate briefs in flight (wf_3d744ffe-a9b): W4.BRIDGE (universalize book
trigger beyond held families), W4.C3 (SOURCE_RUN_ARRIVED → derived stale set → batch cancel with
rate-budget cancel-priority → reconciled re-solve; maker_rest_escalation deletion blast radius —
same-packet law with rest_deadline wiring), W4.SCAN (demote 60s scan to liveness backstop;
freshness-gate false-alarm audit), W4.MFILL (maker_fill_calibration deletion blast radius).
BRIEFS 4/4 (MFILL via cached resume after API error). KEY FINDING: WS subscription ALREADY
universal (all active weather tokens, limit 2000) — no subscription seam; the gap is only the
eligibility predicate. RULINGS: resting-capital families BYPASS the entry screen (managing
existing exposure ≠ proposing entries); NO new caps (entity-key debounce already bounds the lane
at one pending per family by construction — first-principles answer to the flood risk).
DISPATCHED (file-ownership partition): W4.1 bridge third-bucket (price_channel_ingest owner) ·
W4.2 C3 staleness cancel path + maker_rest_escalation deletion same-series (new trigger module +
main.py owner; first real wiring of W2.3 budget at CANCEL priority + W2.1 batch cancel;
INDETERMINATE→no-cancel; no-orphaned-GTC handover proof required) · W4.4 maker_fill_calibration
deletion (event_reactor_adapter owner; per-call-site disposition table; STOP on any site needing
a value the predicates don't provide). W4.3 scan demotion DEFERRED until W4.2 lands (main.py
one-owner). - W4.4 DONE + MERGED → 46211c52e, pushed. Disposition table clean (5 died-with, 1 replaced by
  the PRE-EXISTING deterministic static prior — the module's own documented degrade path); brief's
  blast-radius false-positives corrected by symbol-grep; module was NEVER registered (registry
  disease exhibit). RISK ON RECORD: spread-blind prior returns until a deterministic p_fill
  replacement lands — era.py:15361 is the rewire seam if W4.2 lands a relevant predicate.
  NEW PRE-EXISTING FAILURE LEDGERED (merger found, verified on pre-merge tip):
  test_rest_then_cross_policy.py::TestTakerQualityLiveGate::test_negative_surplus_still_fails_closed
  (assert 0.0 < 0.0 on taker_fee_adjusted_edge) — predates W4.4, independent look needed.
- W4.1 DONE + MERGED → b5cc8d384, pushed. Third bucket (resting-capital families, screen-bypassed
  per ruling) reuses existing join chain; negative case (CANCEL_CONFIRMED latest fact must not
  resolve) + debounce round-trip proven; bucket-size log line = the measurability answer to the
  flood risk (no cap, entity-key debounce bounds by construction). Fixture note: Berlin not a
  registered runtime city (market_phase fails closed) — Denver used for positive-path proofs.
- W4.2 DONE — beca6282a (C3 path: staleness_cancel module + EventStore SOURCE_RUN_ARRIVED claim
  lane) + 82cf9b0a8 (maker_rest_escalation deleted; venue_cancel_journal relocation for 3
  unrelated callers; authority-invalidation lane carried forward — retired job owned it, wholesale
  delete would have silently dropped its only recurring trigger). Proofs: 5-way parametrized
  no-orphaned-GTC handover (all q_version combos), INDETERMINATE fail-closed e2e, budget-denial
  defer chain (W2.3 first real wiring at CANCEL priority), confirmed-families via FRESH
  get_command re-reads. Honest disclosure: no main.py scheduler-glue integration test (matches
  codebase boundary; predecessor had none). DEEP VERIFIER IN FLIGHT (items 5/6b gate: fresh-re-read
  gating + relocation byte-identity).
  ⚠ PRE-EXISTING FAILURE CLUSTER LEDGERED (w42 flagged, predates packet): ~94 tests across
  tests/state/ + test_venue_command_repo + test_command_bus_types fail because
  _validate_entry_submit_payload now requires full execution_capability payload on ENTRY
  SUBMIT_REQUESTED and older fixtures don't supply it — likely fallout from a W1-era packet on
  this base; needs a standalone fixture-hygiene packet. REPRESENTATIVE NAMES (verifier-confirmed
  byte-identical on base, error at venue_command_repo.py:1230):
  test_command_bus_types.py::TestCancelPendingInRecoveryFilter::test_cancel_pending_command_returned_by_find_unresolved;
  test_venue_command_repo.py::TestAppendEventStateTransitionIsGrammarChecked::{test_intent_created_to_submitting,
  test_submitting_to_acked, test_submitting_to_rejected, test_submitting_to_unknown}.
  ALSO LEDGERED: 4 pre-existing collection errors blocking wide-scope runs
  (test_backtest_skill_economics.py + 3 replacement-forecast test files).
  W4.2 VERIFIER VERDICT: VERIFIED-WITH-FINDINGS — item 5 (fresh-re-read gating) CLEAN; item 6b
  refuted as stated (relocation functionally identical but NOT byte-identical: deadline_minutes
  fallback hardcoded 0.0 → misleading "deadline=0min" at all 4 production call sites) + 1 F401.
  FIX IN FLIGHT (w42): fallback → bootstrap_rest_deadline_minutes() (successor TTL owner) + ruff.
  Fixes landed 41773c4c7 (honest precision note: 0min bug not live-visible today — all call sites
  set reasons — fixed regardless). W4.2 MERGED → 6194344cb, pushed (33-name events set diffed
  byte-identical pre/post; cross-packet 185 green; zero maker_rest imports).
- W4.3 DONE — 7dbfd6ff, SHAPE (b) knob-only, with an ARCHITECTURE DISCOVERY that amends the
  design doc's premise: the reactor poll interval is NOT a mere backstop — it is the CONSUMPTION
  CLOCK for every event lane (process_pending's sole caller is the scheduled job; NO wake-on-write
  exists anywhere; even the redecision screen writes to the queue and waits for the next tick).
  "Demote the scan" as written would be an all-lanes latency regression contradicting A2.
  Landed: reactor_scan_interval_seconds knob (default 60 = byte-identical schedule), 3
  freshness-gate no-false-alarm PROOF tests (all three gates confirmed cadence-blind or
  independently-floored), liveness analysis inline. TRUE DEMOTION ENABLER RECORDED as follow-up:
  wake-on-write mechanism (EventWriter signals, reactor waits with timeout=cadence) decouples
  event latency from poll cadence — after that lands, the knob demotes safely. Candidate for the
  A2-driven post-promotion improvement list (E4 measurement will show whether the 60s pull clock
  is the binding latency term).
  MERGED → fba4cee21, pushed.
══ W4 WAVE CLOSED 2026-07-03 ══ — ALL FIVE WAVES (W0-W4) COMPLETE on rebuild/w0-instrumentation
@ fba4cee21 (local + origin). W5 deletions remain post-promotion per DAG.
══ ENDGAME E1 RUNNING ══ — full-branch consult review submitted (REQ-20260703-020827-28de42,
Pro Extended, conversation 6a475ff6…, compare 0380fe3f...fba4cee21 = the whole rebuild diff;
answer → /tmp/cgc/answer_REQ-20260703-020827-28de42.txt). Task bars: cross-packet seams
(ledger↔batch↔cancel composition; shim state under event cadence + reactor threading;
SOURCE_RUN_ARRIVED replay drive; OFF-path identity survivability after W4.2/W4.4 legacy-behavior
deletions), deletion completeness (runbooks/monitoring grep), pre-existing-ledger triage,
EXPLICIT dual verdict: MERGE-TO-MAIN vs DEPLOY-READY (can differ).
E1 VERDICT: NOT DEPLOY-READY / NOT MERGE-AS-IS (confidence 0.78) — ONE REAL BLOCKER + evidence
gaps. THE BLOCKER (validates w42's own disclosure — the bug lives exactly in the untested
scheduler glue): _c3_staleness_cancel_cycle early-returns when no SOURCE_RUN_ARRIVED claimed AND
affected_cities filters BEFORE TTL classification → the deleted maker_rest job's unconditional
every-tick expired-rest scan has NO unconditional successor → orphaned GTC in quiet forecast
periods + cross-city TTL starvation. Consult's minimum fix RATIFIED: two-clock split — global TTL
pass every tick (unconditional, unfiltered) + q-stale pass scoped to claimed events' cities.
Other E1 rulings: cross-packet money/order seam test required (W1.1+W2.1+W4.2 interleaving:
partial-fill in flight × cancel ack × chunk-2 exception); pre-existing clusters must be
repaired/frozen BEFORE deploy (they mask regressions on the exact deploy surface); deletion grep
ops cleanup; migration rehearsal as explicit release op (prod-sized copy, lock duration
captured); OFF-deploy language corrected — deploy is "legacy engine + approved deletions"
(W4.4 maker prior + W4.2 TTL owner are flag-independent behavior deltas, monitor maker/taker/rest
mix in E4). E5-ONLY items (before flag ON, not before OFF deploy): ledger-provider threading,
shim last_plan/last_projection thread-local or per-call assertion, two-worker concurrent decide
stress, G3 rerun.
FIX ROUND IN FLIGHT: w42 (BLOCKER two-clock split + 3 named tests + seam integration test + 
deletion-grep disposition) ∥ fixture-hygiene agent (4 clusters: ~94 execution_capability fixture
repairs [validator-wrong = STOP], 33 events triage, 4 collection errors, taker-quality
investigate-first [gate-wrong = STOP]; NO production src/ changes).
[2026-07-03 update] Both agents hit transient API 429 before starting; RESUMED via SendMessage.
[2026-07-03 08:15] w42 FIX ROUND CLOSED + MERGED: integration branch rebuild/w0-instrumentation
now @ 26bb6f96e (pushed). 4 commits: dfe33429c (BLOCKER: unconditional TTL pass, q-stale scoped
to affected_cities, main.py early-return removed), 774d77f7e (cross-packet W1.1+W2.1+W4.2 seam
test, 4 assertions, no production bug), 1b9256075 (PLAN.md SUPERSEDED annotation), f0035dbf5
(glue-layer regression test on main._c3_staleness_cancel_cycle — closes verifier's coverage gap;
mutation-probe verified). Verifier verdict VERIFIED, zero claim discrepancies, 6 pre-existing
collateral_ledger failures byte-identical at parent commit. Post-merge suites 202 passed.
fixture-hygiene: cluster 1 DONE (32 execution_capability + 2 stale illegal-transition rows,
47 unrelated failures flagged OUT-OF-SCOPE for routing), cluster 2 DONE (tests/events 33→0:
1 ghost-table, 2 renames, 20 payload-key, 1 test-design bug, 1 retired test + topology.yaml),
cluster 3 in flight. Per-cluster commits ruled.
hook-doctor CLOSED: worktree-reaper convention drift (only knew .claude/worktrees + claude/*;
native isolation worktrees invisible) — fixed by AGENT_WT_ROOTS root-membership scoping +
--git-common-dir resolution; diff audited; memory saved (worktree-reaper-scope-by-root-not-branch).
[2026-07-03 08:35] FIX ROUND FULLY CLOSED. fixture-hygiene: all 4 clusters done as 4 per-cluster
commits (7ecd65d35/45fb01c04/09cfd32a9/be45c2118), verifier content-checks 5/5 VERIFIED (its
commit-hygiene FAIL was a race — inspected mid reset/re-commit; adjudicated moot against live
branch state: 4 commits present, tree clean, diff vs dangling full-packet SHA empty). Notable:
2 test files (15 tests) RETIRED with safety trace (Wilson q_lcb mechanism deleted by a1c2163e4;
_replacement_is_live_layer admits only FUSED_NORMAL+certified basis — never live);
47+2 pre-existing failures enumerated as out-of-scope routing backlog (schema/registry/enum
drift + NC-18 exchange_reconcile guard). MERGED: integration @ 1fc6cd687 (pushed); post-merge
suites show only the 2 enumerated pre-existing reds. All wave worktrees removed; only
rebuild-w0 remains (+3 foreign /private/tmp/zeus-pr421-* left untouched).
E1 CONSULT FOLLOW-UP SUBMITTED (same thread 6a475ff6-8de8-83ea-a35c-1b4c02e1948b,
rid REQ-20260703-033058-75c6ab): asks re-verdict on merge-to-main + OFF-deploy readiness of
1fc6cd687, verification of BLOCKER fix as landed, disposition re-check, and whether the 49
enumerated pre-existing reds block OFF-deploy. Detached waiter running; answer lands at
/tmp/cgc_answer_REQ-20260703-033058-75c6ab.txt.
[2026-07-03 09:05] CONSULT ROUND-2 VERDICT (answer: /tmp/cgc_answer_REQ-20260703-033058-75c6ab.txt):
E1 BLOCKER + glue gap + city-starvation all CONFIRMED FIXED. Still NOT merge/deploy-ready — new
findings: [BLOCKER] confirmed_families lacks family-level suppression (staleness_cancel.py:~386 —
family with acked + REVIEW_REQUIRED cancels in same cycle still emitted → redecision against
ambiguous recovery-owned exposure); [BLOCKER] seam test is a FALSE PROOF of exactly that seam
(all 16 commands share one FAMILY; assertion =={FAMILY} enshrines the bug — implementer round-1
claim #4 was wrong); [HIGH] EventStore fetch/claim precedes TTL call with no fail-soft (event-lane
exception still kills TTL — availability regression vs deleted maker-rest owner); [MEDIUM]
release-note bullet (added to E3 below); [MEDIUM] freeze known-red baseline (frozen in E2 below);
[HIGH deferred] shim thread-local stays E5-only. Consult explicitly: after the two code/test fixes
+ baseline freeze → merge-ready and OFF-deploy-ready.
FIX ROUND 2 IN FLIGHT: w42-implementer on rebuild/w4.2-family-gating off 1fc6cd687 (worktree
w4-2b): (1) blocked_families conservative suppression, confirmed = acked − blocked;
(2) seam test split OTHER_FAMILY + same-family suppression test, red-first; (3) EventStore
fail-soft in main.py glue + test_event_store_failure_still_runs_ttl_pass. TDD + mutation probes.
[2026-07-03 09:40] FIX ROUND 2 CLOSED + MERGED: integration @ 0afe42f52 (pushed). Commits
b793167ff (family-level gating: blocked_families from ALL outcomes, confirmed = acked − blocked;
verifier traced batch_order_submission outcome-array completeness — every command gets an outcome
under every branch, so batch-exception/deferred/unknown uniformly block) + 468e009e1 (event-lane
fail-soft: except Exception around world-conn+fetch/claim/commit only, TTL call outside the wrap,
degrade maps affected_cities→None so q-stale pass cannot fire). Seam test restructured
(other_family exclusion) + new same-family suppression test = the load-bearing discriminator
(original seam test alone stays green under old bug — disclosed, verified). Verifier 5/5
(206/0 sweep claim reproduced exactly by orchestrator — verifier had used the round-1 file set).
Double mutation probes re-run independently. w4-2b worktree removed post-merge.
CONSULT ROUND-3 CONFIRMATION SUBMITTED (rid REQ-20260703-040020-bc7d32, detached waiter):
final go/no-go on 0afe42f52 for merge-to-main + OFF-deploy.
[2026-07-03 10:00] ROUND-3 VERDICT: GO (confidence 0.86, remaining blocker set: none) —
merge-to-main ready + OFF-deploy ready under recorded quiet-window procedure. One LOW closed:
47-node baseline + release-note attached in-repo (commit a7e752567).
E2 EXECUTED VIA PR #422 (github.com/fitz-s/zeus/pull/422): main has branch protection
(PR + 2 required checks — direct push rejected GH013). Local milestone merge 08f88adcb +
baseline docs a7e752567 + collateral reconciliation 084db3f92 pushed as rebuild/e2-main-merge.
E2 DISCOVERIES (recorded):
- Hotfix branch is ACTIVELY worked by another session (8+ commits during this session, dirty
  tree with src/ changes). E2 done in separate e2-main worktree, hotfix tree untouched.
  E3 HARD PRECONDITION: merge/reconcile hotfix → main BEFORE deploy, else live fixes regress.
- test_collateral_ledger.py 6 pre-existing reds RECONCILED (not baselined): layered fixture
  drift vs THREE pre-rebuild contract commits (ae2f513b7 taker FOK/FAK proof, a6f47aa4a
  raise→recoverable-rejection, a4707d1be legacy execute_intent blocked + persistence-failure
  REVIEW_REQUIRED). All provenance-verified as settled law. execute_intent is structurally
  dead (get_mode hardcoded live) — future cleanup candidate for W5.
- ADDITIONAL known-red pool (outside frozen 47): tests/test_executor.py ~20 pre-existing
  (stash-compared unchanged by our work). Baseline scope = the suites consult enumerated;
  repo-wide red census belongs to E2 merge record, not re-frozen here.
NEXT: PR #422 checks pass → merge PR → E3 deploy (precondition: hotfix reconcile + quiet
window + migration runner + flag ABSENT) → E4 → E5 → E6.
Housekeeping: 14 merged wave worktrees removed (branches kept, all ancestors of
rebuild/w0-instrumentation@fba4cee21); remaining worktrees = rebuild-w0, w4-2, fixture-hygiene
+ 2 pre-existing DIRTY /private/tmp/zeus-pr421-* (NOT ours — left untouched, surfaced to operator).
hook-doctor agent dispatched: worktree-cleanup hook failed again — locate/diagnose/fix
(suspect: omc plugin-update revert; see memory omc-personal-fork-overlay-model).
THEN: re-merge W4.2 branch + fixture branch → consult follow-up round (same thread) → clean
verdict → E2 merge-to-main (first: P0.1 parked dirty tree) → E3 deploy → E4 → E5 → E6.
THEN: W4.2 verify → merge → W4.3 scan demotion (main.py freed; its final main.py state noted by
verifier item 7) → W4 CLOSED → ENDGAME below.

## ══ ENDGAME — operator-directed closing sequence (recorded 2026-07-03) ══
"按照计划全部执行完毕后进行 consult full deep branch review → 合并 → deploy → 监控真实战况对比理想设计"

E1. FULL DEEP BRANCH REVIEW (consult, after W4 closes):
   - Push final wave branch; deliver `main...rebuild/w0-instrumentation` as a compare link
     (consult.py deliver --compare) — the ENTIRE rebuild diff (W0→W4), not per-packet.
   - deep-review-output scaffold, --output-replace; role: principal reviewer of a live-money
     trading-engine rebuild; task bars: cross-packet seam coherence (things per-packet review
     cannot see: W1 ledger ↔ W2.1 batch journaling ↔ W4.2 cancel path interplay; W3 shim ↔ W4.1
     event lane), deletion completeness, OFF-path byte-identity survivability across ALL merges,
     the pre-existing-failure ledger triage, deploy-readiness verdict.
   - NB proxy fix: export NO_PROXY=127.0.0.1,localhost before cdp scripts.
   - Findings → fix round (same discipline as W3: rulings, implementer, verifier) → follow-up
     consult round until clean or explicitly-accepted residuals.
E2. MERGE TO MAIN:
   - Precondition: E1 clean/accepted + full-suite baseline on the wave branch documented (known
     pre-existing failure ledger frozen as the accepted set).
   - FROZEN KNOWN-RED BASELINE (captured 2026-07-03 @ 1fc6cd687; consult ruling: does NOT block
     OFF-deploy IF the failure set stays EXACTLY this — any delta is a fresh stop; consult's "49"
     was a miscount, verifier-counted 47 = 45 tests/state + 2 test_venue_command_repo):
     47 node ids at scratchpad/known_red_baseline.txt, summarized by file:
     test_boot_migration_v28_antibody×7, test_forecast_db_split_invariant×2,
     test_inv_f1_chain_economics_split×3, test_inv_f5_trade_decisions_demoted×1,
     test_no_world_market_events_v2×1, test_obs_consolidation_v2_wins×1,
     test_p2_byte_equivalence×3, test_position_lots_reconciliation×2,
     test_position_open_idempotency×20, test_table_registry_coherence×5,
     test_venue_command_repo: test_append_order_fact_preserves_prior_terminal_zero_remainder[MATCHED]
     + TestNoModuleOutsideRepoWritesEvents::test_no_direct_venue_command_events_mutation_outside_repo
     (NC-18 guard — acceptable ONLY as the documented direct-write carve-out for synthetic
     external closes, W1.1 live-reservation guard + orphan sweep green; re-verify at E2).
     Full node-id list to be committed alongside the E2 merge record.
   - git checkout main (main worktree /Users/leofitz/zeus — FIRST resolve the parked dirty
     hotfix tree there: P0.1 attribution debt, commit or stash-record it) → merge wave branch
     (--no-ff, milestone message) → full sanity → push origin main.
E3. DEPLOY (operator-gated, quiet window):
   - Deploy gates on record: (a) opportunity_events full-table rebuild fires on first boot
     (W0.3 CHECK migration) — pick a no-cycle-advance window; (b) NEGRISK_SPLIT selector dry-run
     required before any first live neg-risk split (phase-2 concern, recorded not blocking);
     (c) scripts/migrations/202607_cas_reservation_ledger.py runs via the migration runner.
   - Mechanism: launchd zeus-live-main (memory: live-daemon-deploy; LIVE_REPO footgun — verify
     the deployed path is the one the daemon actually runs).
   - FLAG STATE AT DEPLOY: w3_solve_enabled ABSENT (=OFF) — deploy is byte-identical legacy
     behavior by G3 proof; the deploy itself must show ZERO decision-behavior change.
   - RELEASE NOTE (consult round-2 MEDIUM, required): the W4.2 TTL-owner change and the W4.4
     maker-fill-calibration/maker-prior deletion are FLAG-INDEPENDENT behavior deltas — they
     ship live at OFF-deploy and are NOT covered by the W3 OFF byte-identity claim. State this
     in the deploy record; E4 monitors maker/taker/rest mix for exactly these deltas.
E4. REAL-COMBAT MONITORING vs IDEAL DESIGN (the operator axiom made measurable — run a
   comparison dashboard/report over the first N settlement cycles):
   - SPEED (the axiom): latency_from_issue + latency_from_arrival distributions (W0.2) vs the
     design's A2 SLA intent; blind-window totals (restart churn cost, W0.2) — quantify the
     ~20-min auto-restart tax; event-path detection latency vs the old 60-90s scan floor (W4.1
     bucket-size logs + redecision lane timing).
   - MONEY SAFETY: A4 identity findings count (expect zero mismatch findings; any
     collateral_identity_mismatch → root-cause before promotion); CAS CollateralInsufficient
     rejections (expect loud-and-rare, never silent over-reserve); reservation convert-vs-release
     ratios sane vs fill rates.
   - TRUTH: q_version stamp coverage % on new ENTRY commands (expect 100% on decision path,
     NULL only on reconcile backfills); C3 cancel-set sizes + INDETERMINATE rates (blind-family
     fail-closed working, no cancel churn); no-orphaned-GTC: zero rests older than deadline.
   - IDEAL-VS-ACTUAL REPORT: after the window, write docs/rebuild/real_combat_vs_ideal_
     <date>.md — per design-doc §axis (speed/safety/truth), measured vs intended, gaps ranked;
     this report IS the input to the W3 promotion decision and the W5 go-ahead.
E5. W3 PROMOTION (operator gate, evidence from E4): ledger-provider hand-threading (shape in
   W3.4 report) → flag ON → settlement-graded PROJECTION evidence window (actual-submitted-size
   grading per the recorded contract) → α-sensitivity replay {.01,.05,.10} decision-stability →
   ARM flip → DELETE w3_solve_enabled (no-permanent-flags).
E6. W5 DELETIONS (only after promotion): ARM-rewire FIRST (coverage-verdict decoupling) → then
   coverage lanes, selection machinery, kelly haircut stack → κ ownership transfer (single
   commit), monitor_refresh dissolution, strategy dispatch, regret-ledger inputs, Day0 mask
   consolidation — each with tests + registry rows same commit, per §2 blast-radius order.
THEN: verify W1 packets (fresh verifier incl. ≥20-thread stress rerun) → merge W1.1 → W1.2 →
W1 wave CLOSED → W2 venue capabilities dispatch (batch wrapper, self-trade guard, rate budget,
CTF convert/split/merge — CTF first, it is the long pole).
DEFERRED, NOT ABANDONED: P0.1 dirty-tree attribution in main checkout; P0.2 CWA commit-status check.

## APPENDIX — FROZEN KNOWN-RED BASELINE (exact node ids, captured @ 1fc6cd687, E2 record)
```
tests/state/test_boot_migration_v28_antibody.py::TestNoTradeEventsMigrationFromV28::test_new_reason_insert_succeeds_after_migration
tests/state/test_boot_migration_v28_antibody.py::TestNoTradeEventsMigrationFromV28::test_old_reason_still_accepted_after_migration
tests/state/test_boot_migration_v28_antibody.py::TestNoTradeEventsMigrationFromV28::test_schema_version_29_insert_accepted
tests/state/test_boot_migration_v28_antibody.py::TestNoTradeEventsMigrationFromV29::test_new_reason_insert_succeeds_after_migration
tests/state/test_boot_migration_v28_antibody.py::TestNoTradeEventsMigrationFromV29::test_old_v29_reason_still_accepted_after_migration
tests/state/test_boot_migration_v28_antibody.py::TestNoTradeEventsMigrationFromV29::test_schema_version_30_insert_accepted
tests/state/test_boot_migration_v28_antibody.py::TestP02StaleV30OnlyMigration::test_schema_version_canonical_insert_accepted_after_migration
tests/state/test_forecast_db_split_invariant.py::test_rel6_trio_atomicity_commit
tests/state/test_forecast_db_split_invariant.py::test_rel6_trio_atomicity_rollback
tests/state/test_inv_f1_chain_economics_split.py::test_balance_only_rescue_preserves_submitted_economics
tests/state/test_inv_f1_chain_economics_split.py::test_balance_only_rescue_writes_chain_economics_into_projection
tests/state/test_inv_f1_chain_economics_split.py::test_trade_verified_rescue_still_writes_fill_economics
tests/state/test_inv_f5_trade_decisions_demoted.py::test_registry_classifies_trade_decisions_as_archive
tests/state/test_no_world_market_events_v2.py::TestNoWorldMarketEventsV2InSource::test_no_world_market_events_in_src
tests/state/test_obs_consolidation_v2_wins.py::test_settlement_replay_reads_v2_running_max
tests/state/test_p2_byte_equivalence.py::TestInitSchemaForecasts0ByteGuard::test_nonexistent_world_db_takes_static_fallback
tests/state/test_p2_byte_equivalence.py::TestInitSchemaForecasts0ByteGuard::test_zero_byte_stub_takes_static_fallback
tests/state/test_p2_byte_equivalence.py::TestP2ByteEquivalence::test_world_plus_forecasts_schema_names_match_fixture
tests/state/test_position_lots_reconciliation.py::test_f108_no_bare_sum_filled_size_without_dedup_guard_in_src
tests/state/test_position_lots_reconciliation.py::test_f111_live_exposure_sum_shares_excludes_closed_phases
tests/state/test_position_open_idempotency.py::TestConsolidator::test_chain_covers_db_different_condition_stays_divergent
tests/state/test_position_open_idempotency.py::TestConsolidator::test_chain_covers_db_same_identity_open_rows_merge
tests/state/test_position_open_idempotency.py::TestConsolidator::test_consolidate_token_scoped
tests/state/test_position_open_idempotency.py::TestConsolidator::test_consolidator_idempotent
tests/state/test_position_open_idempotency.py::TestConsolidator::test_divergent_when_chain_matches_db
tests/state/test_position_open_idempotency.py::TestConsolidator::test_divergent_when_no_chain_snapshot_skips
tests/state/test_position_open_idempotency.py::TestConsolidator::test_karachi_safety_singleton_is_noop
tests/state/test_position_open_idempotency.py::TestConsolidator::test_overbook_voids_oldest_no_token_row
tests/state/test_position_open_idempotency.py::TestConsolidator::test_overbook_voids_oldest_row
tests/state/test_position_open_idempotency.py::TestLondonReplay::test_full_replay_then_migration_then_writer_block
tests/state/test_position_open_idempotency.py::TestMigrationAndIndex::test_migration_refuses_when_duplicates_exist
tests/state/test_position_open_idempotency.py::TestMigrationAndIndex::test_unique_index_allows_void_then_reopen
tests/state/test_position_open_idempotency.py::TestMigrationAndIndex::test_unique_index_catches_race_past_writer
tests/state/test_position_open_idempotency.py::TestWriterIdempotencyCheck::test_duplicate_open_raises
tests/state/test_position_open_idempotency.py::TestWriterIdempotencyCheck::test_duplicate_open_raises_for_no_token_identity
tests/state/test_position_open_idempotency.py::TestWriterIdempotencyCheck::test_economically_closed_does_not_block_new_open
tests/state/test_position_open_idempotency.py::TestWriterIdempotencyCheck::test_first_insert_succeeds
tests/state/test_position_open_idempotency.py::TestWriterIdempotencyCheck::test_hard_terminal_same_position_id_is_absorbing
tests/state/test_position_open_idempotency.py::TestWriterIdempotencyCheck::test_same_position_id_upsert_is_noop
tests/state/test_position_open_idempotency.py::TestWriterIdempotencyCheck::test_voided_row_does_not_block_new_open
tests/state/test_table_registry_coherence.py::TestA1RegistryVsSqliteMaster::test_a1_world_side_bidirectional
tests/state/test_table_registry_coherence.py::TestA4AssertDbMatchesRegistry::test_a4_allows_migration_ledger_on_world_and_forecasts
tests/state/test_table_registry_coherence.py::TestA4AssertDbMatchesRegistry::test_a4_column_shape_check_raises_on_missing_column
tests/state/test_table_registry_coherence.py::TestA4AssertDbMatchesRegistry::test_a4_passes_on_correct_world_schema
tests/state/test_table_registry_coherence.py::TestA4ManifestReadyForBootWiring::test_a4_trade_tables_init_schema_creates_runtime_tables_and_migration_ledger
tests/test_venue_command_repo.py::test_append_order_fact_preserves_prior_terminal_zero_remainder[MATCHED]
tests/test_venue_command_repo.py::TestNoModuleOutsideRepoWritesEvents::test_no_direct_venue_command_events_mutation_outside_repo
```
