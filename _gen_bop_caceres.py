# -*- coding: utf-8 -*-
"""Genera el mapa de municipios + config del BOP de CÁCERES (familia REST-JSON,
API pública de la Diputación de Cáceres). Offline/_gen."""
import json
import os
import re
import sys
import unicodedata
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
B = "https://bop.dip-caceres.es"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
_PREF = re.compile(r"^(?:Excmo\.?\s+)?Ayuntamiento\s+de\s+(?:la\s+|el\s+)?", re.I)


def g(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read()


def main():
    ents = json.loads(g(B + "/bop/services/listas/entidadesByGrupo?idGrupo=1"))["data"]
    mapa = {}
    for e in ents:
        nombre = _PREF.sub("", (e["descripcion"] or "").strip()).strip()
        if nombre:
            mapa[nombre] = e["id"]      # VALOR = id numérico de la entidad
    with open(os.path.join(DATA, "bop_caceres_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "caceres", "nombre": "Cáceres", "familia": "caceres",
           "base": B, "grupo": 1, "mapa": "bop_caceres_municipios.json",
           "indice_desde": 2010}
    with open(os.path.join(DATA, "bop_caceres_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(mapa)} municipios. Muestra:", sorted(mapa)[:6])


if __name__ == "__main__":
    main()
