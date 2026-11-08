"""ELM327 ovladač. Inicializace kopíruje sekvenci MelcoDiagu, která na Fioně prokazatelně funguje
(CAN 11bit 500 kbit/s, ruční PCI rámcování ATCAF0, hlavičky ATH1 pro ověření odesílatele)."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .isotp import Frame, errors_in, parse_frame, reassemble, single_frame, split_lines
from .protocol import check_request, describe, hx

INIT_SEQUENCE = (
    # (příkaz, povinný) – stejné pořadí jako MelcoDiag 2.0.5.5 v logu 26. 10.
    ("ATE0", True), ("ATL0", False), ("ATS0", True), ("ATH1", True), ("ATSP6", True),
    ("ATAT1", False), ("ATST64", False), ("ATAL", False), ("ATCAF0", True),
    ("ATFCSD300001", False), ("ATFCSM0", False),
)


class SerialLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def close(self) -> None: ...
    @property
    def in_waiting(self) -> int: ...


class ElmError(RuntimeError):
    pass


@dataclass
class Response:
    tx: int
    rx: int
    request: bytes
    lines: list[str] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    messages: list[bytes] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def final(self) -> bytes | None:
        """Poslední zpráva, která není 'response pending'."""
        for m in reversed(self.messages):
            if not (len(m) >= 3 and m[0] == 0x7F and m[2] == 0x78):
                return m
        return None

    @property
    def pending_only(self) -> bool:
        return bool(self.messages) and self.final is None

    @property
    def positive(self) -> bool:
        f = self.final
        return bool(f) and f[0] == self.request[0] + 0x40

    @property
    def nrc(self) -> int | None:
        f = self.final
        if f and len(f) >= 3 and f[0] == 0x7F:
            return f[2]
        return None

    @property
    def answered(self) -> bool:
        return self.final is not None

    def summary(self) -> str:
        if self.errors and not self.messages:
            return ", ".join(sorted(set(self.errors)))
        if self.pending_only:
            return "jen 7F 78 (odpověď se připravuje) – timeout"
        return describe(self.request, self.final)


class TrafficLog:
    """Kompletní záznam komunikace (JSONL) – každý TX/RX s časem. Nic se nezahazuje."""

    def __init__(self, path: Path | None = None, listener: Callable[[str], None] | None = None):
        self.path = path
        self.listener = listener
        self._fh = None
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, kind: str, text: str, **extra) -> None:
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}",
               "kind": kind, "text": text, **extra}
        with self._lock:
            if self._fh:
                self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._fh.flush()
        if self.listener:
            self.listener(f"{rec['t'][11:]} {kind:3} {text}")

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class ELM327:
    def __init__(self, io: SerialLike, log: TrafficLog | None = None, timeout: float = 4.0):
        self.io = io
        self.log = log or TrafficLog()
        self.timeout = timeout
        self.version = ""
        self._tx: int | None = None
        self._rx: int | None = None
        self._lock = threading.RLock()

    # ---------------- nízká úroveň ----------------
    def _read_until_prompt(self, timeout: float) -> str:
        end = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < end:
            n = getattr(self.io, "in_waiting", 0) or 1
            chunk = self.io.read(n)
            if chunk:
                buf += chunk
                if b">" in chunk:
                    break
            else:
                time.sleep(0.005)
        else:
            raise ElmError(f"Adaptér neodpověděl do {timeout:.1f} s (chybí '>').")
        return buf.decode("latin1", errors="replace")

    def raw(self, cmd: str, timeout: float | None = None) -> list[str]:
        with self._lock:
            self.log.write("TX", cmd)
            self.io.write((cmd + "\r").encode("ascii"))
            text = self._read_until_prompt(timeout or self.timeout)
            lines = [ln for ln in split_lines(text) if ln.upper() != cmd.upper()]  # odfiltruj echo
            self.log.write("RX", " | ".join(lines))
            return lines

    def at(self, cmd: str, required: bool = True, timeout: float | None = None) -> list[str]:
        lines = self.raw(cmd, timeout)
        if required and not any(x in ("OK",) or x.startswith("ELM") for x in lines) and cmd not in ("ATRV", "ATI", "ATDPN"):
            raise ElmError(f"{cmd}: neočekávaná odpověď {lines}")
        return lines

    # ---------------- inicializace ----------------
    def initialize(self) -> str:
        with self._lock:
            lines = self.raw("ATZ", timeout=5)
            self.version = next((x for x in lines if "ELM" in x.upper()), "")
            if not self.version:
                lines = self.raw("ATI")
                self.version = next((x for x in lines if "ELM" in x.upper()), "")
            if not self.version:
                raise ElmError("Na portu není ELM327 (žádná identifikace na ATZ/ATI).")
            for cmd, req in INIT_SEQUENCE:
                self.at(cmd, required=req)
            self._tx = self._rx = None
            return self.version

    def voltage(self) -> str:
        lines = self.raw("ATRV")
        return next((x for x in lines if x.upper().endswith("V")), "?")

    def set_target(self, tx: int, rx: int) -> None:
        with self._lock:
            if self._tx != tx:
                self.at(f"ATSH{tx:03X}")
                self._tx = tx
            if self._rx != rx:
                self.at(f"ATCRA{rx:03X}")
                self._rx = rx

    # ---------------- diagnostické dotazy ----------------
    def request(self, tx: int, rx: int, payload: bytes, *, allow_clear: bool = False,
                timeout: float | None = None, pending_wait: float = 6.0) -> Response:
        check_request(payload, allow_clear=allow_clear)
        with self._lock:
            self.set_target(tx, rx)
            t0 = time.monotonic()
            lines = self.raw(single_frame(payload), timeout)
            resp = self._build(tx, rx, payload, lines)
            if resp.pending_only:
                self._wait_pending(resp, pending_wait)
            resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
            self.log.write("DIA", f"{tx:03X}->{rx:03X} {hx(payload)} => {resp.summary()}")
            return resp

    def _build(self, tx: int, rx: int, payload: bytes, lines: list[str]) -> Response:
        frames = [f for f in (parse_frame(x) for x in lines) if f]
        frames = [f for f in frames if f.can_id in (None, rx)]
        return Response(tx, rx, payload, lines, frames, reassemble(frames), errors_in(lines))

    def _wait_pending(self, resp: Response, seconds: float) -> None:
        """ECU poslala 7F xx 78 a ELM mezitím ukončil čekání. Pasivně posloucháme (ATMA s filtrem
        na RX ID), dokud nepřijde finální odpověď. Nic dalšího se neodesílá."""
        lines = self.monitor(seconds, stop_when=lambda fr: not (len(fr.data) >= 4 and fr.data[1] == 0x7F and fr.data[3] == 0x78))
        extra = [f for f in (parse_frame(x) for x in lines) if f and f.can_id in (None, resp.rx)]
        resp.lines += lines
        resp.frames += extra
        resp.messages = reassemble(resp.frames)
        if extra and extra[0].data and extra[0].data[0] >> 4 == 1:
            resp.errors.append("MULTIFRAME_IN_MONITOR")  # ELM v ATMA neposílá flow-control → zopakovat dotaz

    def monitor(self, seconds: float, stop_when: Callable[[Frame], bool] | None = None) -> list[str]:
        """Pasivní poslech (ATMA) po zadanou dobu. Respektuje aktuální filtr ATCRA / ATCF+ATCM."""
        with self._lock:
            self.log.write("TX", "ATMA")
            self.io.write(b"ATMA\r")
            end = time.monotonic() + seconds
            buf = ""
            stop = False
            while time.monotonic() < end and not stop:
                chunk = self.io.read(getattr(self.io, "in_waiting", 0) or 1)
                if not chunk:
                    time.sleep(0.005)
                    continue
                buf += chunk.decode("latin1", errors="replace")
                if stop_when:
                    complete = buf.replace("\n", "\r").rsplit("\r", 1)[0]
                    for ln in split_lines(complete):
                        fr = parse_frame(ln)
                        if fr and fr.data and fr.data[0] >> 4 in (0, 1) and stop_when(fr):
                            stop = True
                if "BUFFER FULL" in buf.upper():
                    break
            self.io.write(b"\r")  # libovolný znak ukončí monitor
            try:
                buf += self._read_until_prompt(2.0)
            except ElmError:
                pass
            lines = [ln for ln in split_lines(buf) if ln.upper() not in ("ATMA", "STOPPED")]
            self.log.write("RX", f"[monitor {len(lines)} řádků] " + " | ".join(lines[:40]))
            return lines

    def set_filter(self, can_filter: int | None, mask: int | None = None) -> None:
        """Pro pasivní poslech: None = bez filtru. Po použití volat set_target znovu (resetuje se cache)."""
        with self._lock:
            self.at("ATCRA")
            if can_filter is not None:
                self.at(f"ATCF{can_filter:03X}")
                self.at(f"ATCM{(mask if mask is not None else 0x7FF):03X}")
            self._rx = None

    def close(self) -> None:
        try:
            self.io.close()
        finally:
            self.log.close()


def open_serial(port: str, baud: int = 38400):
    import serial  # pyserial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, baud, 0.05, 2
    s.rts = False
    s.dtr = False
    s.open()
    return s


def list_ports() -> list[tuple[str, str]]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [(p.device, p.description or "") for p in lp.comports()]
