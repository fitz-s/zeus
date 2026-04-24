# Codex Closeout Prompt

Prepare a clean packet summary for the active workspace authority reconstruction packet.

## Requirements

- preserve unrelated dirty work
- list exactly what was staged and committed
- list exactly which package packet(s) were executed
- include validation command outputs in compact form
- note any deferred items for the next packet
- note any `LOCAL_VERIFICATION_REQUIRED` items still unresolved
- do not claim runtime behavior changed unless it actually did

## Deliverable format

1. packet(s) executed
2. files changed
3. validation summary
4. deferred work
5. risks / followups
6. final commit message(s)
