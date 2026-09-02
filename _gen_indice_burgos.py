# -*- coding: utf-8 -*-
"""Índice EMPAQUETADO del BOP de Burgos (bopbur, Drupal 6/7; patrón Madrid/Cádiz).

Por qué: el buscador /busqueda SOLO indexa el año en curso (verificado 27-jul-2026: un
título literal de 2015 devuelve filas de 2026) y además tarda 18 s. En cambio la página
de cada boletín /bopbur-<año>-<NNN> existe al menos desde 2015, va renderizada en
servidor (0,3-0,7 s) y trae la jerarquía completa: <h2> sección -> <h3> entidad (el id de
la categoría ES el tid del mapa bop_burgos_municipios.json) -> anuncios con título y PDF.
Se recorren todos los boletines desde 2015 y se guardan solo los anuncios NORMATIVOS de
ayuntamientos. El parser y el filtro viven en el backend (bop_burgos / bop_ciudadreal).

Reanudable: el volcado CRUDO por boletín (todos los anuncios de ayuntamientos) se guarda
en %TEMP%/bop-crawl/burgos_boletines.json; al relanzar se saltan los ya hechos y el
índice se reempaqueta entero (se puede afinar el filtro sin recrawlear).

Salida: ordenanzas_data/burgos_indice.json  {"meta": {...}, "anuncios": [...]}
        anuncio = {"tid": tid del ayuntamiento, "t": título, "i": id del anuncio,
                   "b": "aaaa-NNN" (boletín), "f": aaaammdd, "kb": tamaño del PDF}
Uso:    python -X utf8 _gen_indice_burgos.py [--desde 2015] [--hasta 2026] [--workers 4]
                                            [--solo-empaquetar]
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import tempfile
import time
import urllib.error

import bop_burgos as BU
from bop_ciudadreal import es_normativo

HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = BU._IDX_FP
ESTADO = os.path.join(tempfile.gettempdir(), "bop-crawl", "burgos_boletines.json")


def boletin(slug):
    """-> (slug, {"f": fecha, "a": anuncios} | None si no existe (404), error)."""
    ult = ""
    for intento in range(4):
        try:
            html = BU._get(f"{BU.BASE}/bopbur-{slug}", timeout=30).decode("utf-8", "replace")
            if "bopbur-categoria" not in html and "title-number" not in html:
                raise ValueError("página inesperada")
            fecha, anuncios = BU.parse_boletin(html)
            return slug, {"f": fecha, "a": anuncios}, ""
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return slug, None, ""
            ult = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            ult = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(2.0 * (intento + 1))
    return slug, None, ult or "error"


def cargar_estado():
    try:
        return json.load(open(ESTADO, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"boletines": {}}


def guardar_estado(est):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False)
    os.replace(tmp, ESTADO)


def empaquetar(est, desde, hasta):
    anuncios = []
    claves = sorted(k for k in est["boletines"] if desde <= int(k[:4]) <= hasta and est["boletines"][k])
    for k in claves:
        v = est["boletines"][k]
        for a in v["a"]:
            if not es_normativo(a["t"]):
                continue
            anuncios.append({"tid": a["tid"], "t": a["t"][:220], "i": a["i"], "b": k, "f": v.get("f", ""),
                             "kb": a.get("kb", 0)})
    ultimo = claves[-1] if claves else ""
    meta = {"generado": dt.date.today().isoformat(), "desde": desde, "hasta": hasta, "boletines": len(claves),
            "ultimo": ultimo, "fuente": BU.BASE, "municipios": len({a["tid"] for a in anuncios})}
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "anuncios": anuncios}, f, ensure_ascii=False)
    print(f"EMPAQUETADO: {len(anuncios)} anuncios normativos de {meta['municipios']} ayuntamientos, "
          f"{len(claves)} boletines (último {ultimo}), {os.path.getsize(SALIDA)/1e6:.2f} MB -> {SALIDA}")


def main():
    args = sys.argv[1:]
    desde = int(args[args.index("--desde") + 1]) if "--desde" in args else 2015
    hasta = int(args[args.index("--hasta") + 1]) if "--hasta" in args else dt.date.today().year
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 4
    est = cargar_estado()
    if "--solo-empaquetar" not in args:
        t0 = time.time()
        hechos = nuevos = errores = 0
        hoy = dt.date.today().year
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for anio in range(desde, hasta + 1):
                n = 1
                visto_alguno = any(k.startswith(f"{anio}-") and v for k, v in est["boletines"].items())
                ultimo_conocido = max([int(k[5:]) for k, v in est["boletines"].items()
                                       if k.startswith(f"{anio}-") and v] or [0])
                while n <= 320:
                    lote = [f"{anio}-{i:03d}" for i in range(n, n + 16)]
                    # en el año en curso se recrawlean los 3 últimos conocidos (pueden crecer)
                    pend = [s for s in lote if s not in est["boletines"]
                            or (anio == hoy and int(s[5:]) > ultimo_conocido - 3)]
                    for slug, v, err in ex.map(boletin, pend):
                        if err:
                            errores += 1
                            print("ERR", slug, err, flush=True)
                            continue
                        est["boletines"][slug] = v
                        hechos += 1
                        if v:
                            nuevos += 1
                            visto_alguno = True
                    n += 16
                    if visto_alguno and not any(est["boletines"].get(s) for s in lote) \
                            and all(s in est["boletines"] for s in lote):
                        break            # bloque entero inexistente tras el último boletín del año
                    if pend and hechos % 80 < len(pend):
                        guardar_estado(est)
                        print(f"{anio}: {hechos} pedidos · {nuevos} boletines · {errores} err · "
                              f"{time.time()-t0:.0f}s", flush=True)
                guardar_estado(est)
                tot = sum(1 for k, v in est["boletines"].items() if k.startswith(f"{anio}-") and v)
                print(f"AÑO {anio}: {tot} boletines · {time.time()-t0:.0f}s", flush=True)
        guardar_estado(est)
        print(f"CRAWL: {hechos} pedidos en {time.time()-t0:.0f}s · {nuevos} boletines · {errores} errores "
              f"(los boletines con error quedan pendientes para la próxima ejecución)", flush=True)
    empaquetar(est, desde, hasta)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
