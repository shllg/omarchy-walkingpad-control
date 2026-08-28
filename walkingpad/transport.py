#!/usr/bin/python3
"""
BLE plumbing. Knows nothing about WalkingPad semantics -- it moves bytes.

WHY GIO AND NOT BLEAK
---------------------
An Omarchy plugin has no build step and no dependency field in its manifest, so
anything not already on the machine becomes a package the user has to install by
hand before the plugin works. `python-gobject` and `bluez` are both in
omarchy-base.packages, so a Gio/BlueZ transport installs with zero extra steps;
bleak would not. See ADR D-3.

It also dissolves open question Q6. Talking to BlueZ over D-Bus IS what bleak
does internally, so there is no second BLE stack to contend with -- we are the
same kind of client the rest of the desktop already is.

WHY THE SCAN IS NOT OPTIONAL
----------------------------
Connecting to a device BlueZ has not seen recently fails with "device was not
found", which looks exactly like a connection being refused by another client.
That false signal cost real debugging time on 2026-08-24, so connecting ALWAYS
goes through find() first.

WHY find() AND connect() BLOCK
------------------------------
Both run only while we are disconnected. There is no live link to service and no
belt under our control to stop, so there is nothing for a main loop to do in the
meantime. Blocking here keeps the code plain (ADR D-1); the poll loop and the
command path, which DO have a belt to answer for, never block.
"""
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

SERVICE_FE00 = "0000fe00-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY  = "0000fe01-0000-1000-8000-00805f9b34fb"
CHAR_WRITE   = "0000fe02-0000-1000-8000-00805f9b34fb"

# The pad exposes a TI-style OTA service. We never touch it -- a bad image
# bricks the control board. Named here only so nobody "adds support" later.
FORBIDDEN_OTA_SERVICE = "f000ffc0-0451-4000-b000-000000000000"

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
CHAR_IFACE = "org.bluez.GattCharacteristic1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OM_IFACE = "org.freedesktop.DBus.ObjectManager"

CONNECT_TIMEOUT_MS = 30000
SCAN_POLL_S = 0.4


class Device:
    """What find() hands back: enough to connect and to name the link."""

    def __init__(self, path: str, address: str, name: str):
        self.path = path
        self.address = address
        self.name = name


def matches_walkingpad(props: dict) -> bool:
    """Prefer the advertised service UUID; fall back to the vendor name prefix.

    Matching on FE00 identifies a CAPABILITY, so it also catches models whose
    advertised name does not follow the KS- convention.
    """
    uuids = [u.lower() for u in (props.get("UUIDs") or [])]
    if SERVICE_FE00 in uuids:
        return True
    name = props.get("Name") or props.get("Alias") or ""
    return name.upper().startswith("KS-")


def is_in_range(props: dict) -> bool:
    """True only if the pad is advertising right now.

    BlueZ keeps a device object long after the device is gone, so the object
    existing proves nothing. RSSI is populated from live advertisements during
    discovery and dropped when they stop, which makes it the reachability test.
    Without this, a sleeping pad looks present and every connect attempt fails.
    """
    return props.get("RSSI") is not None


def _device_from(path: str, props: dict) -> Device:
    return Device(path,
                  props.get("Address") or "",
                  props.get("Name") or props.get("Alias") or "")


def find_connected(objects: dict):
    """A pad BlueZ is already holding a link to, or None.

    Measured 2026-08-24: a BLE connection outlives the process that made it, so
    an ungraceful exit leaves the pad connected. A CONNECTED PAD DOES NOT
    ADVERTISE, so discovery can never find it again and the bar reads "absent"
    with an awake pad in front of you. Adopting the existing link is the way out,
    and RSSI is irrelevant here: the live connection is the proof of reach.
    """
    for path, ifaces in objects.items():
        props = ifaces.get(DEVICE_IFACE)
        if props and matches_walkingpad(props) and props.get("Connected"):
            return _device_from(path, props)
    return None


def find_advertising(objects: dict):
    """A pad advertising right now, or None. Requires a live RSSI."""
    for path, ifaces in objects.items():
        props = ifaces.get(DEVICE_IFACE)
        if props and matches_walkingpad(props) and is_in_range(props):
            return _device_from(path, props)
    return None


class Transport:
    def __init__(self):
        self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self._device_path = None
        self._write_path = None
        self._notify_path = None
        self._signal_id = None
        self._callback = None

    # -- D-Bus helpers -----------------------------------------------------
    def _call(self, path, iface, method, args=None, reply=None, timeout_ms=-1):
        return self._bus.call_sync(
            BLUEZ, path, iface, method, args,
            GLib.VariantType(reply) if reply else None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        )

    def _objects(self) -> dict:
        res = self._call("/", OM_IFACE, "GetManagedObjects",
                         reply="(a{oa{sa{sv}}})")
        return res.unpack()[0]

    def _get_prop(self, path, iface, name):
        res = self._call(path, PROPS_IFACE, "Get",
                         GLib.Variant("(ss)", (iface, name)), reply="(v)")
        return res.unpack()[0]

    def _adapter_path(self, objects: dict) -> str:
        for path, ifaces in objects.items():
            if ADAPTER_IFACE in ifaces and ifaces[ADAPTER_IFACE].get("Powered"):
                return path
        raise RuntimeError("no powered Bluetooth adapter")

    # -- link state --------------------------------------------------------
    @property
    def connected(self) -> bool:
        if self._device_path is None:
            return False
        try:
            return bool(self._get_prop(self._device_path, DEVICE_IFACE, "Connected"))
        except GLib.Error:
            # The object vanished -- BlueZ drops it when the pad goes away.
            return False

    # -- discovery ---------------------------------------------------------
    def find(self, timeout: float = 10.0, should_continue=None):
        """Return a Device, or None. None is NORMAL: the pad sleeps and stops
        advertising entirely when idle, and BLE cannot wake it.

        should_continue is polled between scan slices. A scan blocks the main
        loop, so without it a shutdown signal waits out the whole window.
        """
        objects = self._objects()
        adopted = find_connected(objects)
        if adopted is not None:
            return adopted

        adapter = self._adapter_path(objects)
        self._call(adapter, ADAPTER_IFACE, "SetDiscoveryFilter",
                   GLib.Variant("(a{sv})", ({"Transport": GLib.Variant("s", "le")},)))
        try:
            self._call(adapter, ADAPTER_IFACE, "StartDiscovery")
        except GLib.Error as exc:
            if "InProgress" not in str(exc):
                raise
        try:
            deadline = time.monotonic() + timeout
            while True:
                found = find_advertising(self._objects())
                if found is not None:
                    return found
                if time.monotonic() >= deadline:
                    return None
                if should_continue is not None and not should_continue():
                    return None
                time.sleep(SCAN_POLL_S)
        finally:
            try:
                self._call(adapter, ADAPTER_IFACE, "StopDiscovery")
            except GLib.Error:
                # Another client may own discovery; that is not our failure.
                pass

    # -- connection --------------------------------------------------------
    def connect(self, device) -> None:
        """Connect and wait for GATT to resolve.

        Raises GLib.Error on refusal. "in use by another device" arrives here:
        the pad has one BLE connection and a client on ANOTHER machine holds it,
        so the phone app and this bridge are mutually exclusive. That is a state
        to report, not a fault to retry (spec §8).

        ⚠ A second client on this host does not arrive here at all. BlueZ owns
        the link and shares it between its own clients (fact 5), so two bridges
        connect successfully and both poll the same pad.
        """
        self._call(device.path, DEVICE_IFACE, "Connect",
                   timeout_ms=CONNECT_TIMEOUT_MS)
        self._device_path = device.path
        deadline = time.monotonic() + 15.0
        while not self._get_prop(device.path, DEVICE_IFACE, "ServicesResolved"):
            if time.monotonic() >= deadline:
                raise RuntimeError("GATT services did not resolve")
            time.sleep(0.2)
        self._notify_path = self._char_path(device.path, CHAR_NOTIFY)
        self._write_path = self._char_path(device.path, CHAR_WRITE)

    def _char_path(self, device_path: str, uuid: str) -> str:
        for path, ifaces in self._objects().items():
            if not path.startswith(device_path + "/"):
                continue
            props = ifaces.get(CHAR_IFACE)
            if props and (props.get("UUID") or "").lower() == uuid:
                return path
        raise RuntimeError("characteristic %s not found" % uuid)

    # -- data --------------------------------------------------------------
    def subscribe(self, callback) -> None:
        """Route FE01 notifications to callback(sender, data).

        Notifications arrive as PropertiesChanged on the characteristic object,
        which needs a running GLib main loop to dispatch. This does NOT make the
        poll loop redundant: measured 2026-08-24, the pad answers queries and
        never volunteers a frame.
        """
        self._callback = callback
        self._signal_id = self._bus.signal_subscribe(
            BLUEZ, PROPS_IFACE, "PropertiesChanged", self._notify_path,
            None, Gio.DBusSignalFlags.NONE, self._on_properties_changed, None,
        )
        self._call(self._notify_path, CHAR_IFACE, "StartNotify")

    def _on_properties_changed(self, _conn, _sender, path, _iface, _signal,
                               params, *_user_data):
        _changed_iface, changed, _invalidated = params.unpack()
        value = changed.get("Value")
        if value is None:
            return
        self._callback(path, bytes(value))

    def write(self, payload: bytes) -> None:
        # "command" is write-without-response: FE02 offers no ACK path at all,
        # so confirmation only ever arrives as a later status frame.
        self._call(self._write_path, CHAR_IFACE, "WriteValue",
                   GLib.Variant("(aya{sv})",
                                (payload, {"type": GLib.Variant("s", "command")})))

    # -- teardown ----------------------------------------------------------
    def disconnect(self) -> None:
        if self._signal_id is not None:
            self._bus.signal_unsubscribe(self._signal_id)
            self._signal_id = None
        if self._device_path is not None:
            try:
                self._call(self._device_path, DEVICE_IFACE, "Disconnect")
            except GLib.Error:
                # Already gone. Absence is the pad's normal resting state.
                pass
        self._device_path = None
        self._write_path = None
        self._notify_path = None
        self._callback = None
