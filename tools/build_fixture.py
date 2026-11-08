"""Sestaví anonymizovaný fixture odpovědí Fiony z logů MelcoCOM Loggeru.

Použití:  python tools/build_fixture.py <složka_s_logy> fionadiag/data/fiona_responses.json
VIN a sériová čísla se nahradí stejně dlouhými zástupnými hodnotami (rozdíl VIN přístrojovky zůstane vidět).
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fionadiag.melcolog import transactions  # noqa: E402
from fionadiag.isotp import parse_frame  # noqa: E402

REPLACE = {}  # původní ASCII -> náhrada (stejná délka); doplní se z argumentů / auto-detekce VIN


def message_positions(frames):
    """Vrátí seznam (frame_idx, byte_idx) pro každý bajt ISO-TP zprávy."""
    pos, need = [], 0
    for fi, d in enumerate(frames):
        k = d[0] >> 4
        if k == 0:
            pos = [(fi, i) for i in range(1, 1 + (d[0] & 15))]
            return pos
        if k == 1:
            need = ((d[0] & 15) << 8) | d[1]
            pos = [(fi, i) for i in range(2, len(d))]
        elif k == 2:
            pos += [(fi, i) for i in range(1, len(d))]
    return pos[:need]


def sanitize(frames):
    frames = [bytearray(f) for f in frames]
    pos = message_positions(frames)
    msg = bytes(frames[fi][bi] for fi, bi in pos)
    for old, new in REPLACE.items():
        o, n = old.encode(), new.encode()
        start = msg.find(o)
        while start >= 0:
            for k in range(len(o)):
                fi, bi = pos[start + k]
                frames[fi][bi] = n[k]
            msg = bytes(frames[fi][bi] for fi, bi in pos)
            start = msg.find(o, start + 1)
    return [bytes(f).hex().upper() for f in frames]


def main(logdir, out):
    import re
    vins = Counter()
    txs = []
    for p in sorted(Path(logdir).glob("melco_com_*.log")):
        txs += transactions(p)
    for t in txs:
        for m in t.messages():
            for v in re.findall(rb"ZDM[0-9A-Z]{14}", m):
                vins[v.decode()] += 1
    for i, (v, _) in enumerate(vins.most_common()):
        REPLACE[v] = f"ZDMTEST{i+1:010d}"
    # Bosch výrobní/sériový řetězec ABS (1A96) – zachovat jen délku
    responses, bus = {}, Counter()
    for t in txs:
        up = t.command.upper()
        if up == "ATMA":
            for ln in t.lines:
                f = parse_frame(ln)
                if f and f.can_id is not None and f.can_id < 0x700:
                    bus[(f"{f.can_id:03X}", f.data.hex().upper())] += 1
            continue
        pl = t.payload()
        if pl is None or not t.tx or len(t.tx) != 3:
            continue
        frames = t.response_frames()
        if not frames:
            continue
        if pl == b"\x1a\x96":
            frames = [bytes.fromhex(x) for x in sanitize(frames)]
            pos = message_positions([bytearray(f) for f in frames])
            fr = [bytearray(f) for f in frames]
            for fi, bi in pos[2:]:
                if 0x30 <= fr[fi][bi] <= 0x39:
                    fr[fi][bi] = 0x30
            frames = [bytes(f) for f in fr]
        key = f"{t.tx}:{pl.hex().upper()}"
        # preferuj pozitivní odpověď, jinak poslední
        sf = sanitize(frames)
        positive = frames and (frames[0][1] == pl[0] + 0x40 or frames[0][2] == pl[0] + 0x40)
        if key not in responses or positive:
            responses[key] = sf
    seen_ids = {}
    for (cid, data), _ in bus.most_common():
        seen_ids.setdefault(cid, data)
    data = {"source": "Anonymizováno z logů MelcoCOM 25. 10. – 4. 11. 2026 (Ducati SuperSport 950 S MY22)",
            "vin_map": {v: f"VIN{i+1}" for i, v in enumerate(REPLACE.values())},
            "responses": dict(sorted(responses.items())),
            "bus": sorted(seen_ids.items())}
    Path(out).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(responses)} odpovědí, {len(seen_ids)} CAN ID, VIN nahrazeno: {len(REPLACE)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
