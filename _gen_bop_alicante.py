# -*- coding: utf-8 -*-
"""Mapa + config del BOP de ALICANTE (webservice JSON eConsulta). Familia
`alicante`. El filtro `publicante` exige el nombre EXACTO (formas valencianas):
por eso cada municipio se indexa bajo VARIAS variantes -> mismo nombre exacto.
Offline/_gen."""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")
B = "https://sede.diputacionalicante.es"
WS = "/wp-content/themes/Desarrollo-Diputacion/webservices/wseConsultaAjax.php"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def uw(v):
    return (v[0] if isinstance(v, list) and v else (v or "")) if v is not None else ""


def g(u, t=45):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read()


VENTANAS = [("01/01/2023", "31/12/2027"), ("01/01/2018", "31/12/2022")]  # el WS limita ~5 años


def buscar(texto, desde, hasta):
    xml = (f"<raiz><entrada><registro><desde>{desde}</desde><hasta>{hasta}</hasta>"
           f"<texto>{texto}</texto><tipoorganismo></tipoorganismo><publicante></publicante>"
           f"</registro></entrada></raiz>")
    u = B + WS + "?" + urllib.parse.urlencode({"nemo": "BOP_EDI", "usuario": "-", "param": xml})
    d = json.loads(g(u).decode("utf-8", "replace"))
    reg = d.get("bop", {}).get("registro") or []
    return [reg] if isinstance(reg, dict) else reg


def variantes(nom):
    """Claves de índice para un municipio con forma valenciana."""
    keys = {nom}
    # "Alcoy/Alcoi" -> ambas
    for parte in re.split(r"\s*/\s*", nom):
        keys.add(parte.strip())
    base = re.sub(r"\s*\([^)]*\)", "", nom).strip()      # quita "(l')", "(el)"...
    keys.add(base)
    for parte in re.split(r"\s*/\s*", base):
        keys.add(parte.strip())
    # artículo entre paréntesis al final -> delante: "Alfàs del Pi (l')" -> "l'Alfàs del Pi"
    m = re.match(r"^(.*?)\s*\((l'|el|la|els|les|los|las)\)\s*$", nom, re.I)
    if m:
        art = m.group(2)
        sep = "" if art.lower().endswith("'") else " "
        keys.add(f"{art}{sep}{m.group(1).strip()}")
        keys.add(m.group(1).strip())
    return {k for k in keys if k and len(k) < 60}


def main():
    exactos = set()
    for texto in ("reglamento", "tasa", "ordenanza fiscal", "anuncio"):
        for desde, hasta in VENTANAS:
            try:
                regs = buscar(texto, desde, hasta)
            except Exception as e:  # noqa: BLE001
                print(f"  {texto} {desde}: ERR {e}"); continue
            for r in regs:
                nom = uw(r.get("denominacion")).strip()
                if nom and "Ayuntamiento" not in nom and len(nom) < 55:
                    exactos.add(nom)
        print(f"  {texto}: total munis {len(exactos)}")
    mapa = {}
    for nom in exactos:
        # el filtro `publicante` acepta la forma CASTELLANA limpia (1ª parte antes
        # de "/", sin paréntesis): "Elche/Elx"->"Elche", "Alfàs del Pi (l')"->"Alfàs del Pi"
        filtro = re.sub(r"\s*\([^)]*\)", "", nom.split("/")[0]).strip()
        for k in variantes(nom):
            mapa.setdefault(k, filtro)       # variante -> forma que acepta el WS
    with open(os.path.join(DATA, "bop_alicante_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
    cfg = {"id": "alicante", "nombre": "Alicante", "familia": "alicante",
           "base": B, "ws": WS, "mapa": "bop_alicante_municipios.json", "indice_desde": 2011}
    with open(os.path.join(DATA, "bop_alicante_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(exactos)} municipios, {len(mapa)} claves. Muestra:", sorted(exactos)[:6])


if __name__ == "__main__":
    main()
