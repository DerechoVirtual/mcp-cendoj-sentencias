# -*- coding: utf-8 -*-
"""Empaqueta los TEXTOS de las ordenanzas más consultadas de los municipios >50k
de la provincia de Málaga (BOP de Málaga).

Por qué (2-sep-2026): bopmalaga.es protege la lectura de edictos (edicto.php y
el PDF) con Cloudflare Turnstile por ráfagas: tras ~8 lecturas en pocos minutos
desde una IP sirve «Verificación necesaria» durante ~5 min. La BÚSQUEDA (Sphinx)
no está protegida. Con el texto empaquetado, la lectura de las ordenanzas
habituales es local; el resto sigue en vivo (con reintentos y mensaje claro).

Ritmo: 1 lectura cada `--pausa` s (defecto 30) y espera de 5 min si bloquea.
Salida: ordenanzas_data/malaga_prov_textos/<eid>.txt.gz + indice.json
Uso:    python -X utf8 _gen_textos_malaga.py [--pausa 30] [--munis Marbella,Mijas]
"""
import gzip
import json
import os
import re
import sys
import time

import bop_engine as B

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ordenanzas_data", "malaga_prov_textos")
IDX = os.path.join(OUT, "indice.json")
PROV = "malaga_prov"
MUNIS = ["Marbella", "Vélez-Málaga", "Mijas", "Fuengirola", "Torremolinos", "Estepona", "Benalmádena",
         "Antequera", "Rincón de la Victoria", "Alhaurín de la Torre", "Ronda", "Cártama", "Alhaurín el Grande"]
MATERIAS = ["terrazas veladores", "residuos limpieza", "ruido contaminacion acustica", "tenencia de animales",
            "convivencia ciudadana", "venta ambulante mercadillo", "vados entrada de vehiculos",
            "ocupacion via publica", "movilidad trafico circulacion", "ordenanza fiscal general",
            "impuesto bienes inmuebles IBI", "construcciones instalaciones obras ICIO", "playas",
            "viviendas de uso turistico", "subvenciones", "cementerio", "publicidad", "zona de bajas emisiones"]


def main():
    args = sys.argv[1:]
    pausa = float(args[args.index("--pausa") + 1]) if "--pausa" in args else 30.0
    munis = args[args.index("--munis") + 1].split(",") if "--munis" in args else MUNIS
    os.makedirs(OUT, exist_ok=True)
    idx = json.load(open(IDX, encoding="utf-8")) if os.path.exists(IDX) else {}
    # 1) localizar los edictos (búsqueda NO protegida)
    pendientes = []
    for muni in munis:
        cat = B._categoria(PROV, muni)
        if not cat:
            print("sin categoria", muni, flush=True)
            continue
        for mat in MATERIAS:
            try:
                res = B._buscar_raw(PROV, mat, cat, rpp=40)
                m = B._mejor(res, mat)
            except Exception as e:  # noqa: BLE001
                print("ERR buscar", muni, mat, str(e)[:60], flush=True)
                continue
            if not m:
                continue
            eid = m.get("eid")
            if eid and eid not in idx and eid not in [p[2] for p in pendientes]:
                pendientes.append((muni, mat, eid, m["titulo"]))
    print(f"{len(pendientes)} edictos por leer ({len(idx)} ya empaquetados)", flush=True)
    # 2) leerlos con calma
    for i, (muni, mat, eid, tit) in enumerate(pendientes, 1):
        intentos = 0
        while True:
            t, via = B._malaga_texto(PROV, {"eid": eid})
            if t:
                with gzip.open(os.path.join(OUT, eid + ".txt.gz"), "wt", encoding="utf-8") as f:
                    f.write(t)
                idx[eid] = {"muni": muni, "materia": mat, "titulo": tit[:180], "chars": len(t)}
                json.dump(idx, open(IDX, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
                print(f"[{i}/{len(pendientes)}] ok {muni:22s} {mat[:22]:22s} {len(t):7d} chars ({via})", flush=True)
                break
            intentos += 1
            if via.startswith("bloqueo") and intentos <= 6:
                print(f"[{i}/{len(pendientes)}] BLOQUEO en {muni} — espero 5 min", flush=True)
                time.sleep(300)
                continue
            print(f"[{i}/{len(pendientes)}] FAIL {muni} {mat} {eid} via={via}", flush=True)
            break
        time.sleep(pausa)
    print("LISTO", len(idx), "edictos empaquetados")


if __name__ == "__main__":
    main()
