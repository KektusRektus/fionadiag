"""Grafické rozhraní (tkinter, je součástí Pythonu i EXE)."""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__, dtc_db
from .core import DtcResult, dtc_rows
from .elm import list_ports
from .session import format_report, log_dir, open_diag, save_report
from .units import UNITS

PAD = dict(padx=6, pady=4)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"FionaDiag {__version__} – Ducati SuperSport 950")
        root.geometry("1100x720")
        root.minsize(900, 560)
        self.q: queue.Queue = queue.Queue()
        self.diag = None
        self.busy = False
        self.last = {"ident": {}, "dtc": [], "probe": [], "imu": None}
        self._build()
        self.refresh_ports()
        root.after(80, self._drain)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ layout
    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("vista" if "vista" in style.theme_names() else "clam")
        except tk.TclError:
            pass
        style.configure("Big.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

        top = ttk.Frame(self.root)
        top.pack(fill="x", **PAD)
        ttk.Label(top, text="Port:").pack(side="left")
        self.port = ttk.Combobox(top, width=28, state="readonly")
        self.port.pack(side="left", padx=4)
        ttk.Button(top, text="⟳", width=3, command=self.refresh_ports).pack(side="left")
        ttk.Label(top, text="Baud:").pack(side="left", padx=(10, 0))
        self.baud = ttk.Combobox(top, width=8, values=["38400", "115200", "500000"], state="readonly")
        self.baud.set("38400")
        self.baud.pack(side="left", padx=4)
        self.btn_conn = ttk.Button(top, text="Připojit", style="Accent.TButton", command=self.connect)
        self.btn_conn.pack(side="left", padx=6)
        ttk.Button(top, text="Odpojit", command=self.disconnect).pack(side="left")
        ttk.Button(top, text="Offline ukázka", command=lambda: self.connect(sim=True)).pack(side="left", padx=6)
        self.status = ttk.Label(top, text="Nepřipojeno", foreground="#a00")
        self.status.pack(side="right")

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, **PAD)
        self.nb = nb

        # --- Přehled
        f = ttk.Frame(nb)
        nb.add(f, text="Přehled")
        bar = ttk.Frame(f)
        bar.pack(fill="x", **PAD)
        ttk.Button(bar, text="Kompletní kontrola", style="Accent.TButton", command=self.full_check).pack(side="left")
        ttk.Button(bar, text="Jen sken jednotek", command=self.scan).pack(side="left", padx=6)
        ttk.Button(bar, text="Uložit zprávu", command=self.save).pack(side="right")
        self.units_tv = self._tree(f, ("unit", "addr", "state", "method", "count"),
                                   ("Jednotka", "TX/RX", "Stav", "Metoda čtení DTC", "DTC"), (220, 100, 160, 260, 60), height=5)
        for u in UNITS.values():
            self.units_tv.insert("", "end", iid=u.key, values=(u.name, f"{u.tx:03X}/{u.rx:03X}", "—", "", ""))
        self.units_tv.insert("", "end", iid="IMU", values=("IMU (pasivně)", "0x174/178/17C", "—", "pasivní poslech", ""))
        ttk.Label(f, text="Shrnutí", style="Big.TLabel").pack(anchor="w", **PAD)
        self.summary = ScrolledText(f, height=12, wrap="word", font=("Consolas", 10))
        self.summary.pack(fill="both", expand=True, **PAD)

        # --- DTC
        f = ttk.Frame(nb)
        nb.add(f, text="Chybové kódy")
        bar = ttk.Frame(f)
        bar.pack(fill="x", **PAD)
        ttk.Button(bar, text="Načíst všechny jednotky", style="Accent.TButton", command=self.read_all).pack(side="left")
        self.dtc_unit = ttk.Combobox(bar, width=24, state="readonly", values=[f"{u.key} – {u.name}" for u in UNITS.values()])
        self.dtc_unit.current(1)
        self.dtc_unit.pack(side="left", padx=(16, 4))
        ttk.Button(bar, text="Načíst jednotku", command=self.read_one).pack(side="left")
        ttk.Button(bar, text="Smazat DTC jednotky…", command=self.clear_one).pack(side="left", padx=6)
        ttk.Button(bar, text="Slovník MelcoDiag…", command=self.load_dict).pack(side="right")
        self.dtc_tv = self._tree(f, ("unit", "code", "state", "status", "desc"),
                                 ("Jednotka", "Kód", "Stav", "Status (bity)", "Popis"), (70, 80, 170, 260, 480))
        ttk.Label(f, foreground="#555", wraplength=1000, justify="left",
                  text="Stav se odvozuje ze stavového bajtu (bit0 = aktivní nyní, bit3 = potvrzená, bit5 = selhala od "
                       "posledního smazání) – stejně jako to zobrazuje MelcoDiag. 'Historická' = závada se teď neprojevuje.").pack(anchor="w", **PAD)

        # --- ABS / sonda
        f = ttk.Frame(nb)
        nb.add(f, text="ABS / sonda")
        bar = ttk.Frame(f)
        bar.pack(fill="x", **PAD)
        self.probe_unit = ttk.Combobox(bar, width=24, state="readonly", values=["ABS – ABS Bosch 9.1MP", "ECU – Motor (ECU)"])
        self.probe_unit.current(0)
        self.probe_unit.pack(side="left")
        ttk.Button(bar, text="Identifikace", command=self.ident_probe_unit).pack(side="left", padx=6)
        ttk.Button(bar, text="Najít metodu čtení DTC", style="Accent.TButton", command=self.probe).pack(side="left")
        ttk.Button(bar, text="Zapomenout naučenou metodu", command=self.forget).pack(side="left", padx=6)
        self.learned_lbl = ttk.Label(f, text="", style="Big.TLabel")
        self.learned_lbl.pack(anchor="w", **PAD)
        ttk.Label(f, foreground="#555", wraplength=1000, justify="left",
                  text="Sonda posílá pouze čtecí dotazy (relace 10 01/10 03, služby 19/17/13, OBD 03/07). Nic nezapisuje, "
                       "nekóduje ani neaktivuje. Jakmile jednotka vrátí seznam chyb, metoda se uloží a příště se použije "
                       "automaticky. Na ABS dosud selhaly 18 00 FF 00 (NRC 11), 19 02 FF a 19 0A (NRC 12) – i MelcoDiag "
                       "ABS DTC nečte.").pack(anchor="w", **PAD)
        self.probe_tv = self._tree(f, ("req", "note", "res"), ("Dotaz", "Poznámka", "Odpověď"), (200, 260, 560))

        # --- IMU
        f = ttk.Frame(nb)
        nb.add(f, text="IMU")
        bar = ttk.Frame(f)
        bar.pack(fill="x", **PAD)
        ttk.Label(bar, text="Doba poslechu (s):").pack(side="left")
        self.imu_sec = ttk.Spinbox(bar, from_=1, to=20, width=5)
        self.imu_sec.set("3")
        self.imu_sec.pack(side="left", padx=4)
        ttk.Button(bar, text="Zkontrolovat IMU", style="Accent.TButton", command=self.imu).pack(side="left", padx=6)
        self.imu_lbl = ttk.Label(f, text="", style="Big.TLabel", wraplength=1000, justify="left")
        self.imu_lbl.pack(anchor="w", **PAD)
        self.imu_txt = ScrolledText(f, height=10, wrap="word", font=("Consolas", 10))
        self.imu_txt.pack(fill="both", expand=True, **PAD)
        self.imu_txt.insert("end", "IMU (Bosch, inerciální plošina) nemá diagnostickou adresu. Když funguje, vysílá na CAN\n"
                                   "rámce 0x174, 0x178 a 0x17C. V srpnu 2026 je MelcoDiag na Fioně viděl při každém skenu;\n"
                                   "od 25. 10. 2026 chybí ve všech záznamech a BBS hlásí U100C (IMU – obecná chyba).\n"
                                   "Test je čistě pasivní – nic se neodesílá.\n")

        # --- Komunikace
        f = ttk.Frame(nb)
        nb.add(f, text="Komunikace")
        self.log = ScrolledText(f, wrap="none", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, **PAD)
        bar = ttk.Frame(f)
        bar.pack(fill="x", **PAD)
        ttk.Button(bar, text="Otevřít složku s logy", command=self.open_logs).pack(side="left")

        self.bottom = ttk.Label(self.root, text="Zapalování ON, motor OFF, motorka stojí. Připojte ELM327 a zvolte port.",
                                relief="sunken", anchor="w")
        self.bottom.pack(fill="x", side="bottom")

    def _tree(self, parent, cols, heads, widths, height=12):
        fr = ttk.Frame(parent)
        fr.pack(fill="both", expand=True, **PAD)
        tv = ttk.Treeview(fr, columns=cols, show="headings", height=height)
        for c, hd, w in zip(cols, heads, widths):
            tv.heading(c, text=hd)
            tv.column(c, width=w, anchor="w", stretch=(c == cols[-1]))
        sb = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tv.tag_configure("active", foreground="#b00000")
        return tv

    # ------------------------------------------------------------------ infrastruktura
    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", payload + "\n")
                    self.log.see("end")
                elif kind == "status":
                    self.bottom.config(text=payload)
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def ui(self, fn):
        self.q.put(("call", fn))

    def work(self, label, fn, need_conn=True):
        if self.busy:
            messagebox.showinfo("Pracuji", "Počkejte na dokončení předchozí operace.")
            return
        if need_conn and not self.diag:
            messagebox.showwarning("Nepřipojeno", "Nejdřív se připojte k ELM327 (nebo spusťte Offline ukázku).")
            return
        self.busy = True
        self.q.put(("status", label + " …"))

        def run():
            t0 = time.monotonic()
            try:
                fn()
                self.q.put(("status", f"{label}: hotovo ({time.monotonic() - t0:.1f} s)"))
            except Exception as e:  # noqa: BLE001 – chyba se zobrazí uživateli
                self.q.put(("status", f"{label}: CHYBA – {e}"))
                self.ui(lambda: messagebox.showerror("Chyba", f"{label}\n\n{e}"))
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()

    def refresh_ports(self):
        ports = list_ports()
        self.port["values"] = [f"{d}  {desc}" for d, desc in ports] or ["(žádný port nenalezen)"]
        pref = next((i for i, (d, desc) in enumerate(ports) if any(k in desc.upper() for k in ("FTDI", "USB SERIAL", "CH340", "ELM"))), 0)
        self.port.current(pref)

    # ------------------------------------------------------------------ akce
    def connect(self, sim=False):
        if self.diag:
            self.disconnect()
        port = self.port.get().split()[0] if self.port.get() else ""
        if not sim and (not port or port.startswith("(")):
            messagebox.showwarning("Port", "Vyberte COM port adaptéru ELM327.")
            return

        def go():
            d = open_diag(port, int(self.baud.get()), sim=sim, listener=lambda s: self.q.put(("log", s)),
                          progress=lambda s: self.q.put(("status", s)))
            v = d.elm.voltage()
            self.diag = d
            self.ui(lambda: self.status.config(text=f"{'SIMULACE' if sim else port} · {d.elm.version} · {v}",
                                               foreground="#070" if not sim else "#a60"))
            self.ui(self._show_learned)
        self.work("Připojování" + (" (simulace)" if sim else ""), go, need_conn=False)

    def disconnect(self):
        if self.diag:
            try:
                self.diag.elm.close()
            except Exception:  # noqa: BLE001
                pass
        self.diag = None
        self.status.config(text="Nepřipojeno", foreground="#a00")

    def on_close(self):
        self.disconnect()
        self.root.destroy()

    def _unit_from(self, combo):
        return UNITS[combo.get().split()[0]]

    def scan(self):
        def go():
            res = self.diag.scan()
            def upd():
                for k, v in res.items():
                    vals = list(self.units_tv.item(k, "values"))
                    vals[2] = v
                    self.units_tv.item(k, values=vals)
            self.ui(upd)
        self.work("Sken jednotek", go)

    def _show_dtc(self, results: list[DtcResult], replace=True):
        if replace:
            self.dtc_tv.delete(*self.dtc_tv.get_children())
        for r in results:
            if r.error:
                self.dtc_tv.insert("", "end", values=(r.unit, "—", "nelze číst", r.method, r.error))
            elif not r.dtcs:
                self.dtc_tv.insert("", "end", values=(r.unit, "—", "bez chyb", r.method, ""))
            for row, d in zip(dtc_rows(r), r.dtcs):
                self.dtc_tv.insert("", "end", values=row, tags=("active",) if d.active else ())
            vals = list(self.units_tv.item(r.unit, "values"))
            vals[3] = r.method or "neznámá – spusťte sondu"
            vals[4] = "—" if r.error else str(len(r.dtcs))
            self.units_tv.item(r.unit, values=vals)

    def read_all(self):
        def go():
            res = [self.diag.read_dtc(u) for u in UNITS.values()]
            self.last["dtc"] = res
            self.ui(lambda: self._show_dtc(res))
        self.work("Čtení DTC", go)

    def read_one(self):
        u = self._unit_from(self.dtc_unit)
        def go():
            r = self.diag.read_dtc(u)
            self.last["dtc"] = [x for x in self.last["dtc"] if x.unit != u.key] + [r]
            self.ui(lambda: self._show_dtc(self.last["dtc"]))
        self.work(f"Čtení DTC {u.key}", go)

    def clear_one(self):
        u = self._unit_from(self.dtc_unit)
        if not self.diag:
            return self.work("", lambda: None)
        if not self.diag.methods_for(u):
            messagebox.showwarning("Mazání", f"U jednotky {u.name} zatím neumíme DTC přečíst, takže nelze ověřit "
                                             "výsledek mazání. Nejdřív spusťte sondu na kartě 'ABS / sonda'.")
            return
        if not messagebox.askyesno("Smazat chybové kódy?",
                                   f"Opravdu smazat VŠECHNY uložené chybové kódy v jednotce\n\n   {u.name} ({u.tx:03X})?\n\n"
                                   "Před mazáním se kódy načtou a uloží do logu. Smazání neopraví závadu – "
                                   "pokud příčina trvá, kód se vrátí. Zapalování ON, motor OFF.", icon="warning"):
            return

        def go():
            before, outcome, after = self.diag.clear_dtc(u, confirmed=True)
            self.last["dtc"] = [x for x in self.last["dtc"] if x.unit != u.key] + [after]
            msg = f"Před: {len(before.dtcs)} kód(ů)\nPříkaz: {outcome}\nPo: " + (after.error or f"{len(after.dtcs)} kód(ů)")
            self.ui(lambda: (self._show_dtc(self.last["dtc"]), messagebox.showinfo("Mazání DTC", msg)))
        self.work(f"Mazání DTC {u.key}", go)

    def ident_probe_unit(self):
        u = self._unit_from(self.probe_unit)
        def go():
            items = self.diag.identify(u)
            self.last["ident"][u.key] = items
            def upd():
                self.probe_tv.delete(*self.probe_tv.get_children())
                for it in items:
                    self.probe_tv.insert("", "end", values=(it.request, "identifikace", it.text or it.response))
            self.ui(upd)
        self.work(f"Identifikace {u.key}", go)

    def probe(self):
        u = self._unit_from(self.probe_unit)
        self.probe_tv.delete(*self.probe_tv.get_children())

        def go():
            r = self.diag.probe(u)
            self.last["probe"] = [p for p in self.last["probe"] if p.unit != u.key] + [r]
            def upd():
                for a in r.attempts:
                    self.probe_tv.insert("", "end", values=(a.method, a.note, a.result), tags=("active",) if a.positive else ())
                self._show_learned()
                if r.found:
                    self._show_dtc([DtcResult(u.key, r.found.label, r.dtcs)], replace=False)
                    messagebox.showinfo("Sonda", f"Metoda nalezena: {r.found.label}\nNačteno {len(r.dtcs)} kód(ů). "
                                                 "Výsledek je na kartě Chybové kódy.")
                else:
                    messagebox.showinfo("Sonda", (r.aborted or "Žádný z bezpečných čtecích dotazů nevrátil seznam DTC.")
                                        + "\n\nCelý průběh je v tabulce a v logu – pošlete ho prosím k analýze.")
            self.ui(upd)
        self.work(f"Sonda {u.key}", go)

    def forget(self):
        if self.diag:
            u = self._unit_from(self.probe_unit)
            self.diag.learned.forget(u.key)
            self._show_learned()

    def _show_learned(self):
        if not self.diag:
            return
        parts = []
        for k in ("ABS", "ECU"):
            m = self.diag.learned.get(k)
            parts.append(f"{k}: {m.label if m else 'metoda zatím nenalezena'}")
            vals = list(self.units_tv.item(k, "values"))
            vals[3] = m.label if m else "neznámá – spusťte sondu"
            self.units_tv.item(k, values=vals)
        self.learned_lbl.config(text="Naučené metody →  " + "   |   ".join(parts))

    def imu(self):
        sec = float(self.imu_sec.get() or 3)

        def go():
            r = self.diag.imu_check(sec)
            self.last["imu"] = r
            def upd():
                self.imu_lbl.config(text=r.verdict, foreground="#070" if r.alive else "#b00000")
                self.imu_txt.insert("end", f"\n{time.strftime('%H:%M:%S')}  IMU rámce: {r.imu_counts}  | ostatní provoz: "
                                           f"{r.bus_frames} rámců, ID {', '.join(r.bus_ids)}\n")
                vals = list(self.units_tv.item("IMU", "values"))
                vals[2] = "vysílá" if r.alive else "NEVYSÍLÁ"
                self.units_tv.item("IMU", values=vals)
            self.ui(upd)
        self.work("Kontrola IMU", go)

    def full_check(self):
        def go():
            scan = self.diag.scan()
            ident = {u.key: self.diag.identify(u) for u in UNITS.values() if scan.get(u.key) == "odpovídá"}
            dtcs = [self.diag.read_dtc(u) for u in UNITS.values() if scan.get(u.key) == "odpovídá"]
            imu = self.diag.imu_check(3)
            self.last.update(ident=ident, dtc=dtcs, imu=imu)
            text = format_report(ident, dtcs, self.last["probe"], imu, self.diag.elm.voltage(), self.diag.elm.version)
            text += "\n\n" + findings(ident, dtcs, imu)

            def upd():
                for k, v in scan.items():
                    vals = list(self.units_tv.item(k, "values"))
                    vals[2] = v
                    self.units_tv.item(k, values=vals)
                vals = list(self.units_tv.item("IMU", "values"))
                vals[2] = "vysílá" if imu.alive else "NEVYSÍLÁ"
                self.units_tv.item("IMU", values=vals)
                self._show_dtc(dtcs)
                self._show_learned()
                self.summary.delete("1.0", "end")
                self.summary.insert("end", text)
            self.ui(upd)
        self.work("Kompletní kontrola", go)

    def save(self):
        if not any(self.last.values()):
            messagebox.showinfo("Zpráva", "Zatím není co uložit – spusťte Kompletní kontrolu.")
            return
        text = self.summary.get("1.0", "end").strip() or format_report(
            self.last["ident"], self.last["dtc"], self.last["probe"], self.last["imu"])
        p = save_report(text, self.last)
        messagebox.showinfo("Uloženo", f"Zpráva uložena:\n{p}")

    def load_dict(self):
        path = filedialog.askopenfilename(title="EnDtcM.dll ze složky MelcoDiag", filetypes=[("MelcoDiag slovník", "EnDtcM.dll"), ("Vše", "*.*")])
        if path:
            try:
                n = dtc_db.load_melco_dictionary(path)
                messagebox.showinfo("Slovník", f"Načteno {n} popisů kódů. Znovu načtěte DTC.")
            except OSError as e:
                messagebox.showerror("Slovník", str(e))

    def open_logs(self):
        import os
        import subprocess
        import sys
        p = str(log_dir())
        if sys.platform.startswith("win"):
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", p])


def findings(ident, dtcs, imu) -> str:
    """Krátká interpretace – co z výsledků plyne prakticky."""
    out = ["== Co z toho plyne =="]
    vins = {}
    for unit, items in ident.items():
        for it in items:
            if it.request in ("1A 90", "22 F1 90", "1A 8F 03", "1A 8F") and len(it.text) == 17:
                vins[unit] = it.text
    if len(set(vins.values())) > 1:
        out.append("• VIN se mezi jednotkami liší: " + ", ".join(f"{k}={v}" for k, v in vins.items())
                   + ". Některá jednotka pochází z jiného kusu / nebyla spárována (u přístrojovky souvisí s P064F).")
    if imu and not imu.alive and imu.bus_frames:
        out.append("• IMU nevysílá své CAN rámce. Prioritou je kontrola konektoru, napájení a CAN vodičů IMU.")
    for r in dtcs:
        act = [d.code for d in r.dtcs if d.active]
        if act:
            out.append(f"• {r.unit}: AKTIVNÍ závady {', '.join(act)}")
        elif r.dtcs:
            out.append(f"• {r.unit}: {len(r.dtcs)} uložených historických kódů (žádný aktivní).")
        elif r.error and r.unit in ("ABS", "ECU"):
            out.append(f"• {r.unit}: chybové kódy zatím nečteme – spusťte sondu (karta ABS / sonda).")
    if len(out) == 1:
        out.append("• Nic podezřelého.")
    return "\n".join(out)


def run():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
