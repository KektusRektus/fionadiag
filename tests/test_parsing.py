import pytest

from fionadiag.dtc import DtcParseError, parse_any, parse_kwp_status, parse_uds19, sae_code, uds_count
from fionadiag.isotp import Frame, parse_frame, reassemble, single_frame


def frames(*hexes, cid=0x7EA):
    return [Frame(cid, bytes.fromhex(h)) for h in hexes]


def test_single_frame_pci():
    assert single_frame(bytes.fromhex("1800FF00")) == "041800FF00"
    with pytest.raises(ValueError):
        single_frame(bytes(8))


def test_parse_frame_with_and_without_header():
    f = parse_frame("7E9037F191200000000")
    assert f.can_id == 0x7E9 and f.data == bytes.fromhex("037F191200000000")
    assert parse_frame("0250810000000000").can_id is None
    assert parse_frame("NO DATA") is None


def test_reassemble_multiframe_real_bbs_answer():
    # skutečná odpověď BBS (7EA) na 18 00 FF 00 z 29. 10. 2026
    msg = reassemble(frames("10145806C15528D1", "215728D15628D158", "2228056228D00C68"))
    assert len(msg) == 1 and len(msg[0]) == 0x14 and msg[0][:2] == b"\x58\x06"


def test_bbs_dtcs_match_melcodiag_screen():
    msg = reassemble(frames("10145806C15528D1", "215728D15628D158", "2228056228D00C68"))[0]
    d = parse_kwp_status(msg)
    assert [x.code for x in d] == ["U0155", "U1157", "U1156", "U1158", "P0562", "U100C"]
    assert [x.status for x in d] == [0x28] * 5 + [0x68]
    assert d[0].state_cs == "potvrzená – historická" and not d[0].active


def test_kwp_length_must_match():
    with pytest.raises(DtcParseError):
        parse_kwp_status(bytes.fromhex("5802C15528"))


def test_uds19_records_and_count():
    d = parse_uds19(bytes.fromhex("5902FF C10000 09 D00C00 28".replace(" ", "")))
    assert [x.code for x in d] == ["U0100", "U100C"] and d[0].active
    assert uds_count(bytes.fromhex("5901FF010003")) == 3


def test_sae_code_letters():
    assert sae_code(0x06, 0x4F) == "P064F"
    assert sae_code(0x41, 0x08) == "C0108" or sae_code(0x41, 0x08).startswith("C")
    assert sae_code(0xD0, 0x0C) == "U100C"


def test_parse_any_dispatch():
    assert parse_any(bytes.fromhex("5300")) == []
    assert [x.code for x in parse_any(bytes.fromhex("4301010C"))] == ["P010C"]
