# -*- coding: utf-8 -*-
"""Mapa + config del BOP de HUELVA (familia bope_web: POST Solr + PDF). Offline/_gen.
Valor del mapa = código numérico de la entidad."""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
B = "https://s2.diphuelva.es"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "X-Requested-With": "XMLHttpRequest"}
_PREF = re.compile(r"^Ayuntamiento\s+de\s+(?:la\s+|el\s+)?", re.I)


def post(path, data):
    req = urllib.request.Request(B + path, data=urllib.parse.urlencode(data).encode(),
        headers={**UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": B + "/servicios/bope_web/"})
    return urllib.request.urlopen(req, timeout=30).read()


def main():
    d = json.loads(post("/lib/bope/anuncios_bop/ajaxBusquedaAvanzada.php",
                        {"accion": "CargarEntidades", "categoria": 8}).decode("utf-8", "replace"))
    lst = d if isinstance(d, list) else d.get("data") or d.get("entidades") or []
    mapa = {}
    for e in lst:
        nombre = _PREF.sub("", (e.get("descripcion") or "").strip()).strip()
        if nombre and e.get("codigo"):
            mapa[nombre] = int(e["codigo"])
    with open(os.path.join(DATA, "bop_huelva_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "huelva", "nombre": "Huelva", "familia": "huelva",
           "base": B, "mapa": "bop_huelva_municipios.json", "indice_desde": 2010}
    with open(os.path.join(DATA, "bop_huelva_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(mapa)} municipios. Muestra:", sorted(mapa)[:6])


if __name__ == "__main__":
    main()
