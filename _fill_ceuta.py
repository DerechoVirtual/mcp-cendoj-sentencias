# -*- coding: utf-8 -*-
"""Empaqueta el TEXTO de las normas de CEUTA en ordenanzas_data/ceuta_textos/<id>.txt.gz
(patrón León: 0 red y 0 OCR en runtime). Reutiliza _fill_textos.py (descarga con
reintentos, HTML con `meta.recorte`, PDF suelto con OCR) y añade lo que Ceuta necesita:

  * formato 'bocce' = PDF del BOLETÍN COMPLETO (hasta 271 págs) que hay que RECORTAR por
    título. Receta probada el 27-jul-2026 (bop_ceuta_config.json → notas): (1) tomar las
    palabras de más de 4 letras del título; (2) candidata = página con al menos
    len(toks)-1 de ellas; (3) descartar la candidata si en sus 3 páginas no hay ≥2
    «Artículo N» (así cae la página de SUMARIO, que lista todos los títulos); (4) leer
    desde ahí hasta que el articulado vuelve a empezar («Artículo 1» de la norma
    siguiente) o 45 páginas. Mejora sobre la receta: se quitan las palabras genéricas del
    título («reglamento», «ciudad», «ceuta», meses…) y se corta la cabeza de la primera
    página hasta la línea del título.
  * Los 4 boletines de 1997-1998 están ESCANEADOS (13 normas): se OCR-ea el boletín ENTERO
    una sola vez (gpt-4o-mini/Gemini, 4 hilos, caché en el scratchpad) y se aplica la
    misma receta sobre el texto OCR. Con el cap de 30 págs de _fill_textos.py se perdían
    las 8 ordenanzas fiscales del extra 19 de 30-12-1998 (119 págs).

Uso:  python -X utf8 _fill_ceuta.py [--workers 2] [--solo-faltan] [--max-ocr-boletin 140]
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _fill_textos as FT  # noqa: E402  (carga ~/.claude/.env: claves de OCR)
import ordenanzas_engine as OE  # noqa: E402
import bop_engine as B  # noqa: E402
from _gen_capital_web import norm  # noqa: E402

CODIGO = "ceuta"
ART = re.compile(r"(?im)^\s*art[íi]culo\s+(\d+)")
ART_ANY = re.compile(r"(?i)\bart[íi]culo\s+(\d+)")
GENERICAS = {"reglamento", "reglamentos", "ordenanza", "ordenanzas", "ciudad", "ceuta", "autonoma",
             "regulador", "reguladora", "municipal", "acuerdo", "aprobacion", "asamblea", "modificacion",
             "modificado", "mediante", "publicado", "bocce", "servicio", "servicios", "general",
             "procedimiento", "impuesto", "normas", "relativas", "enero", "febrero", "marzo", "abril",
             "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
             "determinadas", "determinados", "diversas", "distintos", "excmo", "creacion"}
OCR_DIR = os.environ.get("CEUTA_OCR_DIR") or os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "Temp", "claude", "jpd", "w", "_ceuta_ocr_cache")
MAX_PAG_VENTANA = 45


def paginas_pdf(datos: bytes) -> list:
    import fitz
    doc = fitz.open(stream=datos, filetype="pdf")
    try:
        return [doc[i].get_text() for i in range(doc.page_count)]
    finally:
        doc.close()


def ocr_boletin(datos: bytes, clave: str, max_pag: int) -> list:
    """OCR de TODAS las páginas del boletín (cap max_pag), cacheado en disco por boletín."""
    os.makedirs(OCR_DIR, exist_ok=True)
    fp = os.path.join(OCR_DIR, clave + ".json")
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    import fitz
    doc = fitz.open(stream=datos, filetype="pdf")
    n = min(doc.page_count, max_pag)
    pngs = [doc[i].get_pixmap(dpi=150).tobytes("png") for i in range(n)]
    doc.close()
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        pags = list(ex.map(B._ocr_pagina, pngs))
    print(f"      [ocr] {n} págs en {time.time()-t0:.0f}s · {sum(len(p) for p in pags)} chars", flush=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(pags, f, ensure_ascii=False)
    return pags


def tokens_titulo(titulo: str) -> list:
    palabras = [w for w in norm(titulo).split() if len(w) > 4]
    toks = [w for w in palabras if w not in GENERICAS]
    if len(toks) < 2:
        toks = palabras
    return toks[:5]


def recortar_bocce(pags: list, titulo: str):
    """(texto, info) — la norma dentro del boletín según la receta; ('', motivo) si falla."""
    toks = tokens_titulo(titulo)
    if not toks:
        return "", "sin-tokens"
    npags = [norm(p) for p in pags]
    for umbral in (max(1, len(toks) - 1), max(1, len(toks) - 2)):
        cands = [i for i, t in enumerate(npags) if sum(1 for tk in toks if tk in t) >= umbral]
        for i in cands:
            if len(ART.findall("\n".join(pags[i:i + 3]))) < 2:
                continue                                  # página de SUMARIO u otra mención suelta
            # fin: cuando el articulado vuelve a empezar (Artículo 1 de la norma siguiente)
            fin, maxart = min(len(pags), i + MAX_PAG_VENTANA), 0
            for j in range(i, fin):
                nums = [int(x) for x in ART.findall(pags[j]) if x.isdigit()]
                if j > i and maxart >= 3 and nums and nums[0] == 1 and (len(nums) == 1 or nums[1] <= 3):
                    fin = j
                    break
                if nums:
                    maxart = max(maxart, max(nums))
            cuerpo = pags[i:fin]
            # cabeza de la primera página: saltar la cola de la norma anterior
            lineas = cuerpo[0].split("\n")
            for k, ln in enumerate(lineas):
                if sum(1 for tk in toks if tk in norm(ln)) >= umbral:
                    cuerpo[0] = "\n".join(lineas[k:])
                    break
            texto = OE._reparar_parrafos_pdf("\n".join(cuerpo))
            if len(texto) > 800:
                return texto, f"pag {i+1}-{fin}/{len(pags)} arts={len(ART_ANY.findall(texto))}"
        if len(toks) < 3:
            break
    return "", f"sin-candidata (toks={toks})"


def texto_bocce(norma: dict, datos: bytes, max_ocr_boletin: int):
    if datos[:5] != b"%PDF-":
        return "", "no-pdf"
    pags = paginas_pdf(datos)
    via = "directo"
    if sum(len(p) for p in pags) / max(1, len(pags)) < 250:      # escaneado
        clave = hashlib.sha1(norma["url"].encode()).hexdigest()[:16]
        pags = ocr_boletin(datos, clave, max_ocr_boletin)
        via = f"ocr({len(pags)}p)"
    texto, info = recortar_bocce(pags, norma["titulo"])
    if not texto:
        # respaldo: la heurística genérica del motor sobre el texto completo (solo si corta)
        completo = OE._reparar_parrafos_pdf("\n".join(pags))
        rec = OE.AdaptadorWeb._recortar_por_titulo(completo, norma["titulo"])
        if len(rec) < len(completo) * 0.8 and len(rec) > 800:
            return rec, f"{via}/recorte-generico"
        return "", f"{via}/{info}"
    return texto, f"{via}/{info}"


def main():
    args = sys.argv[1:]
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 2
    max_ocr_boletin = int(args[args.index("--max-ocr-boletin") + 1]) if "--max-ocr-boletin" in args else 140
    solo_faltan = "--solo-faltan" in args
    ad = OE.ADAPTADORES[CODIGO]            # exige meta.aliases (ejecutar antes _gen_catalogo_ceuta.py)
    fp_cat = os.path.join(OE.DATA_DIR, CODIGO + ".json")
    cat = json.load(open(fp_cat, encoding="utf-8"))
    outdir = os.path.join(OE.DATA_DIR, CODIGO + "_textos")
    os.makedirs(outdir, exist_ok=True)
    resumen = {"ok": 0, "fail": 0, "skip": 0, "via": {}}

    def uno(n):
        fp = os.path.join(outdir, n["id"] + ".txt.gz")
        if solo_faltan and os.path.exists(fp) and os.path.getsize(fp) > 300:
            return n["id"], "skip", 0, ""
        try:
            datos, via_b = FT.bajar(n["url"])
            if n.get("formato") == "bocce":
                t, via_t = texto_bocce(n, datos, max_ocr_boletin)
            else:
                t, via_t = FT.texto_de(ad, n, datos, 30)
        except Exception as e:  # noqa: BLE001
            return n["id"], "fail", 0, str(e)[:100]
        if len(t) < 300:
            return n["id"], "fail", len(t), via_t or "texto vacío"
        with gzip.open(fp, "wt", encoding="utf-8") as f:
            f.write(t)
        return n["id"], f"{via_b}/{via_t}", len(t), ""

    t0 = time.time()
    # los 'bocce' de un mismo boletín comparten descarga y OCR: agrupar por URL en el mismo hilo
    normas = sorted(cat["normas"], key=lambda n: (n.get("formato") != "bocce", n["url"]))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for nid, via, n, err in ex.map(uno, normas):
            if via == "fail":
                resumen["fail"] += 1
                print(f"[FAIL] {nid:12s} {err}", flush=True)
            elif via == "skip":
                resumen["skip"] += 1
            else:
                resumen["ok"] += 1
                k = via.split("/")[1].split("(")[0] if "/" in via else via
                resumen["via"][k] = resumen["via"].get(k, 0) + 1
                print(f"[ok  ] {nid:12s} {n:7d} chars  {via}", flush=True)
    for n in cat["normas"]:
        fp = os.path.join(outdir, n["id"] + ".txt.gz")
        if os.path.exists(fp) and os.path.getsize(fp) > 300:
            n["texto"] = n["id"] + ".txt.gz"
    cat["meta"]["textos_dir"] = CODIGO + "_textos"
    cat["meta"]["textos_fecha"] = dt.date.today().isoformat()
    with open(fp_cat, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    tam = sum(os.path.getsize(os.path.join(outdir, x)) for x in os.listdir(outdir))
    print("=" * 70)
    print(f"{CODIGO}: {resumen} · {tam/1e6:.1f} MB · {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
