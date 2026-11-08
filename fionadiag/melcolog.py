"""Čtení logů MelcoCOM Loggeru (COM proxy mezi MelcoDiagem a ELM327) a rekonstrukce transakcí."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .isotp import parse_frame, reassemble

_LINE = re.compile(r"^(\S+) (\S+) -> (\S+) \d+B: ([0-9A-F ]+) \|")


@dataclass
class Transaction:
    time: str
    tx: str | None       # ATSH
    rx: str | None       # ATCRA
    command: str          # co klient poslal (AT nebo hex s PCI)
    lines: list[str]      # odpověď ELM (bez echa a promptu)

    @property
    def is_at(self) -> bool:
        return self.command.upper().startswith("AT")

    def payload(self) -> bytes | None:
        """Hex požadavek bez PCI (předpokládá ATCAF0 rámcování, jaké používá MelcoDiag)."""
        c = self.command.replace(" ", "")
        if self.is_at or len(c) < 4 or len(c) % 2 or not re.fullmatch(r"[0-9A-Fa-f]+", c):
            return None
        b = bytes.fromhex(c)
        n = b[0]
        if 0 < n <= 7 and len(b) >= n + 1:
            return b[1:1 + n]
        return b  # ATCAF1 – bez PCI

    def response_frames(self) -> list[bytes]:
        return [f.data for f in (parse_frame(x) for x in self.lines) if f]

    def messages(self) -> list[bytes]:
        return reassemble([f for f in (parse_frame(x) for x in self.lines) if f])


def transactions(path: str | Path) -> list[Transaction]:
    out: list[Transaction] = []
    pending: tuple[str, str] | None = None
    rxbuf = ""
    sh = cra = None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        ts, src, _dst, hexs = m.groups()
        data = bytes.fromhex(hexs.replace(" ", "")).decode("latin1")
        if src.upper().startswith("ELM327"):
            rxbuf += data
            while ">" in rxbuf:
                resp, rxbuf = rxbuf.split(">", 1)
                lines = [x.strip() for x in re.split(r"[\r\n]+", resp) if x.strip()]
                if pending:
                    if lines and lines[0].upper() == pending[1].upper():
                        lines = lines[1:]
                    out.append(Transaction(pending[0], sh, cra, pending[1], lines))
                    pending = None
        else:
            for part in data.split("\r"):
                part = part.strip()
                if not part:
                    continue
                pending = (ts, part)
                up = part.upper()
                if up.startswith("ATSH"):
                    sh = up[4:]
                elif up.startswith("ATCRA"):
                    cra = up[5:] or None
    return out
