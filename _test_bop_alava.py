# -*- coding: utf-8 -*-
"""Banco ÁLAVA (BOTHA, familia «alava»): mismo flujo que el chat —
buscar_ordenanzas + leer_ordenanza(parrafos=3). Éxito = la lectura empieza por 【,
contiene texto literal de la materia y no es un error. Criterio del brief:
≥ 90 % OK, mediana < 5 s y máximo < 15 s por caso (buscar + leer).

Uso: python -X utf8 _test_bop_alava.py [filtro-municipio] [-v]
"""
import os
import re
import statistics
import sys
import time

_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
try:
    for ln in open(_ENV, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
except Exception:  # noqa: BLE001
    pass

import ordenanzas_engine as OE  # noqa: E402
import bop_engine as B  # noqa: E402

CASOS = [
    # (municipio, materia, regex que debe aparecer LITERAL en la lectura)
    # --- Vitoria-Gasteiz (> 50.000 hab.)
    ("Vitoria-Gasteiz", "terrazas", r"terraza"),
    ("Vitoria-Gasteiz", "ruido", r"ruido|vibraci"),
    ("Vitoria-Gasteiz", "animales", r"animal"),
    ("Vitoria-Gasteiz", "residuos limpieza", r"residu|limpieza"),
    ("Vitoria-Gasteiz", "venta ambulante", r"ambulante"),
    ("Vitoria", "convivencia", r"convivencia|civismo"),
    # --- resto de la provincia
    ("Llodio", "vados", r"\bvados?\b"),
    ("Laudio", "terrazas", r"terraza|velador|mesas"),
    ("Amurrio", "agua", r"agua|abastecimiento"),
    ("Salvatierra", "ordenanza fiscal IBI", r"inmuebles|\bibi\b"),
    ("Iruña de Oca", "cementerio", r"cementerio"),
    ("Zuia", "tenencia de animales", r"animal"),
    ("Ayala", "residuos", r"residu|basura"),
    ("Alegría-Dulantzi", "agua", r"agua"),
]


def main():
    filtro = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    print("provincia_de:", {m: B.provincia_de(m) for m in ("Vitoria-Gasteiz", "Vitoria", "Gasteiz", "Llodio",
                                                            "Laudio", "Elvillar", "El Villar", "Villar del Arzobispo")})
    ok, honestos, tiempos = 0, 0, []
    for muni, mat, rx in CASOS:
        if filtro and not any(f.lower() in muni.lower() for f in filtro):
            continue
        t0 = time.time()
        try:
            b = OE.buscar(muni, mat, 6)
            l = OE.leer(muni, mat, "", 3, mat, 0)
        except Exception as e:  # noqa: BLE001
            b, l = "", f"EXC {e}"
        dt = time.time() - t0
        tiempos.append(dt)
        bien = l.lstrip().startswith("【") and bool(re.search(rx, l, re.I)) \
            and not l.startswith("Error") and not l.startswith("Localicé")
        honesto = l.startswith("No encuentro")
        ok += bien
        honestos += honesto
        cab = re.search(r"【([^】]+)】", l)
        estado = "OK " if bien else ("HON" if honesto else "BAD")
        print(f"[{estado}] {dt:5.1f}s {muni:18s} «{mat}» -> {(cab.group(1) if cab else l[:110])[:120]}")
        if verbose or not bien:
            print("      B=", re.sub(r"\s+", " ", b)[:220])
            print("      L=", re.sub(r"\s+", " ", l)[:300])
    n = len(tiempos)
    print(f"\nRESULTADO ÁLAVA: {ok}/{n} OK ({honestos} honestos) · mediana {statistics.median(tiempos):.1f}s · "
          f"máximo {max(tiempos):.1f}s · media {sum(tiempos) / n:.1f}s")
    return 0 if ok >= 0.9 * n else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
