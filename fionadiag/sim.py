"""Simulovaný ELM327 + motorka. Slouží k testům a k offline ukázce aplikace bez motorky.

Odpovědi pochází ze skutečných záznamů Fiony (anonymizovaný fixture tests/fixtures/fiona_responses.json).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

DEFAULT_FIXTURE = Path(__file__).resolve().parent / "data" / "fiona_responses.json"


class SimBike:
    def __init__(self, responses: dict[str, list[str]] | None = None, bus: list[tuple[int, str]] | None = None,
                 voltage: str = "12.5V"):
        self.responses = responses or {}
        self.bus = bus or []
        self.voltage = voltage

    @classmethod
    def from_fixture(cls, path: Path = DEFAULT_FIXTURE, imu_alive: bool = False) -> "SimBike":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        bus = [(int(i, 16), data) for i, data in d.get("bus", [])]
        if imu_alive:
            bus += [(0x174, "0000000000000000"), (0x178, "0000000000000000"), (0x17C, "0000000000000000")] * 3
        return cls(d["responses"], bus)

    def answer(self, tx: int, payload: bytes) -> list[str] | None:
        key = f"{tx:03X}:{payload.hex().upper()}"
        if key in self.responses:
            return self.responses[key]
        if any(k.startswith(f"{tx:03X}:") for k in self.responses):
            # Jednotka existuje, ale tento dotaz nebyl nikdy zaznamenán → simulace NRC 0x11.
            return [f"037F{payload[0]:02X}1100000000"]
        return None


class SimSerial:
    """Chová se jako pyserial port s ELM327 v1.5 (ATE0/ATH1/ATS0/ATCAF0)."""

    def __init__(self, bike: SimBike, latency: float = 0.0):
        self.bike = bike
        self.latency = latency
        self.out = bytearray()
        self.cmd = ""
        self.echo = True
        self.headers = False
        self.tx = 0x7DF
        self.cra: int | None = None
        self.cf: int | None = None
        self.cm = 0x7FF
        self.monitoring = False
        self._mon_idx = 0
        self.sent: list[str] = []
        self._lock = threading.Lock()

    # pyserial API
    @property
    def in_waiting(self) -> int:
        if self.monitoring and not self.out:
            self._emit_monitor()
        return len(self.out)

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            if self.monitoring and not self.out:
                self._emit_monitor()
            chunk = bytes(self.out[:size])
            del self.out[:size]
            return chunk

    def write(self, data: bytes) -> int:
        with self._lock:
            if self.monitoring:
                self.monitoring = False
                self.out += b"\r\r>"
                return len(data)
            self.cmd += data.decode("ascii")
            while "\r" in self.cmd:
                line, self.cmd = self.cmd.split("\r", 1)
                self._handle(line.strip())
            return len(data)

    def close(self) -> None:
        pass

    # ELM logika
    def _put(self, text: str) -> None:
        if self.latency:
            time.sleep(self.latency)
        self.out += text.encode("latin1")

    def _accepts(self, can_id: int) -> bool:
        if self.cra is not None:
            return can_id == self.cra
        if self.cf is not None:
            return (can_id & self.cm) == (self.cf & self.cm)
        return True

    def _emit_monitor(self) -> None:
        frames = [(i, d) for i, d in self.bike.bus if self._accepts(i)]
        if not frames:
            time.sleep(0.01)
            return
        i, d = frames[self._mon_idx % len(frames)]
        self._mon_idx += 1
        self.out += f"{i:03X}{d}\r".encode()
        time.sleep(0.002)

    def _handle(self, line: str) -> None:
        self.sent.append(line)
        up = line.upper().replace(" ", "")
        echo = (line + "\r") if self.echo else ""
        if not up:
            return
        if up.startswith("AT"):
            a = up[2:]
            if a == "Z":
                self.echo, self.headers, self.cra, self.cf = True, False, None, None
                self._put(echo + "\r\rELM327 v1.5\r\r>")
                return
            if a == "I":
                self._put(echo + "ELM327 v1.5\r\r>")
                return
            if a == "RV":
                self._put(echo + self.bike.voltage + "\r\r>")
                return
            if a == "MA":
                self._put(echo)
                self.monitoring = True
                return
            if a.startswith("E"):
                self.echo = a[1:] == "1"
            elif a.startswith("H"):
                self.headers = a[1:] == "1"
            elif a.startswith("SH"):
                self.tx = int(a[2:], 16)
            elif a.startswith("CRA"):
                self.cra = int(a[3:], 16) if a[3:] else None
                if self.cra is not None:
                    self.cf = None
            elif a.startswith("CF"):
                self.cf = int(a[2:], 16)
            elif a.startswith("CM"):
                self.cm = int(a[2:], 16)
            self._put(echo + "OK\r\r>")
            return
        try:
            raw = bytes.fromhex(up)
        except ValueError:
            self._put(echo + "?\r\r>")
            return
        payload = raw[1:1 + raw[0]] if raw and 0 < raw[0] <= 7 else raw
        frames = self.bike.answer(self.tx, payload)
        rx = self.cra if self.cra is not None else self.tx + 8
        if not frames:
            self._put(echo + "NO DATA\r\r>")
            return
        body = "".join((f"{rx:03X}" if self.headers else "") + f + "\r" for f in frames)
        self._put(echo + body + "\r>")
