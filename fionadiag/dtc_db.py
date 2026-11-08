"""Popisy chybových kódů. Každý záznam nese zdroj, aby bylo jasné, jak moc mu věřit.

Zdroje:
  SAE     – obecná definice SAE J2012 (platí pro kódy 0xxx napříč výrobci)
  MELCO   – text, který pro Fionu zobrazil MelcoDiag (snímky obrazovky)
  MANUAL  – servisní manuály příbuzných modelů (Hypermotard 950 / Multistrada 950), ne SuperSport MY22
Volitelně lze načíst slovník MelcoDiagu (soubor EnDtcM.dll – je to tabulkový text), pokud ho máte.
"""
from __future__ import annotations

import re
from pathlib import Path

BUILTIN: dict[str, tuple[str, str]] = {
    # SAE J2012 – obecné
    "U0100": ("Ztráta komunikace s řídicí jednotkou motoru (ECM)", "SAE"),
    "U0141": ("Ztráta komunikace s řídicí jednotkou karoserie (BCM / u Ducati BBS)", "SAE"),
    "U0155": ("Ztráta komunikace s přístrojovou deskou", "SAE"),
    "U0121": ("Ztráta komunikace s jednotkou ABS", "SAE"),
    "U0151": ("Ztráta komunikace s jednotkou airbagu/SRS", "SAE"),
    "P0562": ("Nízké napětí palubní sítě", "SAE"),
    "P0563": ("Vysoké napětí palubní sítě", "SAE"),
    "P064F": ("Zjištěn neautorizovaný software/kalibrace", "SAE"),
    # MelcoDiag (Fiona)
    "U100C": ("BBS / snímač IMU – obecná chyba", "MELCO"),
    # Příbuzné modely (Hypermotard 950 SP / Multistrada 950) – neověřeno pro SuperSport MY22
    "C1092": ("Snímač IMU – nesprávná montáž", "MANUAL"),
    "C1094": ("Snímač IMU – nesouhlasící signál", "MANUAL"),
    "U2927": ("Snímač IMU – nesprávný signál", "MANUAL"),
    "U2928": ("Snímač IMU – timeout komunikace", "MANUAL"),
    "U2332": ("Nesprávný snímač IMU", "MANUAL"),
    "C1335": ("Nesprávná varianta", "MANUAL"),
    "U2922": ("Sběrnice CAN – BUS-OFF", "MANUAL"),
    "U2923": ("Chyba komunikace CAN s přístrojovou deskou", "MANUAL"),
    "U2924": ("Chyba komunikace CAN s jednotkou BBS", "MANUAL"),
    "U2925": ("Vadná CAN jednotka – obecná HW chyba", "MANUAL"),
    "U2926": ("Chyba komunikace CAN s BBS – čítač rámců", "MANUAL"),
    "U2929": ("Chyba komunikace CAN s řídicí jednotkou motoru", "MANUAL"),
    "U2930": ("Sběrnice CAN – podpětí", "MANUAL"),
    "U2931": ("Sběrnice CAN – přepětí", "MANUAL"),
    "C1033": ("Snímač otáček předního kola – přerušení / zkrat", "MANUAL"),
    "C1034": ("Snímač otáček předního kola – nesouhlasící signál", "MANUAL"),
    "C1031": ("Snímač otáček zadního kola – přerušení / zkrat", "MANUAL"),
    "C1032": ("Snímač otáček zadního kola – nesouhlasící signál", "MANUAL"),
    "C1024": ("Chyba porovnání rychlosti předního a zadního kola", "MANUAL"),
    "C1331": ("Snímač tlaku přední brzdy – HW chyba", "MANUAL"),
    "C1332": ("Snímač tlaku přední brzdy – chyba offsetu", "MANUAL"),
    "C1333": ("Chyba napájení snímače tlaku", "MANUAL"),
    "C1054": ("Závada předního vstupního ventilu", "MANUAL"),
    "C1049": ("Závada předního výstupního ventilu", "MANUAL"),
    "C1052": ("Závada zadního vstupního ventilu", "MANUAL"),
    "C1048": ("Závada zadního výstupního ventilu", "MANUAL"),
    "C1015": ("Závada motoru čerpadla ABS", "MANUAL"),
    "C1014": ("Závada relé ventilů ABS", "MANUAL"),
    "C1021": ("Závada řídicí jednotky ABS", "MANUAL"),
    "C1058": ("Řídicí jednotka ABS – podpětí", "MANUAL"),
    "C1059": ("Řídicí jednotka ABS – přepětí", "MANUAL"),
    "C1334": ("Chyba čtení kódované varianty z EEPROM ABS", "MANUAL"),
}

SOURCE_CS = {"SAE": "SAE obecně", "MELCO": "MelcoDiag", "MANUAL": "manuál příbuzného modelu",
             "MELCO_DICT": "slovník MelcoDiag"}
_CODE = re.compile(r"^[PCBU][0-3][0-9A-F]{3}$", re.I)
_external: dict[str, tuple[str, str]] = {}


def load_melco_dictionary(path: str | Path) -> int:
    """Načte EnDtcM.dll z instalace MelcoDiagu (tab-separated text). Vrací počet kódů."""
    count = 0
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 3 and _CODE.match(parts[0]):
            desc = " | ".join(p for p in parts[2:] if p)
            cat = parts[1].strip("[]")
            _external.setdefault(parts[0].upper(), (f"[{cat}] {desc}" if cat else desc, "MELCO_DICT"))
            count += 1
    return count


def describe(code: str) -> tuple[str, str]:
    base = code.split("-")[0].upper()
    if base in _external:
        return _external[base]
    if base in BUILTIN:
        return BUILTIN[base]
    if len(base) == 5 and base[1] in "12":
        return ("Kód specifický pro výrobce – veřejný popis není k dispozici", "")
    return ("Neznámý kód", "")
