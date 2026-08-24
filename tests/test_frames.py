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
