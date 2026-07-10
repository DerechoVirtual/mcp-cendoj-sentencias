# -*- coding: utf-8 -*-
"""Rellena el texto empaquetado de León SOLO para las normas que aún no lo tienen
(idempotente: no re-descarga ni re-OCR las ya hechas). Offline/_gen."""
import json
import os
import re
import sys
import time
import urllib.request

import fitz

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bop_engine as _bop
import ordenanzas_engine as _oe

_env = open(os.path.expanduser("~/.claude/.env"), encoding="utf-8", errors="replace").read()
for _k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
    _m = re.search(rf"^{_k}=(.+)$", _env, re.M)
    if _m:
        os.environ[_k] = _m.group(1).strip().strip('"')

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CAT = os.path.join(HERE, "ordenanzas_data", "leon_capital.json")
TD = os.path.join(HERE, "ordenanzas_data", "leon_capital_textos")
OCR_MAX = 26


def descargar(url):
    for _ in range(6):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read()
        except urllib.error.HTTPError:
            return None
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return None


# texto REAL = suficientes palabras españolas comunes (no glyphs/CID basura)
_PALS = re.compile(r"\b(de|la|el|los|las|art[íi]culo|ordenanza|ayuntamiento|que|para|del|por|se|con|una?)\b", re.I)


def _es_texto_real(t):
    return len(_PALS.findall(t)) >= 15 and len(t) / max(1, len(t.split("\n"))) < 100000


def extraer(doc):
    directo = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    # sirve el texto directo SOLO si es texto español real (no CID/glyph basura)
    if len(directo) / max(1, doc.page_count) >= 300 and _es_texto_real(directo):
        return _oe._reparar_parrafos_pdf(directo), "texto"
    n = min(doc.page_count, OCR_MAX)
    import concurrent.futures as cf
    pngs = [doc[i].get_pixmap(dpi=150).tobytes("png") for i in range(n)]
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        pags = list(ex.map(_bop._ocr_pagina, pngs))
    nota = "" if doc.page_count <= OCR_MAX else \
        f"\n\n[Nota: documento escaneado de {doc.page_count} págs; transcrito el articulado (primeras {n}).]"
    return "\n".join(p for p in pags if p) + nota, f"ocr({n}/{doc.page_count})"


def main():
    os.makedirs(TD, exist_ok=True)
    cat = json.load(open(CAT, encoding="utf-8"))
    cambios = 0
    for n in cat["normas"]:
        fich = n.get("texto") or (n["id"] + ".txt")
        fp = os.path.join(TD, fich)
        if n.get("texto") and os.path.exists(fp):
            try:                       # re-procesar si el .txt guardado es basura CID
                if _es_texto_real(open(fp, encoding="utf-8").read()):
                    continue
                print(f"↻ {n['id']} texto guardado es basura (CID) -> re-OCR")
            except Exception:  # noqa: BLE001
                continue
        data = descargar(n["url"])
        if not data or data[:5] != b"%PDF-":
            print(f"❌ {n['id']} no se pudo descargar {n['titulo'][:45]}")
            continue
        try:
            txt, via = extraer(fitz.open(stream=data, filetype="pdf"))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {n['id']} error extrayendo: {e}")
            continue
        if len(txt) < 400:
            print(f"❌ {n['id']} SIN_TEXTO {n['titulo'][:45]}")
            continue
        with open(os.path.join(TD, fich), "w", encoding="utf-8") as f:
            f.write(txt)
        n["texto"] = fich
        cambios += 1
        print(f"✅ {n['id']} [{via:10}] {len(txt):7} ch  {n['titulo'][:48]}")
    if cambios:
        with open(CAT, "w", encoding="utf-8") as f:
            json.dump(cat, f, ensure_ascii=False, indent=1)
    faltan = [x["id"] for x in cat["normas"] if not (x.get("texto") and os.path.exists(os.path.join(TD, x["texto"])))]
    print(f"\n{cambios} rellenadas · {len(cat['normas'])} normas · faltan {len(faltan)}: {faltan}")


if __name__ == "__main__":
    main()
