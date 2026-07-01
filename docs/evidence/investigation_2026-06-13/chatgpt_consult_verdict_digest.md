# ChatGPT first-principles consult — verdict digest (2026-06-13)

```
Created: 2026-06-13
Authority basis: ChatGPT Pro consult REQ-20260613-225646-71a908 on
  docs/archive/2026-Q2/operations_historical/system_investigation_workflow_2026-06-13.md (gisted).
Status: ADVISORY. Claude Code verifies locally; ChatGPT never decides correctness.
Full verbatim answer: in session transcript (operator-pasted).
```

## Headline verdict
**Do NOT launch the ~140-agent flat fan-out.** It repeats the prior failure (broad edge-interpretation while the system isn't materializing candidates). Replace with a **GATED** plan: pinned-SHA + pinned-DB **stage-funnel + replay** FIRST (~18–26 agents), fan out only on escalation.

**Predicted first cut (medium-high confidence):** restore/replace the candidate-materialization bridge out of opportunity selection / unified bin-selection, then **prove by replay** that active snapshots again produce candidate evaluations → `edli_no_submit_receipts` or `venue_commands`. Do NOT spend first on calibration / settlement-alpha / rebuild — they're downstream of the silence.

## Edits adopted into the workflow (v2)
1. **Mandatory Gate-0 stage-funnel + replay** before any fan-out. The per-cycle funnel `active family → tradable token → fresh forecast → executable book → opportunity row → selected bin → candidate binding → decision eval → no-submit receipt OR venue command → adapter → fact`; first zero/nonzero transition = the start point. This is the single highest-value, currently under-specified artifact.
2. **Two new money-path angles:** (R1) contract universe / instrument identity / market lifecycle (token IDs, bin boundaries, active/closed/resolved, one-hot settlement); (R11) capital / portfolio / collateral / risk-allocator / account readiness.
3. **5 populations, not 4:** P-A clean-room (selective), **P-B-blind** (symptom+files only, no boundary hypothesis), **P-B-diff** (sees boundary commits, must prove last-good/first-bad), P-C empirical, **P-E execution/data witness** (black-box replay + synthetic-candidate injection + raw settlement regrade — the guard against "DB self-reports the same bias").
4. **Provenance pinning is mandatory** (concurrent-edit hazard is real): pin `code_sha`, `.backup` the 3 DBs, hash DBs+config, stamp every output `{code_sha, db_hashes, config_hash, asof_utc}`. Claims without provenance are invalid.
5. **Event-level empirical protocol** (unit = city-date market family, NOT the K individual contracts — counting K NO-contracts as K wins IS the base-rate illusion): log/Brier/RPS proper scores; benchmarks = market-implied + walk-forward best-model + simple ensemble + climatology; purged walk-forward (group-split by event_id, embargo); q_lcb for eligibility, q for calibration scoring; adverse-selection markout on fills; survivorship denominators U_all…U_fill; n_eff event-cluster power rule (≈200–300 events for 5¢, ≈550–800 for 3¢; <100 events ⇒ exploratory; wide CI ⇒ UNDERPOWERED not "no edge").
6. **Intervention-proof refuter** (replaces majority vote): a mechanical `ROOT_CAUSE` must pass all 7 — stage-locality, temporal-fit, breadth (≥most of 500 markets), reproduction, **minimal-intervention advances the same replayed market to the next stage**, money-path-movement, no later-stage contradiction. One failed kill-test demotes it. Edge claims auto-`REFUTED` without point-in-time + market benchmark + after-cost + separated denominators.
7. **Executable synthesis contract** (not a report): `ordered_first_cut[]` with affected_stage, evidence, local_verification_steps, minimal_patch_spec, expected_stage_delta(before/after), risk_cap(paper_first), kill_criteria, fanout_gate; plus a targeted-fix-vs-rebuild decision rule and a keep-invariants list.
8. **Taxonomy:** merge A14+A16 → one minimal-kernel/rebuild angle; merge empirical A3/A12/A13 → one evidence-provenance angle (stops an edge claim surviving by hopping between "skill"/"grading"/"survivorship").

## Right-sized budget (ChatGPT)
Gate 0 stage-funnel+replay (3–5) → Gate 1 narrow forensic (8–10) → Gate 2 edge smoke (3–5) → Gate 3 refute+synth (4–6) = **18–26 agents**. Escalate to the full fan-out ONLY if: provenance missing so the dead stage can't be pinned; replay vs synthetic contradict; the minimal selector/candidate patch doesn't move the money path; multiple independent dead stages proven; edge smoke strongly-negative after-cost with adequate n_eff; or contract-identity/settlement correctness materially uncertain.

## Local-verification caveat (Claude Code, NOT ChatGPT's word)
ChatGPT predicts `b1825c4a07` deleted the *candidate materializer*. The commit subject is "delete the opportunity-book selector **on/off gate** (one selection path)" — i.e. removed a toggle, collapsed to one path; that may keep materialization intact. **G0.3 (P-B-diff) + G0.4 (replay) must establish which — do not assume the materializer is gone.**
