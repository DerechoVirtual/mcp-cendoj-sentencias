# -*- coding: utf-8 -*-
"""
Generador de fge_data/ — doctrina de la Fiscalía General del Estado EMPAQUETADA.

Lee el corpus crudo descargado del BOE (coleccion 'fiscalia': 396 circulares,
consultas e instrucciones 1979-2026, texto integro en HTML) y produce:

  fge_data/catalogo.json      metadatos de los 396 documentos (ligero)
  fge_data/indice.json.gz     indice invertido BM25 precomputado (texto+titulo)
  fge_data/textos/<REF>.json.gz   texto integro por documento (carga selectiva)

La BUSQUEDA del conector no toca la red: catalogo+indice en memoria (ms).
Uso:  python _gen_fge_data.py <dir_corpus_crudo>
"""
import gzip
import json
import os
import re
import sys
import unicodedata
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fge_data")

# --- tokenizacion identica a la de fge_engine (mantener en sincronia) ---
_STOP = set("""a al algo ante como con contra cual cuales cuando de del desde donde dos el ella ellas ellos en entre era eran es esa esas ese esos esta estas este estos fue fueron ha haber habia han hasta hay la las le les lo los mas me mi mientras muy no nos nosotros o os otra otras otro otros para pero por porque que quien quienes se sea sean segun ser si sin sobre son su sus tal tambien tanto te tiene tienen toda todas todo todos tras tu un una unas uno unos vosotros y ya""".split())


def _quitar_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _stem(t):
    """Stemming MUY ligero para unificar plural/singular y familias -cion."""
    if len(t) > 5 and t.endswith("mente"):
        t = t[:-5]
    for suf, rep in (("ciones", "cion"), ("siones", "sion"), ("idades", "idad"),
                     ("mientos", "miento"), ("amientos", "amiento")):
        if t.endswith(suf):
            return t[: -len(suf)] + rep
    if len(t) > 4 and t.endswith("es") and t[-3] not in "aeiou":
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and t[-2] in "aeiou":
        return t[:-1]
    return t


def tokens(s):
    s = _quitar_tildes((s or "").lower())
    out = []
    for t in re.findall(r"[a-z0-9]{2,}", s):
        if t in _STOP or t.isdigit() and len(t) > 4:
            continue
        out.append(_stem(t))
    return out


def main(src):
    files = sorted(f for f in os.listdir(src) if f.endswith(".json"))
    docs = []
    for f in files:
        docs.append(json.load(open(os.path.join(src, f), encoding="utf-8")))
    # orden estable: mas reciente primero (fecha desc, luego ref)
    docs.sort(key=lambda d: (d.get("fecha") or f"{d['anno']}-00-00", d["ref"]),
              reverse=True)

    os.makedirs(os.path.join(OUT, "textos"), exist_ok=True)
    catalogo = []
    post_txt = {}   # term -> lista [i, tf]
    post_tit = {}   # term -> lista [i, tf]  (titulo + materias)
    longitudes = []
    for i, d in enumerate(docs):
        texto = d.get("texto") or ""
        tt = tokens(texto)
        tl = tokens(d["titulo"] + " " + " ".join(d.get("materias") or []))
        longitudes.append(len(tt))
        for term, tf in Counter(tt).items():
            post_txt.setdefault(term, []).append([i, tf])
        for term, tf in Counter(tl).items():
            post_tit.setdefault(term, []).append([i, tf])
        catalogo.append({
            "ref": d["ref"], "tipo": d["tipo"], "anno": d["anno"],
            "numero": d["numero"], "titulo": d["titulo"],
            "fecha": d.get("fecha"), "materias": d.get("materias") or [],
            "relacionadas": d.get("relacionadas") or [],
            "chars": len(texto),
        })
        with gzip.open(os.path.join(OUT, "textos", d["ref"] + ".json.gz"),
                       "wt", encoding="utf-8") as g:
            json.dump({"ref": d["ref"], "texto": texto}, g, ensure_ascii=False)

    json.dump(catalogo, open(os.path.join(OUT, "catalogo.json"), "w",
                             encoding="utf-8"), ensure_ascii=False)
    avgdl = sum(longitudes) / max(1, len(longitudes))
    idx = {"N": len(docs), "avgdl": avgdl, "dl": longitudes,
           "txt": post_txt, "tit": post_tit}
    with gzip.open(os.path.join(OUT, "indice.json.gz"), "wt",
                   encoding="utf-8") as g:
        json.dump(idx, g, ensure_ascii=False)

    ntxt = sum(len(v) for v in post_txt.values())
    print(f"docs={len(docs)} terminos_txt={len(post_txt)} postings={ntxt} "
          f"avgdl={avgdl:.0f}")
    print("catalogo.json:", os.path.getsize(os.path.join(OUT, 'catalogo.json')))
    print("indice.json.gz:", os.path.getsize(os.path.join(OUT, 'indice.json.gz')))
    tot = sum(os.path.getsize(os.path.join(OUT, 'textos', f))
              for f in os.listdir(os.path.join(OUT, 'textos')))
    print("textos/ total:", tot)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         os.path.join(os.path.dirname(HERE), "corpus"))
