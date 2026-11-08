import json
import re
from pathlib import Path

from fionadiag.core import Diagnostics, LearnedStore
from fionadiag.elm import ELM327
from fionadiag.protocol import READ_ONLY_SIDS
from fionadiag.sim import DEFAULT_FIXTURE, SimBike, SimSerial
from fionadiag.units import UNITS


def make(bike=None, tmp=None):
    io = SimSerial(bike or SimBike.from_fixture())
    elm = ELM327(io, timeout=2)
    elm.initialize()
    return Diagnostics(elm, LearnedStore(Path(tmp) / "learned.json") if tmp else None), io


def test_fixture_is_anonymised():
    text = DEFAULT_FIXTURE.read_text(encoding="utf-8")
    d = json.loads(text)
    msgs = "".join("".join(f[2:] if f[0] == "1" else f[2:] for f in v) for v in d["responses"].values())
    vins = set(re.findall(r"ZDM[0-9A-Z]{14}", bytes.fromhex("".join(c for c in msgs if c in "0123456789ABCDEF")[: len(msgs) // 2 * 2]).decode("latin1", "ignore")))
    assert all(v.startswith("ZDMTEST") for v in vins)


def test_init_uses_melcodiag_framing():
    _, io = make()
    assert {"ATSP6", "ATCAF0", "ATH1", "ATS0"} <= set(io.sent)


def test_identify_abs_and_dash_vin_mismatch():
    d, _ = make()
    abs_id = {i.request: i.text for i in d.identify(UNITS["ABS"])}
    assert abs_id["1A 91"] == "542.4.100.1B"
    dash = {i.request: i.text for i in d.identify(UNITS["DASH"])}
    assert dash["1A 8F"] != abs_id["1A 90"]  # přístrojovka nese jiné VIN


def test_read_known_units():
    d, _ = make()
    bbs = d.read_dtc(UNITS["BBS"])
    assert bbs.ok and len(bbs.dtcs) == 6
    dash = d.read_dtc(UNITS["DASH"])
    assert len(dash.dtcs) == 15 and dash.dtcs[0].code == "P064F"
    assert not d.read_dtc(UNITS["ABS"]).ok  # metoda zatím neznámá


def test_probe_only_sends_read_services_and_learns(tmp_path):
    bike = SimBike.from_fixture()
    # virtuální ABS, který umí KWP 17 FF 00 (scénář "sonda uspěje")
    bike.responses["7E1:17FF00"] = ["085702C10120D12168"]
    d, io = make(bike, tmp_path)
    start = len(io.sent)
    r = d.probe(UNITS["ABS"])
    assert r.found and r.found.request.hex() == "17ff00"
    assert [x.code for x in r.dtcs] == ["U0101", "U1121"]
    for line in io.sent[start:]:
        if not line.upper().startswith("AT"):
            raw = bytes.fromhex(line)
            assert raw[1] in READ_ONLY_SIDS, line
    # metoda se pamatuje i v nové relaci
    d2, _ = make(bike, tmp_path)
    assert len(d2.read_dtc(UNITS["ABS"]).dtcs) == 2


def test_clear_uses_matching_family_and_requires_confirmation(tmp_path):
    bike = SimBike.from_fixture()
    bike.responses["7E1:17FF00"] = ["085702C10120D12168"]
    bike.responses["7E1:14FF00"] = ["0154000000000000"]
    d, io = make(bike, tmp_path)
    d.probe(UNITS["ABS"])
    import pytest
    with pytest.raises(PermissionError):
        d.clear_dtc(UNITS["ABS"], confirmed=False)
    before, outcome, after = d.clear_dtc(UNITS["ABS"], confirmed=True)
    assert "14 FF 00" in outcome and "OK 54" in outcome
    assert "0314FF00" in io.sent


def test_probe_on_real_abs_data_finds_nothing_but_is_complete():
    d, _ = make()
    r = d.probe(UNITS["ABS"])
    assert r.found is None and not r.aborted and len(r.attempts) >= 15


def test_response_pending_then_final():
    bike = SimBike({"7E0:1001": ["0250010000000000"], "7E0:1902FF": ["037F197800000000", "037F1922CCCCCCCC"]})
    d, _ = make(bike)
    resp = d.elm.request(0x7E0, 0x7E8, bytes.fromhex("1902FF"))
    assert resp.nrc == 0x22 and not resp.pending_only


def test_imu_dead_and_alive():
    d, _ = make()
    r = d.imu_check(0.3)
    assert not r.alive and r.bus_frames > 0 and "NEVYSÍLÁ" in r.verdict
    d2, _ = make(SimBike.from_fixture(imu_alive=True))
    assert d2.imu_check(0.3).alive
