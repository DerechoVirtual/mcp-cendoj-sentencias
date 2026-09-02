# -*- coding: utf-8 -*-
"""Banco LLEIDA (eBOP de la Diputació de Lleida, familia externa `bop_lleida.py`) — 2-sep-2026.

Mismo flujo que el chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = la lectura
empieza por 【, contiene texto literal de la materia (en catalán: el boletín está
solo en catalán) y no es un error. Los casos HONESTO deben devolver «No encuentro…»
(materia inexistente), nunca «Error».
Exigencia del brief: ≥ 90 % OK, mediana < 5 s y máximo < 15 s por caso
(buscar + leer, desde este PC).

Uso: .venv/Scripts/python.exe -X utf8 -u _test_bop_lleida.py
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

# (municipio, materia en castellano, regex que debe aparecer en la LECTURA)
CASOS = [
    # Lleida (> 50.000 hab.) primero
    ("Lleida", "movilidad", r"mobilitat|circulaci"),
    ("Lleida", "civismo convivencia", r"civisme|conviv[èe]ncia"),
    ("Lleida", "residuos", r"residus|escombraries|recollida"),
    ("Lérida", "movilidad", r"mobilitat|circulaci"),          # exónimo castellano
    # resto de municipios
    ("Balaguer", "tenencia de animales", r"animal"),
    ("Balaguer", "residuos", r"residus"),
    ("Tàrrega", "circulación", r"circulaci"),
    ("Tàrrega", "terrazas veladores", r"terrass|vetllador"),
    ("Mollerussa", "ICIO obras", r"construccion|obres|obras"),
    ("La Seu d'Urgell", "vados", r"gual"),
    ("La Seu d'Urgell", "animales", r"animal"),
    ("Cervera", "ruido", r"soroll|vibraci"),
    ("Solsona", "residuos", r"residus"),
    ("Solsona", "convivencia", r"conviv[èe]ncia|civisme"),
    ("Vielha e Mijaran", "ordenanza fiscal", r"fiscal|taxa|impost"),
    ("Alcarràs", "vados", r"gual"),
    ("Alcarràs", "civismo", r"civisme|conviv[èe]ncia"),
]
HONESTO = [("Lleida", "aeropuerto"), ("Tàrrega", "puerto deportivo")]

ENRUTADO = {"Lleida": "lleida", "Lérida": "lleida", "Balaguer": "lleida", "Tàrrega": "lleida", "Tarrega": "lleida",
            "Mollerussa": "lleida", "La Seu d'Urgell": "lleida", "Seu d'Urgell": "lleida", "Cervera": "lleida",
            "Solsona": "lleida", "Vielha e Mijaran": "lleida", "Alcarràs": "lleida", "Les Borges Blanques": "lleida",
            "Cervera del Río Alhama": "larioja", "Balaguer, Lleida": "lleida"}

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
    print("\n== LLEIDA: buscar + leer(parrafos=3)")
    for muni, materia, rx in CASOS:
        est, det, tb, tl = caso(muni, materia, rx)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:18} {materia:24} {est:11} {det[:80]}")
    print("\n== honesto (materia inexistente)")
    for muni, materia in HONESTO:
        est, det, tb, tl = honesto(muni, materia)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:18} {materia:24} {est:11} {det[:80]}")
    n = len(CASOS) + len(HONESTO)
    med, mx = statistics.median(tot), max(tot)
    veredicto = ok / n >= 0.9 and med < 5 and mx < 15
    print(f"\nRESULTADO LLEIDA: {ok}/{n} OK · mediana {med:.2f}s · máximo {mx:.2f}s "
          f"(buscar+leer por caso) -> {'CUMPLE' if veredicto else 'NO CUMPLE'}")
    return 0 if veredicto and not mal else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
