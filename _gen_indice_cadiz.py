# -*- coding: utf-8 -*-
"""Índice EMPAQUETADO del BOP de Cádiz (patrón Madrid/BOCM).

Por qué: el buscador nuevo del BOP (/buscador/index.html?q=) es full-text sobre el
PDF del boletín entero y su relevancia es inservible para localizar la ordenanza de
UN ayuntamiento (devuelve boletines donde otro municipio habla de la materia). En
cambio la página de cada boletín (/boletin/Boletin-numero-NNN-del-ano-AAAA/) lista
TODOS sus anuncios con órgano, título y PDF#page, de forma exacta. Se recorren todos
los boletines desde 2010 y se guardan solo los anuncios NORMATIVOS de ayuntamientos.

Salida: ordenanzas_data/cadiz_indice.json  {"meta": {...}, "anuncios": [...]}
Uso:    python -X utf8 _gen_indice_cadiz.py [--desde 2010] [--hasta 2026] [--workers 6]
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time

import bop_engine as B

HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(HERE, "ordenanzas_data", "cadiz_indice.json")
BASE = B.PROVINCIAS["cadiz"]["base"]
NORMATIVO = re.compile(r"ordenan[zç]a|reglament|\btasa\b|precio p[uú]blico|prestaci[oó]n patrimonial|"
                       r"aprobaci[oó]n definitiva|texto (?:[ií]ntegro|refundido)|\bbando\b|"
                       r"normas? urban|plan (?:general|especial)|limitaci[oó]n|regulaci[oó]n|estatutos", re.I)
NO = re.compile(r"padr[oó]n|notificaci[oó]n|licitaci[oó]n|contrataci[oó]n|adjudicaci[oó]n|"
                r"bases (?:de|para|del)|convocatoria|nombramiento|delegaci[oó]n|lista (?:provisional|definitiva)|"
                r"oferta de empleo|expediente sancionador|emplazamiento|subasta|formalizaci[oó]n|"
                r"cobranza|calificaci[oó]n ambiental|licencia de|extracto", re.I)


def boletin(slug):
    try:
        page = B._cadiz_get(BASE + "/boletin/" + slug, timeout=30).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return slug, None, str(e)[:60]
    if "Boletín número" not in page and "Boletin numero" not in page and len(page) < 8000:
        return slug, None, "404"
    out = []
    for mm in re.finditer(r"(\d{1,3}(?:\.\d{3})*)\.-\s*(Ayuntamiento de [^.<]+?)\.\s*(.*?)\s*"
                          r'<a[^>]+href="([^"]+\.pdf#page=(\d+))"', page, re.S):
        tit = re.sub(r"\s+", " ", B._html.unescape(re.sub(r"<[^>]+>", " ", mm.group(3))).strip().rstrip("."))
        if not NORMATIVO.search(tit) or (NO.search(tit) and not re.search(r"ordenan[zç]a|reglament", tit, re.I)):
            continue
        pdf = mm.group(4)
        out.append({"o": mm.group(2).strip(), "t": tit[:220], "n": mm.group(1),
                    "p": (BASE + pdf) if pdf.startswith("/") else pdf, "pg": int(mm.group(5))})
    return slug, out, ""


def main():
    args = sys.argv[1:]
    desde = int(args[args.index("--desde") + 1]) if "--desde" in args else 2010
    hasta = int(args[args.index("--hasta") + 1]) if "--hasta" in args else dt.date.today().year
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 6
    previo = {}
    if os.path.exists(SALIDA):
        try:
            previo = {a["slug"]: True for a in json.load(open(SALIDA, encoding="utf-8")).get("anuncios", [])}
        except Exception:  # noqa: BLE001
            previo = {}
    anuncios = json.load(open(SALIDA, encoding="utf-8")).get("anuncios", []) if previo else []
    slugs = [f"Boletin-numero-{n:03d}-del-ano-{y}" for y in range(desde, hasta + 1) for n in range(1, 261)]
    slugs = [s for s in slugs if s not in previo]
    t0 = time.time()
    hechos = vacios = errores = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (slug, out, err) in enumerate(ex.map(boletin, slugs), 1):
            if out is None:
                if err == "404":
                    vacios += 1
                else:
                    errores += 1
                    print("ERR", slug, err, flush=True)
            else:
                hechos += 1
                for a in out:
                    a["slug"] = slug
                    anuncios.append(a)
            if i % 200 == 0:
                print(f"{i}/{len(slugs)} boletines · {hechos} ok · {vacios} inexistentes · {errores} err · "
                      f"{len(anuncios)} anuncios · {time.time()-t0:.0f}s", flush=True)
                with open(SALIDA, "w", encoding="utf-8") as f:
                    json.dump({"meta": {"generado": dt.date.today().isoformat(), "desde": desde, "hasta": hasta,
                                        "parcial": True}, "anuncios": anuncios}, f, ensure_ascii=False)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump({"meta": {"generado": dt.date.today().isoformat(), "desde": desde, "hasta": hasta,
                            "boletines": hechos, "fuente": BASE}, "anuncios": anuncios}, f, ensure_ascii=False)
    munis = len({a["o"] for a in anuncios})
    print(f"LISTO: {hechos} boletines, {len(anuncios)} anuncios normativos de {munis} órganos, "
          f"{os.path.getsize(SALIDA)/1e6:.1f} MB, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
