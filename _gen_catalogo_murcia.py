# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/murcia.json desde web.murcia.es (Drupal):
  * /institucional/ordenanzas  (+ pager ?page=N) — PDF por norma
  * /institucional/reglamentos (+ pager)
  * ZIP anual de ORDENANZAS FISCALES vigentes (un PDF por ordenanza dentro del
    zip; el motor lo abre con formato="zip" + miembro).

Script OFFLINE (excluido del deploy por `_*`):
    python _gen_catalogo_murcia.py
"""
import io
import json
import os
import re
import sys
import html as H
import urllib.request
import zipfile

from _gen_comun import alias_para, norm

BASE = "https://web.murcia.es"
SECCIONES = [("Ordenanzas", "/institucional/ordenanzas"),
             ("Reglamentos", "/institucional/reglamentos")]
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "murcia.json")

TITULO_OK = re.compile(r"^(ordenanza|reglamento|normas|estatutos)", re.I)

# El PDF de la ficha de algunas normas es solo el ACUERDO de aprobacion; el
# texto integro vive en otra URL (localizada a mano): id -> url del texto.
URLS_OVERRIDE = {
    "mur-ordenanza-reguladora-de-determinadas-actividades-o-c":
        "https://www.murcia.es/documents/11263/242162/Ordenanza-reg-determinadas-actividades-conductas-espacio-publico-2019.pdf",
}
EXTRAS = [
    (r"espacios abiertos|conductas en el espacio publico|convivencia",
     ["botellon", "civismo", "consumo de alcohol en la via publica"]),
    (r"mesas.*sillas|via publica", ["terraza", "terrazas", "veladores"]),
]


def get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (jurisprudenciator-gen)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def get(url):
    return get_bytes(url).decode("utf-8", "replace")


def main():
    normas, vistos = [], set()
    for cat, ruta in SECCIONES:
        pagina, total = 0, 0
        while pagina < 20:
            url = BASE + ruta + (f"?page={pagina}" if pagina else "")
            t = get(url)
            pares = re.findall(r'href="(/sites/default/files/[^"]+\.pdf[^"]*)"[^>]*>(.*?)</a>',
                               t, re.S | re.I)
            nuevos = 0
            for u, lbl in pares:
                titulo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", H.unescape(lbl))).strip()
                # fuera el sufijo "( 25/04/2024 - 2.01 mb)" de las fichas
                titulo = re.sub(r"\s*\(\s*\d{1,2}/\d{1,2}/\d{2,4}[^)]*\)\s*$", "", titulo).strip()
                if not titulo or not TITULO_OK.match(titulo):
                    continue
                # a Titulo legible (las fichas van en MAYUSCULAS)
                if titulo.isupper():
                    titulo = titulo.capitalize()
                nid = "mur-" + re.sub(r"[^a-z0-9]+", "-", norm(titulo))[:52].strip("-")
                if nid in vistos:
                    continue
                vistos.add(nid)
                nuevos += 1
                extras = [al for pat, alias in EXTRAS if re.search(pat, norm(titulo))
                          for al in alias]
                entrada = {"id": nid, "titulo": titulo, "cat": cat, "ref": "",
                           "pub": "", "mod": "", "alias": alias_para(titulo, extras),
                           "url": BASE + u, "formato": "pdf"}
                if nid in URLS_OVERRIDE:
                    entrada["urls"] = [URLS_OVERRIDE[nid], BASE + u]
                    entrada["url"] = URLS_OVERRIDE[nid]
                normas.append(entrada)
            total += nuevos
            if not pares or (nuevos == 0 and pagina > 0):
                break
            pagina += 1
        print(f"{cat}: {total} normas")

    # ---- fiscales: ZIP anual enlazado en /institucional/ordenanzas-historico
    t = get(BASE + "/institucional/ordenanzas-historico")
    m = re.search(r'href="(/sites/default/files/[^"]*fiscales[^"]*\.zip)"', t, re.I)
    if m:
        zurl = BASE + m.group(1)
        print("ZIP fiscales:", zurl.rsplit("/", 1)[-1])
        z = zipfile.ZipFile(io.BytesIO(get_bytes(zurl)))
        nfis = 0
        for miembro in z.namelist():
            if not miembro.lower().endswith(".pdf"):
                continue
            base = miembro.rsplit("/", 1)[-1].removesuffix(".pdf")
            titulo = re.sub(r"^[\d.\- ]+", "", base).replace("-", " ").replace("_", " ").strip()
            if not titulo:
                continue
            titulo = "Ordenanza fiscal: " + titulo
            nid = "mur-of-" + re.sub(r"[^a-z0-9]+", "-", norm(base))[:48].strip("-")
            if nid in vistos:
                continue
            vistos.add(nid)
            nfis += 1
            normas.append({"id": nid, "titulo": titulo, "cat": "Ordenanzas fiscales",
                           "ref": "", "pub": "", "mod": "",
                           "alias": alias_para(titulo), "url": zurl,
                           "formato": "zip", "miembro": miembro})
        print(f"Ordenanzas fiscales (zip): {nfis}")
    else:
        print("AVISO: no se encontro el ZIP de fiscales")

    normas.sort(key=lambda n: (n["cat"], n["titulo"]))
    catalogo = {
        "meta": {"municipio": "murcia",
                 "fuente": "Ayuntamiento de Murcia (web institucional, PDF oficial por norma)",
                 "url": BASE + "/institucional/ordenanzas"},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
