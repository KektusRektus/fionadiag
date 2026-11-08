"""Otevření spojení (skutečný COM port nebo simulátor) a sestavení textové zprávy."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import __version__
from .core import Diagnostics, DtcResult, ImuResult, LearnedStore, ProbeResult, data_dir, dtc_rows, to_jsonable
from .elm import ELM327, TrafficLog, open_serial
from .sim import SimBike, SimSerial


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def open_diag(port: str | None, baud: int = 38400, sim: bool = False, imu_alive: bool = False,
              listener: Callable[[str], None] | None = None, learned: LearnedStore | None = None,
              progress: Callable[[str], None] | None = None) -> Diagnostics:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log = TrafficLog(log_dir() / f"traffic_{stamp}{'_SIM' if sim else ''}.jsonl", listener)
    io = SimSerial(SimBike.from_fixture(imu_alive=imu_alive)) if sim else open_serial(port, baud)
    elm = ELM327(io, log)
    elm.initialize()
    if sim and learned is None:
        learned = LearnedStore(data_dir() / "learned_methods_SIM.json")
    return Diagnostics(elm, learned, progress)


def format_report(ident: dict, dtc: list[DtcResult], probe: list[ProbeResult], imu: ImuResult | None,
                  voltage: str = "", elm_version: str = "") -> str:
    out = [f"FionaDiag {__version__} – zpráva {time.strftime('%Y-%m-%d %H:%M:%S')}",
           f"Adaptér: {elm_version}   Napětí: {voltage}", ""]
    if ident:
        out.append("== Identifikace ==")
        for unit, items in ident.items():
            out.append(f"[{unit}]")
            for it in items:
                out.append(f"  {it.request:10} {it.text or it.response}")
        out.append("")
    if dtc:
        out.append("== Chybové kódy ==")
        for r in dtc:
            if r.error:
                out.append(f"[{r.unit}] {r.method}: {r.error}")
                continue
            out.append(f"[{r.unit}] {r.method}: {len(r.dtcs)} kód(ů)")
            for _, code, state, status, desc in dtc_rows(r):
                out.append(f"  {code:8} {state:24} {status:40} {desc}")
        out.append("")
    for p in probe:
        out.append(f"== Sonda metody čtení DTC – {p.unit} ==")
        for a in p.attempts:
            out.append(f"  {a.method:22} {a.result}")
        out.append(f"  Výsledek: {'NALEZENO ' + p.found.label if p.found else 'metoda nenalezena'}"
                   + (f" (počet DTC dle 19 01: {p.dtc_count_hint})" if p.dtc_count_hint is not None else ""))
        if p.aborted:
            out.append("  " + p.aborted)
        out.append("")
    if imu:
        out.append("== IMU (pasivní poslech CAN) ==")
        out.append(f"  Rámce IMU za {imu.seconds:.0f} s: {imu.imu_counts}")
        out.append(f"  Ostatní provoz: {imu.bus_frames} rámců, ID {', '.join(imu.bus_ids)}")
        out.append(f"  {imu.verdict}")
    return "\n".join(out)


def save_report(text: str, data: dict) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    p = log_dir() / f"report_{stamp}.txt"
    p.write_text(text, encoding="utf-8")
    p.with_suffix(".json").write_text(json.dumps(to_jsonable(data), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
