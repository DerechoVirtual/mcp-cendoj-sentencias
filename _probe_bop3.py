# -*- coding: utf-8 -*-
"""Pipeline COMPLETO BOP Sevilla (offline _*): buscar por municipio+materia
(Solr, sin reCAPTCHA) -> PDF -> texto (directo o OCR Gemini) -> articulo pedido."""
import io
import json
import os
import re
import sys
import time
import base64
import html as H
import urllib.parse
import urllib.request
import http.cookiejar
import unicodedata
import fitz

BASE = "https://bopsevilla.dipusevilla.es"
RES = BASE + "/publica/buscador-anuncios/resultados-anuncios/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAPA = json.load(open(os.path.join(os.path.dirname(__file__), "ordenanzas_data", "bop_sevilla_municipios.json"), encoding="utf-8"))
env = open(os.path.expanduser("~/.claude/.env"), encoding="utf-8", errors="replace").read()
GKEY = re.search(r"^GEMINI_API_KEY=(.+)$", env, re.M).group(1).strip().strip('"')

_op = None; _P = None


def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


_IDX = {_norm(k): v for k, v in MAPA.items()}


def _sesion():
    global _op, _P
    cj = http.cookiejar.CookieJar()
    _op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    _op.addheaders = [("User-Agent", UA["User-Agent"]), ("Accept-Language", "es")]
    page = _op.open(RES, timeout=30).read().decode("utf-8", "replace")
    j = page.find("urlAjax"); ini = page.rfind("{", 0, j); fin = page.find("};", ini)
    _P = {}
    for m in re.finditer(r"(\w+)\s*:\s*(?:'([^']*)'|\"([^\"]*)\"|([\w.\-]+))", page[ini + 1:fin]):
        _P[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)


def _getb(u, t=40):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read()


def resolver_muni(municipio):
    return _IDX.get(_norm(municipio))


def buscar(municipio, materia, rpp=40):
    if _op is None:
        _sesion()
    cat = resolver_muni(municipio)
    p = dict(_P); p["buscarTexto"] = materia; p["ResultadosPorPagina"] = str(rpp); p["paginaActual"] = "1"
    if cat:
        p["buscarCategoria"] = cat; p["CategoriasAListar"] = cat
    req = urllib.request.Request(BASE + p["urlAjax"], data=urllib.parse.urlencode(p).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest", "Referer": RES})
    r = _op.open(req, timeout=25).read().decode("utf-8", "replace")
    if "opencms.org" in r:
        _sesion(); return buscar(municipio, materia, rpp)
    out = []
    for m in re.finditer(r'<a href="(/publica/buscador-anuncios/anuncio/[^"]+)"\s+title="([^"]+)"', r):
        tail = r[m.end():m.end() + 900]
        cve = re.search(r"BOP-SE-\d{4}-\d+", tail)
        fe = re.search(r"(\d{2})/(\d{2})/(\d{4})", tail)
        out.append({"url": BASE + m.group(1), "titulo": H.unescape(m.group(2)),
                    "cve": cve.group(0) if cve else "",
                    "fecha": fe.group(0) if fe else "", "orden": (fe.group(3) + fe.group(2) + fe.group(1)) if fe else "0"})
    return out


def _es_ordenanza(t):
    return bool(re.search(r"ordenanza|reglamento", t, re.I))


_STOPM = {"de", "la", "el", "los", "las", "del", "y", "en", "por", "para"}

# tesauro: cada término de materia expande a variantes que pueden estar en el
# TÍTULO de la ordenanza (los ayuntamientos nombran la misma materia distinto).
_EXPANSION = [
    (r"residuo|basura|limpieza|rsu", ["residuo", "basura", "limpieza", "rsu",
        "higiene urbana", "recogida", "punto limpio", "solidos urbanos", "viaria"]),
    (r"terraza|velador", ["terraza", "velador", "ocupacion", "mesas y sillas", "hosteleria"]),
    (r"ruido|acustic", ["ruido", "acustic", "vibracion", "contaminacion acustica"]),
    (r"animal|perro", ["animal", "perro", "tenencia", "mascota"]),
    (r"movilidad|trafico|circulacion", ["movilidad", "trafico", "circulacion", "vehiculo"]),
    (r"convivencia|civismo", ["convivencia", "civismo", "espacio publico"]),
    (r"cementerio|funerari", ["cementerio", "funerari", "tanatorio"]),
    (r"venta|mercad", ["venta", "mercad", "ambulante"]),
]


def _expandir(materia):
    toks = [_norm(w) for w in materia.split() if _norm(w) and _norm(w) not in _STOPM]
    fam = set(toks)
    for pat, al in _EXPANSION:
        if any(re.search(pat, w) for w in toks):
            fam.update(_norm(a) for a in al)
    return fam


def mejor(res, materia):
    """La mejor ordenanza: su TÍTULO machea la materia (con tesauro) + definitiva + reciente."""
    fam = _expandir(materia)
    cand = [r for r in res if _es_ordenanza(r["titulo"])]
    if not cand:
        return None
    def score(r):
        tn = _norm(r["titulo"])
        s = 3 * sum(1 for w in fam if w and w in tn)
        if re.search(r"definitiv", r["titulo"], re.I): s += 4
        if re.search(r"aprobaci[oó]n inicial", r["titulo"], re.I): s += 1
        if re.search(r"correcci[oó]n de errores|delegaci[oó]n|honores|condecorac|personal", r["titulo"], re.I): s -= 8
        s += int(r["orden"][:8] or 0) / 1e10
        return s
    cand.sort(key=score, reverse=True)
    return cand[0] if any(w and w in _norm(cand[0]["titulo"]) for w in fam) else None


import concurrent.futures as _cf
OKEY = re.search(r"^OPENAI_API_KEY=(.+)$", env, re.M).group(1).strip().strip('"')
_OCR_PROMPT = ("Actúa como un motor OCR de documentos oficiales públicos. Devuelve "
               "EXACTAMENTE el texto que aparece en la imagen, sin añadir ni omitir nada.")


def _ocr_openai(b64):
    body = json.dumps({"model": "gpt-4o-mini", "temperature": 0, "max_tokens": 2200,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}}]}]}).encode()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {OKEY}", "Content-Type": "application/json"}), timeout=60).read())
    t = r["choices"][0]["message"]["content"]
    if "no puedo" in t.lower() or "lo siento" in t.lower():
        raise RuntimeError("rechazo")
    return t


def _ocr_gemini(b64):
    body = json.dumps({"model": "gemini-3.5-flash", "reasoning_effort": "none", "temperature": 0,
        "max_tokens": 2500, "messages": [{"role": "user", "content": [
            {"type": "text", "text": _OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}).encode()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", data=body,
        headers={"Authorization": f"Bearer {GKEY}", "Content-Type": "application/json"}), timeout=90).read())
    return r["choices"][0]["message"]["content"]


def _ocr_pagina(png_bytes):
    b64 = base64.b64encode(png_bytes).decode()
    try:
        return _ocr_openai(b64)
    except Exception:
        return _ocr_gemini(b64)


_PALS = re.compile(r"\b(de|la|el|los|art[íi]culo|ordenanza|ayuntamiento|que|para|del|por)\b", re.I)


def texto_anuncio(url, ocr=True, max_pag=8):
    det = _getb(url).decode("utf-8", "replace")
    m = re.search(r'href="([^"]+Documentos-Anuncios-en-PDF[^"]+\.pdf)"', det)
    if not m:
        return "", "sin-pdf"
    doc = fitz.open(stream=_getb(BASE + m.group(1)), filetype="pdf")
    directo = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    hits = len(_PALS.findall(directo)); ratio = len(directo) / max(1, doc.page_count)
    if hits >= 15 and ratio > 250:
        return directo, "directo"
    if not ocr:
        return directo, "cifrado-sin-ocr"
    # OCR EN PARALELO de las paginas (wall-clock = pagina mas lenta, no la suma)
    n = min(doc.page_count, max_pag)
    pngs = [doc[i].get_pixmap(dpi=150).tobytes("png") for i in range(n)]
    with _cf.ThreadPoolExecutor(max_workers=min(8, n)) as ex:
        pags = list(ex.map(_ocr_pagina, pngs))
    return "\n".join(pags), f"ocr({n}p)"


def _limpia(t):
    t = re.sub(r"P[áa]gina \d+ de(?: un total de)? \d+|N[ºo] \d+ - [\w ]+de \d+|CVE:? ?BOP-SE-[\d-]+|"
              r"Documento firmado[^\n]*|C[óo]d\.? ?Validaci[óo]n[^\n]*|Bolet[íi]n Oficial[^\n]*|"
              r"de la provincia de Sevilla|Plaza de Espa[^\n]*|HASH:[^\n]*|Fecha Firma:[^\n]*", " ", t)
    return re.sub(r"[ \t\xa0]+", " ", t)


def articulo_con(texto, termino):
    """Los articulos relevantes al termino, priorizando los que lo llevan en la
    RÚBRICA (encabezado) sobre los que solo lo mencionan de pasada."""
    t = _limpia(texto)
    marcas = [(m.start(), m.group(1)) for m in re.finditer(
        r"(?im)(?:^|\n)\s*(art[íi]culo\s+\d+[\wº.\- ]{0,70})", t)]
    pal = _norm(termino)
    en_rubrica, en_cuerpo = [], []
    for i, (pos, cab) in enumerate(marcas):
        fin = marcas[i + 1][0] if i + 1 < len(marcas) else len(t)
        cuerpo = t[pos:fin].strip()
        # ¿el término está en la rúbrica (primeros ~120 chars) o solo en el cuerpo?
        if pal in _norm(cuerpo[:120]):
            en_rubrica.append(cuerpo[:1200])
        elif pal in _norm(cuerpo):
            en_cuerpo.append(cuerpo[:1200])
    return en_rubrica + en_cuerpo


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    _sesion()
    casos = [("Lora del Río", "limpieza residuos", "sancion"),
             ("Umbrete", "limpieza residuos", "sancion")]
    for muni, materia, term in casos:
        t0 = time.time()
        res = buscar(muni, materia)
        m = mejor(res, materia)
        print(f"\n### {muni} / basuras -> regimen sancionador  [busqueda {time.time()-t0:.2f}s]")
        print("   candidatos ordenanza:", [r['titulo'][:58] for r in res if _es_ordenanza(r['titulo'])][:5])
        if not m:
            print("   sin ordenanza de la materia"); continue
        print(f"   ORDENANZA: {m['titulo'][:75]}  ({m['fecha']} {m['cve']})")
        texto, via = texto_anuncio(m["url"])
        arts = articulo_con(texto, "sancion") or articulo_con(texto, "infrac")
        print(f"   via={via} | {time.time()-t0:.1f}s TOTAL | articulos sancionador: {len(arts)}")
        for a in arts[:1]:
            print("   >>", re.sub(r"\s+", " ", a)[:420])
