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
import time
import unicodedata
import urllib.parse
import urllib.request
import http.cookiejar

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

# prefijos de entidad en las facets de algunos BOP ("AYUNTAMIENTO DE BAZA", "CONCELLO DE...")
_PREF_ENT = re.compile(r"^(?:EXCMO\.?\s+)?(?:AYUNTAMIENTO|AYTO\.?|CONCELLO|CONCEJO|"
                       r"AJUNTAMENT|UDALA)\s+(?:DE\s+LA\s+|DE\s+L'|DEL?\s+|D')?", re.I)
# entidades NO municipales: se indexan igual pero con menor prioridad
_ENT_MENOR = re.compile(r"junta vecinal|mancomunidad|consorcio|entidad local|diputaci|"
                        r"comarca|pedan[ií]a|e\.?l\.?m\.?", re.I)


def _limpia_nombre(k):
    return _PREF_ENT.sub("", k.strip()).strip()


def _cargar_mapas():
    if _MAPAS:
        return
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
        _MAPAS[prov] = m
        _IDX[prov] = {}
        _NOMBRES[prov] = {}
        # primero los municipios (ayuntamientos); después juntas vecinales, etc.
        claves = sorted(m, key=lambda k: bool(_ENT_MENOR.search(k)))
        for k in claves:
            limpio = _limpia_nombre(k)
            kn = _norm(limpio)
            if not kn:
                continue
            _IDX[prov].setdefault(kn, m[k])
            _NOMBRES[prov].setdefault(kn, limpio if limpio.upper() != limpio else limpio.title())
            _MUNI2PROV.setdefault(kn, prov)


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
    return _saga_texto(prov, m["url"] if isinstance(m, dict) else m, ocr, max_pag)


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
    return bool(re.search(r"ordenanza|reglamento|\btasa\b|precio p[uú]blico", t, re.I))


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
    (r"derogaci[oó]n", None),
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
    t0 = time.time()
    try:
        res = _candidatos(prov, cat, consulta)
    except Exception as e:  # noqa: BLE001
        return f"Error consultando el BOP de {PROVINCIAS[prov]['nombre']}: {e}"
    if consulta.strip():
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
        mcve = re.search(r"BOP-[A-Z]{1,4}-\d{4}-\d+", ordenanza)
        if mcve:
            res = _buscar_raw(prov, mcve.group(0), cat, rpp=10) or _buscar_raw(prov, mcve.group(0), None, rpp=10)
            m = next((r for r in res if r["cve"] == mcve.group(0)), None) or (res[0] if res else None)
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
