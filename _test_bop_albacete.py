# -*- coding: utf-8 -*-
"""Banco ALBACETE (SEDIPUALB@, familia externa `bop_albacete.py`) — 2-sep-2026.

Mismo flujo que el chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = la lectura
empieza por 【, contiene texto literal de la materia y no es un error. Los casos
HONESTO deben devolver «No encuentro…» (materia inexistente), nunca «Error».
Exigencia del brief: ≥ 90 % OK, mediana < 5 s y máximo < 15 s por caso
(buscar + leer, desde este PC).

Requiere el índice empaquetado ordenanzas_data/albacete_indice.json (lo genera
_gen_bop_albacete_indice.py); sin él el backend cae al respaldo en vivo, más lento.

Uso: .venv/Scripts/python.exe -X utf8 -u _test_bop_albacete.py
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
    # Albacete y Hellín (> 50.000 hab.) primero
    ("Albacete", "terrazas veladores", r"terraza|velador"),
    ("Albacete", "convivencia", r"convivencia"),
    ("Albacete", "ruido contaminación acústica", r"ac[úu]stic|ruido"),
    ("Albacete", "ordenanza de circulación", r"circulaci[óo]n|tr[áa]fico"),
    ("Hellín", "tenencia de animales", r"animal"),
    ("Hellín", "vados", r"vado"),
    ("Hellín", "alcantarillado", r"alcantarillado|depuraci"),
    ("Hellín", "taxi", r"taxi"),
    # resto de municipios
    ("Villarrobledo", "residuos", r"residuo|basura"),
    ("Villarrobledo", "ruido", r"ruido|vibraci"),
    ("Villarrobledo", "terrazas", r"terraza|velador"),
    ("Almansa", "IBI", r"bienes inmuebles|\bIBI\b"),
    ("Almansa", "estacionamiento", r"estacionamiento"),
    ("La Roda", "tenencia de animales", r"animal"),
    ("Caudete", "ruido", r"ac[úu]stic|ruido"),
    ("Tobarra", "tráfico", r"tr[áa]fico|circulaci"),
    ("Casas-Ibáñez", "circulación", r"circulaci[óo]n|tr[áa]fico"),
    ("Madrigueras", "plusvalía", r"incremento de(?:l)? valor|plusval"),
]
# materias que NO existen en Albacete (provincia de interior): nada de «deportivo»
# u otras palabras que sí aparecen en títulos de tasas y precios públicos
HONESTO = [("Hellín", "aeropuerto"), ("Madrigueras", "playas")]

ENRUTADO = {"Albacete": "albacete", "Hellín": "albacete", "Hellin": "albacete", "Villarrobledo": "albacete",
            "Almansa": "albacete", "La Roda": "albacete", "Caudete": "albacete", "Tobarra": "albacete",
            "Casas-Ibáñez": "albacete", "Casas Ibáñez": "albacete", "Madrigueras": "albacete",
            "Chinchilla de Monte-Aragón": "albacete", "Fuente-Álamo": "albacete", "Almansa, Albacete": "albacete",
            "Roda de Berà": "tarragona"}

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
    print("\n== ALBACETE: buscar + leer(parrafos=3)")
    for muni, materia, rx in CASOS:
        est, det, tb, tl = caso(muni, materia, rx)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:16} {materia:30} {est:11} {det[:80]}")
    print("\n== honesto (materia inexistente)")
    for muni, materia in HONESTO:
        est, det, tb, tl = honesto(muni, materia)
        ok += est == "OK"
        tot.append(tb + tl)
        print(f"{'OK ' if est == 'OK' else 'XX '}[{tb + tl:4.1f}s = b {tb:.1f} + l {tl:.1f}] "
              f"{muni:16} {materia:30} {est:11} {det[:80]}")
    n = len(CASOS) + len(HONESTO)
    med, mx = statistics.median(tot), max(tot)
    veredicto = ok / n >= 0.9 and med < 5 and mx < 15
    print(f"\nRESULTADO ALBACETE: {ok}/{n} OK · mediana {med:.2f}s · máximo {mx:.2f}s "
          f"(buscar+leer por caso) -> {'CUMPLE' if veredicto else 'NO CUMPLE'}")
    return 0 if veredicto and not mal else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
