# -*- coding: utf-8 -*-
"""Backend MELILLA del motor de ordenanzas (bop_engine): Boletín Oficial de la
Ciudad de Melilla (BOME) — https://bomemelilla.es. Ciudad autónoma UNIMUNICIPAL:
el mapa tiene una sola entrada («Melilla»). Receta verificada en vivo el
27-jul-2026 (sondas _probe_melilla*.py) e implementada el 2-sep-2026.

Cómo funciona (GETs planos, sin sesión, sin cookies, sin captcha):
  * /buscar?from=&to=&tipo=1&contenido=<texto>&cve=&page=n busca en el SUMARIO
    (títulos) de los artículos. TRAMPA: sin from/to solo mira el año en curso ->
    se pasa SIEMPRE el rango completo (2005-01-01 .. año que viene). La búsqueda
    es de FRASE literal («tenencia de animales» = 0; «animales» = 22), insensible
    a tildes. tipo=2 (texto íntegro) tarda ~4 s y es ruidoso: no se usa.
  * Devuelve BOLETINES contenedores (10/página, más recientes primero: «BOME Nº
    6300 del martes, 12 de agosto de 2025»), no artículos. Hay que abrir cada
    /bome/BOME-B-AAAA-N (o BOME-BX-, los extraordinarios) y parsear su sumario:
    <h4> organismo + <li> «ARTÍCULO N (CVE: BOME-A-AAAA-N)» <blockquote> título.
    Los sumarios son inmutables -> caché en memoria y en /tmp.
  * El PDF de cada artículo (/bome/descargar/BOME-A-AAAA-N.pdf) lleva capa de
    texto (fitz directo; OCR solo como excepción). Cada página repite una
    cabecera «BOME Número … CVE verificable en https://bomemelilla.es» que se quita.
  * El BOME está dominado por actos (órdenes, decretos, convenios, padrones):
    solo se devuelven títulos de NORMA (ordenanza/reglamento/bando/estatutos…) y
    se descartan convenios, subvenciones, notificaciones, nombramientos… Sin ese
    filtro el verificador por contenido leería una «Orden de horarios de
    terrazas» y la daría por ordenanza. También publica anuncios de organismos
    ajenos (Ayuntamiento de Chipiona, Juzgados, Autoridad Portuaria): fuera.
  * Techo de recall del propio índice: el sumario está indexado desde ~2016
    (fulltext desde 2014). Las normas anteriores NO son localizables: el motor
    responde honesto y remite a la sede.
  * El config lleva `verifica_texto: true`: el motor lee los mejores candidatos
    y elige por CONTENIDO (los títulos son largos: «Acuerdo de la Excma.
    Asamblea… relativo a la aprobación definitiva del Reglamento…»).
"""
import concurrent.futures as _cf
import html as _html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import bop_engine as B

_SEM = threading.BoundedSemaphore(4)      # cortesía con el BOME: ≤4 peticiones simultáneas
_BUSQ = {}                                # (consulta, página) -> (ts, total, boletines)
_BUSQ_TTL = 600
_SUM = {}                                 # ruta boletín -> [artículos] (inmutable)
_SUM_LOCK = threading.Lock()
_MAX_BOL_MATERIA = 20                     # boletines abiertos por consulta de materia (2 páginas)
_MAX_BOL_GENERICO = 16                    # ... por volcado genérico («ordenanza», «reglamento»)
_MAX_BOL_TOTAL = 30                       # ... por llamada

_RES_RE = re.compile(r'href="(/bome/(BOME-B[A-Z]?-\d{4}-\d+))"[^>]*>\s*(.*?)\s*</a>', re.S)
_ORG_RE = re.compile(r'<h4 class="text-uppercase h5">\s*<i[^>]*></i>\s*(.*?)\s*</h4>(.*?)'
                     r'(?=<h4 class="text-uppercase h5">|</div>\s*</div>\s*</div>)', re.S)
_ART_RE = re.compile(r'\(CVE:\s*(BOME-A[A-Z]?-\d{4}-\d+)\s*\)\s*</span>\s*</h5>\s*'
                     r'<blockquote[^>]*>(.*?)</blockquote>', re.S)
_TOTAL = re.compile(r"total de\s*([\d.]+)\s*elementos?")
_FECHA = re.compile(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})", re.I)
_CVE_RE = re.compile(r"(?i)\bBOME-A[X]?-\d{4}-\d+\b")
_CABECERA = re.compile(r"BOME N[úu]mero \d+\s+Melilla,.{0,220}?CVE verificable en https://bomemelilla\.es", re.S)
_MESES = {"enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05", "junio": "06",
          "julio": "07", "agosto": "08", "septiembre": "09", "setiembre": "09", "octubre": "10",
          "noviembre": "11", "diciembre": "12"}

_NORMA = re.compile(r"(?i)ordenanza|reglamento|\bbando\b|estatutos?\b|texto refundido|"
                    r"normas? (?:reguladora|urban)|plan general|regulaci[oó]n")
_EXCL = re.compile(
    r"(?i)convenio|subvenci|\bayudas?\b|\bbecas?\b|contrat|licitaci|adjudicaci|nombramiento|"
    r"\bcese\b|emplazamiento|notificaci|citaci[oó]n|requerimiento|padr[oó]n|oferta de empleo|"
    r"bases (?:de|para|reguladoras de la convocatoria)|convocatoria|\blistas?\b|tribunal|"
    r"sancionador|sentencia|propuesta de resoluci|calendario|criterios interpretativos|"
    r"delegaci[oó]n de competencia|avocaci[oó]n|rectificaci[oó]n|relaci[oó]n (?:provisional|definitiva)|"
    r"extracto|informaci[oó]n p[uú]blica referente|transmisi[oó]n de (?:la )?licencia")
_AJENO = re.compile(
    r"(?i)ayuntamiento|\bayto\b|juzgado|audiencia|tribunal|ministerio|delegaci[oó]n del gobierno|"
    r"delegaci[oó]n de defensa|tesorer[ií]a|seguridad social|agencia tributaria|autoridad portuaria|"
    r"universidad|\buned\b|notar|registro (?:civil|de la propiedad)|comandancia|jefatura|"
    r"servicio p[uú]blico de empleo|instituto nacional|direcci[oó]n provincial|confederaci[oó]n|"
    r"diputaci[oó]n|junta de|generalitat|xunta|gobierno vasco|cabildo|consejo general del poder")
_GENERICOS = {"ordenanza", "ordenanzas", "reglamento", "reglamentos", "tasa", "tasas"}


def _t(x):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


def _get(url, timeout=25):
    with _SEM:
        return urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": B._UA, "Accept-Language": "es-ES,es;q=0.9"}), timeout=timeout).read()


def _fecha_de(txt):
    m = _FECHA.search(txt or "")
    if not m:
        return "", "0"
    mes = _MESES.get("".join(c for c in m.group(2).lower() if c.isalpha()), "")
    if not mes:
        return "", "0"
    d = m.group(1).zfill(2)
    return f"{d}/{mes}/{m.group(3)}", m.group(3) + mes + d


# ---- búsqueda en el sumario (boletines) ----------------------------------------------
def _buscar_pag(cfg, q, page):
    clave = (q.lower(), page)
    c = _BUSQ.get(clave)
    if c and time.time() - c[0] < _BUSQ_TTL:
        return c[1], c[2]
    p = {"from": "2005-01-01", "to": f"{time.localtime().tm_year + 1}-12-31", "tipo": "1",
         "contenido": q, "cve": ""}
    if page > 1:
        p["page"] = page
    h = _get(cfg["base"] + "/buscar?" + urllib.parse.urlencode(p)).decode("utf-8", "replace")
    m = _TOTAL.search(h)
    total = int(m.group(1).replace(".", "")) if m else 0
    bols, vistos = [], set()
    for ruta, ident, txt in _RES_RE.findall(h):
        if ruta in vistos:
            continue
        vistos.add(ruta)
        fecha, orden = _fecha_de(_t(txt))
        bols.append({"ruta": ruta, "id": ident, "fecha": fecha, "orden": orden})
    _BUSQ[clave] = (time.time(), total, bols)
    return total, bols


def _buscar_and(cfg, norma, termino, page=1):
    """Buscador AVANZADO: dos cláusulas sobre el sumario unidas por AND (por
    ARTÍCULO, no por boletín): «reglamento» ∧ «taxi» = 3 boletines exactos frente
    a los 46 de «taxi» a secas (licencias, transmisiones…). Es lo que hace posible
    el recall sin abrir decenas de boletines."""
    clave = (f"{norma}&{termino}".lower(), page)
    c = _BUSQ.get(clave)
    if c and time.time() - c[0] < _BUSQ_TTL:
        return c[1], c[2]
    p = [("cve", ""), ("from", "2005-01-01"), ("to", f"{time.localtime().tm_year + 1}-12-31"),
         ("departamento", "")]
    for i, txt in enumerate((norma, termino)):
        p += [(f"contenido[{i}][type]", "sumario_articulo"), (f"contenido[{i}][like]", "like"),
              (f"contenido[{i}][content]", txt), (f"contenido[{i}][operator]", "and")]
    if page > 1:
        p.append(("page", page))
    h = _get(cfg["base"] + "/buscador-avanzado?" + urllib.parse.urlencode(p)).decode("utf-8", "replace")
    m = _TOTAL.search(h)
    total = int(m.group(1).replace(".", "")) if m else 0
    bols, vistos = [], set()
    for ruta, ident, txt in _RES_RE.findall(h):
        if ruta in vistos:
            continue
        vistos.add(ruta)
        fecha, orden = _fecha_de(_t(txt))
        bols.append({"ruta": ruta, "id": ident, "fecha": fecha, "orden": orden})
    _BUSQ[clave] = (time.time(), total, bols)
    return total, bols


def _sumario(cfg, ruta):
    """[{cve, organismo, titulo}] del boletín (caché memoria + /tmp: es inmutable)."""
    with _SUM_LOCK:
        c = _SUM.get(ruta)
    if c is not None:
        return c
    clave = "melilla-sumario-" + ruta.rsplit("/", 1)[-1]
    arts = None
    raw = B._txt_cache_get(clave)
    if raw:
        try:
            arts = json.loads(raw)
        except Exception:  # noqa: BLE001
            arts = None
    if arts is None:
        try:
            h = _get(cfg["base"] + ruta).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            h = ""                      # algún boletín antiguo devuelve 500: se salta
        arts = []
        for org, cuerpo in _ORG_RE.findall(h):
            o = _t(org)
            for cve, tit in _ART_RE.findall(cuerpo):
                arts.append({"cve": cve, "organismo": o, "titulo": _t(tit)})
        if arts:
            B._txt_cache_set(clave, json.dumps(arts, ensure_ascii=False))
    with _SUM_LOCK:
        _SUM[ruta] = arts
    return arts


# ---- términos --------------------------------------------------------------------------
def _terminos(texto):
    """(consultas al buscador, términos que deben casar en el título, es_generico).
    El buscador es de frase literal: se consulta por los términos distintivos del
    abogado y por las frases del tesauro («vehiculos de movilidad personal»)."""
    mn = B._mnorm(texto)
    if mn in _GENERICOS:
        base = "tasa" if mn.startswith("tasa") else mn
        return [base], [base], True
    raw, core, _soft = B._familias(texto)
    consultas = [w for w in sorted(raw, key=len, reverse=True) if len(w) >= 4 and w not in B._GENERICO][:2]
    extra = [c for c in core if " " in c and c not in consultas]
    for c in sorted(core, key=len, reverse=True):
        if (" " not in c and len(c) >= 5 and not re.search(r"(?:ic|ari|in)$", c)
                and c not in consultas and not any(r.startswith(c) or c.startswith(r) for r in consultas)):
            extra.append(c)
    consultas += extra[:2]
    if not consultas:
        consultas = [mn] if len(mn) >= 3 else ["ordenanza"]
    return consultas[:4], list(raw) + list(core), False


def _raiz(w):
    if " " in w:
        return w
    if w.endswith("es") and len(w) > 6:
        return w[:-2]
    if w.endswith("s") and len(w) > 5:
        return w[:-1]
    return w


def _casa(w, tm):
    r = _raiz(w)
    return bool(r) and re.search(r"\b" + re.escape(r), tm) is not None


def _por_cve(cfg, cve):
    try:
        h = _get(cfg["base"] + "/buscar-cve?cve=" + urllib.parse.quote(cve)).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    tit = re.search(r'(?s)class="intro-sumario">\s*<p[^>]*>(.*?)</p>', h)
    if not tit:
        return []
    bol = re.search(r'href="/bome/(BOME-B[A-Z]?-\d{4}-\d+)"', h)
    h1 = re.search(r"(?s)<h1[^>]*>(.*?)</h1>", h)
    org = re.search(r"(?s)<h3[^>]*>(.*?)</h3>", h)
    fecha, orden = _fecha_de(_t(h1.group(1)) if h1 else "")
    # el CVE va en el título para que el verificador por contenido lo dé por bueno sin leer
    return [{"url": cfg["base"] + "/bome/descargar/" + cve + ".pdf", "titulo": _t(tit.group(1)) + f" [{cve}]",
             "cve": cve, "fecha": fecha, "orden": orden, "boletin": bol.group(1) if bol else "",
             "organismo": _t(org.group(1)) if org else "", "materia": True}]


# ================================================================ contrato bop_engine
def buscar(prov, texto, filtro=None, rpp=40):
    """Artículos del BOME con título de NORMA que casan con la materia.
    `filtro` (departamento) no se usa: el BOME es unimunicipal y los organismos
    ajenos se descartan por nombre."""
    cfg = B.PROVINCIAS[prov]
    m = _CVE_RE.search(texto or "")
    if m:
        return _por_cve(cfg, m.group(0).upper())
    consultas, match, generico = _terminos(texto or "ordenanza")
    tope = _MAX_BOL_GENERICO if generico else _MAX_BOL_MATERIA
    if generico and consultas[0] == "tasa":
        tope = 10                       # «tasa» sale en cientos de boletines: solo los últimos

    def boletines(q):
        """Volcado genérico: páginas del buscador simple («ordenanza», «reglamento»)."""
        out = []
        for p in range(1, 4):
            try:
                _total, bl = _buscar_pag(cfg, q, p)
            except Exception:  # noqa: BLE001
                break
            out.extend(bl)
            if len(bl) < 10 or len(out) >= tope:
                break
        return out[:tope]

    def boletines_and(par):
        """Materia: «ordenanza»/«reglamento» ∧ término (buscador avanzado, por artículo)."""
        out = []
        for p in range(1, 3):
            try:
                _total, bl = _buscar_and(cfg, par[0], par[1], p)
            except Exception:  # noqa: BLE001
                break
            out.extend(bl)
            if len(bl) < 10 or len(out) >= 12:
                break
        return out[:12]

    bols = {}
    if generico:
        with _cf.ThreadPoolExecutor(max_workers=min(4, len(consultas))) as ex:
            for bl in ex.map(boletines, consultas):
                for b in bl:
                    bols.setdefault(b["ruta"], b)
    else:
        pares = [(n, q) for q in consultas for n in ("ordenanza", "reglamento")]
        with _cf.ThreadPoolExecutor(max_workers=4) as ex:
            for bl in ex.map(boletines_and, pares):
                for b in bl:
                    bols.setdefault(b["ruta"], b)
    rutas = sorted(bols.values(), key=lambda b: b["orden"], reverse=True)[:_MAX_BOL_TOTAL]
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        sums = list(ex.map(lambda b: _sumario(cfg, b["ruta"]), rutas))
    out, vistos = [], set()
    for b, arts in zip(rutas, sums):
        for a in arts:
            if a["cve"] in vistos:
                continue
            tit = a["titulo"]
            if _AJENO.search(a["organismo"]) or not _NORMA.search(tit) or _EXCL.search(tit):
                continue
            tm = B._mnorm(tit)
            if not generico and not any(_casa(w, tm) for w in match):
                continue
            vistos.add(a["cve"])
            out.append({"url": cfg["base"] + "/bome/descargar/" + a["cve"] + ".pdf", "titulo": tit,
                        "cve": a["cve"], "fecha": b["fecha"], "orden": b["orden"], "boletin": b["id"],
                        "organismo": a["organismo"], "materia": not generico})
    # una «corrección de errores» solo vale si no hay otra publicación de la norma
    sin_corr = [r for r in out if not re.search(r"(?i)correcci[oó]n de errores", r["titulo"])]
    if sin_corr:
        out = sin_corr
    out.sort(key=lambda r: r["orden"], reverse=True)
    return out[:max(rpp, 20)]


def texto(prov, m):
    """(texto_plano, via) del artículo: PDF individual con capa de texto."""
    cfg = B.PROVINCIAS[prov]
    cve = (m.get("cve") if isinstance(m, dict) else "") or ""
    url = (m.get("url") if isinstance(m, dict) else m) or ""
    if not url and cve:
        url = cfg["base"] + "/bome/descargar/" + cve + ".pdf"
    if not url:
        return "", "sin-url"
    clave = "melilla-" + (cve or re.sub(r"\W", "_", url)[-40:])
    t = B._txt_cache_get(clave)
    if t:
        return t, "pdf"
    try:
        datos = _get(url, timeout=40)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    t, via = B._pdf_bytes_texto(datos, ocr=False)
    if via == "cifrado":
        if os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            t, via = B._pdf_bytes_texto(datos, ocr=True, max_pag=8)
        else:
            return "", "cifrado"
    if not t or not t.strip():
        return "", (via if via != "directo" else "sin-texto")
    t = _CABECERA.sub(" ", t)
    B._txt_cache_set(clave, t)
    return t, ("pdf" if via == "directo" else via)
