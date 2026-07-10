# -*- coding: utf-8 -*-
"""Mapa + config del BOP de CÁDIZ (OpenCms propio; búsqueda por organo_remitente +
lectura del PDF del día con ancla #page). Familia `cadiz`. Valor del mapa = organo
exacto ("Ayuntamiento de X"). SSL con cadena incompleta -> contexto sin verificar."""
import concurrent.futures as cf
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
B = "https://www.bopcadiz.es"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CTX = ssl._create_unverified_context()
TIPO = "BOP_F:d67ba416-aec8-11e9-9ac3-286ed488c708"


def g(u, t=30):
    return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=t, context=CTX).read().decode("utf-8", "replace")


def boletines(texto):
    p = {"tipo_": TIPO, "ruta_": "/sites/default/.content/BOP_F/", "incluirFiltros_": "true",
         "num_elements_": "30", "num_columns_": "1",
         "listConfig": "/.content/Lista_L/Lista_L_00001.html", "usepagination": "true",
         "page": "1", "texto": texto, "organo_remitente": "", "sortModifier": "desc"}
    r = g(B + "/system/modules/es.dipucadiz.listas/elements/list-inner.jsp?" + urllib.parse.urlencode(p))
    return re.findall(r"/boletin/(Boletin-numero-\d+-del-ano-\d+)", r)


def organos_de(slug):
    try:
        page = g(B + "/boletin/" + slug)
    except Exception:  # noqa: BLE001
        return set()
    return set(re.findall(r"Ayuntamiento de ([A-ZÁÉÍÓÚÑ][^.<]{2,40}?)\.", page))


def main():
    slugs = []
    for t in ("ordenanza", "reglamento", "tasa"):
        slugs += boletines(t)
    slugs = list(dict.fromkeys(slugs))[:24]
    organos = set()
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for s in ex.map(organos_de, slugs):
            organos |= s
    mapa = {}
    for o in organos:
        o = o.strip()
        if o and len(o) < 45:
            mapa[o] = "Ayuntamiento de " + o
    with open(os.path.join(DATA, "bop_cadiz_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "cadiz", "nombre": "Cádiz", "familia": "cadiz", "base": B, "tipo": TIPO,
           "mapa": "bop_cadiz_municipios.json", "indice_desde": 2010}
    with open(os.path.join(DATA, "bop_cadiz_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(mapa)} municipios. Muestra:", sorted(mapa)[:6])


if __name__ == "__main__":
    main()
