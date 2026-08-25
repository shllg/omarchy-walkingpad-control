#!/usr/bin/python3
"""Play captured status frames through the Transport interface.

Replay is deliberately a frame player, not a pad simulator. Measured fact 1 in
AGENTS.md says the pad sends one status frame in reply to each query and never
volunteers one, so captured frames advance only when the bridge polls for them.
"""
import json
import sys

from walkingpad import frames


class ReplayFixtureError(ValueError):
    """A replay fixture could not be read as captured status frames."""


class ReplayDevice:
    """Device-shaped placeholder returned by ReplayTransport.find()."""

    def __init__(self):
        self.path = "replay"
        self.address = "replay"
        self.name = "replay"


class ReplayTransport:
    """Transport-compatible player for a JSONL status-frame capture."""

    def __init__(self, fixture_path):
        self.fixture_path = str(fixture_path)
        self._frames = self._load_frames()
        self._position = 0
        self._connected = False
        self._callback = None
        self._device = ReplayDevice()

    def _load_frames(self) -> list[bytes]:
        """Load and validate the whole fixture before the bridge starts."""
        captured = []
        try:
            with open(self.fixture_path, encoding="utf-8") as fixture:
                for line_number, line in enumerate(fixture, start=1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        raise ReplayFixtureError(
                            "replay fixture '%s' line %d is not valid JSON"
                            % (self.fixture_path, line_number)
                        ) from None
                    if not isinstance(record, dict):
                        raise ReplayFixtureError(
                            "replay fixture '%s' line %d must be a JSON object"
                            % (self.fixture_path, line_number)
                        )
                    value = record.get("hex")
                    if not isinstance(value, str) or not value.strip():
                        raise ReplayFixtureError(
                            "replay fixture '%s' line %d has no usable 'hex' field"
                            % (self.fixture_path, line_number)
                        )
                    try:
                        frame = bytes.fromhex(value)
                    except ValueError:
                        raise ReplayFixtureError(
                            "replay fixture '%s' line %d has invalid hex"
                            % (self.fixture_path, line_number)
                        ) from None
                    if frames.parse_status(frame) is None:
                        raise ReplayFixtureError(
                            "replay fixture '%s' line %d is not a valid status frame"
                            % (self.fixture_path, line_number)
                        )
                    captured.append(frame)
        except UnicodeError as exc:
            raise ReplayFixtureError(
                "cannot decode replay fixture '%s' as UTF-8: %s"
                % (self.fixture_path, exc)
            ) from None
        except OSError as exc:
            detail = exc.strerror or str(exc)
            raise ReplayFixtureError(
                "cannot read replay fixture '%s': %s"
                % (self.fixture_path, detail)
            ) from None
        if not captured:
            raise ReplayFixtureError(
                "replay fixture '%s' contains no status frames"
                % self.fixture_path
            )
        return captured

    @property
    def connected(self) -> bool:
        return self._connected

    def find(self, should_continue=None):
        """Return the replay placeholder unless shutdown is already requested."""
        if should_continue is not None and not should_continue():
            return None
        return self._device

    def connect(self, _device) -> None:
        self._connected = True

    def subscribe(self, callback) -> None:
        self._callback = callback

    def write(self, data) -> None:
        if bytes(data) != frames.query():
            # AGENTS.md fact 2: there is no ACK anywhere in this protocol, and
            # reported speed is ACTUAL while the pad ramps at roughly 0.25
            # km/h/s. A synthetic reply would teach the UI behaviour the real
            # pad does not have, so commands are observed but never answered.
            sys.stderr.write(
                "command frame received, not acknowledged (replay)\n"
            )
            sys.stderr.flush()
            return

        frame = self._frames[self._position]
        self._position = (self._position + 1) % len(self._frames)
        if self._callback is not None:
            self._callback(self._device.path, frame)

    def disconnect(self) -> None:
        self._connected = False
        self._callback = None
