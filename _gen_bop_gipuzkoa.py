# -*- coding: utf-8 -*-
"""Mapa municipio->id del BOG (Gipuzkoa). El <select> del buscador lista 114
"AYUNTAMIENTO DE ..." pero incluye ayuntamientos de FUERA (Bilbao, Logroño,
Chinchón, Tarifa...) que publicaron algún edicto: se filtran con lista de bloqueo
porque uno solo secuestraría el enrutado nacional de municipios."""
import html
import http.cookiejar
import json
import os
import re
import ssl
import urllib.request

BASE = "https://egoitza.gipuzkoa.eus/es/bog"
P = "_BoletinOficial_WAR_LEEboletinOficialportlet_"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
HERE = os.path.dirname(os.path.abspath(__file__))
CTX = ssl._create_unverified_context()

# entidades del select que NO son municipios de Gipuzkoa
FUERA = {"ADRADA DE HAZA", "BAZTAN", "BILBAO", "CABANILLAS", "CENDEA DE GALAR", "CHINCHÓN",
         "DESOJO", "ERMUA", "GESALAZ", "LLEIDA", "LOGROÑO", "MALLABIA",
         "OLAZTI/OLAZAGUTÍA", "OLITE", "SANTA CILIA", "SANTA CRUZ DE LA SERÓS",
         "SANTANDER", "TARIFA", "VELEZ", "GETARIA Y AIA", "URRETXU-ZUMARRAGA",
         "EZKIO", "ITSASO"}


def main():
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPSHandler(context=CTX))
    op.addheaders = [("User-Agent", UA)]
    h = op.open(f"{BASE}?p_p_id=BoletinOficial_WAR_LEEboletinOficialportlet&p_p_lifecycle=0"
                f"&p_p_state=normal&p_p_mode=view&{P}myaction=boletinBusqueda", timeout=40
                ).read().decode("utf-8", "replace")
    m = re.search(r'<select[^>]*inputOrgano"[^>]*>(.*?)</select>', h, re.S) or \
        re.search(r'<select[^>]*name="[^"]*inputOrgano"[^>]*>(.*?)</select>', h, re.S)
    mapa, fuera = {}, []
    for v, t in re.findall(r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', m.group(1), re.S):
        t = html.unescape(re.sub("<[^>]+>", "", t)).strip()
        if not t.upper().startswith("AYUNTAMIENTO DE "):
            continue
        nom = t[len("AYUNTAMIENTO DE "):].strip()
        if nom.upper() in FUERA or "MANCOMUNIDAD" in nom.upper() or "SERVICIO" in nom.upper():
            fuera.append(nom)
            continue
        mapa.setdefault(nom.title() if nom.isupper() else nom, v)
    dest = os.path.join(HERE, "ordenanzas_data")
    with open(os.path.join(dest, "bop_gipuzkoa_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(mapa.items())), f, ensure_ascii=False, indent=1)
    cfg = {"id": "gipuzkoa", "base": "https://egoitza.gipuzkoa.eus",
           "mapa": "bop_gipuzkoa_municipios.json", "nombre": "Gipuzkoa",
           "familia": "gipuzkoa", "indice_desde": 2000,
           "verifica_texto": True, "fulltext": True}
    with open(os.path.join(dest, "bop_gipuzkoa_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"municipios: {len(mapa)} (excluidos {len(fuera)}: {fuera})")
    for k in ("San Sebastián", "Irun", "Errenteria", "Eibar", "Zarautz", "Tolosa", "Hondarribia"):
        print(f"   {k:18s} -> {mapa.get(k)}")


if __name__ == "__main__":
    main()
