# -*- coding: utf-8 -*-
"""Backend del BOP de BURGOS (bopbur, Drupal 6/7) para bop_engine — familia «burgos».

Contrato: buscar(prov, texto, filtro, rpp) -> [{url, titulo, cve, fecha, orden, …}] y
texto(prov, m) -> (texto_plano, via). Lo despacha bop_engine._buscar_raw/_texto por importlib.

Cómo funciona (receta verificada 27-jul-2026 y 2-sep-2026):
  * El buscador /busqueda SOLO indexa el año en curso (un título literal de 2015 devuelve
    filas de 2026) y tarda ~18 s: NO se usa. La vía principal es un ÍNDICE EMPAQUETADO
    (ordenanzas_data/burgos_indice.json, lo genera _gen_indice_burgos.py recorriendo la
    página de cada boletín /bopbur-<año>-<NNN> desde 2015: 0,3-0,7 s, renderizada en
    servidor, con <h3>entidad</h3> cuyo id de categoría ES el tid del mapa). Búsqueda = 0 red.
  * Lo publicado DESPUÉS del último boletín del índice se completa en vivo sondeando los
    números siguientes (caché en memoria 10 min; un 404 cierra la sonda).
  * El texto de un anuncio es su PDF individual (…/bopbur-AAAA-NNN-anuncio-<id>.pdf), con
    capa de texto real (fitz), sin OCR.
  * TLS LEGACY: el Apache solo negocia TLSv1 + DHE-RSA-AES256-SHA; hace falta
    minimum_version=TLSv1 y SECLEVEL=0 (sin eso, UNSUPPORTED_PROTOCOL).
  * El motor decide por contenido (config `verifica_texto`): aquí se marca `materia` cuando
    el título ya lleva la materia (camino rápido de _mejor_verificado).
  * CVE propio: BOP-BU-<año>-<5 cifras> = el CVE oficial BOPBUR-<año>-<5 cifras>.
"""
import concurrent.futures as _cf
import html as _html
import http.client
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request

import bop_engine as B
from bop_ciudadreal import es_normativo

_HERE = os.path.dirname(os.path.abspath(__file__))
_IDX_FP = os.path.join(_HERE, "ordenanzas_data", "burgos_indice.json")
BASE = "https://bopbur.diputaciondeburgos.es"
_CVE = re.compile(r"(?i)\bBOP-?BU(?:R)?-(\d{4})-(\d{1,6})\b")


def _ctx():
    c = ssl._create_unverified_context()
    c.minimum_version = ssl.TLSVersion.TLSv1
    c.set_ciphers("DEFAULT:@SECLEVEL=0")
    try:
        c.options |= ssl.OP_LEGACY_SERVER_CONNECT
    except Exception:  # noqa: BLE001
        pass
    return c


CTX = _ctx()


def _get(url, timeout=25):
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": B._UA}),
                               timeout=timeout, context=CTX)
    try:
        return r.read()
    except http.client.IncompleteRead as e:
        return e.partial


# ---- página de un boletín /bopbur-AAAA-NNN ----------------------------------------------
_TOK = re.compile(r'<li id="bopbur-categoria-(\d+)" class="bopbur-categoria bopbur-categoria-level-(\d)">\s*'
                  r'<h\d>(.*?)</h\d>|<li id="bopbur-anuncio-(\d+)"[^>]*>(.*?)</li>', re.S)
_P = re.compile(r"<p>(.*?)</p>", re.S)
_PDF = re.compile(r'href="([^"]+\.pdf)"[^>]*type="application/pdf; length=(\d+)"')
_FECHA = re.compile(r'<span class="title-date">(.*?)</span>', re.S)
_MESES = {"enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05", "junio": "06",
          "julio": "07", "agosto": "08", "septiembre": "09", "octubre": "10", "noviembre": "11",
          "diciembre": "12"}
_TIDS = None


def _tids():
    """tid -> «Ayuntamiento de X» (del mapa empaquetado)."""
    global _TIDS
    if _TIDS is None:
        try:
            m = json.load(open(os.path.join(_HERE, "ordenanzas_data", "bop_burgos_municipios.json"),
                               encoding="utf-8"))
        except Exception:  # noqa: BLE001
            m = {}
        _TIDS = {v: k for k, v in m.items()}
    return _TIDS


def _t(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_boletin(html):
    """-> (fecha aaaammdd | "", [ {tid, ent, t, i, kb} ] de los AYUNTAMIENTOS del mapa).
    El id de la categoría de nivel 1 es el tid del ayuntamiento (único en la taxonomía),
    así que no hace falta mirar la sección: en 2015 el nivel 0 no siempre va marcado."""
    tids = _tids()
    fecha = ""
    mf = _FECHA.search(html)
    if mf:
        mm = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", _t(mf.group(1)).lower())
        if mm and mm.group(2) in _MESES:
            fecha = f"{mm.group(3)}{_MESES[mm.group(2)]}{int(mm.group(1)):02d}"
    out = []
    cur1 = ("", "")
    for m in _TOK.finditer(html):
        if m.group(1):
            nivel = m.group(2)
            if nivel == "0":
                cur1 = ("", "")
            elif nivel == "1":
                cur1 = (m.group(1), _t(m.group(3)))
            continue
        if cur1[0] not in tids:
            continue
        body = m.group(5)
        mp = _P.search(body)
        pdf = _PDF.search(body)
        if not mp or not pdf:
            continue
        out.append({"tid": cur1[0], "ent": cur1[1], "t": _t(mp.group(1))[:240], "i": m.group(4),
                    "kb": int(pdf.group(2)) // 1024})
    return fecha, out


# ---- índice empaquetado ----------------------------------------------------------------
_IDX = {}          # tid -> [filas]; "_meta" -> meta; "_por_cve" -> {cve: fila}
_LOCK = threading.Lock()


def _fila(tid, t, i, b, f, kb=0):
    f = f or ""
    orden = f if len(f) == 8 else f"{b[:4]}{int(b[5:]):04d}"
    return {"url": f"{BASE}/sites/default/files/private/publicado/bopbur-{b}/bopbur-{b}-anuncio-{i}.pdf",
            "titulo": t, "cve": f"BOP-BU-{i[:4]}-{i[4:]}", "fecha": f"{f[6:8]}/{f[4:6]}/{f[:4]}" if len(f) == 8 else "",
            "orden": orden, "id": str(i), "boletin": b, "tid": str(tid), "kb": kb}


def _indice():
    if _IDX:
        return _IDX
    with _LOCK:
        if _IDX:
            return _IDX
        try:
            d = json.load(open(_IDX_FP, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            d = {"meta": {}, "anuncios": []}
        por, por_cve = {}, {}
        for a in d.get("anuncios", []):
            r = _fila(a["tid"], a["t"], a["i"], a["b"], a.get("f"), a.get("kb", 0))
            por.setdefault(str(a["tid"]), []).append(r)
            por_cve[r["cve"]] = r
        por["_meta"] = d.get("meta", {})
        por["_por_cve"] = por_cve
        _IDX.update(por)
    return _IDX


# ---- boletines posteriores al índice (sonda en vivo) --------------------------------------
_VIVOS = {}        # slug "2026-168" -> (ts, filas normativas | None si no existe)
_VLOCK = threading.Lock()
_MAX_SONDA = 12


def _boletin_vivo(slug):
    c = _VIVOS.get(slug)
    if c and (c[1] is not None or time.time() - c[0] < 600):
        return c[1]
    try:
        html = _get(f"{BASE}/bopbur-{slug}", timeout=15).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _VIVOS[slug] = (time.time(), None)
        return None
    except Exception:  # noqa: BLE001
        return None                    # no se cachea el fallo de red
    fecha, anuncios = parse_boletin(html)
    filas = [_fila(a["tid"], a["t"], a["i"], slug, fecha, a["kb"]) for a in anuncios if es_normativo(a["t"])]
    _VIVOS[slug] = (time.time(), filas)
    return filas


def _recientes():
    """Filas normativas de los boletines publicados después del último del índice."""
    meta = _indice().get("_meta", {})
    ultimo = meta.get("ultimo") or ""
    if not re.match(r"^\d{4}-\d{3}$", ultimo):
        return []
    hoy = time.localtime().tm_year
    out = []
    with _VLOCK:                       # una sola sonda aunque el motor lance 4 consultas a la vez
        anio, n = int(ultimo[:4]), int(ultimo[5:])
        pedidos = 0
        while pedidos < _MAX_SONDA:
            # lotes de 4 en paralelo (≤4 conexiones): un índice envejecido dos semanas cuesta
            # ~1 s en vez de 6; el primer 404 del lote cierra la sonda
            slugs = [f"{anio}-{n + i:03d}" for i in range(1, 5)]
            pedidos += len(slugs)
            with _cf.ThreadPoolExecutor(max_workers=4) as ex:
                res = list(ex.map(_boletin_vivo, slugs))
            fin = False
            for filas in res:
                if filas is None:
                    fin = True
                    break
                out.extend(filas)
            if fin:
                if anio < hoy:         # cambio de año: sigue por el 001 del siguiente
                    anio, n = anio + 1, 0
                    continue
                break
            n += 4
    return out


# ---- contrato ---------------------------------------------------------------------------
def buscar(prov, texto, filtro, rpp=40):
    """filtro = tid del ayuntamiento («226» = Burgos). Devuelve TODOS los anuncios normativos
    del municipio (índice + boletines posteriores); el motor ranquea y verifica."""
    idx = _indice()
    m = _CVE.search(texto or "")
    if m:
        cve = f"BOP-BU-{m.group(1)}-{int(m.group(2)):05d}"
        r = idx["_por_cve"].get(cve)
        if r:
            return [dict(r)]
        return [dict(r) for r in _recientes() if r["cve"] == cve]
    if not filtro:
        return []
    tid = str(filtro)
    out, vistos = [], set()
    for r in list(idx.get(tid) or []) + [r for r in _recientes() if r["tid"] == tid]:
        if r["cve"] in vistos:
            continue
        vistos.add(r["cve"])
        out.append(dict(r))
    raw, core, _soft = B._familias(texto or "")
    fam = {w for w in (set(raw) | core) if w not in B._GENERICO}
    for r in out:
        tm = B._mnorm(r["titulo"])
        r["materia"] = bool(fam) and any(B._hit(w, tm) for w in fam)
    out.sort(key=lambda r: r["orden"], reverse=True)
    return out


_PALABRAS = re.compile(r"\b(?:de|la|el|los|las|que|del|por|con|para)\b")
# cabecera/pie que el boletín repite en CADA página del PDF (van en líneas sueltas):
# «boletín oficial de la provincia» · «– 14 –» · «núm. 159» · «lunes, 24 de agosto de 2026» ·
# «e» · «bopbur.diputaciondeburgos.es» · «D.L.: BU - 1 - 1958» · «burgos» · «C.V.E.: …»
_RUIDO = re.compile(r"(?m)^[ \t]*(?:bolet[íi]n oficial de la provincia(?: de burgos)?|[–-]\s*\d+\s*[–-]|"
                    r"n[úu]m\.?\s*\d+|(?:lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo),?\s+\d{1,2} de "
                    r"\w+ de \d{4}|e|bopbur\.diputaciondeburgos\.es|D\.\s*L\.:?\s*BU[^\n]*|burgos|"
                    r"diputaci[óo]n de burgos|bopbur-\d{4}-\d+[^\n]*|C\.?V\.?E\.?:?\s*[^\n]*|p[áa]g\.\s*\d+)[ \t]*\n",
                    re.I)


def _limpiar(t):
    t = _RUIDO.sub("", t)
    t = re.sub(r"(\w) ?-\n(?=[a-záéíóúñü])", r"\1", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def texto(prov, m):
    """(texto, via) del anuncio: su PDF individual con capa de texto (sin OCR), con caché en disco."""
    url = (m.get("url") if isinstance(m, dict) else m) or ""
    if not url.endswith(".pdf"):
        return "", "sin-pdf"
    ident = (m.get("id") if isinstance(m, dict) else "") or re.sub(r"\D", "", url[-14:])
    clave = f"burgos-{ident}"
    t = B._txt_cache_get(clave)
    if t:
        return t, "pdf-cache"
    try:
        data = _get(url, timeout=25)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{str(e)[:60]}"
    t, via = B._pdf_bytes_texto(data, ocr=False)
    if via == "sin-pdf":
        return "", "sin-pdf"
    t = _limpiar(t or "")
    if via == "cifrado" and len(_PALABRAS.findall(t[:20000])) < 8:
        return "", "sin-texto"
    if len(t) < 120:
        return "", "sin-texto"
    B._txt_cache_set(clave, t)
    return t, "pdf"
