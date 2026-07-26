# -*- coding: utf-8 -*-
"""Índice EMPAQUETADO de anuncios normativos del BOCM por municipio.

Por qué: desde Vercel las páginas de búsqueda del BOCM (450 KB) se cuelgan de
forma intermitente — verificado en producción: 4/10 y hasta 94 s. Las LECTURAS
de un anuncio concreto sí funcionan (1-10 s). Solución: la búsqueda deja de ser
una petición HTTP y pasa a ser una consulta al índice que empaquetamos aquí
(mismo patrón que León capital), y solo se lee en vivo el anuncio elegido.

Escribe ordenanzas_data/madrid_indice/<tid>.json = [{cve,titulo,fecha,orden}]
(uno por municipio, para cargar solo el que hace falta).

Uso:
  ./.venv/Scripts/python.exe _gen_indice_madrid.py            # municipios >50k
  ./.venv/Scripts/python.exe _gen_indice_madrid.py --todos    # los 193
"""
import concurrent.futures as cf
import json
import os
import sys
import time

import bop_engine as B

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "ordenanzas_data", "madrid_indice")
CONSULTAS = ("ordenanza", "reglamento", "tasa", "ordenanzas fiscales")
MAX_PAG = 6                     # 10 filas/página

# Municipios de la Comunidad de Madrid > 50.000 hab. (objetivo de Carlos)
GRANDES = ["Madrid", "Móstoles", "Alcalá de Henares", "Fuenlabrada", "Leganés", "Getafe",
           "Alcorcón", "Torrejón de Ardoz", "Parla", "Alcobendas", "Las Rozas de Madrid",
           "San Sebastián de los Reyes", "Pozuelo de Alarcón", "Coslada", "Rivas-Vaciamadrid",
           "Valdemoro", "Majadahonda", "Collado Villalba", "Aranjuez", "Arganda del Rey",
           "Boadilla del Monte", "Pinto", "Colmenar Viejo", "Tres Cantos",
           "San Fernando de Henares", "Galapagar", "Villaviciosa de Odón"]


def indexa_municipio(nombre):
    cfg = B.PROVINCIAS["madrid"]
    tid = B._categoria("madrid", nombre)
    if not tid:
        return nombre, None, "sin tid"
    filas, t0 = {}, time.time()
    for q in CONSULTAS:
        for p in range(MAX_PAG):
            try:
                fs = B._madrid_filas(B._madrid_get(B._madrid_url(cfg, tid, q, p), timeout=40, intentos=2))
            except Exception as e:  # noqa: BLE001
                return nombre, None, f"error en «{q}» p{p}: {type(e).__name__}"
            nuevos = [f for f in fs if f["cve"] not in filas]
            for f in fs:
                filas.setdefault(f["cve"], {k: f[k] for k in ("cve", "titulo", "fecha", "orden")})
            if len(fs) < 10 or not nuevos:      # última página de esa consulta
                break
    datos = sorted(filas.values(), key=lambda r: r["orden"], reverse=True)
    os.makedirs(DEST, exist_ok=True)
    with open(os.path.join(DEST, f"{tid}.json"), "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    return nombre, len(datos), f"{time.time()-t0:.0f}s"


if __name__ == "__main__":
    if "--todos" in sys.argv:
        B._cargar_mapas()
        objetivo = sorted(B._NOMBRES["madrid"].values())
    else:
        objetivo = GRANDES
    print(f"Indexando {len(objetivo)} municipios de Madrid → {DEST}")
    ok = tot = 0
    with cf.ThreadPoolExecutor(max_workers=3) as ex:     # suave con el BOCM
        for nombre, n, det in ex.map(indexa_municipio, objetivo):
            if n is None:
                print(f"  [FALLO] {nombre:28s} {det}")
            else:
                ok += 1
                tot += n
                print(f"  [ok]    {nombre:28s} {n:4d} anuncios  ({det})")
    print(f"\n{ok}/{len(objetivo)} municipios indexados · {tot} anuncios en total")
