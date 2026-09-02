# -*- coding: utf-8 -*-
"""Banco LA RIOJA (BOR, familia externa `bop_larioja.py`) — 2-sep-2026.

Mismo flujo que el chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = la lectura
empieza por 【, contiene texto literal de la materia y no es un error. Los casos
HONESTO deben devolver «No encuentro…» (materia inexistente), nunca «Error».
Exigencia del brief: ≥ 90 % OK, mediana < 5 s y máximo < 15 s por caso
(buscar + leer, desde este PC).

Uso: .venv/Scripts/python.exe -X utf8 -u _test_bop_larioja.py
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

# (municipio, materia, regex que debe aparecer en la LECTURA)
CASOS = [
    # Logroño (> 50.000 hab.) primero
    ("Logroño", "terrazas veladores", r"terraza|velador"),
    ("Logroño", "tenencia de animales", r"animal|perro"),
    ("Logroño", "vehículos de movilidad personal", r"movilidad personal|patinete|VMP"),
    ("Logroño", "IBI", r"bienes inmuebles|\bIBI\b"),
    ("Logroño", "convivencia", r"convivencia"),
    ("Logroño", "ruido", r"ruido|ac[úu]stic"),
    # resto de municipios
    ("Calahorra", "cementerio", r"cementerio"),
    ("Calahorra", "residuos", r"residuo|basura"),
    ("Calahorra", "terrazas", r"terraza|velador"),
    ("Arnedo", "arbolado", r"arbolado|[áa]rbol"),
    ("Arnedo", "residuos", r"residuo|basura"),
    ("Haro", "tenencia de animales", r"animal|perro"),
    ("Haro", "convivencia", r"convivencia"),
    ("Haro", "estacionamiento regulado", r"estacionamiento"),
    ("Alfaro", "autocaravanas", r"autocaravana"),
    ("Nájera", "basuras", r"basura|residuo"),
    ("Lardero", "terrazas veladores", r"terraza|velador"),
    ("Villamediana de Iregua", "residuos", r"residuo|basura"),
    ("Villamediana de Iregua", "publicidad", r"publicidad"),
    ("Santo Domingo de la Calzada", "subvenciones", r"subvenci"),
]
HONESTO = [("Haro", "aeropuerto"), ("Nájera", "puerto deportivo")]

ENRUTADO = {"Logroño": "larioja", "Calahorra": "larioja", "Arnedo": "larioja", "Haro": "larioja",
            "Alfaro": "larioja", "Nájera": "larioja", "Lardero": "larioja",
            "Villamediana de Iregua": "larioja", "Santo Domingo de la Calzada": "larioja",
            "La Calahorra": "granada", "Santa Coloma": "barcelona", "Santa Coloma, La Rioja": "larioja"}

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
    print("   " + ("OK: todos los municipios resuelven a su provincia" if not mal else f"MAL: {mal}"))
    ok = 0
    tot = []
    print("\n== LA RIOJA: buscar + leer(parrafos=3)")
    for muni, materia, rx in CASOS:
        est, det, tb, tl = caso(muni, materia, rx)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:28} {materia:32} {est:11} {det[:80]}")
    print("\n== honesto (materia inexistente)")
    for muni, materia in HONESTO:
        est, det, tb, tl = honesto(muni, materia)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:28} {materia:32} {est:11} {det[:80]}")
    n = len(CASOS) + len(HONESTO)
    med, mx = statistics.median(tot), max(tot)
    veredicto = ok / n >= 0.9 and med < 5 and mx < 15
    print(f"\nRESULTADO LA RIOJA: {ok}/{n} OK · mediana {med:.2f}s · máximo {mx:.2f}s "
          f"(buscar+leer por caso) -> {'CUMPLE' if veredicto else 'NO CUMPLE'}")
    return 0 if veredicto and not mal else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
