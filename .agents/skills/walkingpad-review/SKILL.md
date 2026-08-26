---
name: walkingpad-review
description: Review a repository change for measured-protocol, safety, packaging, and test regressions without editing it.
---

# WalkingPad correctness review

Review only. Do not edit files.

Read `AGENTS.md` first. Check every changed behavior against the measured facts by
number. A contradiction is wrong until the behavior has been re-measured on
hardware; reasoning does not overturn a measurement here.

## High-risk regressions

Flag any change that:

- removes or slows the poll loop required by fact 1;
- treats reported speed as a target, contrary to facts 2 and 15;
- infers belt state from what was or was not commanded instead of status frames
  (facts 8, 15 and 16);
- adds a timed command retry, contrary to fact 17;
- weakens a recent-frame, motion-state, clamp or repeat-suppression safety rule
  (facts 16 and 18 plus the hard constraints);
- reintroduces a retracted claim; preserve the corrections that the belt does not
  auto-start under a person, stop was not being silently ignored, and stop is not
  unconditionally permitted.

Check that comments cite the measured fact they depend on, constructs remain
plain, and no runtime dependency has appeared.

## Gate

Require `bin/check-repo` to pass; it carries the QML lint. When QML is present,
also require `omarchy plugin validate <repo>`.

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

## Output

Report findings in descending severity. Every finding names the file and line,
the violated fact or rule, and the resulting failure. If nothing is found, state
explicitly: `No findings.`
