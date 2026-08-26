# WalkingPad Bridge (Stage 1) — outcome record


> ## STATUS: COMPLETE (2026-08-24)
>
> All five tasks are implemented, committed and verified against a WalkingPad C2
> on firmware 6.1.2. 36 tests pass with no hardware. **The plan text below is kept
> as written for the record; where it disagrees with the code, the code is right.**
> Five deviations, each forced by evidence rather than preference:
>
> 1. **Task 2 was rewritten onto `gi.repository.Gio` instead of bleak** (ADR D-3).
>    A plugin manifest has no dependency field and no install hook, so bleak would
>    have meant a manual `pacman -S` before the plugin worked at all. This also
>    dissolved open question Q6.
> 2. **Task 4 runs a GLib main loop, not asyncio**, which follows from 1: Gio
>    delivers notifications as D-Bus signals, and bridging to asyncio needs
>    `asyncio-glib`, which Omarchy does not ship. `handle_command`, `emit`,
>    `on_notify` and `_poll_interval` are unchanged; `run()` and stdin reading are
>    not.
> 3. **`belt == 4` was added to the codec.** Measured live: it is the awake-and-idle
>    state, so the plan's map decoded the pad's most common awake state as
>    `"unknown"`.
> 4. **A link watchdog was added** (ADR D-4). BlueZ reported `Connected=True` on a
>    radio link that was already gone and swallowed every write. Fifteen seconds of
>    silence now tears the link down and rescans.
> 5. **SIGINT/SIGTERM are GLib signal sources**, and the staleness gate applies only
>    to `speed` and `start`. Without the first, Ctrl-C did nothing; without the
>    second, an unknown command answered "refusing to move the belt".
>
> Test count is 36, not the 25 predicted here — the extra 11 cover transport

**Spec:** `docs/design-spec.md` (see especially §2 verified facts, §4 the bridge,
§6 safety, §12 ADRs D-1/D-2). The measured protocol facts live in `AGENTS.md` and
`docs/protocol-evidence.md`.

---

## What this file used to contain

Five tasks, as checkbox build steps, for work that shipped on 2026-08-24. Those
steps were removed on 2026-08-26: the code, `tests/` and the deviation list above
are the record, and a checklist of completed work is not something to keep
tracking. Nothing in the codebase cites a `Stage 1 §` section.

## Still open after Stage 1

Only two items survive; the rest of the original deferral list has since shipped.

- **FTMS adapter.** Still deferred, and deliberately. The author's C2 has no
  `0x1826`, so nobody involved can test it, and building it would ship untested
  code claiming support.
- **Open question Q2 (cold start).** Still OPEN. It needs an awake pad *and* an
  operator standing clear, because answering it moves a belt. The procedure is
  `docs/smoke-test.md` §3, which also tracks every other open hardware question.
  It may still change the bridge's `start` handling.

Shipped since, and no longer deferred: `--replay` mode (`walkingpad/replay.py`,
`tests/test_replay.py`), and the QML service, bar widget and `IpcHandler` of
Stage 2. Q6 (BlueZ/bleak cooperation) is **dissolved, not answered** — dropping
bleak for Gio leaves no second BLE stack to contend with.
