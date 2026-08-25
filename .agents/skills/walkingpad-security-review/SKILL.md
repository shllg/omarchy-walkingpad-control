---
name: walkingpad-security-review
description: Review a repository change against this plugin's concrete shell, belt-control, privacy, and distribution threats.
---

# WalkingPad security review

This is the repository's threat model, not a generic checklist. Review only; do
not edit files.

## Containment and physical control

Plugin code runs unsandboxed inside a long-lived shell process with the user's
permissions. A fault is not contained to one widget and can compromise the whole
desktop session.

The pad accepts unauthenticated commands, and a person may be standing on the
belt. Safety gates are security controls, not ergonomics. Anything widening what
can move the belt, or when it can move, requires hardware verification.

The unauthenticated firmware-update service is never written to. No code path may
construct a write to it; a bad image bricks the control board.

## Data and distribution boundary

Tracked content contains no device address, home path, hostname, username or
personal usage data. Local scratch containing real addresses remains untracked
and ignored; check that a change did not stage it.

No new runtime dependency, daemon or unit file, privilege escalation, bundled
binary, installer, or network access is allowed. Each can create an automatic
failure or manual-review trigger in the public plugin directory's baseline, and
each contradicts a design decision already recorded in `docs/design-spec.md`.

Review every new process spawn, every file written outside the plugin's own
directory, and every path by which data could leave the machine.

Use `docs/plugin-evidence.md` for the install and directory trust boundary, and
`AGENTS.md` for the measured safety controls.

## Output

Report findings in descending severity. Every finding includes the file and line,
the concrete failure scenario, and the violated boundary. If nothing is found,
state explicitly: `No findings.`
