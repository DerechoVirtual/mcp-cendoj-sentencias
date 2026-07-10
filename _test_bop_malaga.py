# -*- coding: utf-8 -*-
"""Banco MÁLAGA (familia Sphinx fulltext + HTML edicto) — 5 pruebas específicas."""
import os
import re
import sys
import time

_env = open(os.path.expanduser("~/.claude/.env"), encoding="utf-8", errors="replace").read()
for _k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
    _m = re.search(rf"^{_k}=(.+)$", _env, re.M)
    if _m:
        os.environ[_k] = _m.group(1).strip().strip('"')

import bop_engine as b  # noqa: E402

CASOS = [
    ("Marbella", "terrazas ocupación vía pública", r"ocupaci[oó]n|terraza|v[ií]a p[uú]blica"),
    ("Ronda", "protección animales", r"animal|garant[ií]a"),
    ("Mijas", "veladores", r"velador|v[ií]a p[uú]blica|ocupaci"),
    ("Vélez-Málaga", "residuos limpieza", r"residuos|limpieza|basura"),
    ("Fuengirola", "movilidad circulación", r"movilidad|circulaci|tr[aá]fico|veh"),
]


def main():
    ok = 0
    for muni, mat, rx in CASOS:
        t0 = time.time()
        try:
            r = b.leer(muni, mat, parrafos=2, terminos=mat)
        except Exception as e:  # noqa: BLE001
            r = f"EXC {e}"
        dt = time.time() - t0
        cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
        good = bool(cab) and re.search(rx, cab, re.I) and len(r) > 400 and "No encuentro" not in r[:60] and dt < 5
        ok += bool(good)
        print(f"{'✅' if good else '❌'} [{dt:4.1f}s] {muni:14} {mat:32} -> {(cab or (r or '')[:55])[:66]}")
    print(f"\nRESULTADO MÁLAGA: {ok}/{len(CASOS)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
