# Zeus handoff — P4 start boundary

## 0. Current repo truth

- Branch: `Architects`
- Head: `cc53681` — `Record P3 family completion after the final post-close gate`
- P3 status: complete under current repo truth
- Active packet: none
- Current stop boundary: wait for an explicitly frozen non-P3 packet before more implementation

## 1. Methodology updates landed from the P3 loop

The core governance surfaces now explicitly require:

- no packet close before pre-close critic + verifier finish
- one additional post-close third-party critic + verifier gate before the next packet freeze
- explicit evidence visibility for every `evidence_required` item
- explicit proof for both capability-present and capability-absent paths when a packet depends on optional/runtime substrate availability

These are now the expected operating rules for P4 as well.

## 2. P4 entry truth

Architecture truth says the P4 sequence is:

1. opportunity facts
2. availability facts
3. execution facts
4. outcome facts
5. analytics smoke queries

Current descriptive repo truth:

- the canonical schema already contains:
  - `opportunity_fact`
  - `availability_fact`
  - `execution_fact`
  - `outcome_fact`
- there are currently **no repo writes** to these fact tables in `src/**`
- this means P4 is entering as a writer/flow-install phase, not a schema-add phase

## 3. Recommended next packet

Freeze:

- `P4.1-OPPORTUNITY-FACTS`

Recommended packet intent:

- install the first writer path for `opportunity_fact`
- keep it narrowly scoped to evaluated candidate-direction attempts
- do **not** mix `availability_fact` writes into the same first packet unless a specific rejected-opportunity path forces it and the packet is explicitly widened

## 4. Read order for the next session

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `docs/architecture/zeus_durable_architecture_spec.md` — P4 section
6. `docs/governance/zeus_autonomous_delivery_constitution.md` — updated closeout rules
7. authority surfaces:
   - `architecture/kernel_manifest.yaml`
   - `architecture/invariants.yaml`
   - `architecture/zones.yaml`
   - `architecture/negative_constraints.yaml`
8. inspect likely P4.1 touchpoints in code

## 5. Likely P4.1 touchpoints

Read first, then freeze:

- `src/engine/evaluator.py`
- `src/engine/cycle_runtime.py`
- `src/state/decision_chain.py`
- `src/state/db.py`
- tests that already exercise:
  - `decision_snapshot_id`
  - `availability_status`
  - rejection-stage / no-trade cases

## 6. P4 packeting guidance

- keep one P4 fact layer per packet unless repo truth forces a tighter combined seam
- preserve the new post-close gate discipline on every packet
- prefer proving:
  - fact write path,
  - point-in-time linkage,
  - and explicit degraded/absent-path behavior

## 7. Out-of-scope dirt to preserve

Still out of scope unless a future packet explicitly takes them:

- `README.md`
- `docs/architecture/zeus_durable_architecture_spec.md`
- `docs/governance/zeus_runtime_delta_ledger.md`
- `docs/TOP_PRIORITY_zeus_reality_crisis_response.md`
- `docs/archives/`
- `docs/archives/architects_progress_archive.md`
- `root_progress.md`
- `root_task.md`
- `tests/test_calibration_quality.py`
- `work_packets/MATH-002-BIN-HIT-RATE-CALIBRATION.md`

## 8. Safe wording for repo truth right now

- P3 family is complete under current repo truth
- no live packet is open
- next operational step is to freeze `P4.1-OPPORTUNITY-FACTS`
- P4 starts as a fact-writer installation phase, not a schema-add phase
