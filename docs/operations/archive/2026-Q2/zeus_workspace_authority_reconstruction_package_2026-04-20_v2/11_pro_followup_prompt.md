# Pro Follow-up Prompt After P0

You are performing a second-pass Pro review after P0 of the Zeus workspace authority reconstruction package has been implemented on `data-improve`.

## Your job

Review the actual diff and decide whether the repo is ready for P1.
Do not simply compare prose style.
Judge whether the patch successfully realigned the visible boot surface with the repo's objective authority model.

## Review frame

Use this mental model:

- Zeus is both a runtime machine and a workspace change-control machine.
- Machine manifests are durable law.
- Current-state is live control pointer, not narrative diary.
- Code Review Graph is first-class derived context, not authority.
- Archives are historical cold storage, not default-read context.

## Review questions

1. Did root/docs boot surfaces become truthful about visible vs hidden surfaces?
2. Did the patch correctly demote archives without erasing historical access?
3. Did it correctly elevate graph/context engines without making them authority?
4. Is `docs/operations/current_state.md` now thin enough to remain stable?
5. Is the topology update minimal and correct, or did it leave a machine/prose mismatch that P1 must fix immediately?
6. Did the patch avoid widening into runtime/source behavior?

## Output required

- verdict: `proceed_to_p1` / `p0_needs_fixups` / `stop_and_rethink`
- top 7 findings in priority order
- exact file-level objections if any
- whether `docs/archive_registry.md` is the right visible history interface
- whether P1 should include schema/checker/test changes now or later
