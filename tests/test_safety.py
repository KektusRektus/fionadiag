import pytest

from fionadiag.protocol import SafetyError, check_request


@pytest.mark.parametrize("payload", ["2EF19000", "3101FF00", "2701", "1101", "1002", "1085", "2F000103", "3400", "28", "85"])
def test_dangerous_services_are_blocked(payload):
    with pytest.raises(SafetyError):
        check_request(bytes.fromhex(payload))


@pytest.mark.parametrize("payload", ["1001", "1003", "1081", "1902FF", "1800FF00", "17FF00", "13", "1A90", "22F190", "3E00", "03", "20"])
def test_read_services_allowed(payload):
    check_request(bytes.fromhex(payload))


def test_clear_needs_explicit_flag():
    with pytest.raises(SafetyError):
        check_request(bytes.fromhex("14FF00"))
    check_request(bytes.fromhex("14FF00"), allow_clear=True)
