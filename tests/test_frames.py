import json, pathlib
import pytest
from walkingpad import frames

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "c2-fw6.1.2.jsonl"

def load_frames():
    out = []
    for line in FIXTURES.read_text().splitlines():
        if line.strip():
            out.append(bytes.fromhex(json.loads(line)["hex"].replace(" ", "")))
    return out

def test_checksum_is_sum_of_body_mod_256():
    assert frames.checksum(bytes([0xA2, 0x00, 0x00])) == 0xA2

def test_query_frame_is_exact():
    assert frames.query() == bytes.fromhex("f7a20000a2fd")

def test_start_and_stop_frames_are_exact():
    assert frames.start() == bytes.fromhex("f7a20401a7fd")
    assert frames.stop()  == bytes.fromhex("f7a20400a6fd")

@pytest.mark.parametrize("kmh,expected", [
    (2.0, "f7a20114b7fd"),
    (2.5, "f7a20119bcfd"),
])
def test_set_speed_frames_are_exact(kmh, expected):
    assert frames.set_speed(kmh) == bytes.fromhex(expected)

def test_parses_real_captured_status():
    s = frames.parse_status(load_frames()[0])
    assert s.belt == "running"
    assert s.speed_kmh == 2.5
    assert s.mode == "manual"
    assert s.elapsed_s == 1008
    assert s.distance_km == 0.69
    assert s.steps == 1314

def test_every_captured_frame_parses_and_has_valid_crc():
    for raw in load_frames():
        assert frames.parse_status(raw) is not None

def test_rejects_frame_with_bad_crc():
    raw = bytearray(load_frames()[0])
    raw[-2] ^= 0xFF
    assert frames.parse_status(bytes(raw)) is None

def test_rejects_truncated_frame():
    assert frames.parse_status(bytes.fromhex("f8a20119")) is None


def test_awake_idle_pad_decodes_as_idle():
    # Measured live 2026-08-24: an awake pad with nobody on it reports belt==4.
    # Before this was mapped it decoded as "unknown", which would have shown the
    # pad's most common awake state as a fault in the bar.
    data = bytes.fromhex("f8a204000200000000000000000000000000a8fd")
    status = frames.parse_status(data)
    assert status.belt == "idle"
    assert status.speed_kmh == 0.0
    assert status.mode == "standby"


def test_startup_countdown_decodes_as_starting():
    # Measured: belt 9, 8, 7 are the panel's 3-2-1 countdown after START.
    # These decoded as "unknown" before, i.e. the UI would show a fault while
    # the pad was doing exactly what it was told.
    for hexstr in ("f8a209000100006000000500006f1e000300a1fd",
                   "f8a207000100006000000500006f1e0003009ffd"):
        status = frames.parse_status(bytes.fromhex(hexstr))
        assert status.belt == "starting"
        assert status.speed_kmh == 0.0


def test_stopped_belt_decodes_as_stopped():
    status = frames.parse_status(
        bytes.fromhex("f8a200000100007300000600008314000300b6fd"))
    assert status.belt == "stopped"
    assert status.speed_kmh == 0.0


def test_set_speed_field_is_the_last_ble_command_not_a_target():
    # Converged: actual == what we last commanded over BLE.
    converged = frames.parse_status(
        bytes.fromhex("f8a201140100006900000500007a14000300b7fd"))
    assert converged.speed_kmh == 2.0 and converged.set_speed_kmh == 2.0

    # Mid ramp-down: actual still 3.5 while the commanded value is already 2.0.
    ramping = frames.parse_status(
        bytes.fromhex("f8a201230100006500000500007414000300bcfd"))
    assert ramping.speed_kmh == 3.5 and ramping.set_speed_kmh == 2.0

    # Speed set at the PANEL leaves the field at 0.0 even at 2.5 km/h, which is
    # why it must never be rendered as a target.
    panel = frames.parse_status(
        bytes.fromhex("f8a20119010003f0000045000522000002001efd"))
    assert panel.speed_kmh == 2.5 and panel.set_speed_kmh == 0.0
