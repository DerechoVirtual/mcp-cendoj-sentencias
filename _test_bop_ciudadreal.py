# -*- coding: utf-8 -*-
"""Banco CIUDAD REAL (SIGEM + índice empaquetado, familia «ciudadreal»).

Mismo flujo que el chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = la lectura empieza por
【, contiene texto literal de la materia y no es un error. Los casos «honesto» exigen el
«No encuentro…» del motor (nunca «Error»/«PDF sin texto»). Se mide el tiempo de cada
caso (buscar + leer) y se exige ≥ 90 % OK, mediana < 5 s y máximo < 15 s.
Los casos son normas REALES vistas en el índice (títulos del BOP 2013-2026).
Uso: python -X utf8 _test_bop_ciudadreal.py
"""
import os
import re
import statistics
import sys
import time

_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
try:
    for _ln in open(_ENV, encoding="utf-8"):
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except Exception:  # noqa: BLE001
    pass

import ordenanzas_engine as OE  # noqa: E402
import bop_engine as B  # noqa: E402

# (municipio, materia, regex que debe aparecer en la LECTURA) — primero los > 50.000 hab.
ENCONTRAR = [
    ("Ciudad Real", "terrazas veladores", r"terraza|velador|mesas y sillas"),
    ("Ciudad Real", "ruido contaminación acústica", r"ruido|ac[uú]stic"),
    ("Ciudad Real", "impuesto sobre bienes inmuebles", r"bienes inmuebles"),
    ("Ciudad Real", "movilidad ciclista patinetes", r"movilidad|ciclista|bicicleta"),
    ("Puertollano", "terrazas", r"terraza"),
    ("Puertollano", "convivencia ciudadana", r"convivencia"),
    ("Puertollano", "suministro de agua", r"agua"),
    ("Tomelloso", "terrazas veladores", r"terraza"),
    ("Valdepeñas", "vados", r"vado"),
    ("Valdepeñas", "convivencia ciudadana", r"convivencia"),
    ("Valdepeñas", "limpieza y vallado de solares", r"solares|vallado"),
    ("Alcázar de San Juan", "tenencia de animales", r"animal"),
    ("Alcázar de San Juan", "convivencia ocio", r"convivencia|ocio"),
    ("Alcázar de San Juan", "cementerio", r"cementerio"),
    ("Manzanares", "tráfico", r"tr[aá]fico|circulaci"),
    ("Daimiel", "agua potable saneamiento", r"agua|saneamiento"),
    ("La Solana", "recogida de basuras", r"basura|residu"),
    ("Miguelturra", "terrazas veladores", r"terraza|velador"),
    ("Miguelturra", "animales potencialmente peligrosos", r"animal"),
    ("Campo de Criptana", "caminos", r"camino"),
    ("Socuéllamos", "movilidad personal patinetes", r"movilidad"),
    ("Villarrubia de los Ojos", "venta ambulante", r"ambulante"),
    ("Bolaños de Calatrava", "limpieza y vallado de solares", r"solares|vallado"),
]
# materias que NO existen en ese ayuntamiento: el motor debe responder honesto
HONESTO = [
    ("Miguelturra", "puerto deportivo"),
    ("Daimiel", "aeropuerto"),
]
ERROR = re.compile(r"^Error|no pude leer|no tiene texto legible|PDF sin texto|Traceback", re.I)


def caso_encontrar(muni, materia, rx):
    t0 = time.time()
    try:
        b = OE.buscar(muni, materia, 6)
        r = OE.leer(muni, materia, "", 3, materia, 0)
    except Exception as e:  # noqa: BLE001
        return "EXC", str(e)[:70], time.time() - t0
    dt = time.time() - t0
    cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
    if not (r or "").startswith("【"):
        return ("ERROR" if ERROR.search(r or "") else "NO_ENC"), (r or "")[:90].replace("\n", " "), dt
    if ERROR.search(r):
        return "ERROR", cab[:80], dt
    cuerpo = r.split("】", 1)[1]
    if not re.search(rx, cuerpo, re.I):
        return "SIN_MATERIA", cab[:80], dt
    if not (b or "").startswith("【"):
        return "BUSCAR_KO", (b or "")[:80].replace("\n", " "), dt
    return "OK", cab[:80], dt


def caso_honesto(muni, materia):
    t0 = time.time()
    try:
        r = OE.leer(muni, materia, "", 3, materia, 0)
    except Exception as e:  # noqa: BLE001
        return "EXC", str(e)[:70], time.time() - t0
    dt = time.time() - t0
    if (r or "").startswith("No encuentro"):
        return "OK", "honesto", dt
    if ERROR.search(r or ""):
        return "ERROR", (r or "")[:80], dt
    cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
    return "FALSO_POS", (cab or (r or "")[:80])[:80], dt


def main():
    for muni in ("Ciudad Real", "Puertollano", "Tomelloso", "Valdepeñas", "Alcázar de San Juan"):
        p = B.provincia_de(muni)
        print(f"enrutado {muni:22} -> {p}" + ("" if p == "ciudadreal" else "   <-- MAL"))
    print(f"enrutado {'Villanueva de los Infantes':22} -> {B.provincia_de('Villanueva de los Infantes')}")
    ok = tot = 0
    tiempos = []
    print("\n== CIUDAD REAL debe-encontrar (buscar + leer parrafos=3) ==")
    for muni, materia, rx in ENCONTRAR:
        tot += 1
        estado, det, dt = caso_encontrar(muni, materia, rx)
        tiempos.append(dt)
        ok += estado == "OK"
        print(f"{'✅' if estado == 'OK' else '❌'} [{dt:4.1f}s {estado:11}] {muni:24} {materia:36} -> {det}")
    print("\n== honesto (materia ausente) ==")
    for muni, materia in HONESTO:
        tot += 1
        estado, det, dt = caso_honesto(muni, materia)
        tiempos.append(dt)
        ok += estado == "OK"
        print(f"{'✅' if estado == 'OK' else '❌'} [{dt:4.1f}s {estado:11}] {muni:24} {materia:36} -> {det}")
    med, mx = statistics.median(tiempos), max(tiempos)
    print(f"\nRESULTADO CIUDAD REAL: {ok}/{tot} OK · mediana {med:.2f} s · máximo {mx:.2f} s"
          + ("" if ok >= 0.9 * tot and med < 5 and mx < 15 else "   <-- FUERA DE CRITERIO"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
