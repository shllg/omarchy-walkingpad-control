# Stage 1 kickoff prompt

Paste into a fresh Claude Code session started in `~/src/omarchy-walkingpad`.

---

Read `CLAUDE.md`, `docs/design-spec.md`, and `docs/plans/2026-08-24-bridge-stage1.md`
before doing anything.

Implement Stage 1: the Python `walkingpad-bridge`. Work through the plan's five tasks
in order using the `superpowers:executing-plans` skill, checkpointing with me between
tasks.

The plan is complete and self-reviewed — every step contains the actual code, not a
description of it. Follow it as written. If you believe a step is wrong, stop and say
so rather than silently improvising: the design rests on measured hardware behaviour
recorded in `docs/protocol-evidence.md`, and several obvious-looking "improvements"
(dropping the poll loop, echoing the reported speed directly, connecting by address
without scanning) are specifically known to be wrong.

Notes on the environment:

- Tasks 1–3 need no hardware. Do those first regardless of whether the pad is awake.
- The pad sleeps and stops advertising. If Task 4 or 5 needs it, ask me to wake it at
  the panel — you cannot wake it over BLE.
- `python-bleak` may not be installed yet: `pacman -S python-bleak`. Never pip.
- The repo has no commits yet; the three docs are staged. Do not commit anything
  without asking me first.

Two questions are still open and may affect Task 4:

- **Q2 cold start** — does the pad need an explicit `START` before it accepts a speed
  command on a stopped belt? Untested; the belt was already running when probed.
- **Q6** — do a BlueZ-level connect and bleak's GATT attach cooperate or contend?
  The test was attempted and blocked because the pad had gone to sleep.

Both are cheap to close once the pad is awake. Ask me and I'll wake it.
