# Codex Review Prompt After P0

Review the implemented P0 diff against the reconstruction package.

## Review goals

- did boot surfaces become truthful about visibility?
- did archives stop acting like a live visible docs subtree?
- is `current_state.md` now a live control pointer instead of a diary?
- did the patch avoid source/runtime/graph-body widening?
- did topology receive only the minimal safe update?
- were unrelated dirty files preserved?

## Deliverable

Return:

1. pass / fail / pass-with-followups
2. top 5 residual problems
3. whether P1 should proceed now, later, or not at all
4. any `LOCAL_ADAPTATION` that was sensible and should be kept
