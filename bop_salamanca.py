# -*- coding: utf-8 -*-
"""Backend BOP de SALAMANCA (familia `salamanca`) para el motor de ordenanzas.

Sede de la Diputación (OpenCms + app BOP en JSP). Receta verificada en vivo
(27-jul y 2-sep-2026, sondas _sa_lib.py / _probe_sa*.py):

  * POST /opencms/opencms/sede/BOP/index.jsp, form-urlencoded, sin captcha, sin
    cookies, sin token. Respuesta UTF-8. El header Referer DEBE ser .../sede/BOP/
    (con Referer a index.jsp devuelve el formulario vacío).
  * No hay facet de municipio: el listado es PLANO y la jerarquía va en el
    margin-left de cada div (10=sección, 20=grupo, 30=municipio, 40=anuncio). En
    el grupo "Ayuntamiento de Salamanca" (la capital) el nivel 30 es un ÁREA
    municipal (OAGER, Policía Local...), no un municipio.
  * La búsqueda de `texto` es por SUBCADENA ("terraza" ⊇ "terrazas") y el
    multitérmino es frase exacta. NUNCA fDesde/fHasta vacíos: con texto vacío
    devuelve el histórico entero (35 MB). El parámetro CVE se ignora.
  * Coste medido el 2-sep-2026: 4-6 s POR PETICIÓN, fijo, sea cual sea la ventana
    o el término (el 27-jul eran 0,5-1,6 s). Con eso el flujo buscar→leer en vivo
    no baja de ~6 s... y el volcado COMPLETO del histórico (texto vacío,
    2012→hoy) es UNA petición de ~10 s. Por eso el índice va EMPAQUETADO
    (ordenanzas_data/salamanca_indice.json, lo genera _gen_bop_salamanca_indice.py
    con esa única petición): la búsqueda no toca la red y solo se lee el PDF del
    anuncio elegido. La consulta viva queda de RESPALDO: un CVE que no esté en el
    índice o una materia sin ningún título que la nombre.
  * PDF por anuncio (/documentacion/bop/AAAA/AAAAMMDD/BOP-SA-AAAAMMDD-NNN.pdf) con
    capa de texto: fitz directo, 0,2-0,5 s. Los de 2020 y anteriores son un rango
    de páginas del boletín y pueden arrancar con la cola del anuncio anterior.
"""
import concurrent.futures as _cf
import html as _html
import json
import os
import re
import ssl
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

import bop_engine as B

_RUTA_BOP = "/opencms/opencms/sede/BOP/"
_INDICE = "salamanca_indice.json"
_DIV = re.compile(r'<div style="margin-left:(\d+)px[^"]*">(.*?)</div>', re.S)
_DIA = re.compile(r"Anuncios del d[ií]a\s*(\d{2}/\d{2}/\d{4})")
_PDF = re.compile(r'href="([^"]*/documentacion/bop/[^"]+\.pdf)"')
_CVE = re.compile(r"\bBOP-SA-(\d{8})-(\d+)\b", re.I)
_MANCO = re.compile(r"(?i)^mancomunidad(?:es)?\b\s*(.*)$")
_ES_GENERICO = {"", "ordenanza", "ordenanzas", "reglamento", "reglamentos", "tasa", "tasas"}
# Anuncios que citan una ordenanza en el título pero NUNCA traen su articulado
# (la capital publica cientos de notificaciones de sanción "a la Ordenanza de
# ruidos"): fuera del índice y de los resultados vivos.
RUIDO = re.compile(r"infracci[oó]n|sancionador|notificaci[oó]n|presunta|denuncia|padr[oó]n|lista cobratoria|"
                   r"matr[ií]cula|solicitud de licencia|licencia ambiental|liquidaci[oó]n|cobranza|"
                   r"per[ií]odo voluntario|per[ií]odo de pago|recibos|cuenta general|correcci[oó]n de errores|"
                   r"expropiaci|licitaci[oó]n|adjudicaci[oó]n|contrataci[oó]n|subasta|resoluci[oó]n de alcald[ií]a n", re.I)

_IDX = {}                  # prov -> índice cargado
_LOCK = threading.Lock()
_VIVO = {}                 # (prov, consulta) -> (filas, ts)   caché de consultas vivas
_TXT = {}                  # url -> (texto, ts)
_TTL = 600


def _t(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _fila(cfg, cve, entidad, titulo):
    """Fila del contrato del motor a partir del CVE (la URL y la fecha van dentro)."""
    m = _CVE.search(cve)
    f8, num = (m.group(1), m.group(2)) if m else ("00000000", "000")
    return {"url": f"{cfg['base']}/documentacion/bop/{f8[:4]}/{f8}/{cve}.pdf", "titulo": titulo,
            "cve": cve, "fecha": f"{f8[6:8]}/{f8[4:6]}/{f8[:4]}", "orden": f8 + num.zfill(3),
            "entidad": entidad}


# ---- parseo del listado (vivo y cosecha) ------------------------------------
def parse_listado(h):
    """[{cve, titulo, fecha, grupo, entidad, organo}] del HTML plano del BOP."""
    out, fecha, sec, grupo, muni, organo, capital, pos = [], "", "", "", "", "", False, 0
    for m in _DIV.finditer(h):
        d = _DIA.findall(h[pos:m.start()])
        if d:
            fecha = d[-1]
        pos = m.start()
        lvl, cont = int(m.group(1)), m.group(2)
        if lvl <= 10:
            sec, grupo, muni, organo, capital = _t(cont), "", "", "", False
        elif lvl == 20:
            grupo, muni, organo = _t(cont), "", ""
            capital = grupo.lower().startswith("ayuntamiento de ")
            if capital:
                muni = grupo[len("Ayuntamiento de "):]
        elif lvl == 30:
            # el JSP no repite sección/grupo en cada día: `muni` arrastra a propósito
            if capital:
                organo = _t(cont)
            else:
                muni, organo = _t(cont), ""
        elif lvl >= 40:
            p = _PDF.search(cont)
            if not p:
                continue
            cve = p.group(1).rsplit("/", 1)[-1][:-4]
            tit = _t(re.sub(r"<a\b.*?</a>", " ", cont, flags=re.S))
            out.append({"cve": cve, "titulo": tit, "fecha": fecha, "seccion": sec, "grupo": grupo,
                        "entidad": muni, "organo": organo})
    return out


def _abrir(req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout).read()
    except ssl.SSLError:
        return urllib.request.urlopen(req, timeout=timeout, context=B._SSL_NOVERIFY).read()
    except urllib.error.URLError as e:       # certificado del sector público no reconocido
        if "CERTIFICATE" in str(e).upper():
            return urllib.request.urlopen(req, timeout=timeout, context=B._SSL_NOVERIFY).read()
        raise


def consulta_viva(cfg, texto, desde, hasta, timeout=25):
    """Una búsqueda en vivo (texto por subcadena, ventana OBLIGATORIA)."""
    if not desde or not hasta:
        raise ValueError("BOP Salamanca: fDesde/fHasta no pueden ir vacíos")
    d = {"fechaBoletin": "", "fDesde": desde, "fHasta": hasta, "texto": texto, "etiquetas": "", "CVE": ""}
    req = urllib.request.Request(cfg["base"] + _RUTA_BOP + "index.jsp", data=urllib.parse.urlencode(d).encode(),
                                 headers={"User-Agent": B._UA, "Content-Type": "application/x-www-form-urlencoded",
                                          "Referer": cfg["base"] + _RUTA_BOP})
    return parse_listado(_abrir(req, timeout).decode("utf-8", "replace"))


def _dia_vivo(cfg, f8, timeout=25):
    req = urllib.request.Request(cfg["base"] + _RUTA_BOP + f"index.jsp?fechaBoletin={f8[:4]}-{f8[4:6]}-{f8[6:8]}",
                                 headers={"User-Agent": B._UA, "Referer": cfg["base"] + _RUTA_BOP})
    return parse_listado(_abrir(req, timeout).decode("utf-8", "replace"))


# ---- índice empaquetado -----------------------------------------------------
def _indice(prov):
    if prov in _IDX:
        return _IDX[prov]
    with _LOCK:
        if prov in _IDX:
            return _IDX[prov]
        cfg = B.PROVINCIAS[prov]
        idx = {"meta": {}, "munis": {}, "cves": {}, "manc": []}
        try:
            with open(os.path.join(B._DATA, cfg.get("indice", _INDICE)), encoding="utf-8") as f:
                datos = json.load(f)
            idx["meta"] = datos.get("meta") or {}
            for cve, ent, tit, grp in datos.get("filas") or []:
                if grp == "M":
                    idx["manc"].append((cve, ent, tit))
                else:
                    idx["munis"].setdefault(B._norm(ent), []).append((cve, ent, tit))
                idx["cves"][cve.upper()] = (ent, tit)
        except Exception:  # noqa: BLE001
            pass
        _IDX[prov] = idx
        return idx


def _fam(texto):
    raw, core, _s = B._familias(texto or "")
    return {w for w in (set(raw) | core) if w not in B._GENERICO}


def _con_materia(fam, titulo):
    tm = B._mnorm(titulo)
    return bool(fam) and any(B._hit(w, tm) for w in fam)


_ALIAS_TESAURO = None


def _es_alias_tesauro(texto):
    """¿La consulta es uno de los alias multipalabra del tesauro del motor
    ("mesas y sillas", "higiene urbana")? El motor los lanza como consultas
    secundarias además de la materia: no merecen una búsqueda viva de 4-10 s."""
    global _ALIAS_TESAURO
    if _ALIAS_TESAURO is None:
        _ALIAS_TESAURO = {B._mnorm(a) for _p, cs, _s in getattr(B, "_EXPANSION", []) for a in cs if " " in a}
    return B._mnorm(texto) in _ALIAS_TESAURO


def _raiz(w):
    if " " in w or not w.endswith("s") or len(w) <= 4:
        return w
    if w.endswith("es") and len(w) > 5 and w[-3] in "rlndz" and w[-4] in "aeiou":
        return w[:-2]                       # veladores -> velador
    return w[:-1]                           # terrazas -> terraza


def _variantes_vivas(texto, fam):
    """Términos para la búsqueda viva por SUBCADENA: primero las palabras del
    abogado, después el tesauro (palabras sueltas antes que frases), todo en su
    raíz sin -s. Máximo 3 consultas."""
    raw, _c, _s = B._familias(texto or "")
    orden = [w for w in raw if w in fam] + sorted((w for w in fam if w not in raw),
                                                  key=lambda x: (" " in x, len(x)))
    out, vistos = [], set()
    for w in orden:
        stem = _raiz(w)
        if len(stem) < 4 or any(stem in v or v in stem for v in vistos):
            continue
        vistos.add(stem)
        out.append(stem)
    return out[:3]


def _vivo(prov, q):
    cfg = B.PROVINCIAS[prov]
    c = _VIVO.get((prov, q))
    if c and time.time() - c[1] < _TTL:
        return c[0]
    hoy = time.strftime("%d/%m/%Y")
    try:
        # 12 s: el servidor tarda 4-10 s; más que eso es un día malo y no compensa
        # (el conector corre en Vercel con 60 s por llamada)
        rows = consulta_viva(cfg, q, f"01/01/{cfg.get('indice_desde', 2012)}", hoy, timeout=12)
    except Exception:  # noqa: BLE001
        rows = []
    if len(_VIVO) > 32:
        _VIVO.clear()
    _VIVO[(prov, q)] = (rows, time.time())
    return rows


def buscar(prov, texto, filtro, rpp=40):
    """Anuncios normativos del municipio (filtro = nombre tal como lo escribe el
    BOP) con la marca `materia`. Índice empaquetado primero; vivo de respaldo."""
    cfg = B.PROVINCIAS[prov]
    texto = (texto or "").strip()
    idx = _indice(prov)
    mc = _CVE.search(texto)
    if mc:
        cve = mc.group(0).upper()
        if cve in idx["cves"]:
            ent, tit = idx["cves"][cve]
            return [dict(_fila(cfg, cve, ent, tit), materia=True)]
        try:
            for r in _dia_vivo(cfg, mc.group(1)):
                if r["cve"].upper() == cve:
                    return [dict(_fila(cfg, cve, r["entidad"], r["titulo"]), materia=True)]
        except Exception:  # noqa: BLE001
            pass
        return []
    fam = _fam(texto)
    if not filtro:
        m = _MANCO.match(texto)
        if not m:
            return []
        fam = _fam(m.group(1))
        return [dict(_fila(cfg, cve, ent, tit), materia=True) for cve, ent, tit in idx["manc"]
                if _con_materia(fam, tit)]
    fn = B._norm(filtro)
    filas = idx["munis"].get(fn, [])
    tn = B._norm(texto)
    if tn in _ES_GENERICO:
        if tn in ("reglamento", "reglamentos", "tasa", "tasas"):
            return []                    # el volcado de "ordenanza" ya trae todo el municipio
        return [dict(_fila(cfg, cve, ent, tit), materia=False) for cve, ent, tit in filas]
    out = [dict(_fila(cfg, cve, ent, tit), materia=_con_materia(fam, tit)) for cve, ent, tit in filas]
    if fam and not any(r["materia"] for r in out) and not _es_alias_tesauro(texto):
        # ningún título del índice nombra la materia: consulta viva (≤3 variantes en
        # paralelo, 4-6 s de reloj) por si es posterior al índice o frasea distinto
        qs = _variantes_vivas(texto, fam)
        ya = {r["cve"].upper() for r in out}
        with _cf.ThreadPoolExecutor(max_workers=max(1, len(qs))) as ex:
            for rows in ex.map(lambda q: _vivo(prov, q), qs):
                for r in rows:
                    if B._norm(r["entidad"]) != fn or r["cve"].upper() in ya or RUIDO.search(r["titulo"]):
                        continue
                    ya.add(r["cve"].upper())
                    out.append(dict(_fila(cfg, r["cve"], r["entidad"], r["titulo"]), materia=True))
    out.sort(key=lambda r: r["orden"], reverse=True)
    return out


# ---- lectura ---------------------------------------------------------------
def _limpia_sa(t):
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", t or "")
    t = re.sub(r"([ﬁﬂ])[ \t]+(?=[a-záéíóúüñ])", r"\1", t)
    t = unicodedata.normalize("NFKC", t).replace("­", "")
    t = re.sub(r"(?m)^[ \t]*(?:P[áa]g\. ?\d+|N\.?º ?\d+ ?• ?[^\n]*\d{4}|https?://sede\.diputaciondesalamanca\.gob\.es/BOP/?|"
               r"BOLET[ÍI]N OFICIAL DE LA PROVINCIA DE SALAMANCA|D\.L\.: S ?1-1958|CVE: ?BOP-SA-\d{8}-\d+)[ \t]*\n", "", t)
    return t


def texto(prov, m):
    """(texto, via) del anuncio: su PDF (capa de texto; sin OCR)."""
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    c = _TXT.get(u)
    if c and time.time() - c[1] < _TTL:
        return c[0], "pdf"
    try:
        pdf = _abrir(urllib.request.Request(u, headers={"User-Agent": B._UA}), 25)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = B._pdf_bytes_texto(pdf, ocr=False)
    if via != "directo" or len(t) < 200:
        return "", "sin-texto"
    t = _limpia_sa(t)
    if len(_TXT) > 64:
        _TXT.clear()
    _TXT[u] = (t, time.time())
    return t, "pdf"
