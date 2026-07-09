# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/laspalmas.json (Las Palmas de Gran Canaria) desde
laspalmasgc.es: paginas de ordenanzas fiscales + normativa no fiscal +
ordenanzas de urbanismo (solo las municipales), mas una lista curada de PDFs
clave que no cuelgan de esos listados (convivencia, trafico, ruidos...).

Script OFFLINE (excluido del deploy por `_*`):
    python _gen_catalogo_laspalmas.py
"""
import json
import os
import re
import sys
import html as H
import urllib.request

from _gen_comun import alias_para, norm

BASE = "https://www.laspalmasgc.es"
PAGINAS = [
    ("Ordenanzas fiscales", BASE + "/es/ayuntamiento/normativa/ordenanzas-fiscales/"),
    ("Normativa no fiscal", BASE + "/es/ayuntamiento/normativa/normativa-no-fiscal/"),
    ("Urbanismo", BASE + "/es/areas-tematicas/urbanismo-e-infraestructuras/ordenanzas-y-normativa/"),
]
MANUALES = [
    # el PDF de .galleries esta ESCANEADO sin capa de texto; el de bibliojoven si es legible
    ("Ordenanza General de Convivencia Ciudadana y Via Publica", "Convivencia",
     BASE + "/web/bibliojoven/Ciudadania/Normativa/ORDENAZA%20CIUDADANIA%20LPGC.pdf"),
    ("Ordenanza de Trafico de Las Palmas de Gran Canaria", "Movilidad",
     BASE + "/export/sites/laspalmasgc/.galleries/documentos-normativa/Ordenanza-de-Trafico-de-Las-Palmas-de-Gran-Canaria.pdf"),
    ("Ordenanza Municipal de Proteccion del Medio Ambiente frente a Ruidos y Vibraciones", "Medio ambiente",
     BASE + "/export/sites/laspalmasgc/.galleries/documentos-normativa/Ordenanza-Mcpal.-de-Proteccion-del-Medio-Ambiente-frente-a-Ruidos-y-Vibraciones.pdf"),
    ("Ordenanza Municipal Reguladora de los Usos y Actividades", "Actividades",
     BASE + "/export/sites/laspalmasgc/.galleries/documentos-normativa/Ordenanza-Municipal-Reguladora-de-los-Usos-Actividades.....pdf"),
    ("Ordenanza Municipal de Edificacion", "Urbanismo",
     BASE + "/export/sites/laspalmasgc/.galleries/documentos-normativa/Ord.-Mun.-de-Edificacion.pdf"),
]
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "laspalmas.json")

TITULO_OK = re.compile(r"^[\d.\s]*\s*(ordenanza|reglamento|normas|estatutos)", re.I)
TITULO_MAL = re.compile(r"real decreto|^ley\b|^.?ey de|decreto \d+/|indice|callejero", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (jurisprudenciator-gen)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def main():
    normas, vistos = [], set()

    def alta(titulo, cat, url):
        nid = "lpg-" + re.sub(r"[^a-z0-9]+", "-", norm(titulo))[:50].strip("-")
        if nid in vistos:
            return
        vistos.add(nid)
        normas.append({"id": nid, "titulo": titulo, "cat": cat, "ref": "", "pub": "",
                       "mod": "", "alias": alias_para(titulo), "url": url,
                       "formato": "pdf"})

    for cat, purl in PAGINAS:
        try:
            t = get(purl)
        except Exception as e:  # noqa: BLE001
            print(f"{cat}: ERR {e}")
            continue
        n0 = len(normas)
        for m in re.finditer(r'href="([^"]+\.pdf[^"]*)"[^>]*>(.*?)</a>', t, re.S | re.I):
            u = m.group(1)
            titulo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", H.unescape(m.group(2)))).strip()
            if not titulo or not TITULO_OK.match(titulo) or TITULO_MAL.search(titulo):
                continue
            if titulo.isupper():
                titulo = titulo.capitalize()
            if not u.startswith("http"):
                u = BASE + u
            alta(titulo, cat, u)
        print(f"{cat}: {len(normas) - n0} normas")
    for titulo, cat, url in MANUALES:
        alta(titulo, cat, url)

    # VALIDACION: fuera los PDF escaneados sin capa de texto (ilegibles)
    try:
        import fitz
        legibles = []
        for n in normas:
            try:
                req = urllib.request.Request(n["url"], headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=40).read()
                doc = fitz.open(stream=data, filetype="pdf")
                chars = sum(len(p.get_text()) for p in doc)
                doc.close()
            except Exception:  # noqa: BLE001
                chars = -1
            if chars >= 500:
                legibles.append(n)
            else:
                print(f"  DESCARTADA (escaneada/ilegible, {chars} chars): {n['titulo'][:60]}")
        normas = legibles
    except ImportError:
        print("  (sin fitz: validacion de texto omitida)")

    normas.sort(key=lambda n: (n["cat"], n["titulo"]))
    catalogo = {
        "meta": {"municipio": "laspalmas",
                 "fuente": "Ayuntamiento de Las Palmas de Gran Canaria (PDF oficial por norma)",
                 "url": BASE + "/es/ayuntamiento/normativa/"},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
