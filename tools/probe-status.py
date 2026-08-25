import asyncio, sys
from bleak import BleakClient, BleakScanner

FE00 = "0000fe00-0000-1000-8000-00805f9b34fb"
FE01 = "0000fe01-0000-1000-8000-00805f9b34fb"
FE02 = "0000fe02-0000-1000-8000-00805f9b34fb"

BELT = {0: "stopped/standby", 1: "starting", 2: "running", 4: "?4", 5: "belt-idle", 9: "starting?"}
MODE = {0: "auto", 1: "manual", 2: "standby"}

def crc(body):            # body = bytes between 0xF7 and the crc slot
    return sum(body) % 256

def frame(*body):
    b = bytes(body)
    return bytes([0xF7]) + b + bytes([crc(b), 0xFD])

frames = []

def decode(d: bytes):
    out = [f"raw({len(d):2d}) {d.hex(' ')}"]
    if len(d) >= 14 and d[0] == 0xF8 and d[1] == 0xA2:
        ok = crc(d[1:-2]) == d[-2] and d[-1] == 0xFD
        out.append(
            f"  STATUS belt={d[2]}({BELT.get(d[2],'?')}) "
            f"speed={d[3]/10:.1f}km/h mode={d[4]}({MODE.get(d[4],'?')}) "
            f"time={int.from_bytes(d[5:8],'big')}s "
            f"dist={int.from_bytes(d[8:11],'big')/100:.2f}km "
            f"steps={int.from_bytes(d[11:14],'big')}  crc_ok={ok}"
        )
    elif len(d) >= 2 and d[0] == 0xF8:
        out.append(f"  other F8 subtype 0x{d[1]:02X} (not the A2 status frame)")
    return "\n".join(out)

def on_notify(_, data: bytearray):
    d = bytes(data)
    frames.append(d)
    print(decode(d), flush=True)

async def main():
    print("scanning for a KS-* device by NAME (no hardcoded MAC)...", flush=True)
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or "").upper().startswith("KS-")
        or FE00 in [u.lower() for u in (ad.service_uuids or [])],
        timeout=20.0,
    )
    if not dev:
        print("NOT FOUND — is the pad powered on?"); sys.exit(1)
    print(f"found: {dev.name}  {dev.address}", flush=True)

    async with BleakClient(dev, timeout=30.0) as c:
        print(f"connected={c.is_connected}\n--- subscribing FE01 ---", flush=True)
        await c.start_notify(FE01, on_notify)

        print("listening 4s for unsolicited frames...", flush=True)
        await asyncio.sleep(4)

        q = frame(0xA2, 0x00, 0x00)
        print(f"\n--- sending STATUS QUERY {q.hex(' ')} (read-only opcode) ---", flush=True)
        await c.write_gatt_char(FE02, q, response=False)
        await asyncio.sleep(3)

        print(f"\n--- 3 more polls ---", flush=True)
        for _ in range(3):
            await c.write_gatt_char(FE02, q, response=False)
            await asyncio.sleep(1.5)

        await c.stop_notify(FE01)

    print(f"\n=== {len(frames)} frames received ===")
    if frames:
        lens = sorted({len(f) for f in frames})
        heads = sorted({f[:2].hex() for f in frames})
        print(f"lengths seen: {lens}   headers seen: {heads}")
    else:
        print("NO FRAMES — protocol assumption is wrong for fw 6.1.2")

asyncio.run(main())
