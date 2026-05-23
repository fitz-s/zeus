# WAVE-2 PHASE CRITIC — Adversarial Review

**Date**: 2026-05-17
**Reviewer**: opus critic, fresh context (agent a36a08acba7011a7c)
**Mode**: ADVERSARIAL (escalated after CRIT-1 surfaced)
**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17`
**HEAD at review time**: `e0f4cbe1c9` (90 files, 12,745+/1,395−, 56 commits ahead of origin/main)

---

## Verdict: NEEDS-FIX → APPROVED-FOR-MERGE after CRIT-1 + MAJ-2 + MAJ-3 fixes inline

- **CRIT-1**: F43 antibody deletion (silently wiped by K1-sweep cherry-pick `git checkout --theirs`) — **FIXED inline** at commit `59f902f60a`; sed-break/restore meta-verified (FAIL → 13/13 PASS).
- **MAJ-1**: F109 consolidator has no production caller — **DISPATCHED** to sonnet for daemon-boot wiring.
- **MAJ-2**: `chain_by_token.get(token_id, 0.0)` default-zero footgun — **FIXED inline** (token-absence now classifies DIVERGENT, not OVERBOOK).
- **MAJ-3**: F107 `OPS_FORENSICS.md:129` operator-attended backfill wording — **FIXED inline** (reworded to point at programmatic backfill helper pattern; deferred to WAVE-3).
- **MIN-1**: `live_drift` marker has no consumers — RUN-12 antibody (just landed) tags `@pytest.mark.live_drift`; consumers now exist.
- **MIN-2**: `WRITER_LOCK_DEFER_REVIEW` has no auto-expiry — accepted; tracked under WAVE-3 carry-forward per `F22_WRITER_LOCK_FIX.md`.

---

## Probe-by-probe results (verbatim from critic)

| # | Probe | Verdict | Action taken |
|---|---|---|---|
| 1 | K-A writer fan-out | PASS | — |
| 2 | K-B reader qualifiers | PASS | — |
| 3 | F43 antibody meta-verify | **FAIL (CRIT-1)** | FIXED `59f902f60a` |
| 4 | Karachi-bridge TRIGGER safety | PASS | — |
| 5 | F109 consolidator safety | PARTIAL (MAJ-2) | FIXED inline (chain_by_token absence check) |
| 6 | F109 UNIQUE INDEX deploy ordering | PASS w/ caveat (MAJ-1) | DISPATCHED boot wire |
| 7 | F22 antibody meta-verify | PASS | — |
| 8 | F7-followup decision_id safety | PASS | — |
| 9 | F44 obs_v2_live_tick wiring | PASS | — |
| 10 | Observability registry coherence | PASS | — |
| 11 | Cross-cutting regression | PASS (216+; 2 failures pre-existing on origin/main) | — |
| 12 | No-manual-precedent audit | PASS w/ NIT (MAJ-3) | FIXED inline (OPS_FORENSICS reword) |

---

## Karachi blast inventory per K-axis (per critic)

| K-axis | Blast on c30f28a5-d4e |
|---|---|
| K1 writer/reader | NONE |
| Observability | NONE |
| Karachi-bridge (TRIGGER + synthesizer) | LOW-DEFENDED (synthesizer covers on next lifecycle event) |
| Track G reconciliation | NONE |
| F22 writer-lock | NONE |
| F109 consolidator | NONE (Karachi is single-row; NO-OP) |

---

## Post-fix verification

- F43 antibody: 13/13 PASS + sed-break/restore meta-verified
- F109 consolidator (MAJ-2): 17/17 antibody PASS post-fix
- Cross-cutting: 30/30 (F109 + K1 reader) after MAJ-2 fix
- MAJ-1 dispatched to sonnet `a<TBD>` for boot wire

---

## Resolved verdict: APPROVE-FOR-MERGE pending MAJ-1 landing + final sanity sweep
