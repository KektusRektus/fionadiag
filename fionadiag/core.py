"""Vysoká úroveň: sken jednotek, identifikace, čtení/mazání DTC, sonda metody ABS, kontrola IMU."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from . import dtc_db
from .dtc import Dtc, DtcParseError, parse_any, uds_count
from .elm import ELM327, Response
from .isotp import parse_frame
from .protocol import hx
from .units import PROBE_CANDIDATES, UNITS, DtcMethod, Unit, clear_for, h

IMU_IDS = (0x174, 0x178, 0x17C)
Progress = Callable[[str], None]


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "FionaDiag"
    p.mkdir(parents=True, exist_ok=True)
    return p


class LearnedStore:
    """Zapamatované metody čtení DTC nalezené sondou (per jednotka)."""

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "learned_methods.json")
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, unit: str) -> DtcMethod | None:
        d = self.data.get(unit)
        if not d:
            return None
        return DtcMethod(d["key"], bytes.fromhex(d["request"]),
                         bytes.fromhex(d["session"]) if d.get("session") else None, "naučeno sondou " + d.get("date", ""))

    def put(self, unit: str, m: DtcMethod) -> None:
        self.data[unit] = {"key": m.key, "request": m.request.hex(), "session": m.session.hex() if m.session else "",
                           "date": time.strftime("%Y-%m-%d %H:%M")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def forget(self, unit: str) -> None:
        if self.data.pop(unit, None) is not None:
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


@dataclass
class IdentItem:
    request: str
    response: str
    text: str


@dataclass
class DtcResult:
    unit: str
    method: str = ""
    dtcs: list[Dtc] = field(default_factory=list)
    raw: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class ProbeAttempt:
    method: str
    note: str
    result: str
    positive: bool


@dataclass
class ProbeResult:
    unit: str
    attempts: list[ProbeAttempt] = field(default_factory=list)
    found: DtcMethod | None = None
    dtc_count_hint: int | None = None
    dtcs: list[Dtc] = field(default_factory=list)
    aborted: str = ""


@dataclass
class ImuResult:
    seconds: float
    imu_counts: dict[str, int]
    bus_ids: list[str]
    bus_frames: int
    alive: bool
    verdict: str


def printable(b: bytes) -> str:
    s = b.lstrip(b"\x00")
    txt = "".join(chr(c) if 32 <= c < 127 else "·" for c in s).strip("·").strip()
    return txt if txt and sum(c.isalnum() for c in txt) >= max(2, len(txt) // 2) else ""


class Diagnostics:
    def __init__(self, elm: ELM327, learned: LearnedStore | None = None, progress: Progress | None = None):
        self.elm = elm
        self.learned = learned or LearnedStore()
        self.progress = progress or (lambda s: None)

    # ------------------------------------------------------------ přítomnost a identifikace
    def scan(self) -> dict[str, str]:
        out = {}
        for u in UNITS.values():
            r = self.elm.request(u.tx, u.rx, h("1001"))
            out[u.key] = "odpovídá" if r.answered else ("bez odpovědi" if not r.errors else ", ".join(set(r.errors)))
            self.progress(f"{u.name}: {out[u.key]}")
        return out

    def identify(self, unit: Unit) -> list[IdentItem]:
        items = []
        for req in unit.ident:
            r = self.elm.request(unit.tx, unit.rx, req)
            f = r.final
            text = ""
            if r.positive and f:
                text = printable(f[3:] if f[0] == 0x62 else f[2:]) or hx(f[2:])
            items.append(IdentItem(hx(req), r.summary() if not r.positive else hx(f), text))
        return items

    # ------------------------------------------------------------ DTC
    def methods_for(self, unit: Unit) -> list[DtcMethod]:
        learned = self.learned.get(unit.key)
        return ([learned] if learned else []) + list(unit.dtc_methods)

    def _run(self, unit: Unit, m: DtcMethod) -> Response:
        if m.session:
            self.elm.request(unit.tx, unit.rx, m.session)
        return self.elm.request(unit.tx, unit.rx, m.request)

    def read_dtc(self, unit: Unit) -> DtcResult:
        methods = self.methods_for(unit)
        if not methods:
            return DtcResult(unit.key, error="Metoda čtení zatím není známá – spusťte 'Najít metodu čtení DTC' (sonda).")
        last = DtcResult(unit.key)
        for m in methods:
            r = self._run(unit, m)
            last = DtcResult(unit.key, m.label, raw=hx(r.final) if r.final else r.summary())
            if r.positive:
                try:
                    last.dtcs = parse_any(r.final)
                    break
                except DtcParseError as e:
                    last.error = f"Odpověď nelze dekódovat: {e}"
            else:
                last.error = r.summary()
        if unit.close:
            self.elm.request(unit.tx, unit.rx, unit.close)
        return last

    def probe(self, unit: Unit, candidates: tuple[DtcMethod, ...] = PROBE_CANDIDATES) -> ProbeResult:
        """Zkouší pouze čtecí dotazy, dokud jednotka nevrátí seznam DTC. Nalezenou metodu uloží."""
        res = ProbeResult(unit.key)
        session = None
        silent = 0
        for m in candidates:
            if m.session and m.session != session:
                rs = self.elm.request(unit.tx, unit.rx, m.session)
                res.attempts.append(ProbeAttempt(hx(m.session), "relace", rs.summary(), rs.positive))
                session = m.session
            r = self.elm.request(unit.tx, unit.rx, m.request)
            res.attempts.append(ProbeAttempt(hx(m.request), m.note, r.summary(), r.positive))
            self.progress(f"{m.label}: {r.summary()}")
            silent = 0 if r.answered else silent + 1
            if silent >= 3:
                res.aborted = "Jednotka přestala odpovídat – sonda ukončena (zkontrolujte zapalování a kabel)."
                break
            if not r.positive:
                continue
            cnt = uds_count(r.final)
            if cnt is not None:
                res.dtc_count_hint = cnt
                continue
            try:
                res.dtcs = parse_any(r.final)
            except DtcParseError as e:
                res.attempts[-1].result += f"  (nelze dekódovat: {e})"
                continue
            res.found = m
            self.learned.put(unit.key, m)
            break
        if session and session != h("1001"):
            self.elm.request(unit.tx, unit.rx, h("1001"))
        return res

    def clear_dtc(self, unit: Unit, confirmed: bool) -> tuple[DtcResult, str, DtcResult]:
        """Smaže DTC jednotky. Vyžaduje confirmed=True (GUI se ptá uživatele). Vrací (před, výsledek, po)."""
        if not confirmed:
            raise PermissionError("Mazání DTC nebylo potvrzeno.")
        methods = self.methods_for(unit)
        if not methods:
            raise RuntimeError("Nejdřív je potřeba umět DTC přečíst (sonda), jinak nelze ověřit výsledek mazání.")
        before = self.read_dtc(unit)
        m = methods[0]
        cmd = unit.clear if (unit.clear and not self.learned.get(unit.key)) else clear_for(m)
        if m.session:
            self.elm.request(unit.tx, unit.rx, m.session)
        r = self.elm.request(unit.tx, unit.rx, cmd, allow_clear=True)
        outcome = f"{hx(cmd)} → {r.summary()}"
        time.sleep(0.3)
        after = self.read_dtc(unit)
        return before, outcome, after

    # ------------------------------------------------------------ IMU
    def imu_check(self, seconds: float = 3.0) -> ImuResult:
        """Pasivní kontrola: vysílá IMU své rámce 0x174/0x178/0x17C? (v srpnu ano, od 25. 10. ne)."""
        self.elm.set_filter(None)
        bus_lines = self.elm.monitor(min(1.5, seconds))
        bus = Counter(f"{fr.can_id:03X}" for fr in (parse_frame(x) for x in bus_lines) if fr and fr.can_id is not None)
        self.elm.set_filter(0x170, 0x7F3)          # propustí 170/174/178/17C
        lines = self.elm.monitor(seconds)
        self.elm.set_filter(None)
        c = Counter(fr.can_id for fr in (parse_frame(x) for x in lines) if fr and fr.can_id is not None)
        counts = {f"{i:03X}": c.get(i, 0) for i in IMU_IDS}
        alive = all(v > 0 for v in counts.values())
        if not bus:
            verdict = "Na sběrnici není žádný provoz – je zapnuté zapalování?"
        elif alive:
            verdict = "IMU vysílá (všechny tři rámce přítomné)."
        elif any(counts.values()):
            verdict = "IMU vysílá jen částečně – podezření na závadu IMU nebo kabeláže."
        else:
            verdict = ("IMU NEVYSÍLÁ: rámce 0x174/0x178/0x17C chybí, ostatní provoz běží. "
                       "Zkontrolujte konektor, napájení a CAN vodiče IMU, pak samotnou jednotku.")
        return ImuResult(seconds, counts, sorted(bus), sum(bus.values()), alive, verdict)


def dtc_rows(res: DtcResult) -> list[tuple[str, str, str, str, str]]:
    rows = []
    for d in res.dtcs:
        desc, src = dtc_db.describe(d.code)
        rows.append((res.unit, d.code, d.state_cs, d.status_text(), desc + (f"  [{dtc_db.SOURCE_CS.get(src, src)}]" if src else "")))
    return rows


def to_jsonable(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj
