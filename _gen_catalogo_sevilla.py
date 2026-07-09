# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/sevilla.json desde las páginas del Servicio de Apoyo
Jurídico del Ayuntamiento de Sevilla (PDF por norma, URL estable en sevilla.org):
  * ordenanzas-del-municipio-de-sevilla  (~49 PDFs)
  * reglamentos-del-municipio-de-sevilla (~51 PDFs)

NOTA: las ordenanzas FISCALES de Sevilla las publica la Agencia Tributaria de
Sevilla en su propia web; se añaden aparte si su fuente es scrapeable.

Script OFFLINE (excluido del deploy por `_*`):
    python _gen_catalogo_sevilla.py
"""
import json
import os
import re
import sys
import html as H
import urllib.request

from _gen_comun import alias_para, norm

PAGINAS = [
    ("Ordenanzas", "https://www.sevilla.org/ayuntamiento/unidad-organica/"
                   "servicio-de-apoyo-juridico/ordenanzas-del-municipio-de-sevilla"),
    ("Reglamentos", "https://www.sevilla.org/ayuntamiento/unidad-organica/"
                    "servicio-de-apoyo-juridico/reglamentos-del-municipio-de-sevilla"),
]
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "sevilla.json")

TITULO_OK = re.compile(r"^(ordenanza|reglamento|normas|estatutos)", re.I)

EXTRAS = [
    (r"convivencia|via publica", ["botellon", "civismo"]),
    (r"circulacion", ["movilidad", "trafico", "bicicleta", "patinete", "vmp",
                      "estacionamiento", "zbe"]),
    (r"veladores", ["terraza", "terrazas", "mesas y sillas", "horario de terrazas"]),
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (jurisprudenciator-gen)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def main():
    normas, vistos = [], set()
    for cat, url in PAGINAS:
        t = get(url)
        links = re.findall(r'href="([^"]+\.pdf)"[^>]*>(.*?)</a>', t, re.S | re.I)
        print(f"{cat}: {len(links)} PDFs en la pagina")
        for u, lbl in links:
            titulo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", H.unescape(lbl))).strip()
            if not TITULO_OK.match(titulo):
                continue
            if not u.startswith("http"):
                u = "https://www.sevilla.org" + u
            fich = u.rsplit("/", 1)[-1].removesuffix(".pdf")
            nid = "sev-" + re.sub(r"[^a-z0-9]+", "-", fich.lower())[:48].strip("-")
            if nid in vistos:
                continue
            vistos.add(nid)
            extras = []
            for pat, al in EXTRAS:
                if re.search(pat, norm(titulo)):
                    extras.extend(al)
            normas.append({"id": nid, "titulo": titulo, "cat": cat, "ref": "",
                           "pub": "", "mod": "", "alias": alias_para(titulo, extras),
                           "url": u, "formato": "pdf"})
    normas.sort(key=lambda n: (n["cat"], n["titulo"]))
    print(f"normas utiles: {len(normas)}")
    catalogo = {
        "meta": {"municipio": "sevilla",
                 "fuente": "Ayuntamiento de Sevilla (Servicio de Apoyo Juridico, PDF oficial por norma)",
                 "url": "https://www.sevilla.org/ayuntamiento/reglamentos-y-ordenanzas"},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
