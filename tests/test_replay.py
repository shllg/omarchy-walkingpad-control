import json
from pathlib import Path

import pytest

from walkingpad import frames
from walkingpad.bridge import Bridge
from walkingpad.replay import ReplayFixtureError, ReplayTransport


FIXTURE = Path(__file__).parent.parent / "fixtures" / "c2-fw6.1.2.jsonl"


def captured_frames():
    captured = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            captured.append(bytes.fromhex(json.loads(line)["hex"]))
    return captured


def test_queries_play_frames_in_order_and_wrap():
    expected = captured_frames()
    transport = ReplayTransport(FIXTURE)
    received = []
    transport.subscribe(lambda _sender, data: received.append(bytes(data)))

    for _ in range(len(expected) + 1):
        transport.write(frames.query())

    assert received == expected + [expected[0]]


def test_device_and_connection_state_match_the_transport_interface():
    transport = ReplayTransport(FIXTURE)
    assert transport.connected is False

    device = transport.find()
    assert (device.path, device.address, device.name) == (
        "replay", "replay", "replay"
    )
    transport.connect(device)
    assert transport.connected is True

    transport.disconnect()
    assert transport.connected is False


def test_command_does_not_advance_or_notify(capsys):
    expected = captured_frames()
    transport = ReplayTransport(FIXTURE)
    received = []
    transport.subscribe(lambda _sender, data: received.append(bytes(data)))

    transport.write(frames.start())
    assert received == []
    transport.write(frames.query())

    output = capsys.readouterr()
    assert received == [expected[0]]
    assert output.out == ""
    assert output.err == "command frame received, not acknowledged (replay)\n"


def test_bridge_emits_every_captured_belt_state_as_status_json():
    captured = captured_frames()
    expected_belts = []
    for frame in captured:
        status = frames.parse_status(frame)
        assert status is not None
        expected_belts.append(status.belt)

    transport = ReplayTransport(FIXTURE)
    bridge = Bridge(transport=transport)
    output = []
    bridge._write = output.append

    for _ in captured:
        bridge._service_link()

    messages = [json.loads(line) for line in output]
    statuses = [message for message in messages if message["t"] == "status"]
    assert [status["belt"] for status in statuses] == expected_belts
    assert {"running", "idle", "starting", "stopped"} <= {
        status["belt"] for status in statuses
    }


def test_missing_fixture_raises_a_clear_error(tmp_path):
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(ReplayFixtureError) as error:
        ReplayTransport(missing)

    assert "cannot read replay fixture" in str(error.value)
    assert "missing.jsonl" in str(error.value)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("not JSON\n", "is not valid JSON"),
        ('{"note":"missing frame"}\n', "has no usable 'hex' field"),
        ('{"hex":"not hex"}\n', "has invalid hex"),
        ('{"hex":"00"}\n', "is not a valid status frame"),
    ],
)
def test_malformed_fixture_raises_a_clear_error(tmp_path, contents, message):
    fixture = tmp_path / "malformed.jsonl"
    fixture.write_text(contents, encoding="utf-8")

    with pytest.raises(ReplayFixtureError, match=message):
        ReplayTransport(fixture)


def test_blank_fixture_lines_are_ignored(tmp_path):
    first = captured_frames()[0]
    fixture = tmp_path / "blank-lines.jsonl"
    fixture.write_text('\n{"hex":"%s"}\n\n' % first.hex(), encoding="utf-8")
    transport = ReplayTransport(fixture)
    received = []
    transport.subscribe(lambda _sender, data: received.append(bytes(data)))

    transport.write(frames.query())

    assert received == [first]
