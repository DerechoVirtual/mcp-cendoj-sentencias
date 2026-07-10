# -*- coding: utf-8 -*-
"""Mapa + config del BOP de JAÉN (BOP Digit@l, índice de ordenanzas por municipio).
Familia `jaen`. Valor del mapa = codigoSubseccion. Offline/_gen."""
import http.cookiejar
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
B = "https://bop.dipujaen.es"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_PREF = re.compile(r"^Ayuntamiento\s+de\s+(?:la\s+|el\s+)?", re.I)


def main():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    page = op.open(B + "/ordenanzas", timeout=25).read().decode("iso-8859-1", "replace")
    i = page.find("codigoSubseccion")
    blk = page[i:page.find("</select>", i)]
    opts = re.findall(r"<option value='(\d+)'>([^<]+)</option>", blk)
    mapa = {}
    for cod, nom in opts:
        nom = re.sub(r"\s*\(Ja[eé]n\)\s*$", "", _PREF.sub("", nom.strip())).strip()
        if nom and "Diputaci" not in nom and cod != "1":
            mapa[nom] = int(cod)
    with open(os.path.join(DATA, "bop_jaen_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "jaen", "nombre": "Jaén", "familia": "jaen",
           "base": B, "mapa": "bop_jaen_municipios.json", "indice_desde": 2010}
    with open(os.path.join(DATA, "bop_jaen_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(mapa)} municipios. Muestra:", sorted(mapa)[:6])


if __name__ == "__main__":
    main()
