# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/albacete_indice.json: índice EMPAQUETADO de los sumarios
del BOP de Albacete (SEDIPUALB@) desde 2010 hasta hoy.

Por qué un índice y no solo la búsqueda en vivo (2-sep-2026): el buscador
`busquedaavanzadabop` es full-text sobre el TEXTO de cada página, con lematización
agresiva («terrazas» casa «terrazo»), sin filtro por municipio ni por título, y con
un tope de 100 páginas ordenadas por fecha. «Hellín ordenanza terrazas» devuelve 100
páginas de bases de oposiciones, padrones y edictos que mencionan las tres palabras,
y la ordenanza real se queda fuera del tope. En cambio `busquedapornumbop?a=N&b=AÑO`
devuelve en ~0,1 s el SUMARIO del boletín: entidad («Ayuntamientos» → «Hellín»),
título del anuncio, página y `pid` del PDF del anuncio entero. Con los sumarios
empaquetados, buscar es local (0 red) y leer es un solo PDF con capa de texto.

Uso:  python -X utf8 _gen_bop_albacete_indice.py [--desde 2010] [--workers 4]
Regenerar cuando envejezca (el backend completa en vivo los boletines posteriores
al último indexado, así que no hace falta regenerarlo a menudo).
"""
import argparse
import base64
import concurrent.futures as _cf
import datetime as _dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bop_albacete as A  # noqa: E402
import bop_engine as B  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = os.path.join(_HERE, "ordenanzas_data", "albacete_indice.json")
BASE = "https://bop.dipualba.es"


def dias_publicacion(anyo, mes):
    """Boletines publicados en un mes: [(num, 'dd/mm/aaaa', idboletin)]."""
    j = A._ajax_json(BASE, "obtenerdiaspublicacion", {"a": f"{anyo}-{mes:02d}-01"})
    y = (j or {}).get("yedata") or ""
    if not y:
        return []
    try:
        d = json.loads(base64.b64decode(y))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for x in d:
        try:
            out.append((int(x["idbop"]), x["fechabop"], str(x.get("id") or "")))
        except Exception:  # noqa: BLE001
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", type=int, default=2010)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    hoy = _dt.date.today()
    t0 = time.time()

    # 1) calendario de boletines (mes a mes; barato)
    meses = [(a, m) for a in range(args.desde, hoy.year + 1) for m in range(1, 13)
             if (a, m) <= (hoy.year, hoy.month)]
    boletines = {}
    with _cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for lst in ex.map(lambda am: dias_publicacion(*am), meses):
            for num, fecha, idb in lst:
                anyo = int(fecha[-4:])
                boletines[(anyo, num)] = (fecha, idb)
    print(f"boletines en calendario: {len(boletines)} ({time.time()-t0:.0f}s)")

    # 2) sumario de cada boletín
    claves = sorted(boletines)
    items, fallidos = [], []

    def uno(k):
        anyo, num = k
        for intento in range(3):
            try:
                its = A._sumario_vivo(BASE, num, anyo)
                return k, its, None
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(1.0 * (intento + 1))
        return k, [], err

    hechos = 0
    with _cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for k, its, err in ex.map(uno, claves):
            hechos += 1
            if err is not None:
                fallidos.append([k[0], k[1], str(err)[:80]])
            fecha = boletines[k][0]
            for it in its:
                ent = A._entidad_de(it["cadena"])
                if not ent:
                    continue
                if not (B._es_ordenanza(it["titulo"]) or B._NORMA_AMPLIA.search(it["titulo"])):
                    continue
                items.append([k[0], k[1], fecha, it["pid"], it["pag"], ent, it["titulo"]])
            if hechos % 200 == 0:
                print(f"  {hechos}/{len(claves)} boletines · {len(items)} items · {time.time()-t0:.0f}s")

    items.sort(key=lambda r: (r[2][6:10], r[2][3:5], r[2][0:2], int(r[4])), reverse=True)
    ultimo = max(boletines) if boletines else (0, 0)
    meta = {"generado": hoy.isoformat(), "desde": args.desde, "boletines": len(boletines),
            "ultimo": {"anyo": ultimo[0], "num": ultimo[1], "fecha": boletines.get(ultimo, ("", ""))[0]},
            "fallidos": fallidos,
            "campos": ["anyo", "num", "fecha", "pid", "pag", "entidad", "titulo"],
            "fuente": "SEDIPUALB@ busquedapornumbop (sumarios) + obtenerdiaspublicacion (calendario)"}
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "items": items}, f, ensure_ascii=False, separators=(",", ":"))
    ents = {}
    for r in items:
        ents[r[5]] = ents.get(r[5], 0) + 1
    print(f"ESCRITO {_OUT}: {len(items)} items, {len(ents)} entidades, {os.path.getsize(_OUT)//1024} KB, "
          f"fallidos {len(fallidos)}, {time.time()-t0:.0f}s")
    print("top entidades:", sorted(ents.items(), key=lambda kv: -kv[1])[:12])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
