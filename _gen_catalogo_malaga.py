# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/malaga.json desde malaga.eu (normativa municipal):
ordenanzas administrativas + fiscales + reglamentos. Cada item = <h2> con el
título y un enlace al visor NRMDocumentDisplayer/<id> (PDF). Paginación
index.html?mas=true&pageNum=N (10 por página).

Script OFFLINE (excluido del deploy por `_*`):
    python _gen_catalogo_malaga.py
"""
import json
import os
import re
import sys
import html as H
import urllib.request

from _gen_comun import alias_para, norm

BASE = "https://www.malaga.eu/el-ayuntamiento/normativa-municipal/"
SECCIONES = [("Ordenanzas administrativas", BASE + "ordenanzas-administrativas/"),
             ("Ordenanzas fiscales", BASE + "ordenanzas-fiscales/"),
             ("Reglamentos", BASE + "reglamentos/"),
             ("Urbanismo", "https://urbanismo.malaga.eu/normativa-y-planeamiento/normativa/")]
TITULO_MAL = re.compile(r"\(UE\)|parlamento europeo|real decreto|ley organica|"
                        r"bases reguladoras", re.I)
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "malaga.json")

TITULO_OK = re.compile(r"^(ordenanza|reglamento|normas|estatutos)", re.I)
EXTRAS = [
    (r"convivencia", ["botellon", "civismo", "consumo de alcohol en la via publica"]),
    (r"ocupacion de la via publica", ["terraza", "terrazas", "veladores",
                                      "mesas y sillas", "horario de terrazas"]),
    (r"movilidad", ["zbe", "zona de bajas emisiones"]),
]


# Normas que NO cuelgan de los listados (localizadas a mano).
# (titulo, categoria, id, url_texto_completo)
MANUALES = [
    ("Ordenanza de Movilidad de la ciudad de Malaga", "Movilidad", "640",
     "https://movilidad.malaga.eu/opencms/export/sites/movilidad/.content/"
     "galerias/Documentos-del-site/Ordenanza-de-Movilidad-de-la-Ciudad-de-Malaga-2024.pdf"),
]


def get(url):
    import time
    for intento in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (jurisprudenciator-gen)"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            if intento == 2:
                raise
            time.sleep(2 + intento * 3)


def items_de(t, host):
    """Pares (titulo_h2_anterior, url_visor, id) de una página."""
    pares = []
    h2s = [(m.start(), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", H.unescape(m.group(1)))).strip())
           for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", t, re.S)]
    for m in re.finditer(r'href="([^"]*?/visorcontenido/NRMDocumentDisplayer/(\d+)/[^"]*)"', t):
        u = m.group(1)
        if not u.startswith("http"):
            u = host + u
        previos = [tit for pos, tit in h2s if pos < m.start() and tit]
        if previos:
            pares.append((previos[-1], u, m.group(2)))
    return pares


def main():
    normas, vistos = [], set()
    for cat, base in SECCIONES:
        pagina, total = 1, 0
        while pagina < 40:
            url = base if pagina == 1 else f"{base}index.html?mas=true&pageNum={pagina}"
            try:
                t = get(url)
            except Exception as e:  # noqa: BLE001
                print(f"  {cat} pag{pagina}: ERR {e}")
                break
            host = re.match(r"(https://[^/]+)", base).group(1)
            pares = items_de(t, host)
            if not pares:
                break
            nuevos = 0
            for titulo, docurl, did in pares:
                nid = f"mlg-{did}"
                if nid in vistos or not TITULO_OK.match(titulo) or TITULO_MAL.search(titulo):
                    continue
                vistos.add(nid)
                nuevos += 1
                extras = []
                for pat, al in EXTRAS:
                    if re.search(pat, norm(titulo)):
                        extras.extend(al)
                normas.append({"id": nid, "titulo": titulo, "cat": cat, "ref": "",
                               "pub": "", "mod": "", "alias": alias_para(titulo, extras),
                               "url": docurl, "formato": "pdf"})
            total += nuevos
            if nuevos == 0 and pagina > 1:
                break
            pagina += 1
        print(f"{cat}: {total} normas")
    for titulo, cat, did, url in MANUALES:
        nid = f"mlg-{did}"
        if nid not in vistos:
            vistos.add(nid)
            extras = []
            for pat, al in EXTRAS:
                if re.search(pat, norm(titulo)):
                    extras.extend(al)
            normas.append({"id": nid, "titulo": titulo, "cat": cat, "ref": "",
                           "pub": "", "mod": "", "alias": alias_para(titulo, extras),
                           "url": url, "formato": "pdf"})
    # dedupe por titulo-sin-año (fiscales de ejercicios sucesivos): gana el id
    # de documento MAS ALTO (documento mas reciente en el gestor)
    porclave = {}
    for n in normas:
        clave = re.sub(r"\b(19|20)\d{2}\b", "", norm(n["titulo"])).strip()
        prev = porclave.get(clave)
        if prev is None or int(n["id"].split("-")[1]) > int(prev["id"].split("-")[1]):
            porclave[clave] = n
    normas = list(porclave.values())
    normas.sort(key=lambda n: (n["cat"], n["titulo"]))
    catalogo = {
        "meta": {"municipio": "malaga",
                 "fuente": "Ayuntamiento de Malaga (normativa municipal, PDF oficial por norma)",
                 "url": BASE},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
