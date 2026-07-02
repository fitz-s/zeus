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
ON WAKE: verify implementer SHAs (verifier pass) → merge serially onto rebuild/w0-instrumentation
(git-master) → dispatch W0.4 registry backfill (LAST — shared YAML surface) → review W1 packet
drafts → opus critic gate → present K0 packets for operator sign-off.
DEFERRED, NOT ABANDONED: P0.1 dirty-tree attribution in main checkout; P0.2 CWA commit-status check.
