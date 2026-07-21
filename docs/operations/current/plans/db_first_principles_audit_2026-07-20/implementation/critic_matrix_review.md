# CRITIC VERDICT — Authority Matrix (5+1 class × all-table) for Zeus live-money DB redesign

**VERDICT: REJECT (not-safe to drive wave execution as-is; safe only with the named corrections).**

**Mode:** started THOROUGH, escalated to ADVERSARIAL after the first BLOCKING finding (a full authority inversion on the single most money-critical table). The escalation was warranted — the same root cause (trusting round-2's grain-story over the manifest + code) recurs across the certificate family.

**Overall assessment.** The matrix is ~80% sound and its structural moves are mostly right (collateral → money-hot, book_hash head → money-hot, position_events/venue_order_facts → ledger, calibration_pairs → learning-mart, settlement_outcomes → ledger, opportunity_events vertical split, the 6th `reconstructible-current-cache` class). But it contains one catastrophic, money-data-losing **authority inversion on `decision_certificates` + `decision_certificate_edges`**, plus a cluster of "trade-copy is the ledger" promotions that enshrine pre-PR-S4b **ghost** tables as canonical while demoting the real authority to a deletable class. These are exactly the "delete/rebuild a permanent money table" failures the review was commissioned to catch. The matrix must not drive any wave (especially W2 authority routing → W3/W5 deletion) until the certificate inversion is corrected.

**Pre-commitment predictions vs. found.** I predicted (1) money-truth tables mislabeled deletable, (2) row-splits marked clean-move losing the ledger slice, (3) [INFERRED] rows guessed without checking readers/writers, (4) needs-probe unsafe defaults, (5) the 6th class as a ledger-escape loophole. Found (1) squarely (the certificate inversion — the single highest-value hit), (2) confirmed (world certs + edges clean-moved with no ancestry carve-out), (3) confirmed (the matrix trusted round-2's grain narrative and never checked the certificate writers/readers), (4) confirmed for 2 rows (`market_events`, `selection_hypothesis_fact`). (5) did not materialize — the 6th class is sound. One I did NOT predict: the matrix's own §c manifest-trust rule is unprincipled, and it is what caused (1).

---

## BLOCKING FINDINGS (block execution — money-path data loss)

### B1. `decision_certificates` (world) + `decision_certificate_edges` (world) — AUTHORITY INVERSION. Matrix demotes the live-money certificate authority ledger to a **deletable/rebuildable** class, and promotes the stale trade ghost to "ledger."

**Matrix's class.**
- world `decision_certificates` (3.13 GiB) → **raw-evidence (or learning-mart)**, **clean-move** (matrix trades/world tables §, world row; and §(b) "落选全量→证据/mart").
- world `decision_certificate_edges` (0.67 GiB) → **raw-evidence**, clean-move.
- trade `decision_certificates` (58,021 rows) → **ledger**, clean-move ("that's [manifest legacy_archived] wrong").
- trade `decision_certificate_edges` (105,275 rows) → **ledger**, clean-move.

**Corrected class.**
- world `decision_certificates` + `decision_certificate_edges` → **immutable-ledger** (the authority). Wholesale-preserve; a row-split to shed pure-candidate certs is permissible ONLY with the same hash-preimage-ancestor carve-out the matrix itself gave `decision_log` (never epoch-delete a certificate that is, or is a Merkle parent of, a selected/settled/VERIFIED certificate).
- trade `decision_certificates` + `decision_certificate_edges` → **DEAD (drop per manifest)**, NOT ledger. They are frozen pre-PR-S4b contamination, not the selected-grain money ledger.

**Evidence.**
1. World copy is the authority, by manifest AND code:
   - Manifest `db: world` `schema_class: world_class`, notes: *"EDLI redemption certificate ledger. This is the authority table for proof-carrying decision certificates; projections such as edli_no_submit_receipts are report conveniences."* — `architecture/db_table_ownership.yaml:1240,1257-1260`.
   - The `fact_revocations` **world** instance revokes on this table: *"decision_certificates (LIVE money-certificate integrity revocations, reason codes REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE / REVOKED_INVALID_LIVE_MONEY_PARENT_MODE)"* — `db_table_ownership.yaml:2812-2814`. LIVE money certificates live on **world**, not trade.
   - DDL is created by `init_schema` on the world DB; the comment says so verbatim: *"this world-DB instance carries decision_certificates…"* — `src/state/db.py:3675`.
   - Both writers target the world/world-class connection: the reactor builds `DecisionCertificateLedger(store.conn, schema_initialized=world_schema_initialized)` — `src/events/reactor.py:1194-1197`; the pre-executor persist writes `build_conn`, and `build_conn = live_cap_conn or trade_conn` — `src/engine/event_reactor_adapter.py:7161,7270` — i.e. the world-class live-cap connection. The **selected live-command certs also land on world**, so world is a superset (candidate + selected), not "candidate-only."
2. Trade copy is a ghost, by manifest: `db: trade` `schema_class: legacy_archived`, `created_by: init_schema_pre_pr_s4b_residual`, note *"Ghost on zeus_trades.db from pre-PR-S4b/init_schema(trade_conn) residual drift. Drop after 2026-08-09."* — `db_table_ownership.yaml:2649-2655` (and edges `:2641-2647`). No live writer targets it (both cert writers resolve to world, above). The 58,021 rows are stale.
3. World copy is read on **money-path gates**, not just audit:
   - live-canary **promotion** gate: `scripts/check_edli_live_canary_gate.py:342,365` (certs + edges).
   - **restart preflight** (money-path restart safety) verifies certificate parent chains by JOINing edges: `scripts/check_live_restart_preflight.py:1144-1166,2807`.
   - live profit audit → promotion gates: `src/events/live_profit_audit.py:594,642,658`.
   - live health: `src/control/live_health.py:1601,1697-1698` (cert⋈edge JOIN).
   - It is the **EDLI redemption** certificate ledger — the proof carried to redeem settled on-chain money.
4. Certificates are **not rebuildable** (so "learning-mart" is definitively wrong): each row is a cryptographic commitment over decision-time inputs (`payload_hash`, `certificate_hash`, Merkle `parent_edges`) — `src/decision_kernel/ledger.py:112-163`; edges store `parent_certificate_hash` and `persist_all` only re-verifies parents present in the same batch — `ledger.py:66-75,190-208`. You cannot recompute a decision-time hash after the decision-time evidence is epoch-deleted.
5. Round-2's premise the matrix inherited (`REDESIGN_v2.md:38`: "world 1.35M candidate vs trades 58K selected, not duplicate") was never checked against writers/readers. The true world `max(rowid)=1,346,474` (`probe_measurements.md:38`) — so "1.35M" is right — but the inference that world is *candidate-only* and therefore evidence/mart is false: world holds the selected LIVE money certs too (evidence 1-3). The 58K trade copy is not "the selected grain"; it is dead drift.

**Failure scenario if the matrix's class is followed.** W2 routes world `decision_certificates`+`edges` to a deletable evidence epoch / rebuildable mart and enshrines the 58K trade ghost as "the ledger." At the next epoch expiry (W5, or the W3 shedding water-line), the **live-money proof-carrying certificate authority is deleted / declared rebuildable-but-unrebuildable**: EDLI redemption proofs, live-canary promotion gating, restart-preflight parent-chain verification, and settlement skill grading all lose their source of truth; the money-ledger.db instead canonicalizes stale pre-PR-S4b certs. Detection is partial and late — preflight/promotion gates fail loud *after* deletion, but redemption-proof loss can stay silent until an on-chain settlement dispute. This is permanent money-record loss; per the realist rule I do not downgrade it. Confidence: **HIGH**.

**Note on internal inconsistency (strengthens B1).** The matrix gave `decision_log` a `+ledger slice` precisely because "round-2 explicitly warns a deletable evidence epoch must never become the sole home of bytes a settled certificate's hash depends on." That warning is *literally about certificate hash-preimage bytes* — yet the matrix applied it to `decision_log` and not to the certificates/edges themselves. Same rule, opposite treatment.

---

## MATERIAL FINDINGS (significant rework)

### M1. `decision_log` — both registered copies are `legacy_archived`, but it is a LIVE money-recovery table; and the matrix's §(c) evidence is factually wrong.

- Matrix: trades `decision_log` (7.60 GiB) → **ledger** (+evidence slice), row-split; §(c)#3 asserts *"schema_class: trade_class correct, note is copy-paste."*
- Reality: the trade entry is `schema_class: legacy_archived` (NOT trade_class) — `db_table_ownership.yaml:2626-2632`; the world entry is also `legacy_archived` — `:789`. So the matrix **misread the schema_class** it cited as its evidence.
- But `decision_log` IS live: writer `src/state/decision_chain.py:153,194` (`INSERT INTO decision_log`); read on the money **command-recovery** path `src/execution/command_recovery.py:3335,15686`, plus replay/health.
- Net: the matrix's *conclusion* (preserve as ledger) is safe, but it rests on a false premise, and it fails to surface the real hazard — a live money-recovery table whose only two registry rows both say "Drop after 2026-08-09." That manifest rot must be flagged as its own W2 gate item (a drop script keyed on `legacy_archived` would delete a live recovery table). Confidence: HIGH on the misread; HIGH that decision_log is live.

### M2. Needs-probe rows with an **unsafe delete-default** (attack #3). Probe_measurements.md resolved G3-G6 + posteriors only — none of the 6 authority probes were run, so defaults must stand alone.

- `market_events` (forecasts) → **raw-evidence** (deletable) default. It is a live `forecast_class` table (17,256 rows, `db_table_ownership.yaml:341-350`), market-lifecycle events plausibly read at settlement. Deleting before a reader check risks losing settlement-relevant lifecycle facts. Safe default would be ledger/current-cache until the reader probe runs.
- `selection_hypothesis_fact` (world) → **raw-evidence** (deletable) default. Live `world_class`, tagged by `fact_revocations` (`db_table_ownership.yaml:1799-1804,2815`). Unknown decision/settlement readership; delete-default is unsafe.
- The safe-default needs-probe rows are fine: `provenance_envelope_events`→ledger, `edli_no_submit_receipts`→ledger, `deterministic_forecast_anchors`→learning-mart (regenerable from the preserved `raw_forecast_artifacts`). Preserve/rebuild-from-preserved defaults are acceptable.
- Rule the matrix should adopt and does not: **a needs-probe row defaults to the most-preserving plausible class, never to delete.** `market_events` and `selection_hypothesis_fact` violate it. Confidence: MEDIUM (readership unproven either way — that's the point; the default must be safe under ignorance).

### M3. The matrix has no principled rule for when to trust the manifest — which is the root cause of B1.

The matrix (§c, 裁决2) declares the manifest untrustworthy for ghost labels, yet: (a) it **trusts** manifest `legacy_archived` to call trade `probability_trace_fact`/`availability_fact`/`market_topology_state` DEAD (correct, as it happens — `db_table_ownership.yaml:3111,2505,3054`), while (b) it **overrides** the manifest's *correct* authority labels for `decision_certificates` on the strength of round-2's unverified grain-story. The DEAD calls being right is luck, not method. The required rule (the team-lead already stated it): where a class rests on a manifest label OR on a round-2 assertion, re-derive from the **actual writers/readers**. Had that been done for certificates, B1 would not exist. Confidence: HIGH.

---

## MINOR FINDINGS (suboptimal but not loss-bearing)

- **Trade-ghost citations imprecise.** `edli_live_order_events` (trade) → "DEAD/evidence" cites *"Manifest: this copy is a stale duplicate"* — the trade entry does exist (`db_table_ownership.yaml:3284`) and money readers explicitly use `world.edli_live_order_events` (`src/ingest/price_channel_ingest.py:1169`, `src/state/portfolio.py:2370`), so the DEAD default is defensible, but the class tag should be `[INFERRED]`, not manifest-backed. Same imprecision pattern as M1.
- **`provenance_envelope_events` manifest rot.** Trade entry is `trade_class` but noted "Ghost… Drop after 2026-08-09" (`:3122-3128`) despite a live writer `src/state/venue_command_repo.py:3337`. The matrix's ledger+needs-probe (preserve) is right; flag the note as W2 rot so no drop script fires on it.
- **Row-count nit is NOT a finding.** The matrix's "1.35M candidate-grain" for world `decision_certificates` matches the true `max(rowid)=1,346,474` (`probe_measurements.md:38`); the census `cells=2,093,719` is the b-tree overcount. Matrix correct here.

---

## WHAT'S MISSING (gaps / unclassified-but-matters — attack #6)

- **`outcome_fact` (trade, settlement truth) is unclassified.** `schema_class: trade_class`, canonical settlement-outcome writer `harvester.py log_settlement_event` (`db_table_ownership.yaml:3086-3101`). Tiny (18 rows) so below the top-40 cutoff, but it is settlement money truth and must be routed to **ledger/money-hot** explicitly — the matrix's "small tables are already fine" assumption silently omits it. A settlement-truth table must never be left to inference.
- **The "small = fine" assumption is unstated-risk for the money-hot control surface.** `position_current/lots`, `venue_commands`/`_events`, `settlement_commands`, `collateral_reservations`, `trade_decisions`, `execution_fact` are correctly cited as small, but the matrix should *positively assign* them to money-hot rather than infer their safety from size. `trade_decisions` in particular is the W0 fix-loop table (`REDESIGN_v2.md:75`) — money-hot, and it should say so.
- **No explicit ancestry-preservation contract for any certificate/edge split.** Even after B1 is corrected, if world certs are ever row-split, the plan needs the concrete predicate ("preserve any cert that is a Merkle ancestor of a selected/settled/VERIFIED cert") wired to `decision_certificate_edges`. The matrix names the risk for `decision_log` but never specifies the predicate for the certificate DAG that actually carries it.

---

## AMBIGUITY RISKS (plan-review)

- *"world decision_certificates → raw-evidence (or learning-mart)"* → **Interpretation A:** deletable epoch; **B:** rebuildable mart. Both are lossy; there is no reading of this row that preserves the live-money certificate authority. Whichever an executor picks, money-proof loss follows. (This is B1; the "(or)" makes it worse, not safer.)
- *"trades decision_certificates → ledger … manifest legacy_archived is wrong"* → **A:** migrate the 58K trade rows into money-ledger.db as canonical; **B:** treat as authority for reconciliation. Both canonicalize a ghost. The wrong interpretation is guaranteed because the matrix asserts the ghost is authoritative.

## MULTI-PERSPECTIVE NOTES

- **Executor:** cannot safely execute B1/M1/M2 rows from the text alone — the class tags point at the wrong physical copy (`decision_certificates`) or rest on a misread schema_class (`decision_log`). An executor following only this matrix would delete the certificate authority.
- **Stakeholder (money-path owner):** the matrix's success criterion (co-locate money-hot, epoch-delete evidence) is measurable, but B1 makes the certificate ledger a false negative — it looks like "evidence" and would be reclaimed.
- **Skeptic:** the strongest defense of the matrix is round-2's grain-story (`REDESIGN_v2.md:38`). That defense fails because round-2 never checked writers/readers, and the manifest + `fact_revocations` + gate readers all put the live-money certs on world. The rejected alternative (world = authority) is the correct one and was hand-waved.

## VERDICT JUSTIFICATION

One BLOCKING finding (B1) that permanently loses live-money certificate records is sufficient for **REJECT** on its own; it is compounded by M1/M3 (same root cause, different tables). What would upgrade to REVISE/ACCEPT-WITH-RESERVATIONS: (1) de-invert `decision_certificates`/`decision_certificate_edges` — world = immutable-ledger (authority), trade = DEAD-drop; (2) restate `decision_log` on correct evidence and flag its double-`legacy_archived` rot; (3) flip `market_events` and `selection_hypothesis_fact` needs-probe defaults to preserve; (4) adopt the writers/readers re-derivation rule (M3) and positively assign `outcome_fact` + the money-hot control surface. With those four, the matrix is **safe-with-corrections** — the remainder of its classifications verified sound. Realist check applied: B1 is NOT downgraded (data loss, explicit rule); no other finding was inflated — the many correct rows (collateral, book_hash, position/venue facts, calibration_pairs, settlement_outcomes, opportunity_events split, 6th class) are acknowledged as genuinely right.

**One-line verdict:** NOT-SAFE to drive wave execution as-is; safe only with the named corrections, the load-bearing one being the `decision_certificates`/`decision_certificate_edges` authority de-inversion (world = ledger, trade = dead ghost).

## OPEN QUESTIONS (unscored)

- Does `_persist_live_command_certificates_before_executor_submit` ever run with `live_cap_conn=None` (so `build_conn` falls back to `trade_conn`)? If yes, a *small* live subset of selected command certs may also reach the trade copy — which would make the trade copy "live-but-partial ghost," not purely stale. It does not change B1 (world remains the authority superset and must be preserved), but it would mean the trade copy needs a reconciliation-extract before its 2026-08-09 drop rather than a blind drop. Worth a one-day writer probe.
- `probability_trace_fact` (world, matrix → raw-evidence) is tagged by `fact_revocations` (world) — confirm no live decision-path reader treats a revocation as authority before epoch-deleting it. Low-confidence; likely diagnostic-only.
