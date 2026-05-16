# Runtime Improvement Engineering Package — Live Status

Session date: 2026-05-15 (final pre-merge state)
PR: #119 — 257 files, +50351/-281, 117 commits since main

## Packet Status Table

| Packet | Phase | Critic Verdict | Notes |
|---|---|---|---|
| P1 (topology v_next phase1 additive) | LANDED | FIX_REQUIRED → RESOLVED | P1.1 (data layer + profile_loader + intent_resolver), P1.2 (admission_engine + 4 helpers), P1.3 (severity_overrides + 14-profile binding YAML + public API) |
| P2 (companion_required mechanism) | LANDED | REVISE → ACCEPT_WITH_FOLLOWUP_BLOCKER → RESOLVED | P2.1: BindingLayer companion fields + _check_companion_required + 8 probe regression tests + §3.0 composition rule extension |
| P3 (topology v_next phase2 shadow) | LANDED | REVISE → cleanup applied | P3.1 (divergence_logger atomic JSONL), P3.2 (divergence_summary + dual-metric P4 gate), P3.3 (CLI wire-up + 17 shadow probe tests + dual-import shim) |
| P4 (topology v_next phase3 cutover) | DEFERRED — evidence-gated | n/a | See "P4 Disposition" below — requires 14d shadow data; no code in this PR |
| P5 (maintenance worker core) | LANDED | REVISE → SEV-1 patches applied → composition fix landed (`f19e22bf26`) | P5.0 SCAFFOLD + P5.1 (install_metadata/refusal/kill_switch/guards/engine), P5.2 (subprocess_guard, git_op_guard, gh_op_guard, ActionValidator), P5.3 (rules parser, task_registry, evidence_writer), P5.4 (60-fixture catalog + integration tests), P5.5 (provenance, apply_publisher, scheduler_bindings, cli/entry) |
| P6 (maintenance worker zeus binding) | LANDED | — | bindings/zeus/{config.yaml, safety_overrides.yaml} + launchd plist + install script + plist tests + manifest |
| P7 (lore indexer + promoter) | LANDED | — | lore_indexer (walk docs/lore/**), lore_promoter (CLI move drafts to topic dirs), lore_reverify (subprocess sandbox + signature check), script_manifest registration |
| P8 (3 BLOCKING authority docs) | LANDED | — | All Hypothesis B (process gap, no gate at doc-creation); 3 ref-replacement entries cleared |
| P9 (authority inventory v2 Cohort 7) | LANDED | REVISE → 7 structural fixes → SCAFFOLD landed | P9.1: generator + tests + docs_registry + script_manifest registration |
| P10 (topology_doctor consolidation) | PLAN-ONLY | n/a | 18 modules inventoried; implementation deferred to post-P4 per cross-packet invariant |
| Pdrift (CLAUDE.md drift remediation) | LANDED | — | 3 valid fixes + audit-of-audit caught 50% self-error rate on STALE classification |

## P4 Disposition (evidence-gated, NOT in this PR)

P4 (`topology_v_next_phase3_cutover_pilot`) requires evidence this PR cannot supply:

**Entry criteria** (per PACKET_INDEX.md):
1. 14 days of `--v-next-shadow` data with ≥500 admission calls (P3 shadow blocking output)
2. Per-profile agreement rate >95% on the `companion_required` mechanism (P3.2 dual-metric P4 gate: `agreement_pct_excluding_skips >= 0.95 AND skip_honored_rate < 0.20`)
3. Friction pattern hit counts stable or declining

**This PR ships the prerequisites** (P3.1 divergence_logger, P3.2 divergence_summary + dual-metric P4 gate analyzer) but NOT the cutover code itself. The cutover is a future PR.

**Operator next steps after merge**:
1. Enable `--v-next-shadow` flag on production `topology_doctor.py` invocations (no behavior change; logs divergence to `state/topology_v_next_shadow/divergence.jsonl`)
2. Let shadow data accumulate for ≥14 days (target window: 2026-05-16 → 2026-05-30)
3. Run `python -m scripts.topology_v_next.divergence_summary` to evaluate the dual-metric gate
4. If gate passes for ≥1 admission path: open P4 packet with concrete cutover candidates. Recommended first targets per PACKET_INDEX.md §P4: `docs` write-intent admission and `docs/operations packet creation` (both low blast radius)
5. If gate fails: P3 critic-cycle on the divergence patterns; fix v_next and re-collect

**Rollback recipe** (will be specified in P4 packet): single CLI flag flip from `--v-next-authoritative` back to current default.

P4 is OUT OF SCOPE for this PR's merge gate. No P4 code is expected. No P4 review is needed. Future P4 packet will reference this PR's P3 shadow output as input.

## Critical META Findings This Session

1. **P1.0 SCAFFOLD fabricated diff**: opus critic grep-confirmed `_assemble_navigation_payload` does not exist in scripts/*.py. SCAFFOLD §3 wire-up was fabrication. Caught by critic; reframed to structures-only.
2. **P2.0 SCAFFOLD unsolvable-trap antipattern**: gate's MISSING_COMPANION remediation incompatible with composition_rules pipeline ordering. Exactly the user's "如何避免我们添加的东西成为障碍" antipattern realized in our own gate. Single §3.0 fix resolves.
3. **P5.0 sidecar-quarantine antipattern caught**: validator wrote SELF_QUARANTINE on every FORBIDDEN_* return → single buggy rule bricks agent indefinitely. Critic flagged; Path A/B separation enforced (Path A = pre-mutation refuse, Path B = post-mutation detector).
4. **P5.5 composition defect**: 722 tests passed but ApplyPublisher.publish() never called from cmd_run, check_remote_url_allowlist unreachable, 3 op guards never imported. Pattern: unit-correct pieces but integration broken. Fixed via wire-in patch `f19e22bf26`.
5. **Drift audit had 50% self-error**: original audit haiku misattributed source files + miscounted JSON keys. Pdrift verification reclassified 3 of 6 "STALE" claims as CORRECT. Audit-of-audit antibody pattern works recursively.
6. **MIGRATION_PATH authority telescoping (caught + reverted)**: P1.0 worker unilaterally telescoped Phase 1+2; critic flagged as governance precedent risk; revision reverts to structures-only.
7. **Codex P1 pUSD/CTF allowance collision**: `polymarket_v2_adapter.py:553` return payload's `pusd_allowance_micro` was overwritten by CTF positions loop's `allowance_raw` rebinding. Fixed via separate `pusd_allowance_raw` variable + regression test (commit `75630214e1`, thread resolved).

## Architectural Lessons Surfaced

- **Trust-but-verify is recursive**: applies to our own audit subagents, not just outside data
- **Critic dispatch ROI very high on architectural SCAFFOLDs**: 4 of 4 SCAFFOLDs in this session had ≥1 SEV-1 caught by opus critic that would have shipped
- **Composition-defect vs unit-defect**: tests passing != correct on safety-critical surfaces — wire-up tests are mandatory
- **Plain executor (not namespaced) for SendMessage-resumable agents**: oh-my-claudecode:* agents pin literal model IDs in frontmatter, fail to resume cleanly
- **Sub-packet decomposition mandatory at SCAFFOLD time**: forces independently-testable cuts (per critic M6 on P1.0)
- **Opus revision dispatches systematically timeout**: 3 of 4 opus revision dispatches in this session hit stream idle timeout; sonnet succeeds with same brief

## Merge Readiness

| Gate | Status |
|---|---|
| All packet specs disposed (CRITIC_REVIEW_DISPOSITION.md) | ✅ RESOLVED (3 SEV-1 + 7 SEV-2 + 5 SEV-3) |
| All implementation packets P1.1-P9.1 landed on deploy branch | ✅ |
| Codex P1 review comment resolved | ✅ commit `75630214e1`, thread resolved |
| Full-PR implementation critic | 🟡 in flight (opus, fresh-context) |
| P4 disposition documented | ✅ this STATUS.md |
| Working tree clean | ✅ except docs/operations/AGENTS.md (M) + new audit packet (??) |
