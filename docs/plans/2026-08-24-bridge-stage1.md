# Bridge (Stage 1) — what is still open

Stage 1 shipped on 2026-08-24: the whole Python bridge, verified against a
WalkingPad C2 on firmware 6.1.2. The code, `tests/` and
`docs/protocol-evidence.md` are the record of what was built and why. The build
steps and the deviation list were removed on 2026-08-27 — completed work is not
tracked here, and nothing in the codebase cites a `Stage 1 §` section.

`docs/smoke-test.md` is the live tracker for every open **hardware** question.
This file holds only work that is still not done.

## Still open

- **FTMS adapter.** Deferred deliberately, and not scheduled. The author's C2 has
  no `0x1826`, so nobody involved can test an FTMS path, and building one would
  ship untested code claiming support. Design spec §4.3 records the same
  decision and the same reason. Revisit only if someone with FTMS-capable
  hardware can test it.
