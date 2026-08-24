# tests/test_transport.py
from walkingpad.transport import (
    SERVICE_FE00,
    find_advertising,
    find_connected,
    is_in_range,
    matches_walkingpad,
)


def test_matches_on_advertised_service_uuid():
    assert matches_walkingpad({"Name": "anything", "UUIDs": [SERVICE_FE00]})


def test_matches_on_ks_name_prefix():
    assert matches_walkingpad({"Name": "KS-BLC2"})


def test_service_uuid_match_is_case_insensitive():
    assert matches_walkingpad({"Name": "x", "UUIDs": [SERVICE_FE00.upper()]})


def test_matches_on_alias_when_name_is_absent():
    # BlueZ omits Name for a device it has never fully resolved, but Alias is
    # always present, so the name-prefix fallback must consult both.
    assert matches_walkingpad({"Alias": "KS-BLC2"})


def test_ignores_unrelated_devices():
    assert not matches_walkingpad({"Name": "Glove80"})
    assert not matches_walkingpad({"Name": None})
    assert not matches_walkingpad({})


def test_in_range_requires_a_live_rssi():
    # Measured: BlueZ keeps a device object long after the pad has stopped
    # advertising, and the object alone is NOT evidence the pad is reachable.
    # A live RSSI during discovery is.
    assert is_in_range({"RSSI": -61})
    assert not is_in_range({})
    assert not is_in_range({"RSSI": None})


def _objects(**props):
    return {"/org/bluez/hci0/dev_X": {"org.bluez.Device1": props}}


def test_adopts_a_link_bluez_already_holds():
    # A connected pad stops advertising, so it has no RSSI. Requiring one here
    # would strand the bridge after an ungraceful exit: discovery could never
    # see the pad again, and the bar would read "absent" with the pad awake.
    found = find_connected(_objects(Name="KS-BLC2", Connected=True, Address="AA:BB"))
    assert found is not None and found.address == "AA:BB"


def test_does_not_adopt_a_pad_that_is_merely_known():
    assert find_connected(_objects(Name="KS-BLC2", Connected=False)) is None


def test_does_not_adopt_a_connected_non_pad():
    assert find_connected(_objects(Name="WH-1000XM4", Connected=True)) is None


def test_advertising_search_requires_a_live_rssi():
    assert find_advertising(_objects(Name="KS-BLC2", RSSI=-61)) is not None
    assert find_advertising(_objects(Name="KS-BLC2")) is None
