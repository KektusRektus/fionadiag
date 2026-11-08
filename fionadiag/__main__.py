"""Příkazová řádka. Bez argumentů spustí grafické rozhraní.

  python -m fionadiag ports
  python -m fionadiag --port COM3 scan | ident ABS | dtc all | probe ABS | imu | clear BBS --yes | report
  python -m fionadiag --sim report            (offline simulátor ze záznamů Fiony)
  python -m fionadiag analyze-log melco_com_20261026_183108.log
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .units import UNITS


def _units(arg: str):
    if arg.lower() == "all":
        return list(UNITS.values())
    key = arg.upper()
    if key not in UNITS:
        sys.exit(f"Neznámá jednotka {arg}. Možnosti: {', '.join(UNITS)} nebo all")
    return [UNITS[key]]


def analyze_log(path: str) -> None:
    from .dtc import DtcParseError, parse_any
    from .melcolog import transactions
    from .protocol import describe, hx
    from .core import printable
    for t in transactions(path):
        pl = t.payload()
        if pl is None:
            continue
        msgs = t.messages()
        final = next((m for m in reversed(msgs) if not (len(m) >= 3 and m[0] == 0x7F and m[2] == 0x78)), None)
        extra = ""
        if final and final[:1] in (b"\x58", b"\x57", b"\x59", b"\x53"):
            try:
                extra = "  DTC: " + " ".join(f"{d.code}/{d.status:02X}" if d.status is not None else d.code
                                             for d in parse_any(final))
            except DtcParseError as e:
                extra = f"  ({e})"
        elif final and final[:1] in (b"\x5a", b"\x62"):
            extra = "  '" + printable(final[2:] if final[0] == 0x5A else final[3:]) + "'"
        status = describe(pl, final) if msgs else " | ".join(t.lines) or "—"
        print(f"{t.time[11:23]} {t.tx or '---'}->{t.rx or '---'} {hx(pl):14} {status[:60]}{extra}")


def main(argv=None) -> int:
    if argv is None and len(sys.argv) == 1:
        from .gui import run
        run()
        return 0
    ap = argparse.ArgumentParser(prog="fionadiag", description=f"FionaDiag {__version__} – Ducati SuperSport 950 přes ELM327")
    ap.add_argument("--port", help="COM port ELM327 (např. COM3)")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--sim", action="store_true", help="offline simulátor místo motorky")
    ap.add_argument("--sim-imu-ok", action="store_true", help="simulátor: IMU vysílá")
    ap.add_argument("-v", "--verbose", action="store_true", help="vypisovat veškerou komunikaci")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports")
    sub.add_parser("gui")
    sub.add_parser("scan")
    p = sub.add_parser("ident"); p.add_argument("unit", nargs="?", default="all")
    p = sub.add_parser("dtc"); p.add_argument("unit", nargs="?", default="all")
    p = sub.add_parser("probe"); p.add_argument("unit", nargs="?", default="ABS")
    p = sub.add_parser("clear"); p.add_argument("unit"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("imu"); p.add_argument("--seconds", type=float, default=3.0)
    sub.add_parser("report")
    p = sub.add_parser("send", help="ruční ČTECÍ dotaz, např.: send ABS 19 01 FF")
    p.add_argument("unit"); p.add_argument("hex", nargs="+")
    p.add_argument("--session", help="relace před dotazem, např. 1003")
    p.add_argument("--learn", action="store_true", help="pokud vrátí seznam DTC, uložit jako metodu čtení")
    p = sub.add_parser("analyze-log"); p.add_argument("file")
    a = ap.parse_args(argv)

    if a.cmd == "ports":
        from .elm import list_ports
        for dev, desc in list_ports():
            print(f"{dev:10} {desc}")
        return 0
    if a.cmd == "gui":
        from .gui import run
        run()
        return 0
    if a.cmd == "analyze-log":
        analyze_log(a.file)
        return 0
    if not a.port and not a.sim:
        ap.error("zadejte --port COMx nebo --sim")

    from .core import dtc_rows
    from .session import format_report, open_diag, save_report
    d = open_diag(a.port, a.baud, a.sim, a.sim_imu_ok, listener=print if a.verbose else None,
                  progress=lambda s: print("  ·", s))
    try:
        print(f"Adaptér {d.elm.version}, napětí {d.elm.voltage()}")
        if a.cmd == "scan":
            for k, v in d.scan().items():
                print(f"{k:5} {v}")
        elif a.cmd == "ident":
            for u in _units(a.unit):
                print(f"[{u.key}] {u.name}")
                for it in d.identify(u):
                    print(f"  {it.request:10} {it.text or it.response}")
        elif a.cmd == "dtc":
            for u in _units(a.unit):
                r = d.read_dtc(u)
                print(f"[{u.key}] {r.method or '-'}: {r.error or str(len(r.dtcs)) + ' kód(ů)'}")
                for _, code, state, status, desc in dtc_rows(r):
                    print(f"  {code:8} {state:24} {desc}")
        elif a.cmd == "probe":
            for u in _units(a.unit):
                r = d.probe(u)
                print(f"[{u.key}] " + (f"NALEZENO: {r.found.label} ({len(r.dtcs)} DTC)" if r.found else "metoda nenalezena"))
        elif a.cmd == "clear":
            u = _units(a.unit)[0]
            if not a.yes:
                print("Mazání DTC vyžaduje --yes. Nic nebylo odesláno.")
                return 2
            before, outcome, after = d.clear_dtc(u, confirmed=True)
            print(f"Před: {len(before.dtcs)}  |  {outcome}  |  Po: {len(after.dtcs)} {after.error}")
        elif a.cmd == "imu":
            r = d.imu_check(a.seconds)
            print(r.imu_counts, "\n" + r.verdict)
        elif a.cmd == "send":
            from .dtc import DtcParseError, parse_any
            from .units import DtcMethod
            u = _units(a.unit)[0]
            req = bytes.fromhex("".join(a.hex))
            sess = bytes.fromhex(a.session) if a.session else None
            if sess:
                print("relace:", d.elm.request(u.tx, u.rx, sess).summary())
            r = d.elm.request(u.tx, u.rx, req)
            print(r.summary())
            if r.positive:
                try:
                    codes = parse_any(r.final)
                    print("DTC:", " ".join(x.code for x in codes) or "žádné")
                    if a.learn:
                        d.learned.put(u.key, DtcMethod("manual", req, sess, "ručně"))
                        print(f"Uloženo jako metoda čtení DTC pro {u.key}.")
                except DtcParseError:
                    pass
        elif a.cmd == "report":
            ident = {u.key: d.identify(u) for u in UNITS.values()}
            dtcs = [d.read_dtc(u) for u in UNITS.values()]
            imu = d.imu_check()
            text = format_report(ident, dtcs, [], imu, d.elm.voltage(), d.elm.version)
            print(text)
            print("\nUloženo:", save_report(text, {"ident": ident, "dtc": dtcs, "imu": imu}))
    except PermissionError as e:
        print(f"ODMÍTNUTO: {e}")
        return 3
    finally:
        d.elm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
