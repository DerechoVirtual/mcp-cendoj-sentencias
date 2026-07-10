# -*- coding: utf-8 -*-
"""Mapa + config del BOP de MÁLAGA (Sphinx + HTML edicto). Familia `malaga`.
Valor del mapa = código INE de 5 dígitos (cod_provincia_municipio). Offline/_gen."""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
B = "https://www.bopmalaga.es"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def main():
    req = urllib.request.Request(B + "/inc/xhr.php",
        data=urllib.parse.urlencode({"pag": "datos", "class": "sumario",
            "method": "municipios_busquedas", "selected": "", "cod_provincia": "29"}).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
                 "X-Requested-With": "XMLHttpRequest", "Referer": B + "/buscar.php"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    opts = re.findall(r'<option value="?(\d{4,5})"?>([^<]+)</option>', html)
    mapa = {}
    for ine, nom in opts:
        nom = nom.strip()
        if nom and len(nom) < 50:
            mapa[nom] = ine
    with open(os.path.join(DATA, "bop_malaga_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "malaga_prov", "nombre": "Málaga", "familia": "malaga",
           "base": B, "mapa": "bop_malaga_municipios.json", "indice_desde": 2010}
    with open(os.path.join(DATA, "bop_malaga_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(mapa)} municipios. Muestra:", sorted(mapa)[:6])


if __name__ == "__main__":
    main()
