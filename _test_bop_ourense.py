# -*- coding: utf-8 -*-
"""Banco OURENSE (BOP de Ourense, familia externa `bop_ourense.py`) — 2-sep-2026.

Mismo flujo que el chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = la lectura
empieza por 【, contiene texto literal de la materia y no es un error. Los casos
HONESTO deben devolver «No encuentro…» (materia inexistente), nunca «Error».
Exigencia del brief: ≥ 90 % OK, mediana < 5 s y máximo < 15 s por caso
(buscar + leer, desde este PC). Ojo: el índice legible empieza en marzo-2024.

Uso: .venv/Scripts/python.exe -X utf8 -u _test_bop_ourense.py
"""
import os
import re
import statistics
import sys
import time

_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding="utf-8", errors="replace"):
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import bop_engine as B  # noqa: E402
import ordenanzas_engine as OE  # noqa: E402

# (municipio, materia, regex que debe aparecer en la LECTURA — el texto llega en castellano)
CASOS = [
    # Ourense capital (> 50.000 hab.; publica por 17 departamentos) primero
    ("Ourense", "contaminación acústica", r"ac[úu]stic|ruido"),
    ("Ourense", "terrazas veladores", r"terraza|velador"),
    ("Ourense", "zona de bajas emisiones", r"bajas emisiones|\bZBE\b"),
    ("Ourense", "residuos", r"residuo|basura"),
    ("Ourense", "tráfico y circulación", r"circulaci|tr[áa]fico"),
    ("Ourense", "agua", r"agua|abastecimiento|saneamiento"),
    # resto de concellos
    ("Verín", "basura", r"basura|residuo"),
    ("O Barco de Valdeorras", "IBI", r"bienes inmuebles|\bIBI\b"),
    ("O Barco de Valdeorras", "impuesto de vehículos", r"veh[íi]culo"),
    ("O Carballiño", "rotulación de vías", r"rotulaci|v[íi]as"),
    ("Xinzo de Limia", "agua", r"agua|abastecimiento|suministro"),
    ("Xinzo de Limia", "plusvalía", r"incremento de valor|plusval"),
    ("Celanova", "escuela infantil", r"escuela infantil"),
    ("Allariz", "residuos", r"residuo|basura"),
    ("Ribadavia", "venta ambulante", r"ambulante"),
    ("Ribadavia", "instalaciones deportivas", r"deportiv"),
    ("Barbadás", "convivencia", r"convivencia"),
    ("Barbadás", "animales", r"animal"),
    ("A Rúa", "agua", r"agua|abastecimiento"),
    ("Maceda", "pádel", r"p[áa]del"),
]
HONESTO = [("Allariz", "aeropuerto"), ("Verín", "puerto deportivo")]

ENRUTADO = {"Ourense": "ourense", "Orense": "ourense", "Verín": "ourense",
            "O Barco de Valdeorras": "ourense", "El Barco de Valdeorras": "ourense",
            "O Carballiño": "ourense", "Carballiño": "ourense", "Xinzo de Limia": "ourense",
            "Celanova": "ourense", "Allariz": "ourense", "Ribadavia": "ourense",
            "Barbadás": "ourense", "A Rúa": "ourense", "Maceda": "ourense", "Cea": "leon"}

_ERR = re.compile(r"^Error|no pude leer|no tiene texto legible|Municipio no cubierto", re.I)


def caso(muni, materia, rx):
    t0 = time.time()
    b = OE.buscar(muni, materia, 6) or ""
    t1 = time.time()
    r = OE.leer(muni, materia, "", 3, materia, 0) or ""
    t2 = time.time()
    cab = (re.search(r"【([^】]+)】", r) or [None, ""])[1]
    if _ERR.search(r) or _ERR.search(b):
        estado = "ERROR"
    elif not r.startswith("【"):
        estado = "SIN_ORD"
    elif not re.search(rx, r, re.I):
        estado = "SIN_MATERIA"
    else:
        estado = "OK"
    return estado, (cab or r[:80]), t1 - t0, t2 - t1


def honesto(muni, materia):
    t0 = time.time()
    b = OE.buscar(muni, materia, 6) or ""
    t1 = time.time()
    r = OE.leer(muni, materia, "", 3, materia, 0) or ""
    t2 = time.time()
    if r.startswith("No encuentro") and (b.startswith("No encuentro") or b.startswith("【")):
        return "OK", "honesto", t1 - t0, t2 - t1
    if _ERR.search(r):
        return "ERROR", r[:80], t1 - t0, t2 - t1
    cab = (re.search(r"【([^】]+)】", r) or [None, r[:80]])[1]
    return "FALSO_POS", cab, t1 - t0, t2 - t1


def main():
    print("== enrutado")
    mal = [(m, B.provincia_de(m), p) for m, p in ENRUTADO.items() if B.provincia_de(m) != p]
    print("   " + ("OK: todos los concellos resuelven a su provincia" if not mal else f"MAL: {mal}"))
    ok = 0
    tot = []
    print("\n== OURENSE: buscar + leer(parrafos=3)")
    for muni, materia, rx in CASOS:
        est, det, tb, tl = caso(muni, materia, rx)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:22} {materia:26} {est:11} {det[:80]}")
    print("\n== honesto (materia inexistente)")
    for muni, materia in HONESTO:
        est, det, tb, tl = honesto(muni, materia)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:22} {materia:26} {est:11} {det[:80]}")
    n = len(CASOS) + len(HONESTO)
    med, mx = statistics.median(tot), max(tot)
    veredicto = ok / n >= 0.9 and med < 5 and mx < 15
    print(f"\nRESULTADO OURENSE: {ok}/{n} OK · mediana {med:.2f}s · máximo {mx:.2f}s "
          f"(buscar+leer por caso) -> {'CUMPLE' if veredicto else 'NO CUMPLE'}")
    return 0 if veredicto and not mal else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
