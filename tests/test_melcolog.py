from fionadiag.melcolog import transactions

LOG = """2026-10-26T18:35:06.199+01:00 MelcoDiag -> ELM327 8B: 61 74 73 68 37 45 32 0D | ASCII 'atsh7E2.'
2026-10-26T18:35:06.206+01:00 ELM327 -> MelcoDiag 8B: 61 74 73 68 37 45 32 0D | ASCII 'atsh7E2.'
2026-10-26T18:35:06.212+01:00 ELM327 -> MelcoDiag 5B: 4F 4B 0D 0D 3E | ASCII 'OK..>'
2026-10-26T18:35:06.339+01:00 MelcoDiag -> ELM327 9B: 61 74 63 72 61 37 45 41 0D | ASCII 'atcra7EA.'
2026-10-26T18:35:06.346+01:00 ELM327 -> MelcoDiag 9B: 61 74 63 72 61 37 45 41 0D | ASCII 'atcra7EA.'
2026-10-26T18:35:06.352+01:00 ELM327 -> MelcoDiag 5B: 4F 4B 0D 0D 3E | ASCII 'OK..>'
2026-10-26T18:35:06.685+01:00 MelcoDiag -> ELM327 7B: 30 32 31 30 38 31 0D | ASCII '021081.'
2026-10-26T18:35:06.692+01:00 ELM327 -> MelcoDiag 7B: 30 32 31 30 38 31 0D | ASCII '021081.'
2026-10-26T18:35:06.722+01:00 ELM327 -> MelcoDiag 17B: 30 32 35 30 38 31 30 30 30 30 30 30 30 30 30 30 0D | ASCII '0250810000000000.'
2026-10-26T18:35:06.920+01:00 ELM327 -> MelcoDiag 2B: 0D 3E | ASCII '.>'
"""


def test_transactions(tmp_path):
    p = tmp_path / "melco_com_test.log"
    p.write_text(LOG, encoding="utf-8")
    t = [x for x in transactions(p) if not x.is_at]
    assert len(t) == 1
    assert t[0].tx == "7E2" and t[0].rx == "7EA"
    assert t[0].payload() == bytes.fromhex("1081")
    assert t[0].messages() == [bytes.fromhex("5081")]
