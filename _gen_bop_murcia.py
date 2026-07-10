# -*- coding: utf-8 -*-
"""Mapa + config del BOP de MURCIA (BORM, uniprovincial). Familia REST-JSON propia.
Valor del mapa = nombre del municipio (para anuncianteFaceta). Offline/_gen."""
import http.cookiejar
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def main():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA), ("Referer", "https://www.borm.es/")]
    op.open("https://www.borm.es/", timeout=20).read()
    body = {"textoLibre": "ordenanza", "fechaDesde": "", "fechaHasta": "", "anunciante": "",
            "rango": 0, "tipo": "libre", "nombre": "", "apellidos": "", "nif": "", "etiqueta": 0,
            "origen": 0, "idApartado": "", "anuncianteFaceta": "", "idCategoria": "272", "tipoBusqueda": 0}
    req = urllib.request.Request("https://www.borm.es/services/buscador", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA, "Referer": "https://www.borm.es/", "Accept": "application/json"})
    d = json.loads(op.open(req, timeout=40).read().decode("utf-8", "replace"))
    mapa = {}
    for a in (d.get("anunciantes") or []):
        nombre = (a.get("nombre") if isinstance(a, dict) else a) or ""
        nombre = nombre.strip()
        if nombre and len(nombre) < 60:
            mapa[nombre] = nombre        # VALOR = nombre (para anuncianteFaceta)
    with open(os.path.join(DATA, "bop_murcia_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "murcia_prov", "nombre": "Murcia", "familia": "murcia",
           "base": "https://www.borm.es", "mapa": "bop_murcia_municipios.json", "indice_desde": 2009}
    with open(os.path.join(DATA, "bop_murcia_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(mapa)} municipios. Muestra:", sorted(mapa)[:6])


if __name__ == "__main__":
    main()
