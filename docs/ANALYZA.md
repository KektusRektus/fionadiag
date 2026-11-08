# Analýza projektu FionaDiag a diagnostiky Fiony (SuperSport 950 S MY22)

*Stav k 8. 11. 2026. Podklady: export „Fionadiga_export" – 29 logů MelcoCOM Loggeru (25. 10. – 4. 11.),
55 logů FionaDiag, debug výpisy a snímky MelcoDiag 2.0.5.2–2.0.5.5 (srpen–říjen), zdrojáky FionaDiag
0.2 – 1.3.22, sniffer, manuály, referenční repozitáře.*

## Shrnutí v pěti bodech

1. **ABS jde oslovit, ale nikdo zatím nezná správný příkaz pro čtení chyb – ani MelcoDiag.** MelcoDiag při
   „Scan DTC" pošle na ABS (7E1) jen `18 00 FF 00` a `19 02 FF`. ABS první odmítne jako neexistující službu
   (NRC 0x11), druhou jako nepodporovanou podfunkci (NRC 0x12) a MelcoDiag ho pak potichu vynechá
   („2 ECU s chybami"). FionaDiag 1.x měl v allowlistu přesně jen tyhle dva dotazy plus `19 0A`, takže
   cestu najít nemohl z principu.
2. **IMU od 25. 10. nevysílá.** Rámce 0x174/0x178/0x17C byly v srpnu v každém skenu MelcoDiagu. Ve všech
   záznamech od 25. 10. chybí – tedy ještě **před** pokusem s odpojením IMU 26. 10. Ve stejné době se na BBS
   objevil **U100C** („BBS / IMU – obecná chyba"). To je nejsilnější stopa k původnímu problému a nepotřebuje
   ABS DTC: konektor, napájení a CAN vodiče IMU, potom IMU samotná.
3. **Přístrojová deska má jiné VIN** než motor, BBS a ABS (končí …443 vs. …313) a právě ona hlásí `P064F`
   (neautorizovaný SW/kalibrace). Deska byla zřejmě vyměněna nebo nepřeprogramována. Vyplatí se to ověřit
   u servisu.
4. **Většina uložených kódů je historická a vyvolaná pokusy.** Žádný kód nemá nastavený bit „aktivní nyní".
   15 kódů ztráty komunikace na přístrojovce a U0155/U115x na BBS se objevilo 26. 10. (pokus s odpojením
   IMU). P0562 (nízké napětí) přibyl 29. 10. po měření 10,71 V z 28. 10.
5. **Původní projekt zabředl do dokumentace místo diagnostiky.** 60 verzí, desítky README/SHA manifestů,
   ~11 000 řádků kódu, testy, které se v čistém prostředí ani nesestaví (chybí tkinter → 12 chyb při sběru),
   a zásadní cíl (ABS DTC) se za dva týdny neposunul. Nová verze 2.0.0 má ~1 900 řádků, 41 testů a dělá
   to, co je cílem.

## 1. Jednotky na sběrnici (doloženo odpověďmi z motorky)

CAN 11 bit, 500 kbit/s (`ATSP6`). MelcoDiag rámcuje ručně (`ATCAF0`, PCI bajt v požadavku).

| TX/RX | Jednotka | Protokol | Identifikace z odpovědí |
|---|---|---|---|
| 7E0/7E8 | Motor | UDS (`50 01 00 32 13 88`) | `22 F180` „DU CAX20", `F181` „DU CA1205000", `F184` SW „DN2LT000173", `F190` VIN |
| 7E1/7E9 | **ABS Bosch** | hybrid: relace UDS (`50 03` bez časování), ident. KWP `1A` | `1A90` VIN, `1A91` „542.4.100.1B" (= díl 54241001B), `1A94` „81749", `1A96` Bosch řetězec s „0265…" |
| 7E2/7EA | BBS | KWP2000 (`10 81`, `20`) | `1A 8F 03` VIN |
| 7E3/7EB | Přístrojová deska | KWP2000 | `1A81` díl „40611877A", `1A8B` „DN2LT000173", `1A98` „DN2MT000865", `1A8F` **jiné VIN** |
| – | IMU | bez diag. adresy | vysílá 0x174/0x178/0x17C (v srpnu) |

Pozn.: katalog SPC MY22 uvádí ABS 54241011A, odpověď jednotky „542.4.100.1B" = 54241001B. Rozdíl revize je
možný (servisní náhrada / jiná výrobní série); pro diagnostiku podstatný není, pro objednání dílu ano.

## 2. ABS – co přesně víme

| Dotaz na 7E1 | Odpověď | Význam |
|---|---|---|
| `10 01`, `10 03` | `50 01`, `50 03` | relace OK |
| `10 81` | `7F 10 12` | KWP relace 0x81 nepodporována |
| `1A 90/91/94/96/98` | `5A …` | identifikace OK, ostatní `1A xx` → NRC 12 |
| `18 00 FF 00` | `7F 18 11` | **služba 0x18 v jednotce neexistuje** |
| `19 02 FF` | `7F 19 12` | služba 0x19 existuje, **podfunkce 02 ne** |
| `19 0A` | `7F 19 12` | podfunkce 0A také ne |
| `21 xx` | `7F 21 11` | neexistuje |
| `22 F1xx` | `7F 22 13` / `22` | služba existuje, jiný formát/podmínky |

Z toho plyne: jednotka **má** službu 0x19 (ReadDTCInformation), jen nepřijímá podfunkce 02 a 0A. Nikdo
dosud nezkusil 19 01, 19 02 s jinou maskou, 19 0F, 19 0B/0E, KWP 17 ani 13. Přesně to dělá sonda ve
verzi 2.0.0 (19 dotazů, < 10 s, pouze čtení).

### Jak dostat ABS DTC

1. **Spustit sondu** (karta *ABS / sonda* nebo `fionadiag --port COMx probe ABS`). Pokud uspěje, je hotovo:
   metoda se uloží a od té doby funguje čtení i mazání.
2. Pokud neuspěje, výpis sondy je cenný – ukáže, na co jednotka reaguje jinak než NRC 11/12.
3. **Zachytit komerční tester**, který Ducati ABS čte (Devcom Ducati, iCarsoft MT V6 Ducati, OBDSTAR,
   TopScan Moto, TEXA, Ducati DDS v servisu). Handheldy nejdou proxovat přes COM, ale stačí pasivně
   nahrát CAN stávajícím snifferem (MCP2515) během čtení ABS. Hledat rámce s ID 7E1 → 7E9. Nalezenou
   sekvenci pak ověřit `fionadiag send ABS <hex> --session <relace> --learn` – tím se rovnou uloží.
4. Veřejně dostupný popis protokolu čtení DTC z Bosch ABS 9.1MP přes ELM327 jsem nenašel (ani ve
   17 referenčních repozitářích, ani na webu). Nemá smysl ho dál hledat „naslepo" – bod 1 a 3 jsou rychlejší.

## 3. IMU – hlavní stopa

| Období | Zdroj | 0x174/178/17C |
|---|---|---|
| 14., 15., 22. 8. | MelcoDiag „PIDS active" (6 výpisů) | **ano**, vždy všechny tři |
| 25. 10. – 1. 11. | MelcoDiag 2.0.5.5 + ATMA výpisy v 6 MelcoCOM logách | **ne**, nikdy |

Ostatní provoz (018, 019, 020, 022–024, 080, 100, 188, 18C …) běží normálně, takže nejde o problém
adaptéru ani sběrnice jako celku. BBS hlásí U100C (status 68 = potvrzená, selhala od smazání) už 25. 10.

Doporučený postup (od nejlevnějšího):
1. `Kompletní kontrola` / karta *IMU* – potvrdit, že rámce stále chybí.
2. Konektor IMU: zasunutí, koroze, ohnuté piny (byl rozpojen 26. 10. – i tak problém začal dřív).
3. Na konektoru IMU změřit napájení a kostru při zapnutém zapalování; CAN-H/CAN-L proti kostře (klidově
   kolem 2,5 V) a odpor mezi nimi při vypnutém zapalování (cca 60 Ω = oba terminátory).
4. Až pak IMU (katalog: 38540093B). Po opravě: `Kompletní kontrola` → rámce musí být zpět, poté smazat DTC
   na BBS a přístrojovce a ověřit, že se U100C nevrátí.

## 4. Chybové kódy v čase (z logů)

| Datum | BBS (7E2) | Přístrojovka (7E3) |
|---|---|---|
| 25. 10. | U100C/68 | žádné |
| 26. 10. | + U0155, U1156, U1157, U1158 (28) | 15 kódů: P064F, U0100, U0141, U11xx, C1108 (vše 20) |
| 29. 10. | + P0562/28 | beze změny |
| 1. 11. | beze změny | beze změny |

Status 20 = „selhal od posledního smazání", 28 = navíc „potvrzená", 68 = navíc „test neproběhl v tomto
cyklu". Ani jeden kód nemá bit 0 (aktivní). Interpretace bitů je stejná, jakou zobrazuje MelcoDiag.
Motor (7E0) na `19 02 FF` odpovídá `7F 19 78` → `7F 19 22` (podmínky nesplněny) – i pro něj je ve verzi 2
sonda (navíc OBD-II mód 03/07).

## 5. Hodnocení FionaDiag 1.x

**Co bylo dobře:** MelcoCOM Logger (průhledná COM proxy) – bez něj by tahle analýza nebyla možná. Důraz
na read-only. Sniffer a audit jeho přijímacího řetězce.

**Co nefungovalo:**
* Cíl „přečíst ABS DTC" byl postupně nahrazen sběrem „evidence" – zprávy plné výhrad typu „nelze
  vyvozovat", zatímco jasné vzorce v datech (IMU, VIN přístrojovky) zůstaly nevyslovené.
* Allowlist obsahoval jen dotazy, o kterých už bylo známo, že na ABS selhávají – žádný způsob, jak najít
  nový.
* 60 verzí v samostatných složkách, SHA manifesty, „workflow metriky" – režie převýšila vývoj.
* GUI skládané v jednom 1 200řádkovém souboru, testy závislé na GUI a na prostředí autora.
* Transport přepínal mezi `ATCAF1` a `ATCAF0`, `ATSP0` a `ATSP6` podle verze; MelcoDiag mezitím
  používá jednu sekvenci, která na motorce funguje.

## 6. FionaDiag 2.0.0 – co bylo změněno

* Přepsáno od nuly, z FionaDiag 1.x převzaty jen ověřené poznatky (adresy, tabulka popisů DTC z manuálů).
* Transport 1:1 podle MelcoDiagu (`ATSP6 ATCAF0 ATH1 ATS0`), ověření odesílatele podle CAN ID,
  zpracování „response pending" bez opakování dotazu (pasivní `ATMA`).
* Profily jednotek přímo z logů, sonda + paměť naučených metod, mazání s ověřením před/po.
* Pasivní kontrola IMU, interpretace výsledků (rozdílné VIN, aktivní vs. historické kódy).
* Simulátor z anonymizovaných odpovědí motorky → aplikace jde vyzkoušet a testovat bez motorky.
* 41 testů (parsery na skutečných datech, bezpečnostní politika, end-to-end přes simulátor), CI na
  Windows i Linuxu, sestavení EXE v GitHub Actions.

**Neověřeno:** běh 2.0.0 na skutečné motorce (nebyla k dispozici). Ostatní ELM327 klony se mohou chovat
jinak než „ELM327 v1.5" z logů – v takovém případě pošlete log z karty *Komunikace*.

## 7. Doporučený první test na motorce

1. Zapalování ON, motor OFF. FionaDiag → port → *Připojit* → *Kompletní kontrola* → *Uložit zprávu*.
2. Karta *ABS / sonda* → *Najít metodu čtení DTC* (ABS), potom totéž pro ECU.
3. Karta *IMU* → 5 s.
4. Poslat složku `%APPDATA%\FionaDiag\logs` (obsahuje VIN – nesdílet veřejně).
