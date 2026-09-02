# -*- coding: utf-8 -*-
"""
Backend LUGO para el motor de ordenanzas (familia «lugo»): BOP de Lugo publicado
por la Deputación en su Drupal (www.deputacionlugo.gal), en gallego.

Receta verificada en vivo (sondas _probe_lugo*.py, 27-jul-2026; implementación
2-sep-2026):
  * El buscador «BOP por contido» es un filtro Views sobre el SUMARIO diario:
    AND de palabras por subcadena (allwords), sensible a acentos, insensible a
    mayúsculas, 10 boletines por página ordenados por fecha desc, ~2,5 s por
    página (es el cuello de botella). Devuelve BOLETINES, no anuncios: el
    anuncio se identifica en local. Por eso se cruza la materia con el nombre
    del concello en UNA consulta («terraza MONFORTE DE LEMOS») y las páginas 0
    y 1 se piden EN PARALELO en vez de en serie.
  * Cada resultado es un nodo /gl/node/<id> (0,1-0,7 s) cuyo sumario lleva la
    jerarquía <strong>CONCELLOS</strong> → <strong>NOME</strong> → <a href="…pdf
    #page=N">TÍTULO (PÁX. N, R. NNNN)</a>. Tres formas históricas: cabecera
    <strong> (2016→hoy), cabecera pelada <li>NOME<ul> (2009-2015) y ancla
    #PAGE= en mayúsculas o ausente en los viejos.
  * El PDF es el del DÍA entero (80-130 KB, ~0,1 s, con capa de texto). Dentro,
    cada anuncio TERMINA con su número de rexistro «R. NNNN» en línea propia: el
    anuncio exacto va entre la marca anterior y la suya. TRAMPAS: la marca puede
    caer lejos de la página del ancla, en boletines viejos no es correlativa y a
    veces está DUPLICADA por error (el 08-08-2026 dos anuncios llevan «R. 2129»)
    → se barre el PDF entero y, si hay varias, se elige la que empieza con la
    cabecera del concello y cae en la página del ancla.
  * Los anuncios NO normativos (padróns, cobranzas, bolsas de emprego, listas,
    nomeamentos…) se descartan aquí: son la mayoría del boletín y entierran la
    ordenanza cuando el buscador ordena por fecha.
  * Sumarios y textos cacheados en memoria y en disco (son inmutables); un
    semáforo global limita a 4 las peticiones simultáneas al boletín.
  * Búsqueda por número: «1046 RÁBADE» localiza el boletín → CVE BOP-LU-AAAA-N.
"""
import bisect
import concurrent.futures as _cf
import gzip
import hashlib
import html as _html
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import bop_engine as B

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_SEM = threading.BoundedSemaphore(4)          # cortesía con el boletín
_TANDA = 4                                    # sumarios por tanda (parada temprana)
_MAX_BOLETINES = 40                           # tope de sumarios por llamada (4 búsquedas × 10)
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "bop-lugo")

_DEBUG = bool(os.environ.get("BOP_LUGO_DEBUG"))


def _dbg(msg):
    if _DEBUG:
        import sys
        sys.stderr.write(f"[bop_lugo {time.strftime('%H:%M:%S')}] {msg}\n")


_LOCK = threading.Lock()
_SUM = {}          # nid -> items (sumario parseado)
_INFLIGHT = {}     # nid -> Event (una sola descarga por nodo aunque lo pidan varios hilos)
_BUSQ = {}         # (q, page) -> (filas, ts)
_PDFS = {}         # url -> (paginas, ts)


# ---- HTTP -----------------------------------------------------------------------
def _get(url, timeout=25, binario=False):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Encoding": "gzip",
                                               "Accept-Language": "gl,es;q=0.9"})
    ultimo = None
    for intento in range(2):
        try:
            t0 = time.time()
            with _SEM:
                t1 = time.time()
                with urllib.request.urlopen(req, timeout=timeout, context=B._SSL_NOVERIFY) as r:
                    b = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        b = gzip.decompress(b)
            _dbg(f"GET {url[-70:]} {len(b) // 1024} KB espera {t1 - t0:.1f}s red {time.time() - t1:.1f}s")
            return b if binario else b.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if not intento:
                time.sleep(0.8)
    raise RuntimeError(f"BOP Lugo: {ultimo}")


def _disco_get(nombre):
    try:
        with open(os.path.join(_CACHE_DIR, nombre), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _disco_set(nombre, obj):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = os.path.join(_CACHE_DIR, nombre + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(_CACHE_DIR, nombre))
    except Exception:  # noqa: BLE001
        pass


# ---- normalización ----------------------------------------------------------------
_ACENTOS = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")


def _sin_acentos(s):
    """Solo tildes (la ñ se conserva: «MONDOÑEDO» nunca se escribe «MONDONEDO»)."""
    return (s or "").translate(_ACENTOS)


def _sin_art(s):
    return re.sub(r"^(?:a|o|as|os)\s+", "", (s or "").strip(), flags=re.I)


def _clave_concello(s):
    s = re.sub(r"(?i)^concello\s+(?:de\s+|d')?", "", (s or "").strip())
    return B._norm(_sin_art(s))


def _coincide(cab, muni):
    return B._norm(cab) == B._norm(muni) or _clave_concello(cab) == _clave_concello(muni)


# ---- listado del buscador ----------------------------------------------------------
_ROW = re.compile(r"(?s)<tr[^>]*>(.*?)</tr>")
_NODO = re.compile(r'href="(/[a-z]{2}/node/(\d+))"')
_TIME = re.compile(r'<time datetime="(\d{4})-(\d{2})-(\d{2})')
_PDFL = re.compile(r'href="([^"]+\.pdf)"')


_BUSQ_INFLIGHT = {}   # key -> Event (dos hilos no piden la misma página a la vez)


def _buscar_pagina(cfg, q, page=0):
    """[{href, nid, fecha, orden, pdf}] de la página `page` del buscador para `q`.
    Cacheada 10 min y con vuelo único: el listado del motor pide «ordenanza
    CONCELLO» por dos caminos a la vez y solo debe costar una petición."""
    key = (q.lower(), page)
    c = _BUSQ.get(key)
    if c and time.time() - c[1] < 600:
        return c[0]
    with _LOCK:
        ev = _BUSQ_INFLIGHT.get(key)
        mio = ev is None
        if mio:
            ev = _BUSQ_INFLIGHT[key] = threading.Event()
    if not mio:
        ev.wait(40)
        c = _BUSQ.get(key)
        if c:
            return c[0]
        raise RuntimeError("BOP Lugo: búsqueda fallida")
    try:
        return _buscar_pagina_red(cfg, q, page, key)
    finally:
        with _LOCK:
            _BUSQ_INFLIGHT.pop(key, None)
        ev.set()


def _buscar_pagina_red(cfg, q, page, key):
    ep = (cfg.get("endpoints") or {}).get(
        "buscar", "/gl/boletin-oficial-da-provincia-de-lugo/bop-por-contenido"
                  "?field_ail_bop_contenido_value={q}&page={page}")
    url = cfg["base"] + ep.replace("{q}", urllib.parse.quote(q)).replace("{page}", str(page))
    h = _get(url)
    filas = []
    for tr in _ROW.findall(h):
        mn = _NODO.search(tr)
        if not mn:
            continue
        mt = _TIME.search(tr)
        mp = _PDFL.search(tr)
        filas.append({"href": mn.group(1), "nid": mn.group(2),
                      "fecha": f"{mt.group(3)}/{mt.group(2)}/{mt.group(1)}" if mt else "",
                      "orden": "".join(mt.groups()) if mt else "0",
                      "pdf": urllib.parse.urljoin(cfg["base"], mp.group(1)) if mp else ""})
    _BUSQ[key] = (filas, time.time())
    return filas


# ---- sumario del boletín diario -----------------------------------------------------
_TOK = re.compile(
    r"<strong>(.*?)</strong>"
    r"|<li>\s*([^<>\n]{2,90}?)\s*<ul>"
    r'|<a\b[^>]*href="([^"#]*?\.pdf)(?:#[Pp][Aa][Gg][Ee]=(\d+))?[^"]*"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")
# cabeceras de bloque que NO son un concello
_NO_MUNI = re.compile(
    r"^(CONCELLOS|DEPUTACI|EXCMA|XUNTA|ADMINISTRACI|MINISTERIO|XULGADO|TRIBUNAL|CONFEDERACI|"
    r"MANCOMUNIDADE|CONSORCIO|SERVIZO|SECCI|SECRETAR|ÁREA|AREA|INTERVENCI|NOTAR|BASE DE DATOS|"
    r"FUNDACI|DELEGACI|DEMARCACI|XEFATURA|CONSELLER|GOBIERNO|GOBERNO|AUGAS|FE DE ERRATAS|"
    r"COMUNIDADE|ANUNCIO|OUTR|UNIVERSIDADE|INSTITUTO|AXENCIA|DIRECCI|SUBDELEGACI|"
    r"ENTIDADE|EMPRESA|TESOURER|AUDIENCIA|REXISTRO|CONSELLO)", re.I)
# subunidades que cuelgan de una cabecera (no la sustituyen si esta es un concello)
_SUBENT = re.compile(r"^(SERVIZO|SECCI|SECRETAR|ÁREA|AREA|INTERVENCI|UNIDADE|DEPARTAMENTO|"
                     r"NEGOCIADO|TESOURER|CONCELLAR|XERENCIA|ORGANISMO|PATRONATO|EMPRESA MUNICIPAL)", re.I)
_CAB_SUM = re.compile(r"\(\s*P[ÁA][XG]\.?\s*(\d+)\s*[,.]?\s*R\.?\s*n?[.º]?\s*(\d{1,5})\s*\)\s*$", re.I)


def _limpia_html(s):
    t = _TAG.sub("", s or "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def _parsear_sumario(cfg, h):
    i = h.find("field--name-field-ail-bop-contenido")
    if i < 0:
        return []
    seg = h[i:i + 300000]
    fin = seg.find('<div class="row">')
    if fin > 0:
        seg = seg[:fin]
    out, cab = [], None
    for m in _TOK.finditer(seg):
        if m.group(1) is not None or m.group(2) is not None:
            # OJO: <strong></strong> vacío da "" (no None): no pisa la cabecera
            crudo = m.group(1) if m.group(1) is not None else m.group(2)
            t = _limpia_html(crudo)
            if not t:
                continue
            if _SUBENT.match(t) and cab and not _NO_MUNI.match(cab):
                continue                       # departamento de un concello: sigue el concello
            cab = t
            continue
        url, pag, tit = m.group(3), m.group(4), m.group(5)
        tit = _limpia_html(tit)
        if not cab or _NO_MUNI.match(cab) or not tit:
            continue
        mr = _CAB_SUM.search(tit)
        out.append({"cab": cab, "titulo": _CAB_SUM.sub("", tit).strip().rstrip(".,;:").strip(),
                    "pdf": urllib.parse.urljoin(cfg["base"], url.split("#")[0]),
                    "page": int(pag or (mr.group(1) if mr else 1) or 1),
                    "reg": (mr.group(2).lstrip("0") if mr else "")})
    return out


def _sumario(cfg, nid):
    """Items del sumario del nodo (memoria → disco → red). Una sola descarga por
    nodo aunque lo pidan varios hilos a la vez."""
    with _LOCK:
        if nid in _SUM:
            return _SUM[nid]
        ev = _INFLIGHT.get(nid)
        mio = ev is None
        if mio:
            ev = _INFLIGHT[nid] = threading.Event()
    if not mio:
        ev.wait(40)
        with _LOCK:
            return _SUM.get(nid, [])
    items = None
    try:
        items = _disco_get(f"sum_{nid}.json")
        if items is None:
            h = _get(cfg["base"] + f"/gl/node/{nid}")
            items = _parsear_sumario(cfg, h)
            if items:
                _disco_set(f"sum_{nid}.json", items)
    except Exception:  # noqa: BLE001
        items = None
    finally:
        with _LOCK:
            if items is not None:
                _SUM[nid] = items
            _INFLIGHT.pop(nid, None)
            ev.set()
    return items or []


# ---- búsqueda --------------------------------------------------------------------
_GENERICOS = {"ordenanza": "ordenanza", "ordenanzas": "ordenanza", "reglamento": "regulamento",
              "reglamentos": "regulamento", "regulamento": "regulamento", "tasa": "taxa",
              "tasas": "taxa", "taxa": "taxa", "ordenanzafiscal": "ordenanza"}
_GEN_PAL = {"ordenanza", "ordenanzas", "regulamento", "reglamento", "municipal", "municipais",
            "fiscal", "fiscales", "fiscais", "reguladora", "regulador", "reguladoras", "taxa",
            "tasa", "taxas", "tasas", "norma", "normas", "normativa"}
# títulos que NO son normativa (la mayoría del boletín): fuera, salvo que digan
# ordenanza/regulamento/bando (una «modificación da ordenanza… e padrón» se queda)
_NORMA = re.compile(r"ordenanza|regulamento|regramento|reglamento|\bbando\b|estatutos|normas\b", re.I)
_NO_NORMA = re.compile(
    r"padr[oó]n|cobranza|matr[ií]cula|bolsa de (?:emprego|traballo|trabajo)|convocatoria|"
    r"proceso selectivo|selecci[oó]n|\blista(?:xe|do)?s?\b|nomeamento|nombramiento|delegaci[oó]n|"
    r"licitaci[oó]n|contrataci[oó]n|notificaci[oó]n|conta xeral|cuenta general|orzamento|presupuesto|"
    r"cesi[oó]n|expropiaci|tribunal|oferta de emprego|cadro de persoal|plantilla|adxudicaci|"
    r"adjudicaci|subvenci[oó]n|axudas?\b|\bbecas?\b|\bbases\b|elecci[oó]n|cr[eé]ditos?\b|"
    r"aprobaci[oó]n (?:do|del) proxecto|proxecto de obra|convenio|contas?\b|"
    r"exposici[oó]n p[uú]blica d[ao]s? (?:conta|padr)|licenza de obra|informaci[oó]n p[uú]blica d[ao] (?:proxecto|expediente)",
    re.I)


# una corrección de erro no es «la ordenanza»: se lista, pero no compite como candidato
_ERRATA = re.compile(r"(?:correcci[oó]n|rectificaci[oó]n)\s+d[eo]s?\s+erro|fe de erratas", re.I)


def _es_normativo(titulo):
    # «PRAZA DE ORDENANZA» es el puesto de conserje, no una norma
    t = re.sub(r"(?i)pr[aá]zas?\s+de\s+ordenanzas?", "", titulo or "")
    return bool(_NORMA.search(t)) or not _NO_NORMA.search(t)


def _formas(terminos):
    """Formas normalizadas (mnorm) de los términos de materia para marcar títulos."""
    out = set()
    for t in terminos:
        w = B._mnorm(t)
        if len(w) >= 3:
            out.add(w)
    return out


def _lleva_materia(titulo, formas):
    tm = B._mnorm(titulo)
    return any(B._hit(w, tm) for w in formas)


def _item_a_resultado(cfg, it, fila, materia):
    reg = it.get("reg") or ""
    anio = (fila.get("orden") or "")[:4]
    return {"url": f"{it['pdf']}#page={it['page']}", "titulo": it["titulo"],
            "cve": f"BOP-LU-{anio}-{reg}" if reg and anio else "",
            "fecha": fila.get("fecha", ""), "orden": fila.get("orden", "0"),
            "pdf": it["pdf"], "page": it["page"], "reg": reg, "organo": it["cab"],
            "materia": bool(materia)}


def _recolectar(cfg, filas, muni, formas, tope, solo_reg=None):
    """Anuncios del concello `muni` en los boletines `filas` (más recientes
    primero), leyendo los sumarios por tandas con parada temprana: en cuanto hay
    `tope` anuncios FUERTES (normativos y con la materia en el título) se para."""
    out, vistos = [], set()
    filas = sorted(filas, key=lambda f: f["orden"], reverse=True)[:_MAX_BOLETINES]
    fuertes = 0
    for i in range(0, len(filas), _TANDA):
        tanda = filas[i:i + _TANDA]
        with _cf.ThreadPoolExecutor(max_workers=len(tanda)) as ex:
            sumarios = list(ex.map(lambda f: _sumario(cfg, f["nid"]), tanda))
        for fila, items in zip(tanda, sumarios):
            for it in items:
                if not _coincide(it["cab"], muni):
                    continue
                if solo_reg is not None:
                    if it.get("reg") != solo_reg:
                        continue
                elif not _es_normativo(it["titulo"]):
                    continue
                k = (it["pdf"], it.get("reg") or it["titulo"])
                if k in vistos:
                    continue
                vistos.add(k)
                mat = bool(formas) and _lleva_materia(it["titulo"], formas)                     and not _ERRATA.search(it["titulo"])
                if mat and _NORMA.search(it["titulo"]):
                    fuertes += 1
                out.append(_item_a_resultado(cfg, it, fila, mat))
        if formas and fuertes >= tope:
            break
        if not formas and len(out) >= tope:
            break
        if solo_reg is not None and out:
            break
    return out


def _raiz(w):
    """Raíz para un filtro por SUBCADENA y SENSIBLE A ACENTOS: se corta ANTES de la
    terminación que cambia de tilde entre singular/plural y entre castellano y
    gallego («emisiones»→«emis» casa «EMISIÓNS»; «circulación»→«circulac» casa
    «circulacion» y «CIRCULACIÓN»; «animales»→«animal»; «terrazas»→«terraza»)."""
    w = w.strip()
    wl = w.lower()
    m = re.search(r"i[oó]n(?:e?s)?$", wl)
    if m and m.start() >= 4:
        return w[:m.start()]
    m = re.search(r"[oó]n(?:e?s)?$", wl)
    if m and m.start() >= 5:
        return w[:m.start()]
    if len(w) > 6 and wl.endswith("es"):
        return w[:-2]
    if len(w) > 5 and wl.endswith("s"):
        return w[:-1]
    return w


def _terminos(texto):
    """(consultas, formas): los 1-2 términos más distintivos de lo pedido, su forma
    gallega (tabla _GALEGO del motor) y las formas normalizadas para marcar títulos."""
    pal = [w for w in re.split(r"\W+", texto or "") if w]
    dist = [w for w in pal if len(w) >= 3 and B._norm(w) not in _GEN_PAL
            and B._norm(w) not in {B._norm(x) for x in B._STOPM}]
    dist = sorted(dist, key=len, reverse=True)
    consultas, formas = [], set()
    for w in dist[:2]:
        gl = B._GALEGO.get(w.lower())
        for f in (w, gl):
            if f:
                formas.add(B._mnorm(f))
        # la forma GALLEGA va primero (es la que llevan los títulos del sumario y la
        # que recibe la profundidad de páginas); la castellana, de respaldo
        for f in ((gl, w) if gl and gl.lower() != w.lower() else (w,)):
            r = _raiz(f)
            if r.lower() not in {c.lower() for c in consultas}:
                consultas.append(r)
    if not consultas:
        consultas = [texto.strip()]
        formas.update(B._mnorm(w) for w in pal if len(w) >= 3)
    return consultas[:3], {f for f in formas if len(f) >= 3}


def buscar(prov, texto, filtro, rpp=40):
    """Anuncios del concello `filtro` (cabecera del sumario, p.ej. «MONFORTE DE
    LEMOS») relacionados con `texto`. Claves privadas: pdf, page, reg, organo."""
    cfg = B.PROVINCIAS[prov]
    if not filtro:
        return []
    muni = str(filtro).strip()
    texto = (texto or "").strip() or "ordenanza"
    munis = [muni] + ([_sin_acentos(muni)] if _sin_acentos(muni) != muni else [])

    # 1) CVE propio BOP-LU-AAAA-N: el número de rexistro está en el sumario
    m = re.search(r"BOP-LU-(\d{4})-(\d{1,5})", texto, re.I)
    if m:
        anio, reg = m.group(1), m.group(2).lstrip("0")
        filas = []
        for mu in munis:
            try:
                filas += [f for f in _buscar_pagina(cfg, f"{reg} {mu}", 0) if f["orden"].startswith(anio)]
            except Exception:  # noqa: BLE001
                pass
            if filas:
                break
        return _recolectar(cfg, filas, muni, set(), 1, solo_reg=reg)

    # 2) volcado genérico del motor: solo «ordenanza» (1 búsqueda, ~2,5 s). Los
    #    volcados «reglamento»/«tasa» no aportan nada aquí (los títulos gallegos
    #    dicen «regulamento»/«taxa» y ya entran por «ordenanza … CONCELLO») y
    #    costarían 2,5 s más cada uno.
    tn = B._norm(texto)
    if tn in _GENERICOS:
        if _GENERICOS[tn] != "ordenanza":
            return []
        try:
            filas = _buscar_pagina(cfg, f"ordenanza {muni}", 0)
        except Exception:  # noqa: BLE001
            filas = []
        return _recolectar(cfg, filas, muni, set(), 6)

    # 3) materia: «término concello» páginas 0 y 1 + forma gallega/2º término, EN
    #    PARALELO (cada página del buscador cuesta ~2,5 s); después los sumarios
    # Plan (4 búsquedas en paralelo = una sola ronda de ~2,5 s): el término
    # (páginas 0 y 1: la profundidad importa, la ordenanza suele estar entre los
    # boletines 11-20 porque los padróns la entierran), su forma gallega o el 2º
    # término, y el volcado «ordenanza CONCELLO», que el motor pide también para
    # el listado (misma caché) y que da al leer_ordenanza el recall que la
    # materia sola no tiene (el título gallego puede no llevar la palabra:
    # «verteduras» para «vertidos»). Medido: quitar la página 1 baja 15/16 → 12/16.
    consultas, formas = _terminos(texto)
    tareas = [(f"{consultas[0]} {muni}", 0), (f"{consultas[0]} {muni}", 1)]
    if len(consultas) > 1:
        tareas.append((f"{consultas[1]} {muni}", 0))
    elif len(munis) > 1:
        tareas.append((f"{consultas[0]} {munis[1]}", 0))
    else:
        tareas.append((f"{consultas[0]} {muni}", 2))
    tareas.append((f"ordenanza {muni}", 0))
    tareas = list(dict.fromkeys(tareas))[:4]

    def una(t):
        try:
            return _buscar_pagina(cfg, t[0], t[1])
        except Exception:  # noqa: BLE001
            return []

    filas, vistos = [], set()
    with _cf.ThreadPoolExecutor(max_workers=min(4, len(tareas))) as ex:
        for rs in ex.map(una, tareas):
            for f in rs:
                if f["nid"] not in vistos:
                    vistos.add(f["nid"])
                    filas.append(f)
    _dbg(f"buscar {texto!r} {muni}: {len(tareas)} búsquedas -> {len(filas)} boletines")
    out = _recolectar(cfg, filas, muni, formas, 2)
    _dbg(f"buscar {texto!r} {muni}: {len(out)} anuncios ({sum(1 for r in out if r['materia'])} con materia)")
    return out[:max(int(rpp or 40), 10)]


# ---- lectura ---------------------------------------------------------------------
_RREG = re.compile(r"^\s*R\.?\s*n?[.º]?\s*(\d{1,5})\s*$", re.M)


def _paginas_pdf(url):
    """Texto por página del PDF del día (memoria → disco → red)."""
    c = _PDFS.get(url)
    if c:
        return c[0]
    clave = "pdf_" + hashlib.sha1(url.encode()).hexdigest()[:20] + ".json"
    pags = _disco_get(clave)
    if pags is None:
        if fitz is None:
            return None
        b = _get(url, timeout=25, binario=True)
        if b[:5] != b"%PDF-":
            return None
        doc = fitz.open(stream=b, filetype="pdf")
        pags = [doc[i].get_text() for i in range(doc.page_count)]
        doc.close()
        if sum(len(p) for p in pags) / max(1, len(pags)) < 120:
            pags = []                                   # escaneado sin capa de texto
        _disco_set(clave, pags)
    if len(_PDFS) > 24:
        _PDFS.clear()
    _PDFS[url] = (pags, time.time())
    return pags


def _limpiar(t):
    """Fuera cabeceras/pies de página y párrafos reconstruidos (el texto de fitz
    viene línea a línea)."""
    t = re.sub(r"(?m)^[ \t]*\d{1,3}[ \t]*$\n?", "", t)                       # nº de página
    t = re.sub(r"(?m)^[ \t]*N[úu]m\.\s*\d+\s*[–\-].*$\n?", "", t)             # Núm. 096 – mércores…
    t = re.sub(r"(?m)^[ \t]*BOP Lugo[ \t]*$\n?", "", t)
    t = re.sub(r"(?m)^[ \t]*Anuncio publicado en:.*$\n?", "", t)
    t = re.sub(r"(?im)^[ \t]*((?:art(?:igo|[íi]culo)|art\.)\s+\d+)", r"\n\1", t)   # blanco antes de cada artigo
    t = re.sub(r"([.:;])[ \t]*\n(?=[^\n])", r"\1\n\n", t)                     # fin de frase = párrafo
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", t).strip()


def _cabecera_en(chunk, organo):
    if not organo:
        return False
    pat = r"(?m)^\s*(?:CONCELLO D[EO]\s+)?" + re.escape(_sin_acentos(organo).upper()) + r"\s*$"
    return re.search(pat, _sin_acentos(chunk).upper()) is not None


def texto(prov, m):
    """(texto del anuncio, 'pdf-dia'): del PDF del día, cortado por su número de
    rexistro «R. NNNN»; sin marca, ventana de páginas desde el ancla."""
    pdf = m.get("pdf") if isinstance(m, dict) else str(m).split("#")[0]
    if not pdf:
        return "", "sin-pdf"
    try:
        pags = _paginas_pdf(pdf)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    if pags is None:
        return "", "sin-pdf"
    if not pags:
        return "", "cifrado"
    todo = "\n".join(pags)
    offs, pos = [], 0
    for p in pags:
        offs.append(pos)
        pos += len(p) + 1

    def pagina_de(i):
        return bisect.bisect_right(offs, i)             # 1-based

    reg = (m.get("reg") if isinstance(m, dict) else "") or ""
    page = int((m.get("page") if isinstance(m, dict) else 1) or 1)
    organo = (m.get("organo") if isinstance(m, dict) else "") or ""
    txt = ""
    if reg:
        marcas = list(_RREG.finditer(todo))
        mejor = None
        for idx, fin in enumerate(marcas):
            if fin.group(1).lstrip("0") != reg:
                continue
            ini = marcas[idx - 1].end() if idx else 0
            chunk = todo[ini:fin.end()]
            # varias marcas iguales (error del boletín): la buena empieza con la
            # cabecera del concello y arranca en la página del ancla
            s = (2 if _cabecera_en(chunk[:700], organo) else 0) + (1 if abs(pagina_de(ini) - page) <= 1 else 0)
            if mejor is None or s > mejor[0]:
                mejor = (s, chunk)
        if mejor:
            txt = mejor[1]
    if not txt:
        ini = max(0, min(page - 1, len(pags) - 1))
        txt = "\n".join(pags[ini:ini + 8])
        if organo:
            mo = re.search(r"(?m)^\s*(?:CONCELLO D[EO]\s+)?" + re.escape(_sin_acentos(organo).upper()) + r"\s*$",
                           _sin_acentos(txt).upper())
            if mo and mo.start() < 8000:
                txt = txt[mo.start():]
        mfin = _RREG.search(txt, 300)                   # cierra en el fin de ESTE anuncio
        if mfin:
            txt = txt[:mfin.end()]
    txt = _limpiar(txt)
    return (txt, "pdf-dia") if len(txt) > 200 else ("", "sin-texto")
