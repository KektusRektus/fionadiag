"""Parsování výstupu ELM327 (ATH1/ATS0/ATCAF0) a skládání ISO-TP zpráv."""
from __future__ import annotations

import re
from dataclasses import dataclass

ELM_ERRORS = (
    "NO DATA", "CAN ERROR", "BUFFER FULL", "BUS ERROR", "BUS INIT", "STOPPED",
    "UNABLE TO CONNECT", "<RX ERROR", "FB ERROR", "DATA ERROR", "ACT ALERT", "LV RESET",
)
_HEX = re.compile(r"^[0-9A-F]+$")


@dataclass(frozen=True)
class Frame:
    can_id: int | None  # None = výstup bez hlavičky (ATH0)
    data: bytes

    def __str__(self) -> str:
        cid = f"{self.can_id:03X} " if self.can_id is not None else ""
        return cid + self.data.hex(" ").upper()


def split_lines(raw: str) -> list[str]:
    raw = raw.replace(">", "")
    return [ln.strip() for ln in re.split(r"[\r\n]+", raw) if ln.strip()]


def parse_frame(line: str) -> Frame | None:
    """Jeden řádek ELM -> CAN rámec. 11bit hlavička = 3 hex znaky (lichá délka)."""
    s = line.replace(" ", "").upper()
    if not s or not _HEX.match(s):
        return None
    if len(s) % 2 == 1 and len(s) >= 5:
        return Frame(int(s[:3], 16), bytes.fromhex(s[3:]))
    if len(s) % 2 == 0 and len(s) >= 2:
        return Frame(None, bytes.fromhex(s))
    return None


def errors_in(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        up = ln.upper()
        for e in ELM_ERRORS:
            if e in up:
                out.append(e)
        if up == "?":
            out.append("?")
    return out


def reassemble(frames: list[Frame]) -> list[bytes]:
    """Složí ISO-TP zprávy (SF / FF+CF). Flow-control rámce ignoruje."""
    msgs: list[bytes] = []
    buf = bytearray()
    need = 0
    for fr in frames:
        d = fr.data
        if not d:
            continue
        kind = d[0] >> 4
        if kind == 0:
            n = d[0] & 0x0F
            if 0 < n <= len(d) - 1:
                msgs.append(bytes(d[1:1 + n]))
            need = 0
        elif kind == 1 and len(d) >= 2:
            need = ((d[0] & 0x0F) << 8) | d[1]
            buf = bytearray(d[2:])
            if len(buf) >= need:
                msgs.append(bytes(buf[:need]))
                need = 0
        elif kind == 2 and need:
            buf += d[1:]
            if len(buf) >= need:
                msgs.append(bytes(buf[:need]))
                need = 0
    return msgs


def single_frame(payload: bytes) -> str:
    """Request pro ATCAF0: PCI + data (max. 7 bajtů – všechny naše dotazy jsou krátké)."""
    if not 1 <= len(payload) <= 7:
        raise ValueError("Podporovány jsou jen jednorámcové požadavky (1–7 bajtů).")
    return f"{len(payload):02X}" + payload.hex().upper()
