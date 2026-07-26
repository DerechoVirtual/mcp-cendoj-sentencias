# -*- coding: utf-8 -*-
"""Banco del BOP de PONTEVEDRA (BOPPO) a través del motor integrado.
12 materias REALES (comprobadas en el índice) en 12 concellos distintos.
Uso: ./.venv/Scripts/python.exe _test_pontevedra_engine.py [-v]
"""
import concurrent.futures as cf
import re
import sys
import time

import bop_engine as B

VERBOSE = "-v" in sys.argv

CASOS = [
    ("Vigo",                 "procedimiento administrativo electrónico", r"electr[óo]nic|telem[áa]tic|sede"),
    ("Pontevedra",           "movilidad",                    r"mobilidade|movilidad|circulaci|tr[áa]fico"),
    ("Vilagarcía de Arousa", "IBI bienes inmuebles",         r"inmobles|inmuebles|IBI|imposto|impuesto"),
    ("Redondela",            "administración electrónica",   r"electr[óo]nic|telem[áa]tic|sede"),
    ("Cangas",               "transparencia",                r"transparencia|informaci[óo]n p[úu]blica|acceso"),
    ("Marín",                "terrazas",                     r"terraza|mesas|v[íi]a p[úu]blica|ocupaci"),
    ("Ponteareas",           "venta ambulante",              r"ambulante|venda|mercad|posto"),
    ("Moaña",                "hortas municipais huertos",    r"horta|huerto|parcel|cultiv"),
    ("Poio",                 "furanchos",                    r"furanch|loureiro|viño|vino"),
    ("Nigrán",               "normalización lingüística",    r"ling[üu][íi]stic|galego|normalizaci"),
    ("Tui",                  "ICIO construcciones obras",    r"construcci[óo]n|instalaci|obras|ICIO"),
    ("Sanxenxo",             "contaminación acústica",       r"ac[úu]stic|ru[íi]do|son|decibel"),
]


def una(c):
    muni, consulta, esperado = c
    t0 = time.time()
    try:
        r = B.leer(muni, consulta, parrafos=2, terminos=consulta) or ""
    except Exception as e:  # noqa: BLE001
        return (muni, consulta, False, f"EXCEPCIÓN {type(e).__name__}: {e}", time.time() - t0, "")
    dt, fallos = time.time() - t0, []
    if not r or "No encuentro" in r[:200]:
        fallos.append("sin resultado")
    else:
        if not re.search(rf"Ayuntamiento de {re.escape(muni)}", r, re.I):
            fallos.append("cabecera de OTRO municipio")
        cuerpo = r.split("\n\n", 1)[-1]
        if not re.search(esperado, cuerpo, re.I):
            fallos.append(f"no aparece /{esperado}/")
        if len(cuerpo) < 250:
            fallos.append(f"texto muy corto ({len(cuerpo)}c)")
    return (muni, consulta, not fallos, "; ".join(fallos) or "OK", dt, r)


if __name__ == "__main__":
    print(f"BANCO BOP PONTEVEDRA (motor) — {len(CASOS)} casos\n" + "=" * 88)
    res = []
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(una, CASOS):
            res.append(r)
            muni, consulta, ok, det, dt, txt = r
            print(f"[{'OK ' if ok else 'FAIL'}] {muni:22s} «{consulta[:30]:30s}» {dt:5.1f}s  {det}")
            if VERBOSE or not ok:
                print("        ", re.sub(r"\s+", " ", (txt or ""))[:240])
    n = sum(1 for x in res if x[2])
    print("=" * 88)
    print(f"RESULTADO: {n}/{len(CASOS)} · media {sum(x[4] for x in res)/len(res):.1f}s "
          f"· máx {max(x[4] for x in res):.1f}s")
    sys.exit(0 if n == len(CASOS) else 1)
