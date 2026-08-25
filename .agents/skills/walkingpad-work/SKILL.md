---
name: walkingpad-work
description: Change the WalkingPad plugin while preserving its measured safety and packaging constraints.
---

# WalkingPad work

Read `AGENTS.md` and `docs/design-spec.md` first. The measured facts are not to be
re-derived, “fixed” or weakened by inference.

Definition of done: the requested change is complete, preserves the measured
safety model and plugin boundary, and passes the applicable completion gates.

## Completion gate

Run `bin/check-repo`; it performs the structural checks and test suite. Once QML
exists, also run `omarchy plugin validate <repo>` and
`qmllint -I "$OMARCHY_PATH/shell" <file>.qml`.

## Hardware

Only `docs/smoke-test.md` moves a belt, and it requires the operator to stand
clear. Never write to the OTA service. Never re-send a command on a timer. Send
one command per user action.

Stopping is gated on recent evidence of motion because the stop opcode is a
toggle. Anything that weakens that gate needs hardware re-verification, not
reasoning.

## Public repository

Keep machine-specific content out of tracked files. Never commit a symlink --
the runtime skill trees at `.claude/skills/` and `.codex/skills/` stay gitignored
for that reason, and `bin/link-skills` recreates them. Add no runtime dependency. QML contains no colour literal; build on the theme
singletons. Copy nothing from a private configuration elsewhere.

Do not commit or push; hand back a suggested commit message instead.
