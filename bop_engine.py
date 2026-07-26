# -*- coding: utf-8 -*-
"""
Motor de ORDENANZAS por BOLETÍN OFICIAL DE LA PROVINCIA (BOP) — conector
Jurisprudenciator. Cubre CUALQUIER ayuntamiento de una provincia buscando en su
BOP (plataforma OpenCms/Saga, común a casi todos los BOP de España).

Provincia piloto: SEVILLA (bopsevilla.dipusevilla.es, ~108 municipios).

Claves técnicas (verificadas 10-jul-2026):
  * El buscador full-text lleva reCAPTCHA v2 en el FRONTEND, pero el backend
    Solr NO valida el token: un POST a SagaListado-element.jsp con cookies de
    sesión frescas devuelve resultados (~0.5 s). No se toca el captcha.
  * Filtro por municipio = categoría `entidades/.../Ayuntamientos/<Norm>/`
    (mapa empaquetado en ordenanzas_data/bop_<prov>_municipios.json).
  * Los PDFs de anuncio son MIXTOS: ~60 % con capa de texto (fitz directo,
    gratis) y ~40 % con fuente sin ToUnicode (glyph-ids) → OCR (visión
    OpenAI gpt-4o-mini con fallback Gemini, páginas en PARALELO).
  * NO hay texto consolidado: el BOP publica cada ordenanza/modificación al
    aprobarse; se cita con su CVE y fecha de publicación.

Sin dependencias nuevas (fitz/pypdf/urllib/json/base64 ya presentes).
"""
import base64
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
import http.cookiejar

_SSL_NOVERIFY = ssl._create_unverified_context()

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:  # noqa: BLE001
    _HAS_FITZ = False

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "ordenanzas_data")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# ---- provincias BOP registradas ------------------------------------------
# Cada una: base, ruta del buscador, y el JSON con el mapa municipio->categoría.
PROVINCIAS = {
    "sevilla": {
        "base": "https://bopsevilla.dipusevilla.es",
        "resultados": "/publica/buscador-anuncios/resultados-anuncios/",
        "anuncio_pdf": "Documentos-Anuncios-en-PDF",
        "anuncio_href": "/publica/buscador-anuncios/anuncio/",
        "mapa": "bop_sevilla_municipios.json",
        "nombre": "Sevilla",
        "indice_desde": 2022,
    },
}


def _cargar_provincias():
    """Registra provincias adicionales desde ordenanzas_data/bop_<id>_config.json
    (las escribe la sonda _probe_provincia.py al validar cada BOP)."""
    try:
        import glob as _glob
        for fp in _glob.glob(os.path.join(_DATA, "bop_*_config.json")):
            try:
                cfg = json.load(open(fp, encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            pid = cfg.get("id") or os.path.basename(fp)[4:-12]
            if not pid or pid in PROVINCIAS or not cfg.get("base") or not cfg.get("mapa"):
                continue
            if cfg.get("activo") is False:
                continue          # provincia implementada pero NO servida (ver nota en su config)
            cfg.setdefault("resultados", "/publica/buscador-anuncios/resultados-anuncios/")
            cfg.setdefault("anuncio_pdf", "Documentos-Anuncios-en-PDF")
            cfg.setdefault("anuncio_href", "/publica/buscador-anuncios/anuncio/")
            cfg.setdefault("nombre", pid.title())
            PROVINCIAS[pid] = cfg
    except Exception:  # noqa: BLE001
        pass


_cargar_provincias()


def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


# ---- mapa de municipios (empaquetado) ------------------------------------
_MAPAS = {}      # provincia -> {nombre_legible: categoria}
_IDX = {}        # provincia -> {nombre_norm: categoria}
_NOMBRES = {}    # provincia -> {nombre_norm: nombre limpio legible}
_MUNI2PROV = {}  # nombre_norm -> provincia (para resolver "municipio" a secas)
_MAPAS_LOCK = threading.Lock()

# prefijos de entidad en las facets de algunos BOP ("AYUNTAMIENTO DE BAZA", "CONCELLO DE...")
_PREF_ENT = re.compile(r"^(?:EXCMO\.?\s+)?(?:AYUNTAMIENTO|AYTO\.?|CONCELLO|CONCEJO|"
                       r"AJUNTAMENT|UDALA)\s+(?:DE\s+LA\s+|DE\s+L'|DEL?\s+|D')?", re.I)
# entidades NO municipales: se indexan igual pero con menor prioridad
_ENT_MENOR = re.compile(r"junta vecinal|mancomunidad|consorcio|entidad local|diputaci|"
                        r"comarca|pedan[ií]a|e\.?l\.?m\.?", re.I)


def _limpia_nombre(k):
    return _PREF_ENT.sub("", k.strip()).strip()


def _cargar_mapas():
    """Carga ATÓMICA: se construye en diccionarios locales y solo al final se
    publican. Si se rellenara `_MAPAS` sobre la marcha, otro hilo que entre a la
    vez vería el guard `if _MAPAS` a medio construir y se quedaría con un índice
    incompleto (provincia_de → None para media España)."""
    if _MAPAS:
        return
    with _MAPAS_LOCK:
        if _MAPAS:
            return
        mapas, idx, nombres, m2p = {}, {}, {}, {}
        for prov, cfg in PROVINCIAS.items():
            try:
                m = json.load(open(os.path.join(_DATA, cfg["mapa"]), encoding="utf-8"))
            except Exception:  # noqa: BLE001
                m = {}
            # algunos listados de entidades traen basura (p.ej. el BOP de Cáceres lista
            # "Lepe", que es de Huelva). La config puede declarar `excluir`.
            excl = {_norm(x) for x in cfg.get("excluir", [])}
            if excl:
                m = {k: v for k, v in m.items() if _norm(k) not in excl}
            mapas[prov] = m
            idx[prov] = {}
            nombres[prov] = {}
            # primero los municipios (ayuntamientos); después juntas vecinales, etc.
            claves = sorted(m, key=lambda k: bool(_ENT_MENOR.search(k)))
            for k in claves:
                limpio = _limpia_nombre(k)
                kn = _norm(limpio)
                if not kn:
                    continue
                idx[prov].setdefault(kn, m[k])
                nombres[prov].setdefault(kn, limpio if limpio.upper() != limpio else limpio.title())
                m2p.setdefault(kn, prov)
        _IDX.update(idx)
        _NOMBRES.update(nombres)
        _MUNI2PROV.update(m2p)
        _MAPAS.update(mapas)          # el guard, SIEMPRE el último


def _parse_muni(municipio):
    """Admite 'Baza', 'Baza (Granada)' o 'Baza, Granada' -> (muni, prov_id|None)."""
    m = re.match(r"^\s*(.*?)\s*[,(]\s*([\wÁÉÍÓÚÜÑáéíóúüñ .\-]+?)\s*\)?\s*$", municipio or "")
    if m:
        cand = _norm(m.group(2))
        for pid, cfg in PROVINCIAS.items():
            if cand == _norm(pid) or cand == _norm(cfg.get("nombre", "")):
                return m.group(1), pid
    return (municipio or "").strip(), None


def provincia_de(municipio):
    """Devuelve la provincia cuyo BOP cubre el municipio, o None."""
    _cargar_mapas()
    muni, pid = _parse_muni(municipio)
    if pid:
        return pid if _norm(muni) in _IDX.get(pid, {}) else None
    return _MUNI2PROV.get(_norm(muni))


def _categoria(prov, municipio):
    _cargar_mapas()
    muni, _ = _parse_muni(municipio)
    return _IDX.get(prov, {}).get(_norm(muni))


def municipios_cubiertos():
    _cargar_mapas()
    return sorted({p for p in PROVINCIAS})


# ---- sesión Solr (cacheada por provincia, en memoria del proceso) ---------
_SES = {}  # prov -> (opener, params, timestamp)


def _sesion(prov):
    cfg = PROVINCIAS[prov]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA), ("Accept-Language", "es-ES,es")]
    page = op.open(cfg["base"] + cfg["resultados"], timeout=20).read().decode("utf-8", "replace")
    j = page.find("urlAjax"); ini = page.rfind("{", 0, j); fin = page.find("};", ini)
    params = {}
    for m in re.finditer(r"(\w+)\s*:\s*(?:'([^']*)'|\"([^\"]*)\"|([\w.\-]+))", page[ini + 1:fin]):
        params[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    _SES[prov] = (op, params, time.time())
    return _SES[prov]


def _get_sesion(prov):
    s = _SES.get(prov)
    if s and time.time() - s[2] < 600:   # reusar 10 min
        return s
    return _sesion(prov)


def _getb(url, timeout=40):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=timeout).read()


# ---- búsqueda: DESPACHO POR FAMILIA DE PLATAFORMA -------------------------
# Cada provincia trae "familia" en su config (default "saga"). El motor genérico
# (ranking, pasajes, mensaje honesto) es común; solo cambia el BACKEND que
# produce la lista de anuncios [{url,titulo,cve,fecha,orden}] y el texto de uno.
def _buscar_raw(prov, texto, categoria=None, rpp=40, timeout=20):
    fam = PROVINCIAS[prov].get("familia", "saga")
    if fam == "caceres":
        return _caceres_buscar(prov, texto, categoria, rpp)
    if fam == "toledo":
        return _toledo_buscar(prov, texto, categoria, rpp)
    if fam == "huelva":
        return _huelva_buscar(prov, texto, categoria, rpp)
    if fam == "murcia":
        return _murcia_buscar(prov, texto, categoria, rpp)
    if fam == "alicante":
        return _alicante_buscar(prov, texto, categoria, rpp)
    if fam == "jaen":
        return _jaen_buscar(prov, texto, categoria, rpp)
    if fam == "malaga":
        return _malaga_buscar(prov, texto, categoria, rpp)
    if fam == "cadiz":
        return _cadiz_buscar(prov, texto, categoria, rpp)
    if fam == "madrid":
        return _madrid_buscar(prov, texto, categoria, rpp)
    if fam == "acoruna":
        return _acoruna_buscar(prov, texto, categoria, rpp)
    if fam == "pontevedra":
        return _pontevedra_buscar(prov, texto, categoria, rpp)
    if fam == "tenerife":
        return _tenerife_buscar(prov, texto, categoria, rpp)
    if fam == "bizkaia":
        return _bizkaia_buscar(prov, texto, categoria, rpp)
    if fam == "gipuzkoa":
        return _gipuzkoa_buscar(prov, texto, categoria, rpp)
    if fam == "laspalmas":
        return _laspalmas_buscar(prov, texto, categoria, rpp)
    if fam == "tarragona":
        return _tarragona_buscar(prov, texto, categoria, rpp)
    return _saga_buscar_raw(prov, texto, categoria, rpp, timeout)


def _texto(prov, m, ocr=True, max_pag=10):
    """(texto, via) del anuncio m, según la familia de la provincia."""
    fam = PROVINCIAS[prov].get("familia", "saga")
    if fam == "caceres":
        return _caceres_texto(prov, m)
    if fam == "toledo":
        return _toledo_texto(prov, m)
    if fam == "huelva":
        return _huelva_texto(prov, m)
    if fam == "murcia":
        return _murcia_texto(prov, m)
    if fam == "alicante":
        return _alicante_texto(prov, m)
    if fam == "jaen":
        return _jaen_texto(prov, m)
    if fam == "malaga":
        return _malaga_texto(prov, m)
    if fam == "cadiz":
        return _cadiz_texto(prov, m)
    if fam == "madrid":
        return _madrid_texto(prov, m)
    if fam == "acoruna":
        return _acoruna_texto(prov, m)
    if fam == "pontevedra":
        return _pontevedra_texto(prov, m)
    if fam == "tenerife":
        return _tenerife_texto(prov, m)
    if fam == "bizkaia":
        return _bizkaia_texto(prov, m)
    if fam == "gipuzkoa":
        return _gipuzkoa_texto(prov, m)
    if fam == "laspalmas":
        return _laspalmas_texto(prov, m)
    if fam == "tarragona":
        return _tarragona_texto(prov, m)
    return _saga_texto(prov, m["url"] if isinstance(m, dict) else m, ocr, max_pag)


# ---- backend TARRAGONA (BOPT, app Symfony de la Diputació) -------------------
# GET simple, sin sesión ni CSRF. Dos avisos medidos: (1) sin fechas la consulta
# tarda 18 s y con rango amplio 11 s -> se busca primero en ventana reciente
# (3,5-5 s) y solo se amplía si no hay nada; (2) el boletín está en CATALÁN.
_TG_ROW = re.compile(
    r'<h3 class="card-title[^"]*"><a href="(/bopt/web/anunci/(\d+)/[^"]+)"[^>]*>([^<]*)</a></h3>\s*'
    r"<p>(.*?)</p>.*?Registre</span>:\s*([\w-]+).*?Data de publicaci[óo]</span>:\s*(\d{2}/\d{2}/\d{4})", re.S)
_CATALA = {"residuos": "residus", "residuo": "residus", "basura": "escombraries",
           "basuras": "escombraries", "limpieza": "neteja", "ruido": "soroll",
           "ruidos": "sorolls", "animales": "animals", "animal": "animals",
           "terrazas": "terrasses", "terraza": "terrassa", "vados": "guals", "vado": "gual",
           "movilidad": "mobilitat", "circulación": "circulació", "tasa": "taxa",
           "tasas": "taxes", "venta": "venda", "ambulante": "ambulant", "agua": "aigua",
           "aguas": "aigües", "obras": "obres", "mercado": "mercat", "mercados": "mercats",
           "cementerio": "cementiri", "vivienda": "habitatge", "viviendas": "habitatges",
           "civismo": "civisme", "convivencia": "convivència", "ordenanza": "ordenança",
           "reglamento": "reglament", "licencia": "llicència", "licencias": "llicències",
           "saneamiento": "sanejament", "urbanismo": "urbanisme", "playas": "platges",
           "estacionamiento": "estacionament", "subvenciones": "subvencions"}


def _tarragona_buscar(prov, texto, muni=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not muni:
        return []
    hoy = time.gmtime()
    fin = f"{hoy.tm_mday:02d}-{hoy.tm_mon:02d}-{hoy.tm_year}"
    consultas = _consultas_materia(texto, "ca")

    def una(args):
        q, ini = args
        p = {"bopb_cerca[paraulaClau]": q, "bopb_cerca[dataInici]": ini,
             "bopb_cerca[dataFinal]": fin, "bopb_cerca[tipologiaAnunciant]": str(muni)}
        try:
            h = _madrid_get(cfg["base"] + "/bopt/web/resultats-cerca?" + urllib.parse.urlencode(p),
                            timeout=40, intentos=1)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for href, ident, _org, tit, reg, fecha in _TG_ROW.findall(h):
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit))).strip()
            d, mo, y = fecha.split("/")
            out.append({"url": cfg["base"] + href, "titulo": t, "cve": reg,
                        "fecha": fecha, "orden": f"{y}{mo}{d}", "id": ident,
                        "materia": q not in ("ordenanza", "ordenança")})
        return out

    reciente = f"01-01-{hoy.tm_year - 8}"
    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=min(4, len(consultas))) as ex:
        for rs in ex.map(una, [(q, reciente) for q in consultas]):
            for r in rs:
                vistos.setdefault(r["cve"], r)
    if not vistos:                      # nada reciente: se amplía a todo el índice
        for r in una((consultas[0], f"01-01-{cfg.get('indice_desde', 2010)}")):
            vistos.setdefault(r["cve"], r)
    return list(vistos.values())


def _tarragona_texto(prov, m):
    cfg = PROVINCIAS[prov]
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        h = _madrid_get(u, timeout=30, intentos=1)
        cuerpo = re.search(r'(?s)<div class="card-body">(.*?)</div>\s*</div>', h)
        t = _html_a_texto(cuerpo.group(1) if cuerpo else h)
        if len(t) > 600:
            return t, "html"
    except Exception:  # noqa: BLE001
        pass
    ident = m.get("id") if isinstance(m, dict) else None
    if ident:
        try:
            pdf = _getb(f"{cfg['base']}/bopt/web/anunci/descarrega-pdf/{ident}", timeout=45)
            if pdf[:5] == b"%PDF-":
                return _pdf_bytes_texto(pdf)
        except Exception:  # noqa: BLE001
            pass
    return "", "sin-texto"


# ---- backend LAS PALMAS (nbop2: misma app PHP legada que Tenerife) -----------
# Diferencias con Tenerife: (1) la URL del PDF NO se puede construir (el slug del
# día lleva ceros de forma inconsistente) -> se usa el href del resultado;
# (2) el sumario no trae nº de página, el anuncio CIERRA con su nº de registro
# escrito con punto de millar; (3) el endpoint de sección es searchad.php.
_LP_ROW = re.compile(
    r"<b>(?P<sec>[IVX]+\.[^<]*)</b>\s*<br\s*/?>\s*<b>(?P<org>[^<]*)</b>\s*<br\s*/?>\s*"
    r"(?P<tit>.*?)\s*<br\s*/?>\s*Publicado en el Bolet[ií]n n[uú]mero:\s*(?P<num>\d+)\s*"
    r"de fecha:\s*(?P<fecha>\d{1,2}-\d{1,2}-\d{2})\.\s*"
    r'<a href="(?P<pdf>[^"]+\.pdf)"', re.S)
# el listado de organismos viene MUY sucio (erratas, prefijos, falta el "DE")
_LP_PREF = re.compile(r"^\s*(?:EXCMO\.?|EXCMA\.?|ILUSTR[IÍ]SIMO|ILUSTRE\.?|IILUSTRE|LUSTRE|"
                      r"M\.?\s*I\.?|M\.?\s*ILUSTRE)?\s*A?YUNTAMIENTOS?\s*(?:DE\s+|DEL\s+)?", re.I)
_LP_ALIAS = {"sannicolasdetolentino": "laaldeadesannicolas", "sanmateo": "vegadesanmateo",
             "lavegadesanmateo": "vegadesanmateo", "santalucia": "santaluciadetirajana",
             "santamariadeguiadegrancanaria": "santamariadeguia",
             "valsequillodegrancanaria": "valsequillo", "artenera": "artenara",
             "tede": "telde", "arrrecife": "arrecife", "satalucia": "santaluciadetirajana",
             "puertoderosario": "puertodelrosario"}


def _lp_clave(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).upper()
    s = re.sub(r"\s+", " ", s)
    s = _LP_PREF.sub("", s)
    k = re.sub(r"[^A-Z0-9]+", "", s).lower()
    return _LP_ALIAS.get(k, k)


def _laspalmas_buscar(prov, texto, organismo=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not organismo:
        return []
    ck = _lp_clave(organismo)
    anyos = [time.gmtime().tm_year - i for i in range(int(cfg.get("anyos", 10)))]
    consultas = _consultas_materia(texto, None)[:2] or ["ordenanza"]
    tareas = [(a, q) for a in anyos for q in dict.fromkeys(consultas + ["ordenanza"])]

    def uno(t):
        a, q = t
        # el buscador ignora acentos y con iso-8859-1 devuelve 0: ASCII sin tildes
        qa = "".join(c for c in unicodedata.normalize("NFKD", q) if not unicodedata.combining(c))
        try:
            body = urllib.parse.urlencode({"clave": qa, "ayo": str(a), "pub": "1",
                                           "admi": "3", "BUSCAADM": "IR"}).encode("utf-8")
            raw = urllib.request.urlopen(urllib.request.Request(
                cfg["base"] + "/nbop2/searchad.php", data=body,
                headers={"User-Agent": _UA,
                         "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}),
                timeout=25).read()
            try:
                h = raw.decode("utf-8")
            except UnicodeDecodeError:
                h = raw.decode("iso-8859-1", "replace")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for m in _LP_ROW.finditer(h):
            if _lp_clave(_html.unescape(m.group("org"))) != ck:
                continue
            tit = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group("tit")))).strip()
            d, mo, y = m.group("fecha").split("-")
            iso = f"20{y}-{int(mo):02d}-{int(d):02d}"
            pdf = m.group("pdf").replace("../", "/")
            out.append({"url": (cfg["base"] + pdf) if pdf.startswith("/") else pdf,
                        "titulo": tit, "cve": f"BOP-GC-{iso.replace('-', '')}-{m.group('num')}",
                        "fecha": f"{int(d):02d}/{int(mo):02d}/20{y}", "orden": iso.replace("-", ""),
                        "iso": iso, "materia": q != "ordenanza"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        for rs in ex.map(uno, tareas):
            for r in rs:
                k = r["titulo"][:80] + r["iso"]
                if k in vistos:
                    vistos[k]["materia"] = vistos[k].get("materia") or r["materia"]
                else:
                    vistos[k] = r
    return list(vistos.values())


def _laspalmas_texto(prov, m):
    if not _HAS_FITZ or not isinstance(m, dict) or not m.get("url"):
        return "", "sin-fitz"
    try:
        doc = fitz.open(stream=_getb(m["url"], timeout=90), filetype="pdf")
    except Exception:  # noqa: BLE001
        return "", "sin-boletin"
    obj = {w for w in re.sub(r"[^a-z0-9 ]+", " ", _mnorm(m["titulo"])).split() if len(w) > 3}
    mejor_i, mejor_sc = None, 0.0
    for i in range(min(doc.page_count, 250)):
        pg = set(re.sub(r"[^a-z0-9 ]+", " ", _mnorm(doc[i].get_text())).split())
        sc = len(obj & pg) / max(1, len(obj))
        if sc > mejor_sc:
            mejor_sc, mejor_i = sc, i
    if mejor_i is None or mejor_sc < 0.55:
        return "", "no-localizado"
    txt = "".join(doc[i].get_text() for i in range(mejor_i, min(mejor_i + 25, doc.page_count)))
    # el anuncio CIERRA con su nº de registro escrito con punto de millar (216.586)
    fin = re.search(r"(?m)^\s*\d{1,3}\.\d{3}\s*$", txt[400:])
    if fin:
        txt = txt[:400 + fin.start()]
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return (txt, "pdf") if len(txt) > 200 else ("", "sin-texto")


# ---- backend GIPUZKOA (BOG, Liferay + portlet LEEboletinOficial) -------------
# Búsqueda avanzada con token p_auth (cacheado) y filtro por id numérico de
# ayuntamiento. La lectura es gratis: el enlace de detalle lleva embebida la URL
# del PDF y, cambiando .pdf por .htm, se obtiene el anuncio en HTML (sin OCR).
_GK_P = "_BoletinOficial_WAR_LEEboletinOficialportlet_"
_GK_SES = {}
_GK_PDF = re.compile(r"_pdf=([^&\"']+?\.pdf)")
_GK_FILA = re.compile(r"<tr[^>]*>\s*<td>\s*<h2>\s*<a\s+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)


def _gk_sesion(prov):
    s = _GK_SES.get(prov)
    if s and time.time() - s[2] < 600:
        return s
    cfg = PROVINCIAS[prov]
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPSHandler(context=_SSL_NOVERIFY))   # cadena TLS incompleta
    op.addheaders = [("User-Agent", _UA), ("Accept-Language", "es-ES,es")]
    h = op.open(f"{cfg['base']}/es/bog?p_p_id=BoletinOficial_WAR_LEEboletinOficialportlet"
                f"&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view&{_GK_P}myaction=boletinBusqueda",
                timeout=30).read().decode("utf-8", "replace")
    tok = re.search(r"myaction=submitBusquedaAvanzada[^\"]*p_auth=(\w+)", h)
    if not tok:
        raise RuntimeError("BOG: no encuentro p_auth")
    _GK_SES[prov] = (op, tok.group(1), time.time())
    return _GK_SES[prov]


def _gipuzkoa_buscar(prov, texto, organo=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not organo:
        return []
    op, tok, _ = _gk_sesion(prov)
    url = (f"{cfg['base']}/es/bog?p_p_id=BoletinOficial_WAR_LEEboletinOficialportlet"
           f"&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view"
           f"&{_GK_P}myaction=submitBusquedaAvanzada&p_auth={tok}")

    def una(q):
        d = {_GK_P + "inputTexto": q, _GK_P + "inputOrgano": str(organo),
             _GK_P + "inputSeccion": "10"}
        try:
            h = op.open(urllib.request.Request(
                url, data=urllib.parse.urlencode(d).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"}),
                timeout=30).read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for href, tit in _GK_FILA.findall(h):
            mp = _GK_PDF.search(href)
            if not mp:
                continue
            pdf = urllib.parse.unquote(mp.group(1))
            f = re.search(r"/bog/(\d{4})/(\d{2})/(\d{2})/", pdf)
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit))).strip()
            out.append({"url": pdf, "titulo": t,
                        "cve": pdf.rsplit("/", 1)[-1].replace(".pdf", ""),
                        "fecha": f"{f.group(3)}/{f.group(2)}/{f.group(1)}" if f else "",
                        "orden": (f.group(1) + f.group(2) + f.group(3)) if f else "0",
                        "materia": q != "ordenanza"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        for rs in ex.map(una, _consultas_materia(texto, None)):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    return list(vistos.values())


def _gipuzkoa_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:                       # versión HTML del mismo anuncio: sin PDF ni OCR
        raw = urllib.request.urlopen(urllib.request.Request(
            u[:-4] + ".htm", headers={"User-Agent": _UA}),
            timeout=25, context=_SSL_NOVERIFY).read()
        t = _html_a_texto(raw.decode("iso-8859-15", "replace"))
        if len(t) > 200:
            return t, "html"
    except Exception:  # noqa: BLE001
        pass
    try:
        pdf = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": _UA}),
                                     timeout=40, context=_SSL_NOVERIFY).read()
        if pdf[:5] == b"%PDF-":
            return _pdf_bytes_texto(pdf)
    except Exception:  # noqa: BLE001
        pass
    return "", "sin-texto"


# ---- backend BIZKAIA (BOB, Liferay + portlet IYBIWBCC) -----------------------
# Sin captcha ni cookies (IR SIN SESIÓN: con cookiejar compartido las búsquedas
# concurrentes se contaminan entre sí). Filtro por municipio = nombres de emisor
# ENTRECOMILLADOS unidos por " o " (los códigos numéricos no filtran).
# Busca en el TEXTO ÍNTEGRO, no en el título -> mucho recall y ranking pésimo:
# el orden lo pone el motor genérico. Corte de formato en 2017: antes, boletín
# completo con #page=; después, PDF por anuncio (_cas = castellano).
_BZ_ROW = re.compile(r'<li class="row">.*?numberbob">([^<]+)</p>.*?fechabob">([^<]+)</p>.*?'
                     r'<div class="col-9 col-sm-7">\s*<p>(.*?)</p>.*?href="([^"]*Bao_bob[^"]*)"', re.S)


def _bizkaia_buscar(prov, texto, emisores=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not emisores:
        return []
    consultas = _consultas_materia(texto, None)

    def una(q):
        p = {"p_p_id": "IYBIWBCC", "p_p_lifecycle": "0", "p_p_state": "normal",
             "p_p_mode": "view", "_IYBIWBCC_mvcRenderCommandName": "/search/filtros",
             "_IYBIWBCC_text": q, "_IYBIWBCC_issuersSelect": emisores,
             "_IYBIWBCC_resetCur": "false", "_IYBIWBCC_delta": "50", "_IYBIWBCC_cur": "1"}
        for a in ("dateFromBol", "dateToBol", "dateFromDisp", "dateToDisp"):
            for b in ("Day", "Month", "Year"):
                p[f"_IYBIWBCC_{a}{b}"] = "0"
        try:
            # bizkaia.eus no encadena bien su certificado: mismo contexto tolerante
            # que ya se usa con Cádiz (boletín público, solo lectura)
            h = urllib.request.urlopen(urllib.request.Request(
                cfg["base"] + "/es/bob/resultados?" + urllib.parse.urlencode(p),
                headers={"User-Agent": _UA, "Accept-Language": "es-ES,es"}),
                timeout=25, context=_SSL_NOVERIFY).read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for num, fecha, tit, href in _BZ_ROW.findall(h):
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit))).strip()
            d, mth, y = (fecha.strip().split("/") + ["", "", ""])[:3]
            out.append({"url": href if href.startswith("http") else cfg["base"] + href,
                        "titulo": t, "cve": re.sub(r"\s+", "", num.strip()) + "-" + href[-24:],
                        "fecha": fecha.strip(),
                        "orden": f"{y}{mth}{d}" if y else "0",
                        "materia": q != "ordenanza"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:      # >3 da respuestas parciales
        for rs in ex.map(una, consultas):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    return list(vistos.values())


def _bizkaia_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    pag = None
    if "#page=" in u:
        u, _, p = u.partition("#page=")
        pag = int(re.sub(r"\D", "", p) or 0) or None
    try:
        pdf = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": _UA}),
                                     timeout=60, context=_SSL_NOVERIFY).read()
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-" or not _HAS_FITZ:
        return "", "sin-pdf"
    if pag is None:                       # 2017+: el PDF ES el anuncio
        t, via = _pdf_bytes_texto(pdf)
        return t, (via or "pdf")
    # ≤2016: boletín completo; el ancla #page apunta al anuncio (verificado)
    try:
        doc = fitz.open(stream=pdf, filetype="pdf")
        i0 = max(0, min(pag - 1, doc.page_count - 1))
        txt = "".join(doc[i].get_text() for i in range(i0, min(i0 + 12, doc.page_count)))
        # cada anuncio cierra con su referencia (II-NNNN): sirve para acotarlo
        fin = re.search(r"\(\s*I{1,3}-\d+\s*\)", txt[300:])
        if fin:
            txt = txt[:300 + fin.end()]
        return re.sub(r"\n{3,}", "\n\n", txt).strip(), "pdf-pagina"
    except Exception:  # noqa: BLE001
        return "", "sin-texto"


# ---- backend SANTA CRUZ DE TENERIFE (app PHP legada "bopsc2") ----------------
# Particularidades: (1) el buscador va AÑO A AÑO y solo mira el SUMARIO (título),
# nunca el cuerpo; (2) NO hay PDF por anuncio: solo el boletín completo del día,
# del que hay que RECORTAR el anuncio por su nº de registro. Con capa de texto
# siempre → cero OCR.
_TF_ROW = re.compile(
    r"<b><i>(?P<org>.*?)</i></b></font><br>\s*"
    r"<font[^>]*>(?P<tit>.*?)</font><br>\s*"
    r"<font[^>]*>Boletin numero (?P<num>\d+) de fecha (?P<fecha>\d{2}-\d{2}-\d{4})\s*:?\s*</font>\s*"
    r'<a href="sumario\.php\?codigopub=(?P<pub>\d+)&fecha_mas_reciente=(?P<iso>\d{4}-\d{2}-\d{2})"', re.S)
_TF_PREF = re.compile(r"^\s*(?:M\.?\s*I\.?\s*)?[AY]*UNTAMIENTO\b\s*(?:DE\s+)?", re.I)
_TF_VILLA = re.compile(r"^\s*(?:LA\s+)?VILLA\s+DE\s+", re.I)
_TF_ART = re.compile(r"^\s*(?:EL|LA|LOS|LAS)\s+", re.I)
_TF_CAB = re.compile(r"Bolet[ií]n Oficial de la Provincia de Santa Cruz de Tenerife\.\s*"
                     r"N[úu]mero\s*\d+[^\n]*\n\s*(\d{3,6})")
_TF_IDX = re.compile(r"(?ms)^[ \t]*(\d{4,9})[\t ]\s*(.+?)\s*\.{4,}[ \t]*(?:\n[ \t]*(\d{3,6})[ \t]*$)?")
_TF_PDF = {}


def _tf_clave(s):
    """Normaliza el organismo del boletín a clave de municipio. El listado viene
    sucio en origen (AAYUNTAMIENTO, VILAFOR, BUENA VISTA…) y hay que absorberlo."""
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).upper()
    s = _TF_PREF.sub("", s)
    s = _TF_VILLA.sub("", s)
    s = re.sub(r"^\s*DE\s+", "", s)
    s = _TF_ART.sub("", s)
    for a, b in ((r"\bY\s+SAUCE\b", "Y SAUCES"), (r"\bBUENA\s+VISTA\b", "BUENAVISTA"),
                 (r"\bVILAFOR\b", "VILAFLOR"), (r"\bFUENCALIENTE$", "FUENCALIENTE DE LA PALMA"),
                 (r"^MAZO$", "VILLA DE MAZO")):
        s = re.sub(a, b, s)
    return re.sub(r"[^A-Z0-9]+", "", s)


def _tenerife_buscar(prov, texto, organismo=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not organismo:
        return []
    ck = _tf_clave(organismo)
    anyos = [time.gmtime().tm_year - i for i in range(int(cfg.get("anyos", 8)))]
    # el buscador solo mira el título: se vuelca "ordenanza" y se rankea en local
    consultas = _consultas_materia(texto, None)[:2] or ["ordenanza"]
    tareas = [(a, q) for a in anyos for q in dict.fromkeys(consultas + ["ordenanza"])]

    def uno(t):
        a, q = t
        try:
            body = urllib.parse.urlencode({"clave": q, "ayo": str(a), "pub": "1",
                                           "admi": "3", "BUSCAADM": "IR"}).encode("utf-8")
            req = urllib.request.Request(
                cfg["base"] + "/bopsc2/search1a.php", data=body,
                headers={"User-Agent": _UA,
                         "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
            raw = urllib.request.urlopen(req, timeout=25).read()
            try:
                h = raw.decode("utf-8")
            except UnicodeDecodeError:
                h = raw.decode("iso-8859-1", "replace")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for m in _TF_ROW.finditer(h):
            if _tf_clave(_html.unescape(m.group("org"))) != ck:
                continue          # el filtro por municipio se hace AQUÍ, en local
            tit = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", m.group("tit")))).strip()
            iso = m.group("iso")
            out.append({"url": f"{cfg['base']}/bopsc2/sumario.php?codigopub={m.group('pub')}"
                               f"&fecha_mas_reciente={iso}",
                        "titulo": tit, "cve": f"BOP-TF-{iso.replace('-', '')}-{m.group('num')}",
                        "fecha": f"{iso[8:]}/{iso[5:7]}/{iso[:4]}",
                        "orden": iso.replace("-", ""), "iso": iso, "materia": q != "ordenanza"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        for rs in ex.map(uno, tareas):
            for r in rs:
                clave = r["titulo"][:80] + r["iso"]
                if clave in vistos:
                    vistos[clave]["materia"] = vistos[clave].get("materia") or r["materia"]
                else:
                    vistos[clave] = r
    return list(vistos.values())


def _tf_bytes(cfg, iso, timeout=90):
    if iso in _TF_PDF:
        return _TF_PDF[iso]
    y, mo, d = iso.split("-")
    s = f"{int(d)}-{int(mo)}-{y[2:]}"
    b = _getb(f"{cfg['base']}/boletines/{y}/{s}/{s}.pdf", timeout=timeout)
    if len(_TF_PDF) > 6:
        _TF_PDF.clear()
    _TF_PDF[iso] = b
    return b


def _tf_numpag(doc, i):
    if i < 0 or i >= doc.page_count:
        return None
    g = _TF_CAB.search(doc[i].get_text()[:400])
    return int(g.group(1)) if g else None


def _tf_pagina(doc, pag):
    """Índice de página del PDF cuyo número impreso es `pag` (paginación continua:
    desplazamiento directo + verificación local, sin escanear todo el boletín)."""
    base = None
    for i in (1, 2, 3):
        base = _tf_numpag(doc, i)
        if base:
            base -= i
            break
    if base is None:
        return None
    i = pag - base
    for j in (0, -1, 1, -2, 2, -3, 3, -4, 4, -6, 6, -10, 10):
        k = i + j
        if 0 <= k < doc.page_count and _tf_numpag(doc, k) == pag:
            return k
    return None


def _tenerife_texto(prov, m):
    if not _HAS_FITZ or not isinstance(m, dict) or not m.get("iso"):
        return "", "sin-fitz"
    cfg = PROVINCIAS[prov]
    try:
        doc = fitz.open(stream=_tf_bytes(cfg, m["iso"]), filetype="pdf")
    except Exception:  # noqa: BLE001
        return "", "sin-boletin"          # hay días cuyo PDF no existe aunque se enlace
    idx = []
    for i in range(min(20, doc.page_count)):
        t = doc[i].get_text()
        ms = [x for x in _TF_IDX.finditer(t) if len(x.group(2)) > 15]
        if not ms and idx:
            break
        for x in ms:
            idx.append((x.group(1), re.sub(r"\s+", " ", x.group(2)),
                        int(x.group(3)) if x.group(3) else None))
    if not idx:
        # Sumario con formato que no reconocemos: se localiza el anuncio buscando
        # su TÍTULO por las páginas del boletín (acotado) en vez de rendirse.
        obj0 = set(re.sub(r"[^a-z0-9 ]+", " ", _mnorm(m["titulo"])).split())
        obj0 = {w for w in obj0 if len(w) > 3}
        mejor_i, mejor_sc = None, 0.0
        for i in range(min(doc.page_count, 200)):
            pg = set(re.sub(r"[^a-z0-9 ]+", " ", _mnorm(doc[i].get_text()[:3000])).split())
            sc = len(obj0 & pg) / max(1, len(obj0))
            if sc > mejor_sc:
                mejor_sc, mejor_i = sc, i
        if mejor_i is None or mejor_sc < 0.6:
            return "", "sin-sumario"
        txt = "".join(doc[i].get_text()
                      for i in range(mejor_i, min(mejor_i + 12, doc.page_count)))
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
        return (txt, "pdf-titulo") if len(txt) > 200 else ("", "sin-texto")
    obj = set(re.sub(r"[^a-z0-9 ]+", " ", _mnorm(m["titulo"])).split())
    mejor, best = None, -1.0
    for k, (_r, tit, _p) in enumerate(idx):
        cand = set(re.sub(r"[^a-z0-9 ]+", " ", _mnorm(tit)).split())
        sc = len(obj & cand) / max(1, len(obj))
        if sc > best:
            best, mejor = sc, k
    if mejor is None or best < 0.45:
        return "", "no-localizado"
    reg, _t, pag = idx[mejor]
    regs = {r for r, _, _ in idx}
    pat = re.compile(r"(?m)^[ \t]*" + re.escape(reg) + r"[ \t]*$")
    i0 = _tf_pagina(doc, pag) if pag else None
    if i0 is None:                       # boletines antiguos: escaneo secuencial
        i0 = next((i for i in range(doc.page_count) if pat.search(doc[i].get_text())), None)
        if i0 is None:
            return "", "no-localizado"
    i1 = min(i0 + 60, doc.page_count - 1)
    sig = next((p for (_r2, _t2, p) in idx[mejor + 1:] if p), None)
    if sig:
        j = _tf_pagina(doc, sig)
        if j is not None:
            i1 = j
    txt = "".join(doc[i].get_text() for i in range(i0, min(i1 + 1, doc.page_count)))
    mm = pat.search(txt)
    if mm:
        txt = txt[mm.end():]
    m2 = re.search(r"(?m)^[ \t]*(\d{4,9})[ \t]*$", txt)
    if m2 and m2.group(1) in regs:
        txt = txt[:m2.start()]
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return (txt, "pdf") if len(txt) > 200 else ("", "sin-texto")


# ---- backend PONTEVEDRA (BOPPO, Liferay + portlet bopv2) ---------------------
# Filtro por concello con el id HOJA del árbol de emisores (el id de la categoría
# del concello da "Selecciona un emisor válido"). Texto y PDF sin OCR. El boletín
# está en GALLEGO: "lixo" 18 resultados vs "residuos" 10 vs "basuras" 0.
_PV_SES = {}      # prov -> (opener, p_auth, portlet, ts)
_PV_ROW = re.compile(
    r'<a class="botDesc"\s+href="([^"]+)"[^>]*title="[^"]*?(\d{2}/\d{2}/\d{4})[^"]*"'
    r'.*?<a href="([^"]+/detalle/[^"]+)"[^>]*>(.*?)</a>', re.S)
_PV_CONT = re.compile(r'(?s)<div[^>]+id="contAnuncio"[^>]*>(.*?)<div[^>]+class="ancla"')


def _pv_sesion(prov):
    s = _PV_SES.get(prov)
    if s and time.time() - s[3] < 600:
        return s
    cfg = PROVINCIAS[prov]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA), ("Accept-Language", "gl-ES,gl;q=0.9,es;q=0.8")]
    h = op.open(cfg["base"] + "/buscas-no-boppo", timeout=25).read().decode("utf-8", "replace")
    tok = re.search(r"p_auth=([\w-]+)", h)
    port = re.search(r"(buscadorbopv2portlet_WAR_bopv2portlet_INSTANCE_\w+)", h)
    if not tok or not port:
        raise RuntimeError("BOPPO: no encuentro p_auth/portlet")
    _PV_SES[prov] = (op, tok.group(1), port.group(1), time.time())
    return _PV_SES[prov]


def _consultas_materia(texto, idioma=None, generico="ordenanza", n=2):
    """Términos a consultar en un buscador LITERAL: los más distintivos de lo que
    pidió el abogado + su forma en la lengua del boletín (los BOP gallegos indexan
    «venda ambulante», no «venta ambulante») + un volcado genérico de respaldo."""
    pal = [w for w in re.split(r"\W+", (texto or "").strip()) if w]
    utiles = [w for w in pal if _norm(w) not in {_norm(x) for x in _STOPM} and len(w) >= 4]
    qs = sorted(utiles, key=len, reverse=True)[:n]
    tabla = {"gl": _GALEGO, "ca": _CATALA, "va": _CATALA}.get(idioma or "")
    if tabla:
        for w in list(qs):
            g = tabla.get(w.lower())
            if g and g.lower() != w.lower():
                qs.append(g)
    qs.append(generico)
    if tabla and tabla.get(generico.lower()):      # "ordenanza" -> "ordenança"/"ordenanza"
        qs.append(tabla[generico.lower()])
    fuera, out = set(), []
    for q in qs:
        if q.lower() not in fuera:
            fuera.add(q.lower())
            out.append(q)
    return out


def _pontevedra_buscar(prov, texto, emisor=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not emisor:
        return []
    # el buscador es LITERAL y el boletín está en gallego: se prueban las formas
    # más distintivas (es/gl) EN PARALELO y se unen los resultados
    consultas = _consultas_materia(texto, cfg.get("idioma"))
    _pv_sesion(prov)                      # abrir sesión una vez, no una por hilo

    def una(q):
        try:
            return _pv_una(prov, q, emisor)
        except Exception:  # noqa: BLE001
            return []

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=min(4, len(consultas))) as ex:
        for rs in ex.map(una, consultas):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    return list(vistos.values())


def _pv_una(prov, q, emisor):
    cfg = PROVINCIAS[prov]
    op, tok, port, _ = _pv_sesion(prov)
    qs = urllib.parse.urlencode({
        "p_auth": tok, "p_p_id": port, "p_p_lifecycle": "1", "p_p_state": "normal",
        "p_p_mode": "view", "p_p_col_id": "column-3", "p_p_col_pos": "1",
        "p_p_col_count": "2", f"_{port}_action": "search"})
    body = urllib.parse.urlencode({
        "order": "SCORE", "orderReverse": "true", "content": q, "emisor": str(emisor),
        "idTipoAnuncio": cfg.get("tipo_anuncio", "9674594"), "bopNumberFrom": "",
        "bopNumberTo": "", "dateFrom": "", "dateTo": ""}).encode()
    req = urllib.request.Request(cfg["base"] + "/buscas-no-boppo?" + qs, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": cfg["base"] + "/buscas-no-boppo", "Origin": cfg["base"]})
    try:
        h = op.open(req, timeout=30).read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        _PV_SES.pop(prov, None)          # token caducado -> una reintentada limpia
        op, tok, port, _ = _pv_sesion(prov)
        return []
    out = []
    for pdf, fecha, det, tit in _PV_ROW.findall(h):
        # el título llega con resaltado (<em>) y precedido de la ruta del emisor
        t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", tit))).strip()
        t = re.sub(r"^ADMINISTRACI[ÓO]N LOCAL\s+Municipal\s+", "", t, flags=re.I).strip()
        t = re.sub(r"^.*?Ordenanzas e Regulamentos\s+", "", t, flags=re.I).strip()
        d, mth, y = fecha.split("/")
        idm = re.search(r"/(\d{6,})/?$", det.rstrip("/"))
        out.append({"url": det if det.startswith("http") else cfg["base"] + det,
                    "titulo": t, "cve": idm.group(1) if idm else det[-24:],
                    "fecha": fecha, "orden": f"{y}{mth}{d}", "materia": q != "ordenanza"})
    return out


def _pontevedra_texto(prov, m):
    u = m.get("url") if isinstance(m, dict) else m
    if not u:
        return "", "sin-url"
    try:
        op, _t, _p, _ = _pv_sesion(prov)
        h = op.open(urllib.request.Request(u, headers={"User-Agent": _UA}),
                    timeout=25).read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return "", "sin-texto"
    mm = _PV_CONT.search(h)
    bruto = mm.group(1) if mm else h
    bruto = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", bruto)   # se cuela CSS si no
    t = _html_a_texto(bruto)
    return (t, "html") if len(t) > 200 else ("", "sin-texto")


# ---- backend A CORUÑA (bopportal: 1 POST de búsqueda + HTML publicado) -------
# Sin captcha, sin sesión y sin OCR: el más barato de todos (búsqueda 0,25 s,
# lectura 0,2 s). Dos particularidades del BOP da Coruña:
#   * `soloSumario=true` busca en el TÍTULO (con false, el full-text del PDF da
#     34.000 resultados para "ordenanza" y es inservible);
#   * el índice es BILINGÜE: "regulamento"≡"reglamento", "lixo"≡"residuos"… hay
#     que consultar las dos formas y unir, porque devuelven cosas distintas.
_ACO_ROW = re.compile(r'href="/bopportal/descargarPdf\?page=\d+&(?:amp;)?fecha=(\d{8})'
                      r'&(?:amp;)?numRegistro=([\d/]+)\.pdf"[^>]*>\s*(.*?)\s*</a>', re.S)
_GALEGO = {"residuos": "lixo", "residuo": "lixo", "basura": "lixo", "basuras": "lixo",
           "limpieza": "limpeza", "reglamento": "regulamento", "animales": "animais",
           "animal": "animais", "movilidad": "mobilidade", "ruido": "ruído",
           "ruidos": "ruído", "vehículos": "vehículos", "aguas": "augas", "agua": "auga",
           "obras": "obras", "vado": "vao", "vados": "vaos", "mercado": "mercado",
           "cementerio": "cemiterio", "circulación": "circulación", "tenencia": "tenza",
           "tasa": "taxa", "tasas": "taxas", "saneamiento": "saneamento",
           "abastecimiento": "abastecemento", "licencia": "licenza", "licencias": "licenzas",
           "urbanismo": "urbanismo", "convivencia": "convivencia", "ruidos": "ruídos",
           "protección": "protección", "ambulante": "ambulante", "mercados": "mercados",
           "subvenciones": "subvencións", "transparencia": "transparencia",
           "participación": "participación", "patrimonio": "patrimonio",
           "terrazas": "terrazas", "veladores": "veladores", "aparcamiento": "aparcamento",
           "venta": "venda", "procedimiento": "procedemento", "construcciones": "construcións",
           "huertos": "hortas", "huerto": "horta", "electrónica": "electrónica",
           "administración": "administración", "vivienda": "vivenda", "viviendas": "vivendas",
           "playas": "praias", "playa": "praia", "consumo": "consumo", "comercio": "comercio",
           "alcantarillado": "sumidoiros", "escuela": "escola", "deportes": "deportes"}


def _acoruna_buscar(prov, texto, ids=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not ids:
        return []
    # varios anunciantes por municipio: A Coruña capital tiene la historia partida
    # en 11 ids (el raíz solo cubre 2020→hoy); con uno solo se pierde el 60 %.
    lista = [i.strip() for i in str(ids).split(",") if i.strip()]
    pal = [w for w in re.split(r"\W+", (texto or "").strip()) if w]
    utiles = [w for w in pal if _norm(w) not in {_norm(x) for x in _STOPM} and len(w) >= 4]
    consultas = sorted(utiles, key=len, reverse=True)[:2] or ["ordenanza"]
    for w in list(consultas):                       # variante en gallego
        g = _GALEGO.get(w.lower())
        if g and g not in consultas:
            consultas.append(g)
    consultas.append("ordenanza")
    tareas = [(i, q) for i in lista for q in dict.fromkeys(consultas)]

    def una(t):
        idp, q = t
        d = {"numPag": "1", "tipoBusqueda": "avanzada", "esPortalSN": "S", "texto": q,
             "soloSumario": "true", "idProcedente": idp, "procedente": "", "hProcedente": "",
             "idPortador": "", "hPortador": "", "idPagadora": "", "hPagadora": "",
             "primeraVez": "", "ficheroDoc": "", "numeroBoletinIni": "", "numeroBoletinFin": "",
             "fechapublicacionIni": "", "fechapublicacionFin": "", "especie": "", "idEspecie": ""}
        try:
            req = urllib.request.Request(
                cfg["base"] + "/bopportal/realizarBusqueda",
                data=urllib.parse.urlencode(d).encode(),
                headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded"})
            h = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for fecha, reg, tit in _ACO_ROW.findall(h):
            t2 = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit))).strip()
            out.append({"url": f"{cfg['base']}/bopportal/publicado/{fecha[:4]}/{fecha[4:6]}/"
                               f"{fecha[6:8]}/{reg.replace('/', '_')}.html",
                        "titulo": t2, "cve": reg,
                        "fecha": f"{fecha[6:8]}/{fecha[4:6]}/{fecha[:4]}", "orden": fecha,
                        "materia": q != "ordenanza"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=min(6, max(1, len(tareas)))) as ex:
        for rs in ex.map(una, tareas):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    return list(vistos.values())


def _acoruna_texto(prov, m):
    """El anuncio se sirve como HTML y como PDF en una URL estática predecible.
    El HTML es más rápido y ya trae capa de texto; el PDF queda de respaldo."""
    u = m.get("url") if isinstance(m, dict) else m
    if not u:
        return "", "sin-url"
    try:
        h = _madrid_get(u, timeout=15, intentos=1)
        t = _html_a_texto(h)
        if len(t) > 200:
            return t, "html"
    except Exception:  # noqa: BLE001
        pass
    try:
        pdf = _getb(u[:-5] + ".pdf", timeout=25)
        if pdf[:5] == b"%PDF-":
            t, _ = _pdf_bytes_texto(pdf)
            if t:
                return t, "pdf"
    except Exception:  # noqa: BLE001
        pass
    return "", "sin-texto"


# ---- backend BOCM Madrid (Drupal 7 Views + JSON schema.org, SIN PDF ni OCR) --
# El BOCM es el boletín de la Comunidad de Madrid y hace de BOP (uniprovincial).
# Búsqueda: URL "limpia" server-rendered /advanced-search/p/<vocab>/<tid>[/busqueda/<q>]/seccion/8387
# Lectura : cada anuncio publica JSON schema.org con el TEXTO ÍNTEGRO -> 0 OCR.
_BOCM_ROW = re.compile(
    r'class="views-row.*?about="/(bocm-(\d{8})-(\d+))".*?'
    r'field-name-field-short-description.*?<p>(.*?)</p>', re.S)
_BOCM_ID = re.compile(r"(?i)\bBOCM-(\d{4})(\d{2})(\d{2})-(\d+)\b")
# cuerpo del anuncio en su página HTML (respaldo cuando el JSON no se sirve)
_BOCM_BODY = re.compile(r'(?s)field-name-body.*?<div class="field-items">(.*?)</div>\s*</div>\s*</div>')


def _madrid_get(url, timeout=25, intentos=2):
    """El BOCM corta conexiones en ráfaga (WinError 10060) y su latencia oscila
    (0,6 s … 20 s según carga): reintentos cortos con espera creciente."""
    ult = None
    for i in range(intentos):
        try:
            return urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": _UA, "Accept-Language": "es-ES,es"}),
                timeout=timeout).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            ult = e
            time.sleep(0.5 * (i + 1))
    raise ult


_MAD_IDX = {}          # tid -> [{cve,titulo,fecha,orden}] o None si no hay índice


def _madrid_indice(tid):
    """Índice de anuncios normativos del municipio empaquetado en el repo
    (lo genera _gen_indice_madrid.py). None = municipio no indexado → búsqueda viva."""
    if tid in _MAD_IDX:
        return _MAD_IDX[tid]
    try:
        with open(os.path.join(_DATA, "madrid_indice", f"{tid}.json"), encoding="utf-8") as f:
            datos = json.load(f) or None
    except Exception:  # noqa: BLE001
        datos = None
    _MAD_IDX[tid] = datos
    return datos


def _madrid_url(cfg, tid, texto, pagina=0):
    u = cfg["base"] + "/advanced-search/p/field_orden_organo_y_organismo_3/" + str(tid)
    if (texto or "").strip():
        u += "/busqueda/" + urllib.parse.quote((texto or "").strip())
    u += "/seccion/" + str(cfg.get("seccion", "8387"))
    return u + (f"?page={pagina}" if pagina else "")


def _madrid_json_url(cfg, ident):
    m = _BOCM_ID.search(ident or "")
    if not m:
        return None
    y, mo, d, _ = m.groups()
    return f"{cfg['base']}/boletin/CM_Orden_BOCM/{y}/{mo}/{d}/{m.group(0).upper()}.json"


def _madrid_filas(h):
    out = []
    for m in _BOCM_ROW.finditer(h):
        ident, f8 = m.group(1).upper(), m.group(2)
        tit = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group(4)))).strip()
        tit = re.sub(r"^[–—-]\s*", "", tit).strip()
        out.append({"url": f"https://www.bocm.es/{m.group(1)}", "titulo": tit,
                    "cve": ident, "fecha": f"{f8[6:8]}/{f8[4:6]}/{f8[:4]}", "orden": f8})
    return out


def _madrid_buscar(prov, texto, tid=None, rpp=40):
    cfg = PROVINCIAS[prov]
    # ¿piden un anuncio concreto por su identificador? -> directo, sin buscador
    mid = _BOCM_ID.search(texto or "")
    if mid:
        ident = mid.group(0).upper()
        f8 = ident[5:13]
        fila = {"url": f"{cfg['base']}/{ident.lower()}", "titulo": "", "cve": ident,
                "fecha": f"{f8[6:8]}/{f8[4:6]}/{f8[:4]}", "orden": f8}
        try:
            j = json.loads(_madrid_get(_madrid_json_url(cfg, ident), timeout=10, intentos=1))
            fila["titulo"] = re.sub(r"^[–—-]\s*", "", (j.get("name") or "")).strip()
            fila["text"] = j.get("text") or ""
        except Exception:  # noqa: BLE001
            pass                     # lo resolverá _madrid_texto por la vía HTML
        return [fila]
    if not tid:
        return []
    # ÍNDICE EMPAQUETADO: si el municipio está indexado, la búsqueda NO toca la red.
    # (Desde Vercel las páginas de búsqueda del BOCM se cuelgan de forma
    # intermitente — 4/10 y hasta 94 s en producción—; las lecturas de un anuncio
    # concreto sí funcionan, así que solo se lee en vivo el elegido.)
    idx = _madrid_indice(tid)
    if idx:
        raw, core, _s = _familias(texto or "")
        fam = {w for w in (set(raw) | core) if w not in _GENERICO}
        out = []
        con_titulo = 0
        for r in idx:
            d = dict(r, url=f"{cfg['base']}/{r['cve'].lower()}")
            tm = _mnorm(r["titulo"])
            d["materia"] = bool(fam) and any(_hit(w, tm) for w in fam)
            con_titulo += bool(d["materia"])
            out.append(d)
        # El índice solo guarda TÍTULOS, y el BOCM titula genérico ("Ordenanza"):
        # si ningún título casa con la materia, se lanza UNA consulta viva (el
        # buscador sí mira el cuerpo) para no perder los casos tipo "ruido" de
        # Alcobendas, que vive dentro de la ordenanza de salubridad.
        if fam and not con_titulo:
            qs = [q for q in _madrid_consultas(texto) if q != "ordenanza"][:2]

            def viva(q):
                try:
                    return _madrid_filas(_madrid_get(_madrid_url(cfg, tid, q),
                                                     timeout=20, intentos=1))
                except Exception:  # noqa: BLE001
                    return []

            vivos = {}
            with _cf.ThreadPoolExecutor(max_workers=max(1, len(qs))) as ex:
                for rs in ex.map(viva, qs):
                    for r in rs:
                        vivos.setdefault(r["cve"], r)
            ya = {o["cve"] for o in out}
            for r in out:
                if r["cve"] in vivos:
                    r["materia"] = True
            for cve, r in vivos.items():
                if cve not in ya:
                    out.append(dict(r, materia=True))
        return out
    # El fulltext del BOCM es AND ESTRICTO: "ruido contaminación acústica" da 0
    # resultados. Escalera: término más distintivo + volcado genérico del
    # municipio, EN PARALELO (mismo coste de reloj que una sola consulta).
    consultas = _madrid_consultas(texto)

    def una(q):
        try:
            fs = _madrid_filas(_madrid_get(_madrid_url(cfg, tid, q), timeout=25))
        except Exception:  # noqa: BLE001
            return []
        for f in fs:                       # de qué consulta viene (señal de relevancia)
            f["q"] = q
            f["materia"] = q != "ordenanza"
        return fs

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=max(1, len(consultas))) as ex:
        for rs in ex.map(una, consultas):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    if not vistos and (texto or "").strip():        # último recurso: sin filtro de texto
        for r in una(""):
            vistos.setdefault(r["cve"], r)
    return list(vistos.values())


def _madrid_consultas(texto):
    """Consultas a lanzar para una materia. El buscador exige que TODAS las
    palabras estén en el anuncio, así que la frase del usuario casi nunca casa:
    se usa el término más distintivo (el más largo) y, en paralelo, el volcado
    de 'ordenanza' del municipio para que el ranking por título tenga material."""
    pal = [w for w in re.split(r"\W+", (texto or "").strip()) if w]
    utiles = [w for w in pal if _norm(w) not in {_norm(x) for x in _STOPM} and len(w) >= 4]
    # los DOS términos más distintivos del usuario (el más largo no siempre es el
    # bueno: para "venta ambulante mercadillo" manda «ambulante», no «mercadillo»)
    qs = sorted(utiles, key=len, reverse=True)[:2]
    qs.append("ordenanza")                           # volcado del municipio (respaldo)
    fuera, out = set(), []
    for q in qs[:3]:
        if q.lower() not in fuera:
            fuera.add(q.lower())
            out.append(q)
    return out


def _madrid_normaliza(t):
    """El JSON del BOCM trae el texto en un solo bloque sin saltos: se reinsertan
    marcas estructurales para que el troceo por artículos y los pasajes funcionen."""
    t = re.sub(r"[ \t\xa0]+", " ", (t or "")).strip()
    return re.sub(r"(?i)\s+(?=(?:art[íi]culo\s+\d+|cap[íi]tulo\s+[\wIVXLC]|t[íi]tulo\s+[IVXLC]|"
                  r"disposici[oó]n\s+(?:adicional|transitoria|derogatoria|final)|anexo\s))", "\n\n", t)


def _madrid_texto(prov, m):
    """Texto del anuncio con CADENA DE RESPALDO y timeouts cortos.

    Hay anuncios que el propio BOCM NO sirve en JSON (se quedan colgados hasta
    60 s devolviendo 0 bytes; reproducido también desde fuera de Vercel, así que
    es del boletín, no de la red). Por eso: intento corto en JSON y, si no da,
    la página HTML del anuncio —que trae el mismo texto por otra ruta—. Nunca se
    insiste: si este candidato no se puede leer, el motor prueba el siguiente."""
    if isinstance(m, dict) and m.get("text"):
        return _madrid_normaliza(m["text"]), "json"
    cfg = PROVINCIAS[prov]
    ident = ((m.get("cve") if isinstance(m, dict) else m) or "").upper()
    u = _madrid_json_url(cfg, ident)
    if not u:
        return "", "sin-id"

    # El BOCM publica el mismo anuncio en JSON, HTML y XML, y unos días falla uno
    # y otros otro (verificado: el JSON de un anuncio concreto se cuelga 60 s
    # devolviendo 0 bytes también fuera de Vercel). Se piden LAS TRES A LA VEZ y
    # gana la primera que traiga texto: la lectura deja de depender de una ruta.
    def via_json():
        j = json.loads(_madrid_get(u, timeout=20, intentos=1))
        return _madrid_normaliza(j.get("text") or ""), "json"

    def via_html():
        h = _madrid_get(cfg["base"] + "/" + ident.lower(), timeout=20, intentos=1)
        mm = _BOCM_BODY.search(h)
        return (_madrid_normaliza(_html_a_texto(mm.group(1))) if mm else ""), "html"

    def via_xml():
        x = _madrid_get(u[:-5] + ".xml", timeout=20, intentos=1)
        x = re.sub(r"(?is)<\?xml.*?\?>", " ", x)
        return _madrid_normaliza(_html_a_texto(x)), "xml"

    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(f): n for f, n in ((via_json, "json"), (via_html, "html"), (via_xml, "xml"))}
        mejor = ("", "sin-texto")
        for fut in _cf.as_completed(futs, timeout=25):
            try:
                t, via = fut.result()
            except Exception:  # noqa: BLE001
                continue
            if len(t) > len(mejor[0]):
                mejor = (t, via)
            if len(mejor[0]) > 400:        # ya tenemos texto útil: no esperamos al resto
                break
    return mejor


# ---- backend OpenCms Cádiz (búsqueda -> boletines -> PDF del día con #page) --
def _cadiz_get(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": _UA}),
                                  timeout=timeout, context=_SSL_NOVERIFY).read()


def _cadiz_anuncios(base, slug, organo):
    try:
        page = _cadiz_get(base + "/boletin/" + slug).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    out = []
    on = _norm(organo)
    for mm in re.finditer(r"(\d{3}\.\d{3})\.-\s*(Ayuntamiento de [^.<]+?)\.\s*(.*?)\s*"
                          r'<a[^>]+href="([^"]+\.pdf#page=(\d+))"', page, re.S):
        if _norm(mm.group(2)) != on:
            continue
        tit = _html.unescape(re.sub(r"<[^>]+>", " ", mm.group(3))).strip().rstrip(".")
        pdf = mm.group(4)
        out.append({"titulo": re.sub(r"\s+", " ", tit), "pdf": base + pdf if pdf.startswith("/") else pdf,
                    "cve": f"BOP-CA-{mm.group(1)}", "page": int(mm.group(5))})
    return out


def _cadiz_buscar(prov, texto, organo=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not organo:
        return []
    raw = _familias(texto or "ordenanza")[0]
    q = max(raw, key=len) if raw else (texto or "ordenanza")
    p = {"tipo_": cfg["tipo"], "ruta_": "/sites/default/.content/BOP_F/", "incluirFiltros_": "true",
         "num_elements_": "20", "num_columns_": "1",
         "listConfig": "/.content/Lista_L/Lista_L_00001.html", "usepagination": "true",
         "page": "1", "texto": q, "organo_remitente": organo, "sortModifier": "desc"}
    try:
        r = _cadiz_get(cfg["base"] + "/system/modules/es.dipucadiz.listas/elements/list-inner.jsp?"
                       + urllib.parse.urlencode(p)).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    slugs = list(dict.fromkeys(re.findall(r"/boletin/(Boletin-numero-\d+-del-ano-\d+)", r)))[:4]
    out = []
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        for i, ans in enumerate(ex.map(lambda s: _cadiz_anuncios(cfg["base"], s, organo), slugs)):
            for a in ans:
                m8 = re.search(r"ano-(\d+)", slugs[i])
                a["orden"] = (m8.group(1) if m8 else "0") + f"{len(slugs)-i:03d}"
                a["fecha"] = ""
                a["url"] = a["pdf"]
                out.append(a)
    return out


def _cadiz_texto(prov, m):
    pdf = m.get("pdf") if isinstance(m, dict) else m
    page = (m.get("page") if isinstance(m, dict) else 1) or 1
    if not pdf:
        return "", "sin-pdf"
    try:
        data = _cadiz_get(pdf.split("#")[0], 50)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    if not _HAS_FITZ or data[:5] != b"%PDF-":
        return "", "sin-pdf"
    doc = fitz.open(stream=data, filetype="pdf")
    a, b_ = max(0, page - 1), min(doc.page_count, page + 4)   # el anuncio empieza en #page
    txt = "\n".join(doc[i].get_text() for i in range(a, b_))
    return (txt, "pdf-dia") if len(txt) > 200 else ("", "sin-texto")


# ---- backend Sphinx (Málaga: buscador xhr_sphinxsearch + HTML edicto) ------
def _malaga_buscar(prov, texto, ine=None, rpp=40):
    cfg = PROVINCIAS[prov]
    # Sphinx hace AND con varias palabras -> usar el término MÁS DISTINTIVO (el raw
    # más largo) y ranquear en local; así una consulta multipalabra no da 0.
    raw = _familias(texto or "ordenanza")[0]
    q = max(raw, key=len) if raw else (texto or "ordenanza")
    crit = f"texto = {q}"
    if ine:
        crit += f" #AND# cod_provincia_municipio = {ine}"
    data = {"pag": "res", "parametros": crit, "ordenar_por": "fecha_unix", "orden": "desc"}
    try:
        r = _rest_post(cfg["base"] + "/inc/xhr_sphinxsearch.php", data,
                       referer=cfg["base"] + "/buscar.php").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for art in re.finditer(r"<article>(.*?)</article>", r, re.S):
        blk = art.group(1)
        ed = re.search(r"edicto\.php\?edicto=([\w-]+)", blk)
        if not ed:
            continue
        p = re.search(r"<p>(.*?)</p>", blk, re.S)
        raw = _html.unescape(re.sub(r"<[^>]+>", " ", p.group(1) if p else blk))
        raw = re.sub(r"\s+", " ", raw).strip()
        # el título limpio = desde la 1ª mención normativa ("Ordenanza/Reglamento/
        # Tasa…"); así se salta la cabecera de sección y el "Por el Pleno… aprobar".
        mn = re.search(r"((?:orden(?:anza|za)|reglamento|tasa|precio p[uú]blico|"
                       r"impuesto)\b.*)", raw, re.I)
        tit = mn.group(1) if mn else re.sub(
            r"^ADMINISTRACI[ÓO]N LOCAL\s+.*?(?:Anuncio|Edicto|Expediente:\S*)\s*", "", raw, flags=re.I)
        m8 = re.search(r"(\d{4})(\d{2})(\d{2})", ed.group(1))
        orden = (m8.group(1) + m8.group(2) + m8.group(3)) if m8 else "0"
        fecha = f"{m8.group(3)}/{m8.group(2)}/{m8.group(1)}" if m8 else ""
        out.append({"url": cfg["base"] + "/edicto.php?edicto=" + ed.group(1), "eid": ed.group(1),
                    "titulo": (tit or raw)[:180], "cve": f"BOP-MA-{ed.group(1)}",
                    "fecha": fecha, "orden": orden})
        if len(out) >= max(rpp, 20):
            break
    return out


def _malaga_texto(prov, m):
    cfg = PROVINCIAS[prov]
    eid = m.get("eid") if isinstance(m, dict) else m
    if not eid:
        return "", "sin-id"
    try:
        req = urllib.request.Request(cfg["base"] + "/edicto.php?edicto=" + eid,
                                     headers={"User-Agent": _UA, "Referer": cfg["base"] + "/buscar.php"})
        html = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    t = _html_a_texto(html)
    return (t, "html") if len(t) > 200 else ("", "sin-texto")


# ---- backend BOP Digit@l (Jaén: índice de ordenanzas por municipio + PDF) ---
_JAEN = {}   # {"op": opener, "cache": {codigo: (items, ts)}}
_MESES = {"enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05",
          "junio": "06", "julio": "07", "agosto": "08", "septiembre": "09",
          "octubre": "10", "noviembre": "11", "diciembre": "12"}


def _jaen_op(base):
    d = _JAEN.get(base)
    if d and time.time() - d["ts"] < 600:
        return d["op"]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA)]
    try:
        op.open(base + "/ordenanzas", timeout=25).read()
    except Exception:  # noqa: BLE001
        pass
    _JAEN[base] = {"op": op, "ts": time.time(), "cache": {}}
    return op


def _jaen_buscar(prov, texto, codigo=None, rpp=40):
    # el índice devuelve TODAS las ordenanzas del municipio (una petición); se
    # ignora `texto` y se ranquea en local. Caché por municipio (evita 4 POSTs).
    cfg = PROVINCIAS[prov]
    if not codigo:
        return []
    ent = _JAEN.get(cfg["base"], {})
    cache = ent.get("cache", {})
    hit = cache.get(codigo)
    if hit and time.time() - hit[1] < 120:
        return hit[0]
    op = _jaen_op(cfg["base"])
    cache = _JAEN[cfg["base"]]["cache"]
    try:
        req = urllib.request.Request(cfg["base"] + "/resultados",
            data=urllib.parse.urlencode({"codigoSubseccion": codigo}).encode(),
            headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded",
                     "Referer": cfg["base"] + "/ordenanzas"})
        r = op.open(req, timeout=25)
        tok = re.search(r"/resultados/([^/]+)/", r.geturl())
        r.read()
        if not tok:
            return []
        html_ = op.open(cfg["base"] + f"/resultados/{tok.group(1)}/0/{max(rpp, 40)}/DESC",
                        timeout=25).read().decode("iso-8859-1", "replace")
    except Exception:  # noqa: BLE001
        return []
    res = html_[html_.find("id='resultados'"):]
    out = []
    for mm in re.finditer(r"/edicto/(\d+)/N'[^>]*>.*?</a>(.*?)</li>", res, re.S):
        eid = mm.group(1)
        txt = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", mm.group(2)))).strip()
        mfe = re.match(r"BOP de (\d{1,2})-([a-záéíóú]+)-(\d{4})\s*(.*)$", txt, re.I)
        if mfe:
            dia, mes, anio, titulo = mfe.group(1), _MESES.get(mfe.group(2).lower(), "00"), mfe.group(3), mfe.group(4)
            fecha = f"{int(dia):02d}/{mes}/{anio}"; orden = f"{anio}{mes}{int(dia):02d}"
        else:
            titulo, fecha, orden = txt, "", "0"
        out.append({"url": cfg["base"] + f"/edicto/{eid}/N", "eid": eid,
                    "titulo": titulo.strip(), "cve": f"BOP-JA-{eid}",
                    "fecha": fecha, "orden": orden})
    cache[codigo] = (out, time.time())
    return out


def _jaen_texto(prov, m):
    cfg = PROVINCIAS[prov]
    eid = m.get("eid") if isinstance(m, dict) else m
    if not eid:
        return "", "sin-id"
    op = _jaen_op(cfg["base"])
    try:
        ed = op.open(cfg["base"] + f"/edicto/{eid}/N", timeout=25).read().decode("iso-8859-1", "replace")
        dl = re.search(r"(descargarws\.dip[^\"'\s]*)", ed)
        if not dl:
            return "", "sin-descarga"
        url = cfg["base"] + "/" + dl.group(1).lstrip("/")
        pdf = op.open(urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=45).read()
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    return _pdf_bytes_texto(pdf)


# ---- backend eConsulta (Alicante: webservice JSON + PDF directo) -----------
def _uw(v):
    return (v[0] if isinstance(v, list) and v else (v or "")) if v is not None else ""


# el webservice de Alicante LIMITA la ventana temporal (~5 años) -> se consulta
# por ventanas y se fusiona.
_ALC_VENTANAS = [("01/01/2023", "31/12/2027"), ("01/01/2018", "31/12/2022")]


def _alicante_buscar(prov, texto, publicante=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not publicante:
        return []                      # el webservice exige publicante para acotar
    vistos = {}

    def ventana(win):
        desde, hasta = win
        xml = (f"<raiz><entrada><registro><desde>{desde}</desde><hasta>{hasta}</hasta>"
               f"<texto>{_html.escape(texto or 'ordenanza')}</texto><tipoorganismo></tipoorganismo>"
               f"<publicante>{_html.escape(publicante)}</publicante></registro></entrada></raiz>")
        u = cfg["base"] + cfg["ws"] + "?" + urllib.parse.urlencode({"nemo": "BOP_EDI", "usuario": "-", "param": xml})
        try:
            d = json.loads(_rest_get(u, 30).decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return []
        reg = d.get("bop", {}).get("registro") or []
        return [reg] if isinstance(reg, dict) else reg

    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        for regs in ex.map(ventana, _ALC_VENTANAS):
            for r in regs:
                pdf = _uw(r.get("ubicacion"))
                if not pdf or pdf in vistos:
                    continue
                fe = _uw(r.get("fechaPublica"))
                mfe = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", fe)
                orden = (mfe.group(3) + mfe.group(2) + mfe.group(1)) if mfe else re.sub(r"\D", "", fe)[:8] or "0"
                vistos[pdf] = {"url": pdf, "pdf": pdf,
                               "titulo": _html.unescape(_uw(r.get("extracto"))),
                               "cve": f"BOP-A-{_uw(r.get('anyo'))}-{_uw(r.get('nBop'))}",
                               "fecha": (mfe.group(0) if mfe else ""), "orden": orden}
    return list(vistos.values())


def _alicante_texto(prov, m):
    pdf = m.get("pdf") if isinstance(m, dict) else m
    if not pdf:
        return "", "sin-pdf"
    try:
        return _pdf_bytes_texto(_getb(pdf, 50))
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"


# ---- backend BORM (Murcia: REST JSON, anti-bot Radware -> sesión con cookie) --
_MURCIA_OP = {}


def _murcia_op(base):
    o = _MURCIA_OP.get("op")
    if o and time.time() - _MURCIA_OP.get("t", 0) < 600:
        return o
    cj = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    o.addheaders = [("User-Agent", _UA), ("Referer", base + "/")]
    try:
        o.open(base + "/", timeout=20).read()
    except Exception:  # noqa: BLE001
        pass
    _MURCIA_OP.update(op=o, t=time.time())
    return o


def _murcia_buscar(prov, texto, muni=None, rpp=40):
    cfg = PROVINCIAS[prov]
    op = _murcia_op(cfg["base"])
    body = {"textoLibre": texto or "ordenanza", "fechaDesde": "", "fechaHasta": "", "anunciante": "",
            "rango": 0, "tipo": "libre", "nombre": "", "apellidos": "", "nif": "", "etiqueta": 0,
            "origen": 0, "idApartado": "", "anuncianteFaceta": muni or "", "idCategoria": "272",
            "tipoBusqueda": 0}
    req = urllib.request.Request(cfg["base"] + "/services/buscador", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": _UA,
                 "Referer": cfg["base"] + "/", "Accept": "application/json"})
    try:
        d = json.loads(op.open(req, timeout=30).read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in (d.get("anuncios") or [])[:max(rpp, 20)]:
        # doble filtro (la faceta a veces no acota) por nombre de municipio
        if muni and _norm(a.get("anunciante") or "") != _norm(muni):
            continue
        fe = a.get("fechaPublicacion") or ""
        mfe = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", fe)
        orden = (mfe.group(3) + mfe.group(2) + mfe.group(1)) if mfe else re.sub(r"\D", "", fe)[:8] or "0"
        idA = a.get("idAnuncio")
        out.append({"url": cfg["base"] + f"/services/anuncio/{idA}/txt", "idAnuncio": idA,
                    "titulo": _html.unescape(a.get("sumario") or ""), "cve": str(idA or ""),
                    "fecha": (mfe.group(0) if mfe else ""), "orden": orden})
    return out


def _murcia_texto(prov, m):
    cfg = PROVINCIAS[prov]
    idA = m.get("idAnuncio") if isinstance(m, dict) else m
    if not idA:
        return "", "sin-id"
    op = _murcia_op(cfg["base"])
    try:
        req = urllib.request.Request(cfg["base"] + f"/services/anuncio/{idA}/txt",
                                     headers={"User-Agent": _UA, "Referer": cfg["base"] + "/"})
        t = op.open(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    return (t, "txt") if len(t) > 100 else ("", "sin-texto")


# ---- backend bope_web (Huelva: POST Solr + PDF con capa de texto) ---------
def _rest_post(url, data, timeout=30, referer=""):
    h = {"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded",
         "X-Requested-With": "XMLHttpRequest"}
    if referer:
        h["Referer"] = referer
    return urllib.request.urlopen(urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                                         headers=h), timeout=timeout).read()


def _huelva_buscar(prov, texto, codigo=None, rpp=40):
    cfg = PROVINCIAS[prov]
    data = {"tipo": 3, "Seccion": 1, "Categoria": 8, "PClave": texto or "ordenanza",
            "Fecha_Desde": "01/01/2009", "Fecha_Hasta": "31/12/2027"}
    if codigo:
        data["Entidad"] = codigo
    try:
        d = json.loads(_rest_post(cfg["base"] + "/lib/bope/anuncios_bop/SOLRajaxAnuncios2.php",
                                  data, referer=cfg["base"] + "/servicios/bope_web/").decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in (d.get("Anuncios") or [])[:max(rpp, 20)]:
        fe = a.get("fecha_publicacion") or ""
        mfe = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", fe)
        orden = (mfe.group(3) + mfe.group(2) + mfe.group(1)) if mfe else re.sub(r"\D", "", fe)[:8] or "0"
        doc = a.get("documento") or ""
        out.append({"url": cfg["base"] + "/portalweb/bope/anuncios/" + doc if doc else cfg["base"],
                    "titulo": _html.unescape(a.get("titulo") or ""), "cve": a.get("csv") or "",
                    "fecha": (mfe.group(0) if mfe else ""), "orden": orden, "documento": doc})
    return out


def _huelva_texto(prov, m):
    cfg = PROVINCIAS[prov]
    doc = m.get("documento") if isinstance(m, dict) else ""
    if not doc:
        return "", "sin-doc"
    try:
        pdf = _getb(cfg["base"] + "/portalweb/bope/anuncios/" + doc, 50)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    return _pdf_bytes_texto(pdf)


# ---- backend SOLR expuesto (Toledo: webEbop/solr_select.jsp) --------------
def _solr_escape(s):
    return re.sub(r'(["\\])', r"\\\1", s or "")


def _toledo_buscar(prov, texto, facet=None, rpp=40):
    cfg = PROVINCIAS[prov]
    # busca el término en el CONTENIDO, filtrado por municipio; el ranking por
    # TÍTULO (subject) lo hace el motor genérico sobre los candidatos.
    partes = []
    if facet:
        partes.append(f'publisher_facet:"{_solr_escape(facet)}"')
    t = (texto or "ordenanza").strip()
    partes.append(f"contents:({_solr_escape(t)})")
    q = urllib.parse.urlencode({"q": " AND ".join(partes), "wt": "json",
                                "rows": max(rpp, 20), "sort": "publication_date desc",
                                "fl": "subject,contents,publication_date,insert_number,insert_year,bop_number"})
    try:
        d = json.loads(_rest_get(cfg["base"] + "/solr_select.jsp?" + q).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for x in d.get("response", {}).get("docs", []):
        pd = str(x.get("publication_date") or "")            # yyyymmdd... o ISO
        orden = re.sub(r"\D", "", pd)[:8] or "0"
        fe = f"{orden[6:8]}/{orden[4:6]}/{orden[:4]}" if len(orden) == 8 else ""
        cve = f"BOP-TO-{x.get('insert_year','')}-{x.get('insert_number','')}"
        out.append({"url": cfg["base"] + f"#{cve}", "titulo": _html.unescape(x.get("subject") or ""),
                    "cve": cve, "fecha": fe, "orden": orden,
                    "contents": x.get("contents") or ""})
    return out


def _toledo_texto(prov, m):
    # el texto viene INLINE en el propio doc (campo contents) -> 0 red extra
    c = m.get("contents") if isinstance(m, dict) else ""
    return (c, "solr") if c else ("", "sin-texto")


# ---- backend REST-JSON (Cáceres: API pública Diputación) ------------------
def _rest_get(url, timeout=25):
    return urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"}), timeout=timeout).read()


def _epoch_a_fecha(ms):
    try:
        t = time.gmtime(int(ms) / 1000)
        return f"{t.tm_mday:02d}/{t.tm_mon:02d}/{t.tm_year}", f"{t.tm_year}{t.tm_mon:02d}{t.tm_mday:02d}"
    except Exception:  # noqa: BLE001
        return "", "0"


def _caceres_buscar(prov, texto, entidad=None, rpp=40):
    cfg = PROVINCIAS[prov]
    q = {"grupo": cfg.get("grupo", 1), "texto": texto or "ordenanza",
         "start": 0, "limit": max(rpp, 20)}
    if entidad:
        q["entidad"] = entidad
    url = cfg["base"] + "/bop/services/anuncios/busquedaAvanzada?" + urllib.parse.urlencode(q)
    try:
        d = json.loads(_rest_get(url).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in (d.get("data") or []):
        csv = a.get("csv") or ""
        tit = re.sub(r"^BOP[A-Z]?-\d{4}-\d+\s*", "", a.get("tituloAnuncio") or "").strip()
        fecha, orden = _epoch_a_fecha(a.get("fechaPublicacion"))
        out.append({"url": cfg["base"] + "/bop/services/anuncios/contenidoHtmlIdAnuncio?csv=" + urllib.parse.quote(csv),
                    "titulo": _html.unescape(tit), "cve": csv, "csv": csv,
                    "fecha": a.get("fecha") or fecha, "orden": orden})
    return out


def _caceres_texto(prov, m):
    cfg = PROVINCIAS[prov]
    csv = (m.get("csv") or m.get("cve")) if isinstance(m, dict) else m
    if not csv:
        return "", "sin-csv"
    txt = ""
    try:
        url = cfg["base"] + "/bop/services/anuncios/contenidoHtmlIdAnuncio?csv=" + urllib.parse.quote(csv)
        d = json.loads(_rest_get(url).decode("utf-8", "replace"))
        node = d.get("data") if isinstance(d.get("data"), dict) else d
        txt = _html_a_texto((node or {}).get("contenidoHtml") or "")
    except Exception:  # noqa: BLE001
        txt = ""
    # el HTML a veces trae solo el preámbulo ("aprobación definitiva…"); el texto
    # íntegro está en el PDF -> fallback a PDF (con OCR si va escaneado/CID).
    if len(txt) < 1500:
        try:
            pdf = _rest_get(cfg["base"] + "/bop/services/anuncios/contenidoPdfIdAnuncio?csv="
                            + urllib.parse.quote(csv), timeout=50)
            if pdf[:5] == b"%PDF-":
                pt, _ = _pdf_bytes_texto(pdf)
                if len(pt) > len(txt):
                    return pt, "pdf"
        except Exception:  # noqa: BLE001
            pass
    return txt, ("html" if txt else "sin-texto")


def _html_a_texto(html):
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|tr|h\d)>", "\n", html)
    txt = _html.unescape(re.sub(r"<[^>]+>", " ", html))
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", txt).strip()


def _saga_buscar_raw(prov, texto, categoria=None, rpp=40, timeout=20):
    cfg = PROVINCIAS[prov]
    op, params, _ = _get_sesion(prov)
    p = dict(params)
    p["buscarTexto"] = texto; p["ResultadosPorPagina"] = str(rpp); p["paginaActual"] = "1"
    if categoria:
        p["buscarCategoria"] = categoria; p["CategoriasAListar"] = categoria
    req = urllib.request.Request(cfg["base"] + p["urlAjax"], data=urllib.parse.urlencode(p).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Requested-With": "XMLHttpRequest", "Referer": cfg["base"] + cfg["resultados"]})
    r = op.open(req, timeout=timeout).read().decode("utf-8", "replace")
    if "opencms.org" in r:            # sesión caducada -> renovar 1 vez
        _sesion(prov)
        op, params, _ = _get_sesion(prov)
        p2 = dict(params); p2.update({"buscarTexto": texto, "ResultadosPorPagina": str(rpp), "paginaActual": "1"})
        if categoria:
            p2["buscarCategoria"] = categoria; p2["CategoriasAListar"] = categoria
        req = urllib.request.Request(cfg["base"] + p2["urlAjax"], data=urllib.parse.urlencode(p2).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "X-Requested-With": "XMLHttpRequest", "Referer": cfg["base"] + cfg["resultados"]})
        r = op.open(req, timeout=timeout).read().decode("utf-8", "replace")
    out = []
    href = re.escape(cfg.get("anuncio_href", "/publica/buscador-anuncios/anuncio/"))
    for m in re.finditer(r'<a href="(' + href + r'[^"]+)"\s+title="([^"]+)"', r):
        tail = r[m.end():m.end() + 900]
        cve = re.search(r"BOP-[A-Z]{1,4}-\d{4}-\d+", tail)
        fe = re.search(r"(\d{2})/(\d{2})/(\d{4})", tail)
        u = m.group(1)
        out.append({"url": (cfg["base"] + u) if u.startswith("/") else u,
                    "titulo": _html.unescape(m.group(2)),
                    "cve": cve.group(0) if cve else "",
                    "fecha": fe.group(0) if fe else "",
                    "orden": (fe.group(3) + fe.group(2) + fe.group(1)) if fe else "0"})
    return out


def _es_ordenanza(t):
    # los boletines de Galicia, Cataluña y C. Valenciana titulan en su lengua:
    # "Ordenança", "Regulamento", "taxa", "preu públic"… (sin esto no filtraba nada)
    return bool(re.search(r"ordenan[zç]a|reglament|regulamento|\btaxa\b|\btasa\b|"
                          r"pre[uc]?io? p[uú]blic|pre[uz]o p[uú]blic", t, re.I))


_STOPM = {"de", "la", "el", "los", "las", "del", "y", "o", "en", "por", "para", "un",
          "una", "sobre", "municipal", "municipales", "ordenanza", "ordenanzas",
          "reglamento", "reglamentos", "reguladora", "regulador", "norma", "normativa"}

# Tesauro v2: (patrón sobre la materia normalizada CON espacios, términos CORE
# —específicos, pesan—, términos SOFT —genéricos, solo desempatan—). Los alias
# multipalabra se comparan como subcadena del título normalizado.
_EXPANSION = [
    (r"residuo|basura|\brsu\b|desecho|escombro|derribo|limpieza|punto limpio",
     ["residuo", "basura", "recogida de residuos", "recogida de basura", "solidos urbanos",
      "punto limpio", "higiene urbana", "limpieza viaria", "gestion de residuos", "derribo", "escombro"],
     ["limpieza"]),
    (r"terraza|velador", ["terraza", "velador", "mesas y sillas", "ocupacion del espacio publico",
                          "ocupacion de terrenos de uso publico"], ["ocupacion", "establecimiento", "hosteleria"]),
    (r"ocupacion|via publica|espacio publico|quiosco|kiosco",
     ["ocupacion del espacio publico", "ocupacion de terrenos", "mesas y sillas", "terraza",
      "velador", "quiosco", "kiosco"], ["ocupacion"]),
    (r"ruido|acustic|sonor|vibracion", ["ruido", "acustic", "vibracion", "sonor"], []),
    (r"animal|perro|gato|mascota|tenencia|felin|canin",
     ["animal", "perro", "tenencia", "mascota", "proteccion", "bienestar",
      "colonias felinas", "felin", "canin"], []),
    # VMP/patinete: CORE estrecho (no arrastrar estacionamiento/circulación, que
    # macheaban modificaciones fiscales genéricas)
    (r"patinete|\bvmp\b|vehiculos? de movilidad|movilidad personal|monopatin",
     ["vehiculos de movilidad personal", "vmp", "patinete", "movilidad personal"], []),
    (r"\bmovilidad\b", ["movilidad", "vehiculos de movilidad personal"], ["trafico", "circulacion"]),
    (r"\btrafico\b|circulacion|seguridad vial|peaton|ciclista",
     ["trafico", "circulacion", "seguridad vial"], ["vehiculo"]),
    (r"estacionamiento|aparcamiento|zona azul|\bora\b",
     ["estacionamiento", "aparcamiento", "zona azul", "ora"], []),
    (r"bicicleta|\bbici\b", ["bicicleta", "bici"], []),
    (r"\bzbe\b|bajas emisiones|baja emision|zona de bajas|emisiones",
     ["zonas de bajas emisiones", "bajas emisiones", "zbe", "baja emision"], []),
    (r"impuesto (de |sobre )?(circulacion|vehiculos)|\bivtm\b|traccion mecanica",
     ["vehiculos de traccion mecanica", "traccion mecanica", "ivtm"], ["vehiculo", "impuesto"]),
    (r"\bibi\b|bienes inmuebles|contribucion", ["bienes inmuebles", "ibi"], ["impuesto"]),
    (r"plusvalia|incremento de valor|iivtnu", ["plusvalia", "incremento de valor", "iivtnu",
                                               "terrenos de naturaleza urbana"], []),
    (r"\bicio\b|construccion|\bobras?\b|licencia urbanistica",
     ["construcciones instalaciones y obras", "icio", "obra", "urbanistic"], ["licencia"]),
    (r"\biae\b|actividades economicas", ["actividades economicas", "iae"], []),
    (r"convivencia|civismo|botellon", ["convivencia", "civismo", "botellon"], ["espacio publico"]),
    (r"cementerio|funerari|tanatorio|sepultura", ["cementerio", "funerari", "tanatorio", "sepultura"], []),
    (r"venta ambulante|mercadillo|\bmercados?\b|comercio",
     ["ambulante", "mercadillo", "mercado", "no sedentaria", "comercio"], ["venta"]),
    (r"agua|saneamiento|vertido|alcantarillado|abastecimiento|depuracion",
     ["agua", "saneamiento", "vertido", "alcantarillado", "abastecimiento", "depuracion",
      "ciclo integral"], []),
    (r"vado|entrada de vehiculo|paso de carruaje",
     ["vado", "entrada de vehiculos", "paso de carruajes", "reserva de aparcamiento"], []),
    (r"publicidad|cartel|valla|rotulo", ["publicidad", "carteleras", "vallas", "monopostes",
                                         "rotulo", "publicitari"], []),
    (r"patrocinio|mecenazgo", ["patrocinio"], []),
    (r"feria|fiesta|festejo|caseta", ["feria", "fiesta", "festejo", "caseta"], []),
    (r"grua|retirada de vehiculo", ["retirada y recogida de vehiculos", "retirada de vehiculos",
                                    "grua", "inmovilizacion"], []),
    (r"ayuda a domicilio|servicios sociales|dependencia",
     ["ayuda a domicilio", "ayudas tecnicas", "servicios sociales"], []),
    (r"examen|oposicion|derechos de examen", ["derechos de examen"], ["examen", "seleccion"]),
    (r"boda|matrimonio civil|celebracion civil|union civil",
     ["celebraciones civiles", "matrimonio civil", "bodas civiles"], []),
    (r"honor|distincion|protocolo|ceremonial", ["honores", "distinciones", "protocolo", "ceremonial"], []),
    (r"infancia|adolescencia|menores", ["infancia", "adolescencia"], ["consejo"]),
    (r"mosquito|mosca|plaga|insecto", ["mosquitos", "moscas", "plagas", "control de plagas"], []),
    (r"entidades urbanisticas|registro de entidades", ["entidades urbanisticas"], ["registro"]),
    (r"subvencion", ["subvencion"], ["ayuda"]),
    (r"transparencia|administracion electronica|sede electronica",
     ["transparencia", "administracion electronica"], []),
    (r"huerto", ["huertos"], []),
    (r"deporte|instalacion deportiva|piscina", ["deportiv", "piscina"], []),
    (r"biblioteca|cultura", ["biblioteca", "cultural"], []),
    (r"apertura|declaracion responsable|licencia de actividad",
     ["apertura", "declaracion responsable"], ["licencia", "actividad"]),
    (r"negociacion|mesa general", ["mesa general de negociacion", "negociacion"], []),
    (r"escuela infantil|guarderia", ["escuela infantil", "guarderia"], []),
    (r"expedicion de documentos|compulsa", ["expedicion de documentos"], ["documento"]),
]

# materias de competencia frecuentemente SUPRAMUNICIPAL (mancomunidades/consorcios)
_SUPRA = re.compile(r"residuo|basura|\brsu\b|limpieza|agua|saneamiento|alcantarillado|"
                    r"abastecimiento|depuracion|escombro|derribo")

# títulos a demote salvo que la materia pedida los justifique (guardián)
_DEMOTE = [
    (r"correcci[oó]n de errores", None),
    (r"delegaci[oó]n de", None),
    (r"nombramiento|bases (de|para)|convocatoria|bolsa de (trabajo|empleo)", "examen"),
    (r"honores|condecorac|protocolo|ceremonial", "honores"),
    (r"\bpersonal\b|plantilla|oferta de empleo|\brpt\b|negociaci[oó]n", "negociacion"),
    (r"presupuesto", "presupuesto"),
    (r"derogaci[oó]n|desistimiento|no aprobaci[oó]n|inadmisi[oó]n|anulaci[oó]n", None),
    (r"lista provisional|admitidos y excluidos|tribunal", "examen"),
]


def _mnorm(s):
    """Normaliza CONSERVANDO espacios (para regex de materia)."""
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


# tokens demasiado genéricos para decidir un match por sí solos
_WEAK = {"recogida", "servicio", "servicios", "publico", "publica", "publicos", "publicas",
         "via", "uso", "usos", "general", "gestion", "termino", "prestacion", "regimen",
         "actividad", "actividades", "establecimiento", "establecimientos"}


def _hit(term, tm):
    """¿El término (mnorm) aparece en el título (mnorm) con frontera de palabra?
    Singulariza plurales simples (vados->vado) para machear ambos sentidos."""
    if " " in term:
        return term in tm
    t = term[:-1] if term.endswith("s") and len(term) > 4 else term
    return re.search(r"\b" + re.escape(t), tm) is not None


def _familias(materia):
    """(raw, core, soft): términos del usuario y expansión del tesauro (mnorm)."""
    mn = _mnorm(materia)
    toks = [w for w in mn.split() if w not in _STOPM and len(w) >= 2]
    raw = [w for w in toks if w not in _WEAK]
    core, soft = set(), set()
    soft.update(w for w in toks if w in _WEAK)
    for pat, cs, ss in _EXPANSION:
        if re.search(pat, mn):
            core.update(_mnorm(a) for a in cs)
            soft.update(_mnorm(a) for a in ss)
    return raw, core, soft


def _puntuar(r, raw, core, soft):
    tm = _mnorm(r["titulo"])
    rawhits = sum(1 for w in raw if _hit(w, tm))
    s = 5.0 * rawhits + 2 * sum(1 for w in core if _hit(w, tm)) + sum(1 for w in soft if _hit(w, tm))
    if raw:
        s += 6.0 * rawhits / len(raw)          # cobertura de lo que pidió el usuario
    if re.search(r"definitiv", r["titulo"], re.I):
        s += 3
    if not re.search(r"modificaci[oó]n", r["titulo"], re.I):
        s += 2                                  # texto íntegro > modificación parcial
    if re.search(r"aprobaci[oó]n inicial|retrotracci", r["titulo"], re.I):
        s -= 1
    fam = set(raw) | core | soft
    for pat, guardia in _DEMOTE:
        if re.search(pat, r["titulo"], re.I) and not (guardia and any(guardia in w for w in fam)):
            s -= 8
    return s + int(r["orden"][:8] or 0) / 1e10


def _ranquear(res, materia):
    """Candidatos tipo ordenanza ordenados por relevancia; solo pasan el gate los
    que llevan ≥1 término raw o core (con frontera de palabra) en el título."""
    raw, core, soft = _familias(materia)
    cand = [r for r in res if _es_ordenanza(r["titulo"])]
    if not cand:
        return []
    cand.sort(key=lambda r: _puntuar(r, raw, core, soft), reverse=True)
    gate = set(raw) | core
    if not gate:
        return cand
    return [r for r in cand if any(_hit(w, _mnorm(r["titulo"])) for w in gate)]


def _mejor(res, materia):
    top = _ranquear(res, materia)
    return top[0] if top else None


def _no_demote(r, fam):
    for pat, guardia in _DEMOTE:
        if re.search(pat, r["titulo"], re.I) and not (guardia and any(guardia in w for w in fam)):
            return False
    return True


def _mejor_fulltext(res, materia):
    """Para backends de BÚSQUEDA FULL-TEXT (Sphinx/Solr con relevancia): el propio
    buscador ya ordenó por relevancia a la materia (aunque el TÍTULO frasee distinto,
    p.ej. 'ocupación de la vía pública' para 'terrazas'). Se respeta ese orden: se
    coge la 1ª ordenanza no-demotada, prefiriendo las que además llevan la materia en
    el título."""
    raw, core, soft = _familias(materia)
    fam = set(raw) | core | soft
    cand = [r for r in res if _es_ordenanza(r["titulo"]) and _no_demote(r, fam)]
    if not cand:
        return None
    con_materia = [r for r in cand if any(_hit(w, _mnorm(r["titulo"])) for w in fam)]
    definitivas = [r for r in (con_materia or cand) if re.search(r"definitiv", r["titulo"], re.I)]
    return (definitivas or con_materia or cand)[0]


# Palabras de relleno administrativo: aparecen en CUALQUIER ordenanza, así que no
# sirven para decidir si el documento va de la materia preguntada.
_GENERICO = {"entrada", "entradas", "salida", "vehiculo", "vehiculos", "publica", "publico",
             "publicas", "publicos", "servicio", "servicios", "municipal", "municipales",
             "urbana", "urbano", "general", "generales", "local", "locales", "uso", "usos",
             "actividad", "actividades", "espacio", "espacios", "zona", "zonas", "obras",
             "gestion", "normas", "vigente", "titulo", "ciudad", "termino", "aplicacion"}

# Anuncios que NO son normativa (el buscador del BOCM los mezcla con las ordenanzas)
_NO_NORMA = re.compile(r"extracto|convocat[oò]ria|convocatoria|atorgament|ajuts econ[oò]mics|borsa de treball|nomenament|delegaci[óo]n de funciones|"
                       r"oferta[s]? de empleo|bases (del )?proceso|proceso selectivo|"
                       r"plan especial|plan parcial|plan general|calificaci[óo]n (de )?suelo|"
                       r"expropiaci|nombramiento|cese\b|list[ao] (provisional|definitiv)|"
                       r"informaci[óo]n p[úu]blica de|estudio de detalle|convenio urban[íi]stico", re.I)


def _mejor_verificado(prov, res, materia, top_n=4):
    """Elige la ordenanza VERIFICANDO EL CONTENIDO, no solo el título.

    Necesario cuando el boletín titula de forma genérica ('Alcobendas.
    Organización y funcionamiento. Ordenanza'): el título no dice la materia, así
    que se leen los N mejores candidatos (en PARALELO; en el BOCM el texto viene
    en JSON y cuesta ~1 s) y gana el que realmente habla de la materia pedida.
    El texto ganador viaja en m['text'] para no volver a descargarlo."""
    raw, core, soft = _familias(materia)
    cand = [r for r in res if _es_ordenanza(r["titulo"]) and not _NO_NORMA.search(r["titulo"])]
    if not cand:
        cand = [r for r in res if not _NO_NORMA.search(r["titulo"])]
    if not cand:
        return None

    # El gate se juega en los términos DISTINTIVOS: "vados entrada de vehículos"
    # se decide por «vados», no por «entrada»/«vehículos» (que salen en cualquier
    # ordenanza de convivencia y colarían un resultado equivocado).
    clave = [w for w in raw if w not in _GENERICO] or list(raw)
    clave_core = set(clave) | {w for w in core if w not in _GENERICO}
    # boletines en lengua cooficial: el texto dice "auga"/"lixo"/"regulamento"
    # aunque el abogado pregunte "agua"/"residuos"/"reglamento"
    _tabla = {"gl": _GALEGO, "ca": _CATALA, "va": _CATALA}.get(PROVINCIAS[prov].get("idioma") or "")
    if _tabla:
        for w in list(clave_core):
            for cas, loc in _tabla.items():
                if _norm(w) == _norm(cas):
                    clave_core.add(_mnorm(loc))
                elif _norm(w) == _norm(loc):
                    clave_core.add(_mnorm(cas))

    def en_titulo(r):
        tm = _mnorm(r["titulo"])
        return sum(1 for w in clave_core if _hit(w, tm))

    def pre(r):
        tm = _mnorm(r["titulo"])
        s = (5.0 * sum(1 for w in raw if _hit(w, tm)) + 2 * sum(1 for w in core if _hit(w, tm))
             + sum(1 for w in soft if _hit(w, tm)))
        if r.get("materia"):
            s += 4                    # lo encontró la consulta de MATERIA, no el volcado
        if re.search(r"definitiv", r["titulo"], re.I):
            s += 2
        if re.search(r"modificaci[óo]n", r["titulo"], re.I):
            s -= 1
        if _es_ordenanza(r["titulo"]):
            s += 1
        return s + int((r.get("orden") or "0")[:8] or 0) / 1e10

    # si la búsqueda por materia dio algo, el volcado genérico no compite
    pool = [r for r in cand if r.get("materia")] or cand
    pool.sort(key=pre, reverse=True)
    # consulta SIN términos distintivos ("ordenanza", "tasa"): no hay materia que
    # verificar, así que se devuelve la mejor por título en vez de no devolver nada
    if not clave_core:
        return pool[0] if pool else None
    # CAMINO RÁPIDO: si el título ya dice la materia, no hace falta verificar nada
    # (importa de verdad: desde Vercel el BOCM estrangula las descargas en paralelo,
    # así que cada lectura que ahorramos es latencia y riesgo de timeout que quitamos).
    if pool and en_titulo(pool[0]):
        return pool[0]
    top = pool[:max(1, top_n)]

    def carga(r):
        try:
            t, _ = _texto(prov, r)
        except Exception:  # noqa: BLE001
            t = ""
        return r, t

    # SECUENCIAL con salida temprana (no en paralelo): desde Vercel el boletín
    # estrangula las descargas simultáneas y una ráfaga acaba en timeout. Lo normal
    # es resolver con 1 lectura; solo si esa no convence se mira la siguiente.
    mejor, mejor_s, mejor_t, mejor_ok = None, float("-inf"), "", False
    limite = time.time() + 16          # tope duro: nunca disparar la latencia
    if True:
        for r, t in map(carga, top):
            if not t:
                if time.time() > limite:
                    break
                continue
            tn = _mnorm(t[:80000])
            hits = sum(tn.count(w) for w in raw) + sum(tn.count(w) for w in core)
            hclave = sum(tn.count(w) for w in clave_core)
            # DENSIDAD, no volumen: si no, una ordenanza fiscal de 170.000 caracteres
            # gana a la ordenanza de la materia por mención incidental.
            dens = hits * 1000.0 / max(len(tn), 1)
            dclave = hclave * 1000.0 / max(len(tn), 1)
            s = pre(r) + 2.0 * min(hits, 12) + 10.0 * min(dens, 2.0) + 4.0 * min(hclave, 10)
            s += min(len(re.findall(r"(?i)art[íi]culo\s+\d+", t[:80000])), 40) / 8.0
            # ¿de verdad va de lo que preguntan? (término distintivo en título o densidad real)
            ok = bool(en_titulo(r)) or (hclave >= 3 and dclave >= 0.12)
            if (ok, s) > (mejor_ok, mejor_s):
                mejor, mejor_s, mejor_t, mejor_ok = r, s, t, ok
            if ok and dclave >= 0.30:      # claramente es esta: no leo más
                break
            if time.time() > limite:       # se agotó el presupuesto de tiempo
                break
    if mejor is None or not mejor_ok:
        return None                   # honesto: no hay ordenanza de esa materia
    mejor = dict(mejor)
    mejor["text"] = mejor_t
    return mejor


def _ranquear_fulltext(res, materia):
    raw, core, soft = _familias(materia)
    fam = set(raw) | core | soft
    cand = [r for r in res if _es_ordenanza(r["titulo"]) and _no_demote(r, fam)]
    con = [r for r in cand if any(_hit(w, _mnorm(r["titulo"])) for w in fam)]
    return con + [r for r in cand if r not in con]     # materia-en-título primero, resto en orden Sphinx


# ---- lectura del PDF (directo u OCR paralelo) -----------------------------
_PALS = re.compile(r"\b(de|la|el|los|art[íi]culo|ordenanza|ayuntamiento|que|para|del|por)\b", re.I)
_OCR_PROMPT = ("Actúa como un motor OCR de documentos oficiales públicos. Devuelve "
               "EXACTAMENTE el texto que aparece en la imagen, sin añadir ni omitir nada.")
_OCR_PROMPT2 = ("Transcripción de accesibilidad de un documento OFICIAL ya publicado en un "
                "Boletín Oficial (dominio público). Transcribe literalmente el texto impreso "
                "normativo de la imagen. Omite firmas, sellos y datos manuscritos. "
                "Si algo es ilegible escribe [ilegible]. No comentes nada: solo el texto.")


def _ocr_openai(b64, prompt=_OCR_PROMPT):
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("sin OPENAI_API_KEY")
    body = json.dumps({"model": "gpt-4o-mini", "temperature": 0, "max_tokens": 2200,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}}]}]}).encode()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}), timeout=60).read())
    t = r["choices"][0]["message"]["content"]
    if len(t) < 200 and ("no puedo" in t.lower() or "lo siento" in t.lower() or "sorry" in t.lower()):
        raise RuntimeError("rechazo")
    return t


def _ocr_gemini(b64):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("sin GEMINI_API_KEY")
    body = json.dumps({"model": os.environ.get("GEMINI_VISION_MODEL", "gemini-3.5-flash"),
        "reasoning_effort": "none", "temperature": 0, "max_tokens": 2500,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}).encode()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}), timeout=90).read())
    return r["choices"][0]["message"]["content"]


def _ocr_pagina(png):
    b64 = base64.b64encode(png).decode()
    try:
        return _ocr_openai(b64)
    except Exception:  # noqa: BLE001
        pass
    try:
        # segundo intento con prompt reformulado (evita falsos rechazos de contenido)
        return _ocr_openai(b64, _OCR_PROMPT2)
    except Exception:  # noqa: BLE001
        pass
    try:
        return _ocr_gemini(b64)
    except Exception:  # noqa: BLE001
        return ""


def _pdf_de_anuncio(prov, url_anuncio):
    cfg = PROVINCIAS[prov]
    det = _getb(url_anuncio).decode("utf-8", "replace")
    m = re.search(rf'href="([^"]+{cfg["anuncio_pdf"]}[^"]+\.pdf)"', det)
    if not m:
        return None
    u = m.group(1)
    return cfg["base"] + u if u.startswith("/") else u


def _pdf_bytes_texto(datos, ocr=True, max_pag=12):
    """(texto, via) desde bytes PDF: capa de texto si es español real; si no, OCR
    paralelo del articulado (cap de páginas). Compartido por todas las familias."""
    if not _HAS_FITZ or datos[:5] != b"%PDF-":
        return "", "sin-pdf"
    doc = fitz.open(stream=datos, filetype="pdf")
    directo = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    if len(_PALS.findall(directo)) >= 15 and len(directo) / max(1, doc.page_count) > 250:
        return directo, "directo"
    if not ocr:
        return directo, "cifrado"
    n = min(doc.page_count, max_pag)
    pngs = [doc[i].get_pixmap(dpi=150).tobytes("png") for i in range(n)]
    with _cf.ThreadPoolExecutor(max_workers=min(8, n)) as ex:
        pags = list(ex.map(_ocr_pagina, pngs))
    nota = "" if doc.page_count <= max_pag else \
        f"\n[Documento largo de {doc.page_count} págs; transcrito el articulado (primeras {n}).]"
    return "\n".join(p for p in pags if p) + nota, f"ocr({n}/{doc.page_count}p)"


def _saga_texto(prov, url_anuncio, ocr=True, max_pag=10):
    """(texto, via). 'directo' si el PDF tiene capa de texto; 'ocr(Np)' si no."""
    pdf_url = _pdf_de_anuncio(prov, url_anuncio)
    if not pdf_url:
        return "", "sin-pdf"
    return _pdf_bytes_texto(_getb(pdf_url, 50), ocr, max_pag)


def _limpia(t):
    t = re.sub(r"P[áa]gina \d+ de(?: un total de)? \d+|N[ºo] \d+ - [\w ]+de \d+|"
              r"CVE:? ?BOP-[A-Z]{2}-[\d-]+|Documento firmado[^\n]*|C[óo]d\.? ?Validaci[óo]n[^\n]*|"
              r"Bolet[íi]n Oficial[^\n]*|de la provincia de \w+|HASH:[^\n]*|Fecha Firma:[^\n]*", " ", t)
    return re.sub(r"[ \t\xa0]+", " ", t).strip()


def _articulos(texto):
    """[(rubrica, cuerpo)] troceando por 'Artículo N'."""
    t = _limpia(texto)
    marcas = [(m.start(), m.group(1)) for m in re.finditer(
        r"(?im)(?:^|\n|\.)\s*(art[íi]culo\s+\d+[\wº.\-]{0,4}[.\-–—:]?)", t)]
    out = []
    for i, (pos, cab) in enumerate(marcas):
        fin = marcas[i + 1][0] if i + 1 < len(marcas) else len(t)
        out.append((cab.strip(), t[pos:fin].strip()))
    return out


def _articulo_num(articulos, num):
    n = _norm(re.sub(r"^art\w*\.?\s*", "", num.strip(), flags=re.I))
    # puede haber VARIAS ocurrencias del mismo nº (índice/TOC sin cuerpo + el
    # artículo real): quedarse con el cuerpo MÁS LARGO (el articulado de verdad).
    mejor = None
    for rub, cuerpo in articulos:
        m = re.search(r"(\d+)", rub)
        if m and _norm(m.group(1)) == n and len(cuerpo) > len(rub) + 20:
            if mejor is None or len(cuerpo) > len(mejor):
                mejor = cuerpo
    return mejor


def _pasajes(texto, terminos, k=3):
    arts = _articulos(texto)
    if not arts:
        # sin articulado ('Artículo N'): usar bloques de párrafos como unidades
        t = _limpia(texto)
        arts = [("", p.strip()) for p in re.split(r"\n\s*\n+", t) if len(p.strip()) > 150]
        if not arts:
            arts = [("", t[i:i + 1400]) for i in range(0, min(len(t), 4200), 1400)]
    pal = [w[:-1] if w.endswith("s") and len(w) > 4 else w
           for w in _mnorm(terminos).split() if len(w) >= 4 and w not in _STOPM]
    scored = []
    for i, (rub, cuerpo) in enumerate(arts):
        cn = _mnorm(cuerpo)
        # prioriza termino en la RÚBRICA
        s = 5 * sum(1 for w in set(pal) if w in _mnorm(rub)) + sum(cn.count(w) for w in pal)
        if s:
            scored.append((s, i, cuerpo))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored:
        # honesto pero útil: los primeros bloques del texto real
        return [c for _, c in arts[:k]]
    return [c for _, _, c in scored[:k]]


# ================================================================ API pública
def _cabecera(prov, muni_nombre, ord_info):
    cfg = PROVINCIAS[prov]
    ref = f" · {ord_info['cve']}" if ord_info.get("cve") else ""
    fe = f", pub. {ord_info['fecha']}" if ord_info.get("fecha") else ""
    aviso = ""
    if re.search(r"modificaci[oó]n", ord_info.get("titulo", ""), re.I):
        aviso = ("\n⚠️ Es una MODIFICACIÓN: contiene solo los artículos modificados; "
                 "el articulado completo está en la aprobación original (puede ser anterior al índice).")
    return (f"【{ord_info['titulo']} — Ayuntamiento de {muni_nombre}】{ref}{fe}\n"
            f"Fuente: Boletín Oficial de la Provincia de {cfg['nombre']} (texto publicado; "
            "el BOP no consolida: verifica modificaciones posteriores)." + aviso)


def _nombre_muni(prov, municipio):
    _cargar_mapas()
    muni, _ = _parse_muni(municipio)
    return _NOMBRES.get(prov, {}).get(_norm(muni)) or muni.strip().title()


def _candidatos(prov, cat, materia, profundo=True):
    """Escalera de recall: consulta de materia + volcados genéricos del municipio
    (el filtro por categoría acota; el ranking se hace en local). Las consultas se
    lanzan EN PARALELO (el cuello era hacerlas en serie). Dedup por URL."""
    consultas = []
    if materia.strip():
        consultas.append((materia, 60))
    if profundo:
        for q in ("ordenanza", "reglamento", "tasa"):
            if _norm(q) not in _norm(materia):
                consultas.append((q, 100))
    if not consultas:
        consultas = [("ordenanza", 100)]

    def run(c):
        try:
            return _buscar_raw(prov, c[0], cat, rpp=c[1])
        except Exception:  # noqa: BLE001
            return []

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=min(4, len(consultas))) as ex:
        for rs in ex.map(run, consultas):
            for r in rs:
                vistos.setdefault(r["url"], r)
    return list(vistos.values())


def _aviso_indice(prov):
    d = PROVINCIAS[prov].get("indice_desde")
    return (f"el índice electrónico de este BOP solo cubre publicaciones desde ~{d}; "
            if d else "el índice electrónico de este BOP puede no cubrir publicaciones antiguas; ")


def _honesto(prov, nombre, consulta, supra_hits):
    lin = [f"No encuentro una ordenanza de «{consulta}» del Ayuntamiento de {nombre} en el "
           f"Boletín Oficial de la Provincia de {PROVINCIAS[prov]['nombre']}."]
    lin.append(f"Ojo: {_aviso_indice(prov)}la norma puede existir y ser anterior (búscala en la "
               "sede electrónica del ayuntamiento) o no haberse publicado aún.")
    if supra_hits:
        lin.append("\nAdemás, esa materia suele ser SUPRAMUNICIPAL (mancomunidad/consorcio). "
                   "En el BOP constan estas normas supramunicipales que podrían aplicar:")
        for i, r in enumerate(supra_hits[:5], 1):
            lin.append(f"{i}. {r['titulo']}" + (f" · {r['cve']} · pub. {r['fecha']}" if r.get("cve") or r.get("fecha") else f" · pub. {r['fecha']}"))
        lin.append("Puedes leerlas con leer_ordenanza(municipio, ordenanza=<CVE o título>).")
    lin.append("\nNo invento articulado: si me das el CVE (BOP-XX-AAAA-N) de un anuncio concreto, lo leo al instante.")
    return "\n".join(lin)


def _supra(prov, consulta):
    """Normas supramunicipales (mancomunidades/consorcios) si la materia lo sugiere."""
    if not _SUPRA.search(_mnorm(consulta)):
        return []
    try:
        res = _buscar_raw(prov, f"mancomunidad {consulta}", None, rpp=30)
        return _ranquear(res, consulta)[:5]
    except Exception:  # noqa: BLE001
        return []


def buscar(municipio, consulta="", limite=12):
    prov = provincia_de(municipio)
    if not prov:
        return None  # no cubierto por ningún BOP -> el caller decide
    cat = _categoria(prov, municipio)
    nombre = _nombre_muni(prov, municipio)
    fulltext = PROVINCIAS[prov].get("fulltext")
    t0 = time.time()
    try:
        res = _buscar_raw(prov, consulta or "ordenanza", cat, rpp=40) if fulltext \
            else _candidatos(prov, cat, consulta)
    except Exception as e:  # noqa: BLE001
        return f"Error consultando el BOP de {PROVINCIAS[prov]['nombre']}: {e}"
    if consulta.strip() and fulltext:
        ords = _ranquear_fulltext(res, consulta)
    elif consulta.strip():
        ords = _ranquear(res, consulta)
    else:
        ords = [r for r in res if _es_ordenanza(r["titulo"])]
        ords.sort(key=lambda r: r["orden"], reverse=True)
    ords = [r for r in ords if not re.search(r"correcci[oó]n de errores|delegaci[oó]n de", r["titulo"], re.I)]
    if not ords:
        return _honesto(prov, nombre, consulta, _supra(prov, consulta))
    dt = (time.time() - t0) * 1000
    lin = [f"【Ordenanzas de {nombre.upper()} en el BOP de {PROVINCIAS[prov]['nombre']}"
           + (f" — «{consulta}»】" if consulta.strip() else "】")]
    for i, r in enumerate(ords[:limite], 1):
        lin.append(f"\n{i}. {r['titulo']}"
                   + (f"\n   {r['cve']} · pub. {r['fecha']}" if r.get("cve") or r.get("fecha") else ""))
    lin.append("\nSiguiente paso: leer_ordenanza(municipio, ordenanza=<titulo/materia o CVE>, "
               "articulo=\"N\" o parrafos=3 + terminos=\"...\").")
    lin.append(f"Nota: el BOP publica al aprobarse (no consolida) y {_aviso_indice(prov)}"
               "una «modificación» trae solo los artículos modificados.")
    lin.append(f"Fuente: BOP de {PROVINCIAS[prov]['nombre']} · {dt:.0f} ms")
    return "\n".join(lin)


def leer(municipio, ordenanza, articulo="", parrafos=0, terminos="", max_chars=0):
    prov = provincia_de(municipio)
    if not prov:
        return None
    nombre = _nombre_muni(prov, municipio)
    cat = _categoria(prov, municipio)
    t0 = time.time()
    # localizar el anuncio: por CVE si lo dan, si no por materia (escalera de recall)
    try:
        mcve = re.search(r"BOP-[A-Z]{1,4}-\d{4}-\d+|BOCM-\d{8}-\d+", ordenanza, re.I)
        if mcve:
            res = _buscar_raw(prov, mcve.group(0), cat, rpp=10) or _buscar_raw(prov, mcve.group(0), None, rpp=10)
            m = next((r for r in res if r["cve"] == mcve.group(0)), None) or (res[0] if res else None)
        elif PROVINCIAS[prov].get("verifica_texto"):
            # títulos genéricos (BOCM): se decide leyendo el contenido de los mejores
            res = _buscar_raw(prov, ordenanza, cat, rpp=40)
            m = _mejor_verificado(prov, res, ordenanza)
        elif PROVINCIAS[prov].get("fulltext"):
            # backend full-text (Sphinx): confiar en la relevancia del buscador
            res = _buscar_raw(prov, ordenanza, cat, rpp=40)
            m = _mejor_fulltext(res, ordenanza)
        else:
            # 1) consulta directa de la materia (camino rápido)
            res = _buscar_raw(prov, ordenanza, cat, rpp=60)
            m = _mejor(res, ordenanza)
            if not m:
                # 2) volcado genérico del municipio + ranking local (recall profundo)
                m = _mejor(_candidatos(prov, cat, ordenanza), ordenanza)
    except Exception as e:  # noqa: BLE001
        return f"Error buscando la ordenanza en el BOP de {PROVINCIAS[prov]['nombre']}: {e}"
    if not m:
        return _honesto(prov, nombre, ordenanza, _supra(prov, ordenanza))
    try:
        texto, via = _texto(prov, m)
    except Exception as e:  # noqa: BLE001
        return f"Localicé la ordenanza «{m['titulo']}» ({m.get('cve','')}) pero no pude leer su PDF: {e}"
    if not texto:
        return (f"Localicé «{m['titulo']}» ({m.get('cve','')}) pero su PDF no tiene texto legible. "
                f"Enlace oficial: {m['url']}")
    cab = _cabecera(prov, nombre, m)
    if articulo.strip():
        arts = _articulos(texto)
        cuerpo = _articulo_num(arts, articulo)
        if not cuerpo:
            idx = "\n".join(r for r, _ in arts[:40])
            return f"{cab}\n\nNo encuentro el artículo «{articulo}». Índice:\n{idx[:2500]}"
        cuerpo = cuerpo[:max_chars] if max_chars > 0 else cuerpo
        return f"{cab}\n\n{cuerpo}\n\n(vía {via} · {(time.time()-t0)*1000:.0f} ms)"
    if parrafos and int(parrafos) > 0:
        # anuncio corto (aprobación, derogación, modificación puntual): devolverlo
        # ENTERO es más útil y más fiel que recortar un fragmento suelto
        corto = _limpia(texto)
        if len(corto) <= 6000:
            return f"{cab}\n\n{corto}\n\n(vía {via} · {(time.time()-t0)*1000:.0f} ms)"
        pas = _pasajes(texto, terminos or ordenanza, int(parrafos))
        if not pas:
            return f"{cab}\n\nSin pasajes que maquen «{terminos}». Enlace: {m['url']}"
        return f"{cab}\n\n" + "\n\n[...]\n\n".join(pas) + f"\n\n(vía {via} · {(time.time()-t0)*1000:.0f} ms)"
    cuerpo = _limpia(texto)
    tope = max_chars if max_chars > 0 else 55000
    if len(cuerpo) > tope:
        corte = cuerpo.rfind("\n", 0, tope)
        cuerpo = cuerpo[:corte if corte > 0 else tope] + "\n[TRUNCADO: pide un articulo concreto o parrafos=3 + terminos]"
    return f"{cab}\n\n{cuerpo}\n\n(vía {via} · {(time.time()-t0)*1000:.0f} ms)"
