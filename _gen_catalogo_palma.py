# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/palma.json desde la Seu Electrònica de Palma
(classificacio-cronologica?categoryId=40483, ~96 normas server-rendered).

Peculiaridades:
  * Cada ficha lista el PDF ORIGINAL (BOIB) + sus modificaciones: se guardan
    hasta 4 URLs y el motor se queda con el texto más largo (el original).
    OJO: no hay texto consolidado oficial por norma.
  * Las ORDENANZAS FISCALES van en UN PDF anual con todas ("Ordenanzas
    fiscales 2026.pdf"): entrada única en el catálogo.

Script OFFLINE (excluido del deploy por `_*`):
    python _gen_catalogo_palma.py
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import html as H
import urllib.request

from _gen_comun import alias_para, norm

LISTADO = "https://seuelectronica.palma.cat/classificacio-cronologica?categoryId=40483"
BASE = "https://seuelectronica.palma.cat"
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "palma.json")

EXTRAS = [
    (r"civisme|civismo|convivencia", ["botellon", "consumo de alcohol en la via publica"]),
    (r"ocupacion de (la )?via publica|terrasses", ["terraza", "terrazas", "veladores",
                                                   "mesas y sillas"]),
    (r"fiscales", ["ibi", "plusvalia", "iivtnu", "icio", "iae", "ivtm",
                   "impuesto de circulacion", "tasas", "tipo de gravamen",
                   "tributos municipales"]),
]


def get(url):
    for intento in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (jurisprudenciator-gen)", "Accept-Language": "es"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            if intento == 2:
                raise


def main():
    lst = get(LISTADO)
    items, vistos_slug = [], set()
    for u, l in re.findall(r'href="([^"]*)"[^>]*>([^<]{10,120})</a>', lst):
        titulo = " ".join(H.unescape(l).split())
        if not re.match(r"(Ordenan|Reglament)", titulo, re.I):
            continue
        u = H.unescape(u)
        slug = u.split("?")[0].rsplit("/", 1)[-1]
        if slug in vistos_slug:
            continue
        vistos_slug.add(slug)
        items.append((titulo, u if u.startswith("http") else BASE + u, slug))
    # fiscales anuales: quedarnos solo con el año más alto
    fiscales = [(t, u, s) for t, u, s in items if re.search(r"fiscal", t, re.I)]
    if fiscales:
        mejor = max(fiscales, key=lambda x: max([int(a) for a in re.findall(r"(20\d{2})", x[0])] or [0]))
        items = [(t, u, s) for t, u, s in items if (t, u, s) not in fiscales or (t, u, s) == mejor]
    print(f"items utiles: {len(items)}")

    def ficha(entrada):
        titulo, url, slug = entrada
        try:
            h = get(url)
        except Exception as e:  # noqa: BLE001
            return ("ERR", titulo, str(e)[:60])
        docs = []
        for m in re.finditer(r'href="([^"]*(?:/documents/[^"]+|\.pdf[^"]*))"', h):
            u2 = H.unescape(m.group(1))
            if not u2.startswith("http"):
                u2 = BASE + u2
            if u2 not in docs and re.search(r"\.pdf", u2, re.I):
                docs.append(u2)
        if not docs:
            return ("SIN_DOC", titulo, "")
        extras = []
        for pat, al in EXTRAS:
            if re.search(pat, norm(titulo)):
                extras.extend(al)
        return {"id": "pal-" + re.sub(r"[^a-z0-9]+", "-", norm(titulo))[:50].strip("-"),
                "titulo": titulo, "cat": "Normativa municipal", "ref": "", "pub": "",
                "mod": "", "alias": alias_para(titulo, extras),
                "url": docs[0], "urls": docs[:4], "formato": "pdf"}

    normas, incid = [], []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(ficha, items):
            (incid if isinstance(r, tuple) else normas).append(r)
    # dedupe por id
    porid = {}
    for n in normas:
        porid.setdefault(n["id"], n)
    normas = sorted(porid.values(), key=lambda n: n["titulo"])
    print(f"normas con doc: {len(normas)} | incidencias: {len(incid)}")
    for i in incid[:6]:
        print("  ", i[0], i[1][:60], i[2])

    catalogo = {
        "meta": {"municipio": "palma",
                 "fuente": "Seu Electronica de l'Ajuntament de Palma (PDF oficial BOIB por norma; "
                           "texto original + modificaciones, sin consolidar)",
                 "url": LISTADO},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
