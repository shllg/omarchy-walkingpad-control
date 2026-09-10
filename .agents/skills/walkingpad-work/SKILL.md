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

Run `bin/check-repo` on an Omarchy machine; it performs the structural checks,
validates the exported plugin tree, runs QML lint, and runs the test suite.
Do not validate this checkout directly: the ignored skill symlinks do not ship
and would make the validator reject it. The gate exports tracked and non-ignored
new files before invoking the Omarchy validator.

⚠ Do NOT invoke `qmllint` by bare name, and do not point `-I` at the shell
directory alone. Both are traps that have already made a review report five
files "clean" that were never checked:

- `/usr/bin/qmllint` is the **Qt 5** binary (`qt5-declarative`). Given Qt 6 QML
  it exits 255 printing NOTHING, which reads exactly like a pass. The real one
  is `/usr/lib/qt6/bin/qmllint`.
- `-I "$OMARCHY_PATH/shell"` does not resolve `qs.Commons` or `qs.Ui`.
  Quickshell maps the config root to `qs`, so qmllint needs an import path
  containing a `qs` directory. Without it every `qs.Ui` type is "not found" and
  the type checking is hollow.

`bin/check-repo` now does this correctly and gates on it, so running it is
enough. To lint one file by hand:

```
d=$(mktemp -d) && ln -s "$OMARCHY_PATH/shell" "$d/qs"
/usr/lib/qt6/bin/qmllint -I "$d" <file>.qml
```

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
