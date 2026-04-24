# Codex Review Prompt — After P0

Review the P0 lane repair diff.

Focus areas:
1. Does navigation return useful route context when unrelated global drift exists?
2. Are direct blockers separated from repo-health warnings?
3. Does strict/global health still expose full drift?
4. Does closeout still block changed-file and companion obligations?
5. Did the patch hide or downgrade real blockers?
6. Are CLI and JSON outputs backward-compatible enough?
7. Are tests deterministic, or do they depend on current live drift?
8. Did any runtime/source behavior change?

Required review output:
- approve/reject,
- semantic concerns,
- noise concerns,
- ownership concerns,
- drift risks,
- missing tests,
- recommended follow-up before P1.
