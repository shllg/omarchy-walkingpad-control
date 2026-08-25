import asyncio, os, subprocess, sys, time
from bleak import BleakClient, BleakScanner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from local_device import MissingAddress, pad_address

# Read at runtime from the gitignored docs/local-device.md. Never hardcode an
# address here: one committed in a tracked file was pushed public on 2026-08-24
# and had to be scrubbed from every commit. tests/test_no_pii.py enforces this.
try:
    ADDR = pad_address()
except MissingAddress as exc:
    sys.exit("%s: %s" % (os.path.basename(__file__), exc))
FE01 = "0000fe01-0000-1000-8000-00805f9b34fb"
FE02 = "0000fe02-0000-1000-8000-00805f9b34fb"

def frame(*b):
    body = bytes(b); return bytes([0xF7]) + body + bytes([sum(body) % 256, 0xFD])
QUERY  = frame(0xA2, 0x00, 0x00)
def speed(t): return frame(0xA2, 0x01, t)
START  = frame(0xA2, 0x04, 0x01)
STOP   = frame(0xA2, 0x04, 0x00)

st = {}
def on_notify(_, data):
    d = bytes(data)
    if len(d) >= 14 and d[0] == 0xF8 and d[1] == 0xA2:
        st.update(belt=d[2], speed=d[3], mode=d[4], steps=int.from_bytes(d[11:14], "big"))

async def poll(c, n, gap=1.0, tag=""):
    for _ in range(n):
        await c.write_gatt_char(FE02, QUERY, response=False)
        await asyncio.sleep(gap)
        print(f"    {tag} belt={st.get('belt')} speed={st.get('speed',0)/10:.1f} mode={st.get('mode')}", flush=True)

def bctl(*args):
    r = subprocess.run(["bluetoothctl", "--timeout", "8", *args],
                       capture_output=True, text=True, timeout=25,
                       stdin=subprocess.DEVNULL)
    return r.stdout.strip()

async def main():
    print("### Q6 — does BlueZ-level connect cooperate with bleak's GATT attach?\n")
    print("1. BlueZ connect (what Quickshell's device.connect() calls):", flush=True)
    out = bctl("connect", ADDR)
    print("   " + (out.splitlines()[-1] if out else "(no output)"), flush=True)
    info = bctl("info", ADDR)
    conn = [l.strip() for l in info.splitlines() if "Connected:" in l]
    print(f"   BlueZ says: {conn[0] if conn else '?'}", flush=True)

    print("\n2. Now attaching bleak for GATT on top of that link...", flush=True)
    t = time.monotonic()
    try:
        c = BleakClient(ADDR, timeout=30.0)
        await c.connect()
        print(f"   bleak connect OK in {time.monotonic()-t:.2f}s  (is_connected={c.is_connected})")
        print("   => Q6 ANSWER: COOPERATE — BlueZ holds the link, bleak attaches for GATT")
    except Exception as e:
        print(f"   bleak connect FAILED: {e}")
        print("   => Q6 ANSWER: CONTEND — plugin must not BlueZ-connect before the bridge")
        return

    await c.start_notify(FE01, on_notify)

    print("\n### Q2 — cold start: does a speed command work on a stopped belt?\n")
    await poll(c, 2, tag="initial")

    try:
        if st.get("belt") == 1 or st.get("speed", 0) > 0:
            print("\n  belt is running — stopping it first to create the cold-start condition")
            await c.write_gatt_char(FE02, STOP, response=False)
            await poll(c, 6, tag="stopping")

        print(f"\n  A) sending SPEED 1.0 with NO start (belt={st.get('belt')} speed={st.get('speed',0)/10:.1f})")
        await c.write_gatt_char(FE02, speed(10), response=False)
        await poll(c, 5, tag="  after-speed")
        moved_without_start = st.get("belt") == 1 or st.get("speed", 0) > 0

        if moved_without_start:
            print("\n  => Q2 ANSWER: NO cold-start needed — speed alone starts the belt")
            print("     ⚠ SAFETY: a bare speed command can start a stopped pad.")
        else:
            print("\n  B) speed alone did nothing. Sending explicit START, then SPEED 1.0")
            await c.write_gatt_char(FE02, START, response=False)
            await poll(c, 4, tag="  after-start")
            await c.write_gatt_char(FE02, speed(10), response=False)
            await poll(c, 4, tag="  after-speed")
            ok = st.get("belt") == 1 or st.get("speed", 0) > 0
            print(f"\n  => Q2 ANSWER: START IS REQUIRED first (worked={ok})")
    finally:
        print("\n--- STOPPING (guaranteed) ---", flush=True)
        for _ in range(3):
            try:
                await c.write_gatt_char(FE02, STOP, response=False); break
            except Exception as e:
                print(f"  stop retry: {e}"); await asyncio.sleep(0.5)
        await poll(c, 6, tag="  stopping")
        print(f"\n=== FINAL belt={st.get('belt')} speed={st.get('speed',0)/10:.1f} km/h ===")
        try:
            await c.stop_notify(FE01)
        except Exception: pass
        await c.disconnect()
        bctl("disconnect", ADDR)
        print("disconnected (bleak + BlueZ)")

asyncio.run(main())
