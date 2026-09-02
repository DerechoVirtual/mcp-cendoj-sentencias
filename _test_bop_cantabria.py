# -*- coding: utf-8 -*-
"""Banco CANTABRIA (BOC, familia externa `cantabria2`).

Mismo flujo que el chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = la lectura
empieza por 【, contiene texto literal de la materia y no es un error. Los casos
HONESTO exigen «No encuentro…» (nunca "Error" ni "PDF sin texto").

    python -X utf8 _test_bop_cantabria.py            # todo
    python -X utf8 _test_bop_cantabria.py santander  # filtra por municipio
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

# (municipio, materia, regex que debe aparecer en la lectura)
CASOS = [
    # >50.000 hab.
    ("Santander", "terrazas veladores", r"velador|terraza|mesas"),
    ("Santander", "residuos limpieza", r"residu|limpieza|basura"),
    ("Santander", "animales", r"animal|perro"),
    ("Santander", "circulación tráfico", r"circulaci|tr[aá]fico|movilidad"),
    ("Santander", "transparencia", r"transparencia"),
    ("Torrelavega", "residuos", r"residu|basura|limpieza"),
    ("Torrelavega", "vados", r"vado|entrada de veh"),
    ("Torrelavega", "animales", r"animal|perro"),
    ("Torrelavega", "transparencia", r"transparencia"),
    ("Castro-Urdiales", "terrazas veladores", r"velador|terraza|mesas"),
    ("Castro-Urdiales", "animales", r"animal|perro"),
    ("Castro-Urdiales", "IBI bienes inmuebles", r"inmueble|\bibi\b|contribuci"),
    ("Camargo", "animales", r"animal|perro"),
    ("Camargo", "instalaciones deportivas", r"deportiv"),
    ("Camargo", "voluntarios de protección civil", r"protecci[oó]n civil|voluntari"),
    # otros
    ("Piélagos", "ordenanza urbanística obras", r"urban[ií]stic|obra|edificaci"),
    ("El Astillero", "animales", r"animal|perro"),
    ("Laredo", "residuos", r"residu|basura|limpieza"),
    ("Santa Cruz de Bezana", "abastecimiento de agua", r"agua|abastecimiento|saneamiento"),
    ("Reinosa", "residuos", r"residu|basura|limpieza"),
    ("Santoña", "cementerio", r"cementerio|funerari"),
    ("Santoña", "agua saneamiento", r"agua|saneamiento"),
]

HONESTO = [
    ("Reinosa", "puerto deportivo"),
    ("Santoña", "aeropuerto"),
]


def caso(muni, q, rx):
    t0 = time.time()
    b = OE.buscar(muni, q, 6)
    t1 = time.time()
    l = OE.leer(muni, q, "", 3, q, 0)
    t2 = time.time()
    bien = l.startswith("【") and bool(re.search(rx, l, re.I)) and not l.startswith("Error") \
        and "no tiene texto" not in l[:400]
    return bien, (t2 - t0, t1 - t0, t2 - t1), b, l


def honesto(muni, q):
    t0 = time.time()
    OE.buscar(muni, q, 6)
    t1 = time.time()
    l = OE.leer(muni, q, "", 3, q, 0)
    t2 = time.time()
    return l.startswith("No encuentro"), (t2 - t0, t1 - t0, t2 - t1), "", l


def main():
    solo = [s.lower() for s in sys.argv[1:]]
    ok, tot, tiempos = 0, 0, []
    for muni, q, rx in CASOS:
        if solo and not any(s in muni.lower() for s in solo):
            continue
        tot += 1
        try:
            bien, (dt, db, dl), b, l = caso(muni, q, rx)
        except Exception as e:  # noqa: BLE001
            bien, dt, db, dl, b, l = False, 0.0, 0.0, 0.0, "", f"EXC {e}"
        ok += bien
        tiempos.append(dt)
        cab = (re.search(r"【([^】]+)】", l) or [None, l[:90]])[1]
        print(f"[{'OK ' if bien else 'BAD'}] {muni:22s} «{q[:26]:26s}» {dt:5.1f}s (b {db:4.1f} + l {dl:4.1f}) | {cab[:100]}")
        if not bien:
            print(f"      B={re.sub(r'\s+', ' ', b)[:200]}")
            print(f"      L={re.sub(r'\s+', ' ', l)[:300]}")
    for muni, q in HONESTO:
        if solo and not any(s in muni.lower() for s in solo):
            continue
        tot += 1
        try:
            bien, (dt, db, dl), _b, l = honesto(muni, q)
        except Exception as e:  # noqa: BLE001
            bien, dt, db, dl, l = False, 0.0, 0.0, 0.0, f"EXC {e}"
        ok += bien
        tiempos.append(dt)
        print(f"[{'OK ' if bien else 'BAD'}] {muni:22s} «{q[:26]:26s}» {dt:5.1f}s (b {db:4.1f} + l {dl:4.1f}) | honesto: {re.sub(r'\s+', ' ', l)[:90]}")
    if tiempos:
        print(f"\nRESULTADO CANTABRIA: {ok}/{tot}  mediana {statistics.median(tiempos):.1f}s  "
              f"máximo {max(tiempos):.1f}s  media {statistics.mean(tiempos):.1f}s")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
