# -*- coding: utf-8 -*-
"""
Backend ÁLAVA para el motor de ordenanzas (familia «alava»): BOTHA, el Boletín
Oficial del Territorio Histórico de Álava (www.araba.eus/botha).

Verificado en vivo (27-jul-2026 sondas, 2-sep-2026 implementación):
  * ASP.NET WebForms sin captcha. La búsqueda exige una CASCADA de tres POST a la
    misma página reenviando __VIEWSTATE/__VIEWSTATEGENERATOR/__EVENTVALIDATION del
    último HTML: ddlSeccion1=2 (Administración Local) → ddlSeccion2=5 (Municipios)
    → ddlSeccion3=<código del ayuntamiento> + ddlTipo1=1 (ORDENANZAS Y REGLAMENTOS)
    + tbResumen (título) o tbAnuncio (texto íntegro) + btnBuscar. Sin la cascada,
    __EVENTVALIDATION rechaza el POST («Se ha producido un error en la aplicación»).
  * 30 resultados por página en orden de FECHA (no de relevancia): el motor ranquea
    en local por título. Para pasar de 30 no sirve el botón de página siguiente:
    se trocea con ventanas tbFecDesde/tbFecHasta (dd/mm/aaaa).
  * tbAnioAnun + tbNAnun localizan un anuncio por su número (CVE BOP-VI-AAAA-N).
  * Lectura: GET Resultado.aspx?File=Boletines/AAAA/NNN/AAAA_NNN_NNNNN_C.xml&hl=
    (sin cookies, 0,3-0,4 s) devuelve el texto ÍNTEGRO en HTML; el castellano va
    en <div id="detalle_cast"> y el euskera en «detalle_eus». SIN PDF y SIN OCR.
  * La página del buscador mezcla plantilla en ISO-8859-1 con datos en UTF-8: se
    decodifica en UTF-8 con reemplazo (los títulos, que es lo que importa, van bien).
  * ASP.NET SERIALIZA las peticiones de una misma sesión: pool de hasta 4 sesiones
    independientes reutilizables ~9 min. Cada POST (~1,1 s) se cachea 10 min: el
    leer_ordenanza que sigue a buscar_ordenanzas no vuelve a preguntar al boletín.
  * Coincidencia por SUBCADENA e insensible a acentos y mayúsculas (se consulta la
    raíz «terraza», que casa «terraza» y «terrazas»). Con ddlTipo1=1 aún se cuelan
    padrones y exposiciones públicas: se descartan aquí salvo que el título diga
    ordenanza/reglamento/bando.
"""
import concurrent.futures as _cf
import datetime as _dt
import html as _html
import http.cookiejar
import queue
import re
import threading
import time
import urllib.parse
import urllib.request

import bop_engine as B

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_TTL = 540              # una sesión se reutiliza ~9 min (la nota dice ~10)
_MAX_SES = 4            # sesiones independientes (ASP.NET serializa dentro de cada una)
_POR_PAGINA = 30
_PAGINAS_MATERIA = 3    # hasta 90 anuncios por término
_PAGINAS_GENERICO = 2   # el volcado «ordenanza» de Vitoria son 271: bastan los 60 últimos
_CACHE_TTL = 600

_LOCK = threading.Lock()
_POOL = queue.Queue()   # sesiones libres
_VIVAS = [0]            # sesiones creadas y no descartadas
_CACHE = {}             # (cod, extra) -> (html, total, ts)


class _ErrorApp(Exception):
    """BOTHA respondió «Se ha producido un error en la aplicación» (ViewState
    caducado o cascada perdida): se descarta la sesión y se reintenta con otra."""


class _Ses:
    def __init__(self, base):
        self.base = base
        self.url = base + "/Busquedas/SGBO5016.aspx"
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.addheaders = [("User-Agent", _UA), ("Accept-Language", "es-ES,es")]
        self.campos = {}
        self.ts = time.time()

    def get(self, u, timeout=25):
        return self.op.open(u, timeout=timeout).read().decode("utf-8", "replace")

    def post(self, d, timeout=25):
        body = urllib.parse.urlencode(d, encoding="utf-8", errors="replace").encode("ascii")
        req = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded", "Referer": self.url})
        return self.op.open(req, timeout=timeout).read().decode("utf-8", "replace")


def _campos(h):
    d = {}
    for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', h):
        n = re.search(r'name="([^"]+)"', m.group(0))
        v = re.search(r'value="([^"]*)"', m.group(0))
        if n:
            d[n.group(1)] = _html.unescape(v.group(1)) if v else ""
    return d


def _crear(cfg):
    """GET + los dos postbacks de la cascada. Deja en s.campos el ViewState válido
    para lanzar búsquedas (vale para varias seguidas)."""
    s = _Ses(cfg["base"])
    d = _campos(s.get(s.url))
    d.update({"ddlSeccion1": cfg.get("seccion1", "2"), "btnSeccion1": "Ver subsección 2"})
    d = _campos(s.post(d))
    d.update({"ddlSeccion1": cfg.get("seccion1", "2"), "ddlSeccion2": cfg.get("seccion2", "5"),
              "btnSeccion2": "Ver subsección 3"})
    h = s.post(d)
    if "ddlSeccion3" not in h:
        raise RuntimeError("BOTHA: la cascada de secciones no devolvió los ayuntamientos")
    s.campos = _campos(h)
    s.ts = time.time()
    return s


def _tomar(cfg):
    """Sesión libre y fresca del pool; si no hay y cabe, se crea; si no, se espera."""
    while True:
        try:
            s = _POOL.get_nowait()
        except queue.Empty:
            s = None
        if s is not None:
            if time.time() - s.ts < _TTL:
                return s
            with _LOCK:
                _VIVAS[0] -= 1
            continue
        with _LOCK:
            crear = _VIVAS[0] < _MAX_SES
            if crear:
                _VIVAS[0] += 1
        if crear:
            try:
                return _crear(cfg)
            except Exception:
                with _LOCK:
                    _VIVAS[0] -= 1
                raise
        try:
            s = _POOL.get(timeout=20)
        except queue.Empty:
            raise RuntimeError("BOTHA: sin sesión libre")
        if time.time() - s.ts < _TTL:
            return s
        with _LOCK:
            _VIVAS[0] -= 1


def _soltar(s, ok=True):
    if ok and s is not None:
        _POOL.put(s)
    elif s is not None:
        with _LOCK:
            _VIVAS[0] -= 1


# ---- parseo del listado --------------------------------------------------------
_TR = re.compile(r"(?s)<tr[^>]*>(.*?)</tr>")
_FECHA = re.compile(r'lblFecha">(\d{2})-(\d{2})-(\d{4})<')
_NUM = re.compile(r'btnAnuncio"\s+value="(\d{4})-(\d+)"')
_BOL = re.compile(r'lblAnuBoletin2">(\d{4})-(\d+)<')
_FILE = re.compile(r'lnkHtml1"[^>]*href="[^"]*File=([^"&]+)')
_TIT = re.compile(r'(?s)lnkHtml1"[^>]*>(.*?)</a>')
_PDF = re.compile(r'panPdf"[^>]*href="([^"]+\.pdf)"')
_TOTAL = re.compile(r'lblValorTotalAnuncios">(?:&nbsp;|\s)*(\d+)')

# anuncios que NO son normativa aunque BOTHA los archive como «ordenanzas y
# reglamentos»: padrones, exposiciones públicas de matrículas, listas…
_NORMA = re.compile(r"ordenanza|reglamento|\bbando\b|estatutos|normas\b", re.I)
_NO_NORMA = re.compile(r"padr[oó]n|matr[ií]cula|cobranza|per[ií]odo voluntario|lista|nombramiento|"
                       r"delegaci[oó]n|convocatoria|bolsa de (?:trabajo|empleo)|licitaci[oó]n|"
                       r"adjudicaci[oó]n|subvenci|presupuesto|cuenta general|expropiaci", re.I)


def _filas(cfg, h, materia, solo_cve=False):
    out = []
    for f in _TR.findall(h):
        if "lnkHtml1" not in f:
            continue
        mf, mn, mt = _FECHA.search(f), _NUM.search(f), _TIT.search(f)
        if not (mf and mt):
            continue
        d, m, y = mf.groups()
        tit = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", mt.group(1)))).strip()
        if not solo_cve and _NO_NORMA.search(tit) and not _NORMA.search(tit):
            continue
        mfile = _FILE.search(f)
        fichero = _html.unescape(mfile.group(1)) if mfile else ""
        url = (cfg["base"] + "/Busquedas/Resultado.aspx?File=" + urllib.parse.quote(fichero) + "&hl=") \
            if fichero else ""
        mp = _PDF.search(f)
        pdf = urllib.parse.urljoin(cfg["base"] + "/Busquedas/", mp.group(1)) if mp else ""
        anio, num = (mn.group(1), mn.group(2)) if mn else (y, "")
        mb = _BOL.search(f)
        out.append({"url": url or pdf, "titulo": tit,
                    "cve": f"BOP-VI-{anio}-{num}" if num else "",
                    "fecha": f"{d}/{m}/{y}", "orden": f"{y}{m}{d}",
                    "file": fichero, "pdf": pdf,
                    "boletin": f"{mb.group(1)}-{mb.group(2)}" if mb else "",
                    "materia": bool(materia)})
    return out


def _post_busqueda(cfg, cod, extra):
    """Un POST de búsqueda, cacheado 10 min (con reintento en sesión nueva si el
    ViewState falla). Devuelve (html, total_anuncios)."""
    key = (str(cod), tuple(sorted(extra.items())))
    c = _CACHE.get(key)
    if c and time.time() - c[2] < _CACHE_TTL:
        return c[0], c[1]
    ultimo = None
    for intento in range(2):
        s = None
        try:
            s = _tomar(cfg)
            d = dict(s.campos)
            d.update({"ddlSeccion1": cfg.get("seccion1", "2"), "ddlSeccion2": cfg.get("seccion2", "5"),
                      "ddlSeccion3": str(cod), "ddlTipo1": cfg.get("tipo_ordenanzas", "1"),
                      "btnBuscar": "Buscar"})
            d.update(extra)
            h = s.post(d)
            if "Se ha producido un error" in h or "lblValorTotalAnuncios" not in h:
                raise _ErrorApp("BOTHA rechazó la búsqueda")
            _soltar(s, True)
            mt = _TOTAL.search(h)
            total = int(mt.group(1)) if mt else 0
            if len(_CACHE) > 400:
                _CACHE.clear()
            _CACHE[key] = (h, total, time.time())
            return h, total
        except _ErrorApp as e:
            _soltar(s, False)
            ultimo = e
        except Exception as e:  # noqa: BLE001
            _soltar(s, False)
            ultimo = e
            if intento:
                break
    raise RuntimeError(f"BOTHA: {ultimo}")


def _dia_anterior(orden):
    """'aaaammdd' -> 'dd/mm/aaaa' del día anterior (ventana tbFecHasta)."""
    f = _dt.date(int(orden[:4]), int(orden[4:6]), int(orden[6:8])) - _dt.timedelta(days=1)
    return f.strftime("%d/%m/%Y")


def _consulta(cfg, cod, campo, termino, rpp, materia, extra=None, max_paginas=_PAGINAS_MATERIA,
              solo_cve=False):
    """Resultados de un término en `campo` (tbResumen = título, tbAnuncio = texto).
    Si la página viene llena y se piden más, se sigue por ventanas de fecha."""
    filas, vistos = [], set()
    hasta = ""
    for _pag in range(max(1, max_paginas)):
        ex = dict(extra or {})
        if campo and termino:
            ex[campo] = termino
        if hasta:
            ex["tbFecHasta"] = hasta
        h, total = _post_busqueda(cfg, cod, ex)
        brutas = _filas(cfg, h, materia, solo_cve)
        nuevas = [r for r in brutas if r["cve"] not in vistos]
        for r in nuevas:
            vistos.add(r["cve"])
        filas.extend(nuevas)
        llena = len(_TR.findall(h)) >= _POR_PAGINA + 1 or len(brutas) >= _POR_PAGINA - 1
        if not nuevas or not llena or len(filas) >= min(rpp, total):
            break
        hasta = _dia_anterior(min(r["orden"] for r in nuevas))
    return filas


_GENERICOS = {"ordenanza", "ordenanzas", "reglamento", "reglamentos", "tasa", "tasas",
              "ordenanzafiscal", "ordenanzasfiscales", "ordenanzamunicipal"}


def _raiz(w):
    """«terrazas» → «terraza», «animales» → «animal» (BOTHA casa por subcadena)."""
    w = B._mnorm(w).strip()
    if w.endswith("es") and len(w) > 5:
        return w[:-2]
    if w.endswith("s") and len(w) > 4:
        return w[:-1]
    return w


def _alias_tesauro(texto, fuera):
    """Sinónimos de UNA palabra del tesauro del motor, en su orden (el primero es
    el principal: «basura» para residuos, «velador» para terrazas)."""
    mn = B._mnorm(texto)
    out = []
    for pat, cs, _ss in B._EXPANSION:
        if re.search(pat, mn):
            for a in cs:
                r = _raiz(a)
                if " " not in a and len(r) >= 4 and r not in fuera and r not in out:
                    out.append(r)
    return out[:2]


def buscar(prov, texto, filtro, rpp=40):
    """Anuncios de ordenanzas/reglamentos del ayuntamiento `filtro` (código del
    desplegable) para la materia `texto`. Orden del boletín = fecha desc; el motor
    ranquea por título en local. Claves privadas: file, pdf, boletin."""
    cfg = B.PROVINCIAS[prov]
    if not filtro:
        return []
    texto = (texto or "").strip() or "ordenanza"
    rpp = max(1, int(rpp or 40))
    campo_t, campo_x = cfg.get("campo_titulo", "tbResumen"), cfg.get("campo_texto", "tbAnuncio")

    # 1) CVE propio: BOP-VI-AAAA-N → número de anuncio
    m = re.search(r"BOP-VI-(\d{4})-(\d{1,6})", texto, re.I)
    if m:
        try:
            return _consulta(cfg, filtro, "", "", 10, False,
                             {"tbAnioAnun": m.group(1), "tbNAnun": m.group(2)}, 1, solo_cve=True)
        except Exception:  # noqa: BLE001
            return []

    # 2) volcado genérico («ordenanza», «reglamento», «tasa»): por título
    tn = B._norm(texto)
    if tn in _GENERICOS:
        base = "reglamento" if tn.startswith("reglamento") else ("tasa" if tn.startswith("tasa") else "ordenanza")
        try:
            return _consulta(cfg, filtro, campo_t, base, rpp, False, None, _PAGINAS_GENERICO)
        except Exception:  # noqa: BLE001
            return []

    # 3) materia: los 1-2 términos MÁS DISTINTIVOS del abogado en el TÍTULO (raíz,
    #    subcadena) + los dos en AND en el TEXTO ÍNTEGRO (recall: «vados» aparece en
    #    la ordenanza del espacio público aunque el título no lo diga; con un solo
    #    término el texto íntegro trae ordenanzas que solo lo mencionan de paso).
    #    Si nada casa en el título, sinónimos del tesauro («basura» para residuos).
    raw, _core, _soft = B._familias(texto)
    terms = []
    for w in sorted(raw, key=len, reverse=True):
        r = _raiz(w)
        if len(r) >= 4 and r not in terms:
            terms.append(r)
    terms = terms[:2]
    if not terms:
        r = _raiz(texto)
        terms = [r] if len(r) >= 3 else ["ordenanza"]
    tareas = [(campo_t, t) for t in terms] + [(campo_x, " ".join(terms))]

    def una(t):
        try:
            return _consulta(cfg, filtro, t[0], t[1], rpp, True)
        except Exception:  # noqa: BLE001
            return []

    with _cf.ThreadPoolExecutor(max_workers=min(3, len(tareas))) as ex:
        resultados = list(ex.map(una, tareas))
    vistos, out = set(), []

    def anade(rs):
        for r in rs:
            if r["cve"] not in vistos:
                vistos.add(r["cve"])
                out.append(r)

    for rs in resultados:
        anade(rs)
    en_titulo = sum(len(r) for r in resultados[:len(terms)])
    extra = []
    if not en_titulo:
        extra += [(campo_t, a) for a in _alias_tesauro(texto, set(terms))]
    if len(terms) > 1 and not resultados[-1]:
        extra.append((campo_x, terms[0]))
    if extra:
        with _cf.ThreadPoolExecutor(max_workers=min(3, len(extra))) as ex:
            for rs in ex.map(una, extra):
                anade(rs)
    return out


# ---- lectura ------------------------------------------------------------------
def _div(h, did):
    """Contenido del <div id=did> respetando los div anidados."""
    i = h.find('id="%s"' % did)
    if i < 0:
        i = h.find("id='%s'" % did)
    if i < 0:
        return ""
    j = h.find(">", i) + 1
    d, k = 1, j
    while d > 0 and k < len(h):
        a, b = h.find("<div", k), h.find("</div>", k)
        if b < 0:
            break
        if 0 <= a < b:
            d += 1
            k = a + 4
        else:
            d -= 1
            k = b + 6
    return h[j:k - 6]


def texto(prov, m):
    """(texto castellano íntegro, 'html') del anuncio; ("", "sin-texto") si no hay."""
    cfg = B.PROVINCIAS[prov]
    u = m.get("url") if isinstance(m, dict) else m
    if not u:
        return "", "sin-url"
    if u.lower().endswith(".pdf"):
        try:
            t, _via = B._pdf_bytes_texto(B._getb(u, 25), ocr=False)
            return (t, "pdf") if len(t) > 200 else ("", "sin-texto")
        except Exception:  # noqa: BLE001
            return "", "sin-texto"
    h = ""
    for intento in range(2):
        try:
            h = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": _UA}),
                                       timeout=25).read().decode("utf-8", "replace")
            break
        except Exception:  # noqa: BLE001
            if intento:
                return "", "sin-texto"
            time.sleep(1.0)
    x = _div(h, cfg.get("div_castellano", "detalle_cast")) or _div(h, "detalle_eus")
    if not x:
        return "", "sin-texto"
    x = re.sub(r"(?is)<head>.*?</head>", " ", x)
    t = B._html_a_texto(x)
    # menú y cabecera del visor («PDF», «Texto bilingüe», «Imprimir», «Publicado
    # en: BOTHA Num…», «Referencia:», «Otros formatos:»): fuera
    lineas = t.split("\n")
    corte = 0
    for i, ln in enumerate(lineas[:20]):
        s = ln.strip()
        if s and not re.match(r"(?i)^(pdf|texto biling[üu]e|imprimir|otros formatos:?|"
                              r"publicado en:.*|referencia:.*)$", s):
            corte = i
            break
    t = "\n".join(lineas[corte:]).strip()
    return (t, "html") if len(t) > 200 else ("", "sin-texto")
