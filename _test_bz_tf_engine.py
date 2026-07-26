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
    "laspalmas": [
        ("Telde",                      "ordenanza fiscal IBI",   r"bienes inmuebles|IBI|imponible|gravamen"),
        ("Arrecife",                   "residuos",               r"residuo|basura|recogida|limpieza"),
        ("Santa Lucía de Tirajana",    "ordenanza",              r"ordenanza|reglamento|tasa"),
        ("San Bartolomé de Tirajana",  "ordenanza fiscal",       r"tasa|tarifa|cuota|impuesto"),
        ("Arucas",                     "ordenanza",              r"ordenanza|reglamento|tasa"),
        ("Puerto del Rosario",         "ordenanza",              r"ordenanza|reglamento|tasa"),
        ("Ingenio",                    "ordenanza fiscal",       r"tasa|tarifa|cuota|impuesto"),
        ("Agüimes",                    "ordenanza",              r"ordenanza|reglamento|tasa"),
        ("Gáldar",                     "ordenanza",              r"ordenanza|reglamento|tasa"),
        ("Mogán",                      "terrazas vía pública",   r"terraza|v[íi]a p[úu]blica|ocupaci|utilizaci"),
        ("Santa María de Guía",        "ordenanza",              r"ordenanza|reglamento|tasa"),
        ("Pájara",                     "ordenanza",              r"ordenanza|reglamento|tasa"),
    ],
    "tarragona": [
        ("Tarragona",   "terrazas",              r"terrass|terraza|via p[úu]blica|ocupaci"),
        ("Reus",        "residuos",              r"residu|neteja|escombrar|recollida"),
        ("El Vendrell", "ordenanza",             r"ordenan|reglament|taxa|tasa"),
        ("Cambrils",    "ordenanza fiscal",      r"taxa|tasa|tarifa|impost"),
        ("Salou",       "ordenanza fiscal",      r"taxa|tasa|tarifa|impost"),
        ("Valls",       "ordenanza",             r"ordenan|reglament|taxa"),
        ("Tortosa",     "ordenanza",             r"ordenan|reglament|taxa"),
        ("Amposta",     "ordenanza",             r"ordenan|reglament|taxa"),
        ("Calafell",    "ordenanza",             r"ordenan|reglament|taxa"),
        ("Vila-seca",   "ordenanza",             r"ordenan|reglament|taxa"),
        ("Deltebre",    "ordenanza",             r"ordenan|reglament|taxa"),
        ("Alcanar",     "ordenanza",             r"ordenan|reglament|taxa"),
    ],
    "asturias": [
        ("Gijón",     "residuos",            r"residuo|basura|limpieza|recogida"),
        ("Oviedo",    "terrazas",            r"terraza|hosteler|v[íi]a p[úu]blica"),
        ("Avilés",    "ordenanza fiscal",    r"tasa|tarifa|tributo|impuesto|ingreso"),
        ("Siero",     "ordenanza",           r"ordenanza|reglamento|tasa"),
        ("Langreo",   "ordenanza",           r"ordenanza|reglamento|tasa"),
        ("Mieres",    "ordenanza fiscal",    r"tasa|tarifa|tributo|impuesto|cuota"),
        ("Castrillón", "ordenanza",          r"ordenanza|reglamento|tasa"),
        ("Llanes",    "ordenanza",           r"ordenanza|reglamento|tasa"),
        ("Villaviciosa", "ordenanza",        r"ordenanza|reglamento|tasa"),
        ("Corvera de Asturias", "ordenanza", r"ordenanza|reglamento|tasa"),
        ("San Martín del Rey Aurelio", "ordenanza", r"ordenanza|reglamento|tasa"),
        ("Valdés",    "ordenanza",           r"ordenanza|reglamento|tasa"),
    ],
    "valencia": [
        ("Torrent",         "residuos",          r"residu|basura|neteja|limpieza|recollida"),
        ("Gandia",          "ordenanza",         r"ordenan|reglament|taxa|tasa"),
        ("Paterna",         "ordenanza fiscal",  r"taxa|tasa|tarifa|impost|impuesto"),
        ("Sagunt",          "ordenanza",         r"ordenan|reglament|taxa|tasa"),
        ("Alzira",          "ordenanza",         r"ordenan|reglament|taxa|tasa"),
        ("Mislata",         "ordenanza fiscal",  r"taxa|tasa|tarifa|impost|tribut|fraccionad|ingres"),
        ("Burjassot",       "ordenanza",         r"ordenan|reglament|taxa|tasa"),
        ("Ontinyent",       "ordenanza",         r"ordenan|reglament|taxa|tasa"),
        ("Xirivella",       "ordenanza",         r"ordenan|reglament|taxa|tasa"),
        ("Manises",         "ordenanza fiscal",  r"taxa|tasa|tarifa|impost"),
        ("Quart de Poblet", "ordenanza",         r"ordenan|reglament|taxa|tasa"),
        ("Catarroja",       "ordenanza",         r"ordenan|reglament|taxa|tasa"),
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
