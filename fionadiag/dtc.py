"""Dekódování odpovědí na čtení chybových kódů (KWP2000 18/17/13, UDS 19, OBD 03)."""
from __future__ import annotations

from dataclasses import dataclass

STATUS_BITS = (
    (0x01, "testFailed", "aktivní nyní"),
    (0x02, "testFailedThisOperationCycle", "selhal v tomto cyklu"),
    (0x04, "pendingDTC", "čekající"),
    (0x08, "confirmedDTC", "potvrzená"),
    (0x10, "testNotCompletedSinceLastClear", "test neproběhl od smazání"),
    (0x20, "testFailedSinceLastClear", "selhal od posledního smazání"),
    (0x40, "testNotCompletedThisOperationCycle", "test neproběhl v tomto cyklu"),
    (0x80, "warningIndicatorRequested", "rozsvícena kontrolka"),
)


@dataclass(frozen=True)
class Dtc:
    code: str            # např. U0155
    raw: str             # původní bajty kódu (hex)
    status: int | None   # stavový bajt, None pokud ho služba nevrací

    @property
    def active(self) -> bool:
        return self.status is not None and bool(self.status & 0x01)

    @property
    def state_cs(self) -> str:
        if self.status is None:
            return "uložená (bez stavu)"
        if self.status & 0x01:
            return "AKTIVNÍ"
        if self.status & 0x08:
            return "potvrzená – historická"
        if self.status & 0x04:
            return "čekající"
        if self.status & 0x20:
            return "historická"
        return "uložená"

    def status_text(self) -> str:
        if self.status is None:
            return "—"
        bits = [cs for bit, _, cs in STATUS_BITS if self.status & bit]
        return f"{self.status:02X}: " + (", ".join(bits) if bits else "žádný bit")


def sae_code(hi: int, lo: int) -> str:
    return "PCBU"[hi >> 6] + f"{(hi >> 4) & 3}{hi & 0x0F:X}{lo:02X}"


class DtcParseError(ValueError):
    pass


def parse_kwp_status(msg: bytes, sid: int = 0x58) -> list[Dtc]:
    """KWP2000 58/57: <SID> <počet> [hi lo status]*počet. Délka musí sedět přesně."""
    if len(msg) < 2 or msg[0] != sid:
        raise DtcParseError("Není odpověď 0x%02X" % sid)
    n = msg[1]
    if len(msg) != 2 + 3 * n:
        raise DtcParseError(f"Délka {len(msg)} neodpovídá {n} záznamům (čekáno {2 + 3*n}).")
    return [Dtc(sae_code(msg[2+3*i], msg[3+3*i]), msg[2+3*i:4+3*i].hex().upper(), msg[4+3*i]) for i in range(n)]


def parse_kwp13(msg: bytes) -> list[Dtc]:
    """KWP2000 53: 53 <počet> [hi lo]*počet."""
    if len(msg) < 2 or msg[0] != 0x53:
        raise DtcParseError("Není odpověď 0x53")
    n = msg[1]
    if len(msg) < 2 + 2 * n:
        raise DtcParseError("Krátká odpověď 0x53")
    return [Dtc(sae_code(msg[2+2*i], msg[3+2*i]), msg[2+2*i:4+2*i].hex().upper(), None) for i in range(n)]


def parse_uds19(msg: bytes) -> list[Dtc]:
    """UDS 59 <subfn> <availabilityMask> [DTC_hi DTC_mid FTB status]*  (subfn 02, 0A, 0F, 0B–0E, 15...)."""
    if len(msg) < 3 or msg[0] != 0x59:
        raise DtcParseError("Není odpověď 0x59")
    if msg[1] == 0x01:
        return []  # počet – zpracuje dtc_count()
    body = msg[3:]
    if len(body) % 4:
        raise DtcParseError(f"Tělo 59 {msg[1]:02X} nemá násobek 4 bajtů ({len(body)}).")
    out = []
    for i in range(0, len(body), 4):
        hi, mid, ftb, st = body[i:i+4]
        code = sae_code(hi, mid) + (f"-{ftb:02X}" if ftb else "")
        out.append(Dtc(code, body[i:i+3].hex().upper(), st))
    return out


def uds_count(msg: bytes) -> int | None:
    if len(msg) >= 6 and msg[0] == 0x59 and msg[1] == 0x01:
        return (msg[4] << 8) | msg[5]
    return None


def parse_obd03(msg: bytes) -> list[Dtc]:
    """OBD-II mód 03 přes CAN: 43 <počet> [hi lo]*."""
    if not msg or msg[0] not in (0x43, 0x47, 0x4A):
        raise DtcParseError("Není odpověď OBD 43/47/4A")
    pairs = msg[2:] if len(msg) % 2 == 0 else msg[1:]
    return [Dtc(sae_code(pairs[i], pairs[i+1]), pairs[i:i+2].hex().upper(), None)
            for i in range(0, len(pairs) - 1, 2) if pairs[i] or pairs[i+1]]


def parse_any(msg: bytes) -> list[Dtc]:
    if not msg:
        raise DtcParseError("Prázdná odpověď")
    sid = msg[0]
    if sid in (0x58, 0x57):
        return parse_kwp_status(msg, sid)
    if sid == 0x53:
        return parse_kwp13(msg)
    if sid == 0x59:
        return parse_uds19(msg)
    if sid in (0x43, 0x47, 0x4A):
        return parse_obd03(msg)
    raise DtcParseError(f"Neznámý typ odpovědi 0x{sid:02X}")
