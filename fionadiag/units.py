"""Řídicí jednotky Fiony a metody čtení DTC.

Vše níže je odvozeno ze skutečných odpovědí motorky v logách MelcoCOM 25. 10. – 1. 11. 2026:
  7E0/7E8  motor (UDS: 50 01 00 32 13 88, 22 F180 = "DU CAX20", F184 = SW "DN2LT…")
  7E1/7E9  ABS Bosch (1A90 = VIN, 1A91 = díl "542.4.100.1B", 1A96 = Bosch "…0265…")
  7E2/7EA  BBS (KWP: 10 81 → 50 81, 18 00 FF 00 → 58 …, 1A 8F 03 = VIN)
  7E3/7EB  přístrojová deska (KWP: 1A 81 = díl "40611877A", 18 00 FF 00 → 58 …)
  IMU      nemá vlastní diagnostickou adresu – ověřuje se pasivně (CAN 0x174/0x178/0x17C).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DtcMethod:
    key: str
    request: bytes
    session: bytes | None = None   # relace před čtením (např. 10 03), None = žádná změna
    note: str = ""

    @property
    def label(self) -> str:
        s = (self.session.hex(" ").upper() + " → ") if self.session else ""
        return s + self.request.hex(" ").upper()


@dataclass(frozen=True)
class Unit:
    key: str
    name: str
    tx: int
    rx: int
    family: str                         # "UDS" | "KWP" | "BOSCH"
    ident: tuple[bytes, ...] = ()
    dtc_methods: tuple[DtcMethod, ...] = ()   # ověřené metody (v pořadí)
    clear: bytes | None = None          # ověřený / předpokládaný příkaz mazání
    close: bytes | None = None          # ukončení relace (KWP 20)
    notes: str = ""
    probe: bool = False                 # metoda čtení DTC se má teprve najít sondou


def h(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


KWP18 = DtcMethod("kwp18", h("18 00 FF 00"), note="KWP2000 readDTCByStatus – ověřeno na BBS a přístrojovce")

UNITS: dict[str, Unit] = {
    "ECU": Unit("ECU", "Motor (ECU)", 0x7E0, 0x7E8, "UDS",
                ident=(h("22F190"), h("22F180"), h("22F181"), h("22F184")),
                dtc_methods=(),  # 19 02 FF → 7F 19 22 (conditionsNotCorrect) – hledá se sondou
                clear=h("14FFFFFF"), probe=True,
                notes="19 02 FF vrací NRC 22. Sonda zkusí OBD-II mód 03 a další masky."),
    "ABS": Unit("ABS", "ABS Bosch 9.1MP", 0x7E1, 0x7E9, "BOSCH",
                ident=(h("1A90"), h("1A91"), h("1A94"), h("1A96"), h("1A98")),
                dtc_methods=(), clear=None, probe=True,
                notes="18 → NRC 11 (služba není), 19 02 FF a 19 0A → NRC 12 (podfunkce není). "
                      "Ani MelcoDiag ABS DTC nečte. Metodu najde sonda a uloží si ji."),
    "BBS": Unit("BBS", "BBS (Black Box System)", 0x7E2, 0x7EA, "KWP",
                ident=(h("1A8F03"),),
                dtc_methods=(DtcMethod("kwp18_s81", h("18 00 FF 00"), h("10 81"), "jako MelcoDiag"),),
                clear=h("14FF00"), close=h("20")),
    "DASH": Unit("DASH", "Přístrojová deska", 0x7E3, 0x7EB, "KWP",
                 ident=(h("1A81"), h("1A8B"), h("1A98"), h("1A8F")),
                 dtc_methods=(KWP18,), clear=h("14FF00")),
}

# Kandidáti pro sondu – výhradně čtecí služby. Pořadí: nejpravděpodobnější první.
# Pozn.: 18 xx na ABS vrací NRC 11 = služba vůbec neexistuje, proto ji nezkoušíme znovu.
S_DEF, S_EXT = h("1001"), h("1003")
PROBE_CANDIDATES: tuple[DtcMethod, ...] = (
    DtcMethod("uds_count", h("1901FF"), S_DEF, "UDS počet DTC"),
    DtcMethod("uds_02_08", h("190208"), S_DEF, "UDS maska potvrzené"),
    DtcMethod("uds_02_09", h("190209"), S_DEF, "UDS maska aktivní+potvrzené"),
    DtcMethod("uds_02_2F", h("19022F"), S_DEF),
    DtcMethod("uds_0F", h("190FFF"), S_DEF, "UDS mirror memory"),
    DtcMethod("uds_0E", h("190E"), S_DEF, "UDS poslední potvrzený"),
    DtcMethod("uds_0B", h("190B"), S_DEF, "UDS první selhaný"),
    DtcMethod("kwp17_ff00", h("17FF00"), S_DEF, "KWP readStatusOfDTC – vše"),
    DtcMethod("kwp17_ffff", h("17FFFF"), S_DEF),
    DtcMethod("kwp13", h("13"), S_DEF, "KWP readDTC"),
    DtcMethod("kwp13_ff00", h("13FF00"), S_DEF),
    DtcMethod("obd03", h("03"), None, "OBD-II mód 03 (uložené)"),
    DtcMethod("obd07", h("07"), None, "OBD-II mód 07 (čekající)"),
    DtcMethod("uds_02_ff_ext", h("1902FF"), S_EXT, "UDS v rozšířené relaci"),
    DtcMethod("uds_count_ext", h("1901FF"), S_EXT),
    DtcMethod("uds_02_08_ext", h("190208"), S_EXT),
    DtcMethod("kwp17_ext", h("17FF00"), S_EXT),
    DtcMethod("kwp13_ext", h("13FF00"), S_EXT),
    DtcMethod("uds_02_ff", h("1902FF"), S_DEF, "kontrola (dříve NRC 12/22)"),
)


def clear_for(method: DtcMethod) -> bytes:
    """Příkaz mazání odpovídající rodině nalezené čtecí služby."""
    sid = method.request[0]
    if sid == 0x19:
        return h("14FFFFFF")
    if sid in (0x03, 0x07):
        return h("04")  # OBD-II mód 04
    return h("14FF00")  # KWP2000 clearDiagnosticInformation, skupina vše
