# -*- coding: utf-8 -*-
"""Mapa + config del BOP de TOLEDO (familia SOLR expuesto, webEbop). Offline/_gen.
El valor del mapa es el publisher_facet EXACTO (para el fq de Solr)."""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
B = "https://bop.diputoledo.es/webEbop"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_PREF = re.compile(r"^AYUNTAMIENTO\s+DE\s+(?:LA\s+|EL\s+)?", re.I)
# entidades que NO son municipios (se ven al final del facet)
_NO = re.compile(r"E\.?A\.?T\.?I\.?M|ENTIDAD(ES)? URBAN|MANCOMUNIDAD|COMUNIDAD DE|"
                 r"CONSORCIO|DIPUTACION|JUNTA DE", re.I)


def g(u, t=30):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read()


def main():
    q = urllib.parse.urlencode({"q": "publisher_type:Ayuntamiento", "rows": 0, "facet": "true",
                                "facet.field": "publisher_facet", "facet.mincount": 1,
                                "facet.limit": 999999, "wt": "json"})
    ff = json.loads(g(B + "/solr_select.jsp?" + q))["facet_counts"]["facet_fields"]["publisher_facet"]
    mapa = {}
    for i in range(0, len(ff), 2):
        facet = ff[i]
        if _NO.search(facet):
            continue
        nombre = _PREF.sub("", facet.strip()).strip().title()
        if nombre:
            mapa[nombre] = facet          # VALOR = publisher_facet exacto
    with open(os.path.join(DATA, "bop_toledo_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "toledo", "nombre": "Toledo", "familia": "toledo",
           "base": B, "mapa": "bop_toledo_municipios.json", "indice_desde": 2011}
    with open(os.path.join(DATA, "bop_toledo_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(mapa)} municipios. Muestra:", sorted(mapa)[:6])


if __name__ == "__main__":
    main()
