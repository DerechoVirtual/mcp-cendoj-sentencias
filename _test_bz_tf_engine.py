# -*- coding: utf-8 -*-
"""Bancos de BIZKAIA y SANTA CRUZ DE TENERIFE a través del motor integrado.
Uso: ./.venv/Scripts/python.exe _test_bz_tf_engine.py [bizkaia|tenerife|todo]
"""
import concurrent.futures as cf
import re
import sys
import time

import bop_engine as B

BANCOS = {
    "bizkaia": [
        ("Bilbao",             "terrazas",                r"terraza|velador|v[íi]a p[úu]blica|ocupaci"),
        ("Barakaldo",          "residuos",                r"residuo|basura|limpieza|hondakin"),
        ("Getxo",              "tenencia de animales",    r"animal|perro|txakur|censo"),
        ("Portugalete",        "ordenanza fiscal",        r"tasa|tarifa|cuota|impuesto|zerga"),
        ("Santurtzi",          "circulación",             r"circulaci|tr[áa]fico|veh[íi]culo"),
        ("Basauri",            "venta ambulante",         r"ambulante|mercad|puesto"),
        ("Leioa",              "ruido contaminación acústica", r"ruido|ac[úu]stic|decibel"),
        ("Galdakao",           "subvenciones",            r"subvenci|ayuda|beneficiari"),
        ("Durango",            "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Sestao",             "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Erandio",            "vados",                   r"vado|entrada de veh|garaje"),
        ("Amorebieta-Etxano",  "ordenanza fiscal",        r"tasa|tarifa|cuota|impuesto"),
    ],
    "gipuzkoa": [
        ("San Sebastián",   "ordenanza fiscal tasa",       r"tasa|tarifa|cuota|impuesto"),
        ("Hondarribia",     "terrazas",                    r"terraza|hosteler|v[íi]a p[úu]blica"),
        ("Tolosa",          "estacionamiento en superficie", r"estacionamiento|aparcamiento|veh[íi]culo"),
        ("Mondragón",       "circulación",                 r"circulaci|tr[áa]fico|veh[íi]culo"),
        ("Azpeitia",        "vivienda",                    r"vivienda|etxebiz|alojamiento"),
        ("Lasarte-Oria",    "cambio de uso de locales",    r"local|uso|vivienda|edificaci"),
        ("Eibar",           "ordenanzas fiscales",         r"tasa|tarifa|cuota|impuesto"),
        ("Beasain",         "edificación",                 r"edificaci|obra|construc|urban"),
        ("Hernani",         "precios públicos",            r"precio|tarifa|tasa|cuota"),
        ("Andoain",         "ordenanza fiscal",            r"tasa|tarifa|cuota|impuesto"),
        ("Irun",            "ordenanza",                   r"ordenanza|reglamento|tasa"),
        ("Errenteria",      "tasa",                        r"tasa|tarifa|cuota|precio"),
    ],
    "tenerife": [
        ("Santa Cruz de Tenerife",     "zona de bajas emisiones", r"emisiones|ZBE|veh[íi]culo|circulaci"),
        ("San Cristóbal de La Laguna", "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Arona",                      "ordenanza fiscal",        r"tasa|tarifa|cuota|impuesto"),
        ("Adeje",                      "residuos limpieza",       r"residuo|limpieza|basura"),
        ("Granadilla de Abona",        "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("La Orotava",                 "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Los Realejos",               "ordenanza fiscal",        r"tasa|tarifa|cuota|impuesto"),
        ("Puerto de la Cruz",          "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Candelaria",                 "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Santa Cruz de la Palma",     "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Los Llanos de Aridane",      "ordenanza",               r"ordenanza|reglamento|tasa"),
        ("Icod de los Vinos",          "ordenanza",               r"ordenanza|reglamento|tasa"),
    ],
}


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
        # criterio: lo que ve el abogado (título + texto) identifica la norma pedida
        if not re.search(esperado, r, re.I):
            fallos.append(f"no aparece /{esperado}/")
        if len(cuerpo) < 250:
            fallos.append(f"texto corto ({len(cuerpo)}c)")
    return (muni, consulta, not fallos, "; ".join(fallos) or "OK", dt, r)


if __name__ == "__main__":
    quien = (sys.argv[1] if len(sys.argv) > 1 else "todo").lower()
    bancos = BANCOS if quien == "todo" else {quien: BANCOS[quien]}
    gt_ok = gt = 0
    for prov, casos in bancos.items():
        print(f"\n=== BANCO {prov.upper()} — {len(casos)} casos " + "=" * 40)
        res = []
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            for r in ex.map(una, casos):
                res.append(r)
                muni, consulta, ok, det, dt, txt = r
                print(f"[{'OK ' if ok else 'FAIL'}] {muni:26s} «{consulta[:24]:24s}» {dt:5.1f}s  {det}")
                if not ok:
                    print("        ", re.sub(r"\s+", " ", (txt or ""))[:190])
        n = sum(1 for x in res if x[2])
        gt_ok += n
        gt += len(res)
        print(f"  -> {n}/{len(res)} · media {sum(x[4] for x in res)/len(res):.1f}s · máx {max(x[4] for x in res):.1f}s")
    print(f"\nTOTAL: {gt_ok}/{gt}")
    sys.exit(0 if gt_ok == gt else 1)
