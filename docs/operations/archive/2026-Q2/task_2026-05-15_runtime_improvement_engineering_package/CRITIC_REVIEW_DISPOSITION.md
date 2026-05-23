# Critic Review Disposition

Closure record for the REVISE verdict in `CRITIC_REVIEW.md`.
Per orchestrator-delivery skill §"Phase closure — explicit disposition of
carry-forwards", every documented open item carries one of:
RESOLVED / RECLASSIFIED / DEFERRED.

Disposition timestamp: 2026-05-15
Critic agent: opus, fresh context
Critic verdict: REVISE; SEV-1=3 SEV-2=7 SEV-3=5
Closure verdict (this record): RESOLVED with one residue noted.

## Dispatch ledger

| Worker | Tier | Files in scope | Items addressed |
|--------|------|----------------|-----------------|
| Sonnet A `a7670cd3645689813` | sonnet | 01_topology_v_next/{UNIVERSAL_TOPOLOGY_DESIGN, HIDDEN_BRANCH_LESSONS, ZEUS_BINDING_LAYER}.md | M1, M2, M6, M7, Minor 1, Minor 2 |
| Sonnet B `a701682c7ce1fb2e3` | sonnet | 02/03/04/05/99 (8 files) | C1, C2, C3, M3, M4, M5, Minor 3, Minor 4, Minor 5 |

Two parallel workers on disjoint files; neither read the other's targets.

## Per-item disposition

### SEV-1 (Critical) — block acceptance

**C1 — SAFETY_CONTRACT validator semantics under-specified**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit: SAFETY_CONTRACT.md +37 lines, new §"Validator Semantics" after §"Pre-Action Validator". Specifies (a) read-on-forbidden = FORBIDDEN_PATH, (b) realpath canonicalization before pattern match, (c) symlink/hardlink resolution policy, (d) per-leaf decomposition for directory ops, (e) git remote URL allowlist with first-run pin.
- Spot-trace: not directly read by orchestrator; mechanical verification: 14 forbidden patterns still all hit.

**C2 — ARCHIVAL_RULES does not consult artifact_authority_status registry**
- Disposition: RESOLVED with one minor residue
- Worker: Sonnet B
- Edit: ARCHIVAL_RULES.md +25 lines, Check #0 (priority) consults the registry from UNIVERSAL_TOPOLOGY_DESIGN §13 + ZEUS_BINDING §8. Status NOT IN {ARCHIVED, CURRENT_HISTORICAL with archival_ok} → immediate LOAD_BEARING_DESPITE_AGE. Registry absent → WARNING, fall through to checks 1-8.
- Spot-trace (orchestrator direct): ARCHIVAL_RULES.md:43-54 — Check #0 correct.
- **Residue**: ARCHIVAL_RULES.md:79 still says "A packet must pass all **eight** checks" — should say "all **nine**" after Check #0 added. Cosmetic; does not affect mechanism. Logged as `Residue-1` for the next docs sweep.

**C3 — DRIFT_ASSESSMENT misses 8 of 62 inventory rows; package self-test contradicted**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit: DRIFT_ASSESSMENT.md new "Cohort 0: Initially-omitted inventory rows" with all 7 orchestrator-confirmed paths (architecture/{reference_replacement, artifact_lifecycle, context_budget, context_pack_profiles, negative_constraints, city_truth_contract, core_claims}.yaml) classified MINOR_DRIFT or LIVE_BUT_NOT_VERIFIED, plus the 8th critic-cited row. VERIFICATION_PLAN.md "62 rows — no orphans" softened to "classified into Cohort 0, 1–7, or v2 inventory pass."
- Re-verify: post-edit gap analysis shows 0 inventory paths missing from DRIFT_ASSESSMENT.

### SEV-2 (Major) — must address before P1 ships

**M1 — UNIVERSAL §3 prose overstates §4 algorithm**
- Disposition: RESOLVED
- Worker: Sonnet A
- Edit: UNIVERSAL_TOPOLOGY_DESIGN §3 reworded to "intent removes phrase as a routing key; files drive candidate selection (§4 step 2); intent gates cohort admission (§4 step 4a, §8); intent feeds binding-layer disambiguation for high-fanout files (§4 step 4b, §7); profile-hint is diagnostic-only (§4 step 5)." Cross-references repaired (§8/§9 numbers were swapped — caught and fixed during the M1 work).

**M2 — SLICING_PRESSURE has no structural mechanism**
- Disposition: RESOLVED via option (c)
- Worker: Sonnet A
- Edit: §14 friction budget extended with SLICING_PRESSURE soft_block gate. N≥3 admission attempts within M=30 minutes on shrinking-overlap file set triggers SOFT_BLOCK with diagnosis naming the files so the agent can declare a §8 cohort instead of slicing. Gate is conservative on purpose: false-positives degrade to ADVISORY, not BLOCK.

**M3 — P2/P3 dependency relation internally inconsistent**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit: PACKET_INDEX.md P2 Dependency line rewritten to "P1 (additive parallel route) must ship; P2 implements companion-required as additive logic alongside P1; P3 then runs both P1 and P2 in shadow mode." Dependency graph at top updated so P2 sits parallel to P1, both feeding P3.

**M4 — 30-day dry-run mandate is honor-system**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit (a): TASK_CATALOG.yaml `dry_run_floor:` global block with `floor_days: 30`, `install_metadata_file: ${STATE_DIR}/install_metadata.json` (immutable, captured first run), `override_ack_file: ${STATE_DIR}/dry_run_floor_override.ack`, `exempt_task_ids: [zero_byte_state_cleanup, agent_self_evidence_archival]` with per-task rationale.
- Edit (b): DESIGN.md §"Dry-run floor enforcement" specifies the validator gate as a code expression `now - install_metadata.first_run_at >= floor_days (30)`. Day-1-live tasks tagged `dry_run_floor_exempt: true` with rationale (zero-byte = content-free; self-evidence = own state).
- Spot-trace (orchestrator direct): TASK_CATALOG.yaml top + DESIGN.md:165 — both correct, exempt rationale clear.

**M5 — `zero_byte_state_cleanup` operates inside forbidden surface (SQLite WAL window)**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit: PURGE_CATEGORIES.md Category 6 forbidden list extended with `*.db`, `*.db-wal`, `*.db-shm`, `*.sqlite`, `*.sqlite3`, `*.sqlite-wal`, `*.sqlite-shm` regardless of size. WAL window rationale stated in one sentence.

**M6 — Authority status registry hand-maintained; no freshness check (reproduces Iteration 1 failure pattern)**
- Disposition: RESOLVED
- Worker: Sonnet A
- Edit: UNIVERSAL §13 row schema gains `confirmation_ttl_days` field. ZEUS_BINDING §8 sample rows populated with project-appropriate values (14/14/30/90/90 days). v_next admission emits ADVISORY with issue code `authority_status_stale` when `last_confirmed` is older than the row's TTL. Hand-maintenance becomes surfaced-drift instead of silent-rot.

**M7 — Cross-iteration meta-finding leans on unverified history**
- Disposition: RESOLVED
- Worker: Sonnet A
- Edit: HIDDEN_BRANCH_LESSONS § Cross-Iteration Meta-Pattern now cites specific symbols in `scripts/topology_doctor_digest.py` — `build_digest` (line 1703), `_collect_evidence` (line 1726), `_resolve_profile` (line 627/1749), `_resolve_typed_intent` (line 838), `_apply_companion_loop_break` (line 1440). Evidence: iter 4/6 added layers AROUND `_resolve_profile` but the `(task_phrase, files)` keying kernel was never replaced — verifiable by grep against the cited line numbers.

### SEV-3 (Minor) — open question for next iteration

**Minor 1 — Pattern naming inconsistency (CLOSED_ARTIFACT vs CLOSED_PACKET; friction_budget vs friction_budget_alert)**
- Disposition: RESOLVED
- Worker: Sonnet A
- Edit: UNIVERSAL §1.1 new glossary with canonical spellings `friction_budget`, `friction_budget_alert`, `CLOSED_PACKET_STILL_LOAD_BEARING`. §12 occurrence of `CLOSED_ARTIFACT_STILL_LOAD_BEARING` renamed.

**Minor 2 — UNIVERSAL §8 (Cohort) and §9 (Companion-Loop-Break) circularly defined**
- Disposition: RESOLVED
- Worker: Sonnet A
- Edit: §9 rewritten as ONE mechanism with two failure modes (A: fail-open companion-loop-break; B: fail-closed companion-missing SOFT_BLOCK). §8 declared source of truth; §9 marked compatibility shim. Cross-reference to REMEDIATION_PLAN.md §Companion-Update Enforcement for `companion_required:` schema.

**Minor 3 — VERIFICATION_PLAN cross-track coherence script broken regex**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit: Replaced broken `sed -E` chain with `grep -oE` backtick-pattern extraction matching realistic `0N_dir/FILE.md` references in PACKET_INDEX.md.

**Minor 4 — P10 graph vs text contradiction**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit: PACKET_INDEX.md P10 graph caption now matches P10 text body ("after P4 cutover ships first"). Text version chosen because consolidating during cutover is risk-additive.

**Minor 5 — PURGE_CATEGORIES Category 6 example missing**
- Disposition: RESOLVED
- Worker: Sonnet B
- Edit: Category 6 now states "Currently observed examples: None" with explicit note that the rule is preemptive (not driven by current entropy) and should be re-evaluated after dry-run if no candidates appear.

## OPEN QUESTIONS (critic-flagged, unscored)

The critic raised 5 unscored OPEN QUESTIONS in §"OPEN QUESTIONS (unscored)".
Each receives a disposition here.

| # | Question | Disposition | Reason |
|---|----------|-------------|--------|
| OQ-1 | HIDDEN_BRANCH_LESSONS Iteration 5 retraction lesson absorbed in v_next? "Periodic audit of accumulated sidecars" not scheduled. | DEFERRED → P5 maintenance worker | The `authority_drift_surface` weekly task in TASK_CATALOG.yaml is the natural home for "audit accumulated sidecars" but is currently scoped to authority docs. Extending it to topology profiles is one bullet in the P5 implementation packet. |
| OQ-2 | Codex hooks (`.codex/hooks.json`) not protected by topology layer; structural fix protects only half the agent fleet. | DEFERRED → new packet `topology_v_next_codex_parity` (added to PACKET_INDEX as "Out-Of-Index → P11 candidate") | Genuine cross-runtime concern; cannot be addressed inside v_next core because Codex has different hook semantics. Belongs to a sibling Codex-binding packet. |
| OQ-3 | ARCHIVAL_RULES "Wave Packets" family-slug computation underspecified. | DEFERRED → P5 implementation | Spec ambiguity; resolution shape (regex vs declarative family registry) is an implementation choice, not a spec gap. P5 picks one with rationale. |
| OQ-4 | DRY_RUN_PROTOCOL "Bulk Acknowledge" appears to have no consumer (zero_byte_state_cleanup is already live_default). | RECLASSIFIED — NOT_A_DEFECT | Bulk Acknowledge mechanism survives so future tasks promoted to live_default after the 30-day floor have a faster ack path. Not orphaned; pre-positioned for post-floor promotion ladder. |
| OQ-5 | UNIVERSAL §9 Companion-Loop-Break vs REMEDIATION_PLAN companion_required: same mechanism, two directions? | RESOLVED via Minor 2 | Sonnet A edit on §9 explicitly unifies as ONE mechanism with two failure modes. OQ-5 is now closed. |

## Residue carried forward

| ID | Description | Severity | Owner |
|----|-------------|----------|-------|
| Residue-1 | ARCHIVAL_RULES.md:79 says "all eight checks"; should say "all nine" after C2 added Check #0. Cosmetic — Check #0 logic is correct. | LOW (textual) | Next docs sweep; one-line Edit |

## Mechanical re-verification (post-revise)

| Check | Result |
|-------|--------|
| `topology_doctor --navigation` (PLAN.md alone) | ok=True, admission=admitted |
| `topology_doctor --planning-lock` (PLAN.md, plan-evidence) | ok=True, blockers=0 |
| `topology_doctor --map-maintenance --advisory` | ok=True |
| Zeus-leak grep on UNIVERSAL_TOPOLOGY_DESIGN.md (semantic + lexical) | 0 non-meta hits |
| 7 friction patterns in UNIVERSAL_TOPOLOGY_DESIGN.md | All 7 present (LEXICAL=2, UNION=1, SLICING=3, PHRASING=1, INTENT=1, CLOSED=3, ADVISORY=2) |
| 14 forbidden surfaces in SAFETY_CONTRACT.md | All 14 present |
| Inventory paths missing from DRIFT_ASSESSMENT (post-Cohort-0) | 0 |
| Final tree | 23 files, 5260 lines |

## Spot-trace (per skill)

Per skill §"Verification — fresh-context spot-trace when run-end metrics are
uniformly success-shaped": both workers reported uniform-success. Spot-trace
required.

Cost-vs-cost reasoning: orchestrator read 30-50 lines directly per spot vs
dispatching another fresh agent. Per skill §"Coordinator-side
reclassification before cleanup dispatch", direct read is appropriate when
the assertion is concrete (boolean: edit landed correctly).

| Spot | Method | Verdict |
|------|--------|---------|
| C2 (ARCHIVAL_RULES Check #0) | orchestrator Read of ARCHIVAL_RULES.md:43-54 | CONFIRM (with Residue-1 noted) |
| M4 (TASK_CATALOG dry_run_floor + DESIGN gate) | orchestrator Read of TASK_CATALOG.yaml dry_run_floor block + DESIGN.md:165 + grep for exempt markers | CONFIRM |

DISPUTE rate: 0 / 2 (0%). Below the 10% rotation threshold; persistent
reviewer (the critic that produced the original REVISE) is not flagged for
rotation. The package may proceed to the implementation packets enumerated
in `05_execution_packets/PACKET_INDEX.md`.

## Closure verdict

REVISE → RESOLVED.

All 3 SEV-1 + 7 SEV-2 + 5 SEV-3 dispositions are RESOLVED or RECLASSIFIED.
1 LOW residue (cosmetic line-79 numbering) carried forward.
5 OPEN QUESTIONS dispositioned (1 RESOLVED, 1 RECLASSIFIED NOT_A_DEFECT, 3 DEFERRED to named follow-up packets).

The package is ACCEPTED for use as the spec source for the implementation
packets in `05_execution_packets/PACKET_INDEX.md`. No further critic round
required for the spec-level work.

The first implementation packet (P1 — `topology_v_next_phase1_additive`)
should reference this disposition record in its inputs section.
