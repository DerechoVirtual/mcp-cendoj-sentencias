# -*- coding: utf-8 -*-
"""Banco del BOP de A CORUÑA a través del MOTOR integrado (bop_engine.leer).
12 materias concretas en 12 concellos distintos.
Uso: ./.venv/Scripts/python.exe _test_acoruna_engine.py [-v]
"""
import concurrent.futures as cf
import re
import sys
import time

import bop_engine as B

VERBOSE = "-v" in sys.argv

CASOS = [
    ("A Coruña",               "terrazas",                     r"terraza|velador|dominio p[úu]blico|ocupaci"),
    ("Santiago de Compostela", "residuos",                     r"residuo|lixo|basura|limpeza|limpieza"),
    ("Ferrol",                 "contaminación acústica ruido", r"ac[úu]stic|ru[íi]do|decibel|son"),
    ("Arteixo",                "vertidos y saneamiento",       r"vertedur|vertido|saneament|saneamiento|augas"),
    ("Culleredo",              "administración electrónica",   r"electr[óo]nic|telem[áa]tic|sede"),
    ("Ames",                   "estacionamiento autocaravanas", r"autocaravan|estacionamento|estacionamiento|pernoita"),
    ("Carballo",               "gestión de residuos",          r"residuo|lixo|xesti[óo]n|recollida"),
    ("Ribeira",                "retirada de vehículos grúa",   r"retirada|inmobilizaci|veh[íi]culo|guind|gr[úu]a"),
    ("Betanzos",               "administración electrónica",   r"electr[óo]nic|telem[áa]tic|sede"),
    ("Cambre",                 "ordenanza fiscal",             r"taxa|tasa|tarifa|cuota|imposto|impuesto|tributo|recadaci|ingreso"),
    ("Oleiros",                "ordenanza fiscal",             r"taxa|tasa|cuota|tarifa"),
    ("Narón",                  "ordenanza",                    r"ordenanza|regulamento|taxa|tasa"),
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
    print(f"BANCO BOP A CORUÑA (motor) — {len(CASOS)} casos\n" + "=" * 86)
    res = []
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(una, CASOS):
            res.append(r)
            muni, consulta, ok, det, dt, txt = r
            print(f"[{'OK ' if ok else 'FAIL'}] {muni:24s} «{consulta[:24]:24s}» {dt:5.1f}s  {det}")
            if VERBOSE or not ok:
                print("        ", re.sub(r"\s+", " ", (txt or ""))[:240])
    n = sum(1 for x in res if x[2])
    print("=" * 86)
    print(f"RESULTADO: {n}/{len(CASOS)} · media {sum(x[4] for x in res)/len(res):.1f}s "
          f"· máx {max(x[4] for x in res):.1f}s")
    sys.exit(0 if n == len(CASOS) else 1)
