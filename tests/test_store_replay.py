import json
from datetime import datetime, timezone

from walkingpad import frames
from walkingpad.bridge import Bridge
from walkingpad.replay import ReplayTransport
from walkingpad.store import Store


class FakeClock:
    def __init__(self, wall):
        self.wall = wall

    def __call__(self):
        return self.wall

    def advance(self, seconds):
        self.wall += seconds


def status_frame(belt_code, speed_kmh, mode_code, elapsed_s, dist_km, steps,
                 set_speed_kmh=0.0, state_byte=0x00):
    data = bytearray(20)
    data[0] = 0xF8
    data[1] = 0xA2
    data[2] = belt_code
    data[3] = int(round(speed_kmh * 10))
    data[4] = mode_code
    data[5:8] = int(elapsed_s).to_bytes(3, "big")
    data[8:11] = int(round(dist_km * 100)).to_bytes(3, "big")
    data[11:14] = int(steps).to_bytes(3, "big")
    data[14] = int(round(set_speed_kmh * 10))
    data[15] = 0x00
    data[16] = state_byte
    data[17] = 0x00
    body = bytes(data[1:18])
    data[18] = frames.checksum(body)
    data[19] = frames.FRAME_END
    return bytes(data)


def test_synthetic_replay_persists_a_counter_reset_as_a_new_epoch(tmp_path):
    captured = [
        status_frame(1, 2.0, 1, 100, 0.20, 200, set_speed_kmh=2.0),
        status_frame(1, 2.1, 1, 101, 0.21, 202, set_speed_kmh=2.0),
        status_frame(0, 0.0, 1, 102, 0.22, 204, set_speed_kmh=2.0),
        # Generated reset evidence: no tracked fixture claims this happened on
        # hardware before the Stage 3 smoke-test gate has measured it.
        status_frame(1, 1.0, 1, 1, 0.01, 2, set_speed_kmh=1.0),
        status_frame(1, 1.1, 1, 2, 0.02, 4, set_speed_kmh=1.0),
    ]
    decoded = frames.parse_status(captured[0])
    assert decoded == frames.Status(
        belt="running",
        speed_kmh=2.0,
        set_speed_kmh=2.0,
        mode="manual",
        elapsed_s=100,
        distance_km=0.20,
        steps=200,
    )

    fixture = tmp_path / "synthetic.jsonl"
    fixture.write_text(
        "".join(json.dumps({"hex": frame.hex()}) + "\n" for frame in captured),
        encoding="utf-8",
    )
    first_wall = 1_756_137_600.0
    clock = FakeClock(first_wall)
    store_path = tmp_path / "store"
    store = Store(store_path, now=clock)
    bridge = Bridge(
        transport=ReplayTransport(fixture),
        store=store,
    )
    output = []
    bridge._write = output.append

    for _ in captured:
        bridge._service_link()
        clock.advance(1)
    store.close()

    bouts = [
        json.loads(line)
        for line in (store_path / "bouts.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert bouts[0]["observed"] is True
    assert bouts[0]["e"] == 0

    state = json.loads((store_path / "state.json").read_text(encoding="utf-8"))
    assert state["epoch"] == 1

    expected_date = datetime.fromtimestamp(
        first_wall, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    sample_path = store_path / "samples" / f"{expected_date}.jsonl"
    samples = [
        json.loads(line)
        for line in sample_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    second_walk = [sample for sample in samples if sample["pe"] in (1, 2)]
    assert [sample["e"] for sample in second_walk] == [1, 1]
