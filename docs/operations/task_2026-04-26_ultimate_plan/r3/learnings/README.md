# R3 Learnings Directory

Phase-specific learnings captured DURING implementation. Read by future
agents on the same phase + wave-close multi-review.

## File-naming convention

```
<phase_id>_<short_topic>.md           # in-flight learnings
<phase_id>_<author>_<date>_retro.md   # end-of-phase retrospective
```

Examples:
- `Z2_v2_sdk_create_order_signed_bytes_shape.md`
- `M3_polymarket_ws_auth_hmac_signature.md`
- `F2_scipy_version_pin_for_platt_determinism.md`
- `Z4_alice_bob_2026-05-15_retro.md`

## See also

- `../SELF_LEARNING_PROTOCOL.md` — when and how to capture
- `../CONFUSION_CHECKPOINTS.md` — moments that trigger learning capture
- `../IMPLEMENTATION_PROTOCOL.md` — the parent protocol

## Auto-loading

The phase prompt template (`../templates/phase_prompt_template.md`)
mandates that cold-start agents read all `<phase_id>_*.md` files in
this directory before writing code. This is how learnings compound.

(empty — populated as phases ship)
