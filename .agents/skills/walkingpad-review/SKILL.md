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

Require `bin/check-repo` to pass. When QML is present, also require
`omarchy plugin validate <repo>` and
`qmllint -I "$OMARCHY_PATH/shell" <file>.qml` for the changed QML files.

## Output

Report findings in descending severity. Every finding names the file and line,
the violated fact or rule, and the resulting failure. If nothing is found, state
explicitly: `No findings.`
