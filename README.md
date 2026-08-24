# omarchy-walkingpad

Control a KingSmith WalkingPad from the Omarchy bar over Bluetooth LE. No vendor
app, no account, no network access.

This repository currently contains **Stage 1**: `walkingpad-bridge`, a standalone
process that owns the BLE link and exposes it as line-delimited JSON on stdio. The
QML bar widget is Stage 2.

## Requirements

Nothing to install. The bridge runs on system Python and uses only
`python-gobject` and `bluez`, both of which are already part of an Omarchy base
install. There is no build step, no virtualenv, and no pip.

Running the tests additionally needs `pytest`: `sudo pacman -S python-pytest`.

## Usage

    ./bin/walkingpad-bridge [--max-speed 6.0] [--max-step 0.5]

It writes one JSON object per line to stdout and reads one JSON object per line
from stdin. Out:

    {"t": "link", "state": "connected", "addr": "<pad-addr>", "name": "KS-BLC2"}
    {"t": "status", "belt": "running", "speed": 2.3, "target": 2.5, "mode": "manual",
     "time": 1011, "dist_km": 0.7, "steps": 1318}

In:

    {"cmd": "speed", "kmh": 2.5}
    {"cmd": "start"}
    {"cmd": "stop"}

`speed` is the belt's **actual** speed and `target` is what was last commanded.
They differ during a ramp, and that is not an error — see below. `target` is
`null` until something is commanded: attaching to a belt already running does not
tell us what it was aimed at, and reporting 0.0 there would read as a stop.
`pad_set_speed` is the pad's own stored value and is diagnostic only — it reads
0.0 when the speed was set at the panel.

## Starting the belt is the dangerous part

⚠ **The pad starts at ~3.5 km/h regardless of the speed it has stored, and a speed
command sent to a stopped belt does not start it** — the command is kept and the
belt stays put. So starting is always two steps: `start`, then set the speed as
soon as `belt` becomes `running`. There is roughly a two-second window at
`belt: "running", speed: 0.0` before the pad accelerates.

The per-command 0.5 km/h clamp does **not** protect this. It governs changes; the
start speed is the pad's own. Anything offering a one-click start has to handle
both steps or it puts 3.5 km/h under someone's feet.

## The pad must be in manual mode

In `mode: "standby"` the pad answers status queries but silently ignores start,
stop and speed alike — and the BLE mode-switch command is ignored too, so nothing
here can leave that state. **Only the M button on the panel can.** The bridge
refuses motion commands in standby and says so, rather than writing into the void:

    {"t":"error","code":"standby","msg":"pad is in standby and ignores commands; …"}

## Things that look like bugs and are not

**The pad is usually absent.** It sleeps and stops advertising entirely when idle,
and BLE cannot wake it — that needs the panel or the remote. `{"t":"link",
"state":"absent"}` is the normal resting state, so the bridge backs off for 30 s
between scans rather than retrying hard, and a bar widget should render it as
deliberate rather than broken.

**Speed changes ramp, they do not jump.** The pad accelerates at roughly
0.25 km/h per second and there is no acknowledgement anywhere in the protocol. The
bridge therefore reports `speed` and `target` separately; a UI that showed only
`speed` would make every click look like it had failed for two seconds.

**Status is polled, not pushed.** The pad answers queries and never volunteers a
frame — with notifications subscribed, silence produces nothing at all. The 1 Hz
poll loop is load-bearing. Removing it as "redundant with notifications" produces
a permanently frozen UI that looks like an idle pad rather than a bug.

**Only one connection at a time.** The phone app and this bridge are mutually
exclusive. Being unable to connect while the app is attached is a state to
surface, not a fault to retry into.

## Safety

- ⚠ **Stop is NOT unconditional.** The pad's stop opcode is a toggle: it stops a
  running belt and **starts a stopped one**. Stopping is therefore only sent when
  a recent frame shows the belt moving or counting down. Without that evidence the
  bridge refuses and says so — the panel and remote always work. An earlier
  version of this file claimed stop was always safe; it was wrong.
- Everything that moves the belt is refused unless a status frame arrived
  recently. Without an acknowledgement in the protocol, a recent frame is the
  only evidence there is a live pad on the other end.
- **Speed changes clamp to one step per command** (0.5 km/h by default). A person
  may be standing on the belt, so the worst case of a stuck key or a double click
  is one small increment rather than a continuous acceleration.
- A link that goes silent for 15 s is torn down and rescanned rather than shown
  as live, because BlueZ will report a dead link as connected.

## The OTA service is deliberately untouched

The pad exposes an unauthenticated TI-style firmware update service at
`f000ffc0-0451-4000-b000-000000000000`. **Nothing here ever writes to it, and
nothing should.** A bad image bricks the control board. It is named in the source
only so that nobody later mistakes it for a missing feature.

## Testing

    python -m pytest tests/ -v

The protocol codec is tested against real frames captured from a WalkingPad C2 on
firmware 6.1.2, in `fixtures/`, so the entire suite runs with no hardware present.
Moving a belt is covered separately by `docs/smoke-test.md`, which requires an
operator standing clear of the pad.

## Documentation

- `docs/design-spec.md` — architecture, safety model, and decision records
- `docs/protocol-evidence.md` — what was measured on real hardware, and why
- `docs/smoke-test.md` — the hardware checklist

## Licence

MIT. See [LICENSE](LICENSE).
