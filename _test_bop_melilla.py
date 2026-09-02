# -*- coding: utf-8 -*-
"""Banco MELILLA (BOME, backend externo bop_melilla.py) — 2-sep-2026.

Ciudad autónoma unimunicipal: todos los casos son «Melilla». Mismo flujo que el
chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = el listado y la
lectura empiezan por 【, la cabecera es la norma esperada, el texto trae la materia
literal y no hay «Error»/«PDF sin texto». Los casos HONESTO exigen «No encuentro…»
(el BOME está lleno de órdenes, convenios y padrones: ahí es donde no se puede
colar un acto como si fuera una ordenanza). Objetivo del brief: ≥ 90 % OK,
mediana < 5 s y máximo < 15 s.

Uso: .venv/Scripts/python.exe -X utf8 _test_bop_melilla.py
"""
import os
import re
import statistics
import sys
import time

_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
try:
    for _ln in open(_ENV, encoding="utf-8", errors="replace"):
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except Exception:  # noqa: BLE001
    pass

import ordenanzas_engine as OE  # noqa: E402

MUNI = "Melilla"

# (materia, regex esperado en la CABECERA 【…】, regex esperado en el TEXTO)
ENCONTRAR = [
    ("taxi", r"taxi", r"taxi"),
    ("patinetes", r"movilidad personal|vmp|patinete", r"patinete|movilidad personal"),
    ("policía local", r"polic[ií]a local", r"polic[ií]a"),
    ("zona de bajas emisiones", r"bajas emisiones", r"emisiones"),
    ("ordenanza fiscal", r"ordenanza fiscal", r"impuesto|tasa|tarifa|tributo"),
    ("desfibriladores", r"desfibrilador", r"desfibrilador"),
    ("terrazas", r"terrazas", r"terraza|velador"),
    ("protección animal", r"animal", r"animal"),
    ("residuos", r"residuos|punto limpio", r"residuo"),
    ("playas", r"playa", r"playa"),
    ("suelos contaminados", r"suelos contaminados", r"suelo"),
    ("ocupación del espacio público", r"espacio p[uú]blico", r"espacio p[uú]blico|ocupaci"),
]

HONESTO = [
    ("aeropuerto"),
    ("energía nuclear"),
    ("puerto deportivo"),
]

_ERR = re.compile(r"^Error|no pude leer|no tiene texto legible|anti-robots", re.I)


def encontrar(materia, rx_cab, rx_txt):
    t0 = time.time()
    try:
        b = OE.buscar(MUNI, materia, 6)
    except Exception as e:  # noqa: BLE001
        return "EXC", f"buscar: {e}"[:70], 0.0, time.time() - t0
    t1 = time.time()
    try:
        r = OE.leer(MUNI, materia, "", 3, materia, 0) or ""
    except Exception as e:  # noqa: BLE001
        return "EXC", f"leer: {e}"[:70], t1 - t0, time.time() - t1
    t2 = time.time()
    dtb, dtl = t1 - t0, t2 - t1
    if _ERR.search(r):
        return "ERROR", r[:70], dtb, dtl
    if not (b or "").startswith("【"):
        return "SIN_LISTA", (b or "")[:70], dtb, dtl
    m = re.search(r"【([^】]+)】", r)
    if not r.startswith("【") or not m:
        return "SIN_CAB", r[:70], dtb, dtl
    cab = m.group(1)
    if not re.search(rx_cab, cab, re.I):
        return "MAL_ORD", cab[:70], dtb, dtl
    cuerpo = r[m.end():]
    if len(cuerpo) < 300 or not re.search(rx_txt, cuerpo, re.I):
        return "SIN_MATERIA", cab[:70], dtb, dtl
    return "OK", cab[:70], dtb, dtl


def honesto(materia):
    t0 = time.time()
    try:
        b = OE.buscar(MUNI, materia, 6) or ""
        t1 = time.time()
        r = OE.leer(MUNI, materia, "", 3, materia, 0) or ""
    except Exception as e:  # noqa: BLE001
        return "EXC", str(e)[:70], 0.0, time.time() - t0
    t2 = time.time()
    if _ERR.search(r) or _ERR.search(b):
        return "ERROR", r[:70], t1 - t0, t2 - t1
    if r.startswith("No encuentro") and b.startswith("No encuentro"):
        return "OK", "honesto", t1 - t0, t2 - t1
    m = re.search(r"【([^】]+)】", r)
    return "FALSO_POS", (m.group(1) if m else r)[:70], t1 - t0, t2 - t1


def main():
    ok = tot = 0
    totales, lecturas = [], []
    print("== MELILLA debe-encontrar (buscar -> leer parrafos=3) ==")
    for materia, rx_cab, rx_txt in ENCONTRAR:
        tot += 1
        estado, det, dtb, dtl = encontrar(materia, rx_cab, rx_txt)
        ok += estado == "OK"
        totales.append(dtb + dtl)
        lecturas.append(dtl)
        print(f"{'✅' if estado == 'OK' else '❌'} [{dtb + dtl:4.1f}s = b{dtb:3.1f}+l{dtl:3.1f} {estado:11}] "
              f"{MUNI:10} {materia:30} -> {det}")
    print("\n== honesto (materia inexistente) ==")
    for materia in HONESTO:
        tot += 1
        estado, det, dtb, dtl = honesto(materia)
        ok += estado == "OK"
        totales.append(dtb + dtl)
        lecturas.append(dtl)
        print(f"{'✅' if estado == 'OK' else '❌'} [{dtb + dtl:4.1f}s = b{dtb:3.1f}+l{dtl:3.1f} {estado:11}] "
              f"{MUNI:10} {materia:30} -> {det}")
    med, mx = statistics.median(totales), max(totales)
    medl, mxl = statistics.median(lecturas), max(lecturas)
    print(f"\nRESULTADO MELILLA: {ok}/{tot} OK ({100 * ok / tot:.0f} %) · buscar+leer mediana {med:.1f} s, "
          f"máximo {mx:.1f} s · solo leer mediana {medl:.1f} s, máximo {mxl:.1f} s")
    cumple = ok / tot >= 0.9 and med < 5 and mx < 15
    print("CUMPLE el brief (≥90 %, mediana <5 s, máx <15 s)" if cumple else "NO CUMPLE el brief")
    return 0 if cumple else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
