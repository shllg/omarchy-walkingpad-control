# tests/test_transport.py
from walkingpad.transport import matches_walkingpad, is_in_range, SERVICE_FE00


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
