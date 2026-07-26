# -*- coding: utf-8 -*-
"""Mapa municipio->id del BOPT (Tarragona). Los ids salen del <select> de
/bopt/web/cercador-butlletins con data-parent-id=418 (AJUNTAMENTS DE LA
PROVÍNCIA DE TARRAGONA), así que ya vienen acotados a la provincia."""
import html
import json
import os
import re
import urllib.request

BASE = "https://www.dipta.cat"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
HERE = os.path.dirname(os.path.abspath(__file__))


def canon(n):
    """'AJUNTAMENT ALBIOL, L'' -> \"L'Albiol\"; 'AJUNTAMENT ALCANAR' -> 'Alcanar'."""
    n = re.sub(r"^AJUNTAMENT\s+", "", n.strip(), flags=re.I).strip()
    m = re.match(r"^(.*?),\s*(l'|el|la|els|les|es|sa)\s*$", n, re.I)
    if m:
        art = m.group(2).lower()
        n = (art + m.group(1)) if art.endswith("'") else (art + " " + m.group(1))
    return " ".join(w if w.lower() in ("de", "del", "la", "les", "els", "i", "d'", "l'")
                    else w.capitalize() for w in n.lower().split()).replace("L'", "l'")


def main():
    h = urllib.request.urlopen(urllib.request.Request(
        BASE + "/bopt/web/cercador-butlletins", headers={"User-Agent": UA}),
        timeout=45).read().decode("utf-8", "replace")
    ops = re.findall(r'<option value="(\d+)"[^>]*data-parent-id="418"[^>]*>(.*?)</option>', h, re.S)
    mapa = {}
    for v, t in ops:
        nom = canon(html.unescape(re.sub("<[^>]+>", "", t)).strip())
        if nom:
            mapa.setdefault(nom, v)
    dest = os.path.join(HERE, "ordenanzas_data")
    with open(os.path.join(dest, "bop_tarragona_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(mapa.items())), f, ensure_ascii=False, indent=1)
    cfg = {"id": "tarragona", "base": BASE, "mapa": "bop_tarragona_municipios.json",
           "nombre": "Tarragona", "familia": "tarragona", "indice_desde": 2010,
           "idioma": "ca", "verifica_texto": True, "fulltext": True}
    with open(os.path.join(dest, "bop_tarragona_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"municipios: {len(mapa)}")
    for k in ("Tarragona", "Reus", "El Vendrell", "Cambrils", "Salou", "Valls", "Tortosa", "Amposta"):
        print(f"   {k:16s} -> {mapa.get(k)}")


if __name__ == "__main__":
    main()
