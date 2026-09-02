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
import tempfile
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
            # `excluir` debe casar tanto con la clave CRUDA del mapa como con el
            # nombre ya limpio de prefijos: el listado de Huesca trae "AYUNTAMIENTO
            # DE SANTANDER" y excluir "Santander" no filtraba nada (Santander
            # acababa resolviéndose con el BOP de Huesca).
            excl = {_norm(x) for x in cfg.get("excluir", [])}
            if excl:
                m = {k: v for k, v in m.items()
                     if _norm(k) not in excl and _norm(_limpia_nombre(k)) not in excl}
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


# Nombres alternativos (castellano ↔ cooficial, abreviaturas de uso común) -> el
# nombre con el que figura en el mapa del BOP. Solo se consultan cuando el nombre
# exacto no está en el índice. Origen (2-sep-2026): el banco de los 300 municipios
# >50k hab. no resolvía "Crevillente" (el mapa dice Crevillent), "La Coruña",
# "Las Rozas", "La Laguna", "Hospitalet", "Santiago"...
_ALIAS_MUNI = {
    "crevillente": "crevillent", "la coruna": "a coruna", "coruna": "a coruna",
    "donostia": "san sebastian", "donostia san sebastian": "san sebastian",
    "san sebastian donostia": "san sebastian", "iruna": "pamplona", "pamplona iruna": "pamplona",
    "la vila joiosa": "villajoyosa", "vila joiosa": "villajoyosa", "renteria": "errenteria",
    "sangenjo": "sanxenxo", "villagarcia de arousa": "vilagarcia de arousa",
    "villagarcia": "vilagarcia de arousa", "riveira": "ribeira", "vich": "vic",
    "hospitalet": "l hospitalet de llobregat", "l hospitalet": "l hospitalet de llobregat",
    "hospitalet de llobregat": "l hospitalet de llobregat",
    "san baudilio": "sant boi de llobregat", "san baudilio de llobregat": "sant boi de llobregat",
    "sant boi": "sant boi de llobregat", "cornella": "cornella de llobregat",
    "petrel": "petrer", "maspalomas": "san bartolome de tirajana",
    "santa cruz": "santa cruz de tenerife", "zarauz": "zarautz",
    "santiago": "santiago de compostela", "guecho": "getxo", "galdacano": "galdakao",
    "lejona": "leioa", "santurce": "santurtzi", "onteniente": "ontinyent",
    "alcira": "alzira", "jativa": "xativa", "torrente": "torrent", "burjasot": "burjassot",
    "aldaya": "aldaia", "alacuas": "alaquas", "cuart de poblet": "quart de poblet",
    "gerona": "girona", "figueras": "figueres", "mahon": "mao", "ibiza": "eivissa",
    "palma de mallorca": "palma", "santa eulalia del rio": "santa eularia des riu",
    "rivas": "rivas vaciamadrid", "rivas vaciamadrid": "rivas-vaciamadrid",
    "las rozas": "las rozas de madrid", "la laguna": "san cristobal de la laguna",
    "santa lucia": "santa lucia de tirajana", "granadilla": "granadilla de abona",
    "orotava": "la orotava", "realejos": "los realejos", "icod": "icod de los vinos",
    "los llanos": "los llanos de aridane", "vendrell": "el vendrell", "roquetas": "roquetas de mar",
    "ejido": "el ejido", "amorebieta": "amorebieta etxano", "collado villalba": "collado villalba",
    "villalba": "collado villalba", "san fernando de henares": "san fernando de henares",
    "puerto de santa maria": "el puerto de santa maria", "la linea": "la linea de la concepcion",
    "sanlucar": "sanlucar de barrameda", "chiclana": "chiclana de la frontera",
    "jerez": "jerez de la frontera", "arcos": "arcos de la frontera",
    "moron": "moron de la frontera", "alcala de guadaira": "alcala de guadaira",
    "los palacios": "los palacios y villafranca", "mairena": "mairena del aljarafe",
    "velez malaga": "velez-malaga", "rincon de la victoria": "rincon de la victoria",
    "alhaurin": "alhaurin de la torre", "molina": "molina de segura", "torre pacheco": "torre-pacheco",
    "san pedro": "san pedro del pinatar", "san javier": "san javier", "caravaca": "caravaca de la cruz",
    "las torres": "las torres de cotillas", "san vicente": "san vicente del raspeig",
    "vilanova": "vilanova i la geltru", "sant cugat": "sant cugat del valles",
    "santa coloma": "santa coloma de gramenet", "cerdanyola": "cerdanyola del valles",
    "mollet": "mollet del valles", "esplugues": "esplugues de llobregat",
    "sant feliu": "sant feliu de llobregat", "barbera": "barbera del valles",
    "sant adria": "sant adria de besos", "el prat": "el prat de llobregat", "prat de llobregat": "el prat de llobregat",
    "torrejon": "torrejon de ardoz", "san sebastian de los reyes": "san sebastian de los reyes",
    "pozuelo": "pozuelo de alarcon", "boadilla": "boadilla del monte", "arganda": "arganda del rey",
    "colmenar": "colmenar viejo", "villaviciosa": "villaviciosa de odon",
    "san bartolome": "san bartolome de tirajana", "puerto del rosario": "puerto del rosario",
    "vega de san mateo": "vega de san mateo",
}
_ALIAS_N = {_norm(k): _norm(v) for k, v in _ALIAS_MUNI.items()}


# Nombres que solo se resuelven EXACTOS (nunca por aproximación): capitales de
# provincia y municipios grandes de provincias aún sin cubrir. Sin esto, «Cuenca»
# caía en «Cuenca de Campos» (Valladolid) y «Palencia» en «Palenciana» (Córdoba).
_PROTEGIDOS = {_norm(x) for x in (
    "Albacete", "Alicante", "Almería", "Ávila", "Badajoz", "Barcelona", "Bilbao", "Burgos", "Cáceres",
    "Cádiz", "Castellón", "Castellón de la Plana", "Ciudad Real", "Córdoba", "Cuenca", "Girona", "Granada",
    "Guadalajara", "Huelva", "Huesca", "Jaén", "León", "Lleida", "Logroño", "Lugo", "Madrid", "Málaga",
    "Murcia", "Ourense", "Oviedo", "Palencia", "Palma", "Pamplona", "Pontevedra", "Salamanca",
    "San Sebastián", "Santander", "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo",
    "Valencia", "Valladolid", "Vitoria", "Zamora", "Zaragoza", "Ceuta", "Melilla", "Santa Cruz de Tenerife",
    "Las Palmas", "A Coruña", "Valdepeñas", "Puertollano", "Tomelloso", "Alcázar de San Juan",
    "Torrelavega", "Castro-Urdiales", "Camargo", "Mérida", "Almendralejo", "Vila-real", "Burriana",
    "Miranda de Ebro", "Hellín", "Villarreal", "Borriana")}


def _clave_muni(kn, pid=None, kn_original=None):
    """Resuelve un nombre normalizado a (provincia, clave del índice) probando:
    exacto -> alias -> palabra completa única. Con `pid` se restringe a esa
    provincia (forma "Municipio, Provincia"). `kn_original` = (nombre tal cual,)
    para la aproximación por palabras."""
    if not kn:
        return None, None
    universo = {pid: _IDX.get(pid, {})} if pid else _IDX
    for cand in (kn, _ALIAS_N.get(kn, "")):
        if not cand:
            continue
        for p, idx in universo.items():
            if cand in idx:
                return p, cand
    if len(kn) < 4 or kn in _PROTEGIDOS:
        return None, None
    # aproximación solo por PALABRA completa (con el nombre legible, que conserva
    # espacios): «las rozas» -> «las rozas de madrid», «hospitalet» -> «l'hospitalet
    # de llobregat»; pero «cuenca» NO puede caer en «cuenca de campos» (Valladolid)
    # ni «palencia» en «palenciana» (Córdoba): las capitales y los municipios
    # grandes de provincias sin cubrir están en _PROTEGIDOS y solo casan exactos.
    qn = " ".join(_mnorm(kn_original[0]).split()) if kn_original else ""
    hits = []
    for p, idx in universo.items():
        for k in idx:
            nom = _mnorm(_NOMBRES.get(p, {}).get(k, ""))
            if qn and nom and (nom.startswith(qn + " ") or (" " + qn + " ") in (" " + nom + " ")):
                hits.append((p, k))
    # entidades menores (juntas vecinales, mancomunidades) no compiten con municipios
    if len(hits) > 1:
        munis = [(p, k) for p, k in hits if not _ENT_MENOR.search(_NOMBRES.get(p, {}).get(k, ""))]
        hits = munis or hits
    if len(hits) == 1:
        return hits[0]
    return None, None


def provincia_de(municipio):
    """Devuelve la provincia cuyo BOP cubre el municipio, o None."""
    _cargar_mapas()
    muni, pid = _parse_muni(municipio)
    p, _ = _clave_muni(_norm(muni), pid, (muni,))
    return p


def _categoria(prov, municipio):
    _cargar_mapas()
    muni, _ = _parse_muni(municipio)
    _, k = _clave_muni(_norm(muni), prov, (muni,))
    return _IDX.get(prov, {}).get(k) if k else None


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
_RAW_CACHE = {}          # (prov, texto, categoria, rpp) -> (ts, resultados)
_RAW_LOCK = threading.Lock()


def _buscar_raw(prov, texto, categoria=None, rpp=40, timeout=20):
    """Con memoria de 10 min: el chat llama buscar_ordenanzas y acto seguido
    leer_ordenanza con la misma materia, y leer repetía la consulta al boletín
    (en Tarragona/Girona/Barcelona son 5-18 s cada una). Misma instancia de
    Vercel = misma memoria; si cae en otra, simplemente vuelve a consultar."""
    clave = (prov, (texto or "").strip().lower(), categoria, rpp)
    with _RAW_LOCK:
        c = _RAW_CACHE.get(clave)
        if c and time.time() - c[0] < 600:
            return [dict(r) for r in c[1]]
    res = _buscar_raw_sin_cache(prov, texto, categoria, rpp, timeout)
    with _RAW_LOCK:
        if len(_RAW_CACHE) > 300:
            _RAW_CACHE.clear()
        _RAW_CACHE[clave] = (time.time(), [dict(r) for r in res])
    return res


def _buscar_raw_sin_cache(prov, texto, categoria=None, rpp=40, timeout=20):
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
    if fam == "asturias":
        return _asturias_buscar(prov, texto, categoria, rpp)
    if fam == "valencia":
        return _valencia_buscar(prov, texto, categoria, rpp)
    if fam == "barcelona":
        return _barcelona_buscar(prov, texto, categoria, rpp)
    if fam == "navarra":
        return _navarra_buscar(prov, texto, categoria, rpp)
    if fam == "cantabria":
        return _cantabria_buscar(prov, texto, categoria, rpp)
    if fam == "cordoba":
        return _cordoba_buscar(prov, texto, categoria, rpp)
    if fam == "baleares":
        return _baleares_buscar(prov, texto, categoria, rpp)
    if fam == "almeria":
        return _almeria_buscar(prov, texto, categoria, rpp)
    if fam == "girona":
        return _girona_buscar(prov, texto, categoria, rpp)
    if fam == "valladolid":
        return _valladolid_buscar(prov, texto, categoria, rpp)
    ext = _backend_externo(fam)
    if ext is not None:
        return ext.buscar(prov, texto, categoria, rpp)
    return _saga_buscar_raw(prov, texto, categoria, rpp, timeout)


# Backends EXTERNOS: familias nuevas viven en su propio módulo bop_<familia>.py
# (funciones buscar(prov, texto, filtro, rpp) -> [{url,titulo,cve,fecha,orden,...}]
# y texto(prov, m) -> (texto, via)). Así se añaden provincias sin tocar este
# fichero (y varias a la vez sin pisarse). Reciben PROVINCIAS[prov] como config.
_EXT = {}


def _backend_externo(fam):
    if fam in _EXT:
        return _EXT[fam]
    try:
        import importlib
        mod = importlib.import_module("bop_" + fam)
        if not (hasattr(mod, "buscar") and hasattr(mod, "texto")):
            mod = None
    except Exception:  # noqa: BLE001
        mod = None
    _EXT[fam] = mod
    return mod


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
    if fam == "asturias":
        return _asturias_texto(prov, m)
    if fam == "valencia":
        return _valencia_texto(prov, m)
    if fam == "barcelona":
        return _barcelona_texto(prov, m)
    if fam == "navarra":
        return _navarra_texto(prov, m)
    if fam == "cantabria":
        return _cantabria_texto(prov, m)
    if fam == "cordoba":
        return _cordoba_texto(prov, m)
    if fam == "baleares":
        return _baleares_texto(prov, m)
    if fam == "almeria":
        return _almeria_texto(prov, m)
    if fam == "girona":
        return _girona_texto(prov, m)
    if fam == "valladolid":
        return _valladolid_texto(prov, m)
    ext = _backend_externo(fam)
    if ext is not None:
        return ext.texto(prov, m)
    return _saga_texto(prov, m["url"] if isinstance(m, dict) else m, ocr, max_pag)


# ---- backend VALLADOLID (Liferay + portlet BOPBusqueda) ----------------------
# GET puro, sin cookies ni token: los parámetros van en un JSON codificado en
# base64. Es de las más rápidas del motor (0,74 s E2E) y el PDF por anuncio ya
# viene en el listado.
_VA_FILA = re.compile(r"<tr[^>]*role=\"row\"[^>]*>(.*?)</tr>", re.S)
_VA_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _valladolid_buscar(prov, texto, organismo=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not organismo:
        return []

    def una(q):
        # btoa no admite no-ASCII: el texto va urlencoded DENTRO del JSON
        datos = {"textToSearch": urllib.parse.quote(q), "scopeTexto": "1", "section": "0",
                 "organismo": str(organismo), "fechaDesde": "", "fechaHasta": "",
                 "anyo": "0", "numeroboletin": "", "elementsPerPage": "20",
                 "scopeGranularidad": "3"}
        h64 = base64.b64encode(json.dumps(datos).encode()).decode()
        try:
            h = _madrid_get(f"{cfg['base']}/buscarenbop?jsonDataAsHash={h64}",
                            timeout=35, intentos=1)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for fila in _VA_FILA.findall(h):
            tds = _VA_TD.findall(fila)
            if len(tds) < 8:
                continue
            # la 1ª celda es el botón de detalle: las columnas van corridas una
            pdf = re.search(r'href="(https?://[^"]+\.pdf)"', tds[1])
            if not pdf:
                continue

            def lim(x):
                return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()

            fecha = lim(tds[3])
            d, mo, y = (fecha.split("/") + ["", "", ""])[:3]
            out.append({"url": pdf.group(1), "titulo": lim(tds[4]),
                        "cve": lim(tds[5]) or pdf.group(1)[-22:], "fecha": fecha,
                        "orden": f"{y}{mo}{d}" if y else "0",
                        "organismo": lim(tds[7]) if len(tds) > 7 else "",
                        "materia": q != "ordenanza"})
        return out

    # cada respuesta pesa ~300 KB (el servidor incrusta un <select> de 1.070
    # organismos en TODAS), así que se lanza UNA consulta y solo se prueba la
    # siguiente si no dio nada: con 3 en paralelo la latencia se disparaba a 30 s
    # desde Vercel aunque en local fuese 1,2 s.
    vistos = {}
    for q in _consultas_materia(texto, None):
        for r in una(q):
            if r["cve"] in vistos:
                vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
            else:
                vistos[r["cve"]] = r
        if any(_es_ordenanza(r["titulo"]) and not _NO_NORMA.search(r["titulo"])
               for r in vistos.values()):
            break
    return list(vistos.values())


def _valladolid_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        pdf = _getb(u, timeout=45)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = _pdf_bytes_texto(pdf)
    return (t, via) if len(t) > 400 else ("", "sin-texto")


# ---- backend GIRONA (eBOP, JSF/Jakarta Faces) --------------------------------
# El coste lo domina el filtro `entitat`: ~2,2 s por año de ventana, lineal. Sin
# acotar son 52 s; con VENTANAS DE 3 AÑOS EN PARALELO, ~8 s y el mismo conjunto
# exacto de resultados. Paginar no compensa (una página cuesta una búsqueda).
_GI_BLOQUE = re.compile(r'<div class="resultat-cerca\s*"(.*?)</dl>', re.S)
_GI_DT = re.compile(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", re.S)
_GI_PDF = re.compile(r'<a href="(https?://[^"]+\.pdf)"[^>]*>(.*?)</a>', re.S)


def _girona_buscar(prov, texto, entitat=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not entitat:
        return []
    ck = _norm(entitat)
    desde = int(cfg.get("indice_desde", 2001))
    hasta = time.gmtime().tm_year
    ventanas = [(a, min(a + 2, hasta)) for a in range(desde, hasta + 1, 3)]
    # el eBOP está SOLO en catalán (castellano ≈ 0 resultados en todo el corpus):
    # la forma catalana va PRIMERO o se gastan 9 ventanas en una consulta estéril
    _locales = {_norm(v) for v in _CATALA.values()}
    consultas = sorted(_consultas_materia(texto, "ca"),
                       key=lambda q: 0 if _norm(q) in _locales else 1)[:2]

    def una(args):
        (y0, y1), q = args
        try:
            cj = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            op.addheaders = [("User-Agent", _UA), ("Accept-Language", "ca,es;q=0.9")]
            h = op.open(cfg["base"] + "/bop/cerca", timeout=25).read().decode("utf-8", "replace")
            # el name del submit es AUTOGENERADO (j_idt81 hoy, j_idt78 ayer): nunca fijarlo
            fm = re.search(r'(?s)<form id="formCercaContingut"[^>]*action="([^"]+)"(.*?)</form>', h)
            if not fm:
                return []
            action, cuerpo = fm.group(1), fm.group(2)
            vs = re.findall(r'name="jakarta\.faces\.ViewState"[^>]*value="([^"]+)"', h)
            sub = re.search(r'name="(formCercaContingut:j_idt\d+)"[^>]*value="Cerca', cuerpo) or \
                re.search(r'type="submit"[^>]*name="(formCercaContingut:j_idt\d+)"', cuerpo)
            if not (vs and sub):
                return []
            d = {"formCercaContingut": "formCercaContingut",
                 "formCercaContingut:exerciciDesde": str(y0),
                 "formCercaContingut:exerciciFins": str(y1),
                 "formCercaContingut:edicteDesde": "", "formCercaContingut:edicteFins": "",
                 "formCercaContingut:bopDesde": "", "formCercaContingut:bopFins": "",
                 "formCercaContingut:dataDesde": "", "formCercaContingut:dataFins": "",
                 "formCercaContingut:seccio": "", "formCercaContingut:titol": q,
                 "formCercaContingut:entitat": str(entitat), "formCercaContingut:text": "",
                 sub.group(1): "Cerca", "jakarta.faces.ViewState": vs[-1]}
            r = op.open(urllib.request.Request(
                cfg["base"] + action if action.startswith("/") else action,
                data=urllib.parse.urlencode(d).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Referer": cfg["base"] + "/bop/cerca"}),
                timeout=22).read().decode("utf-8", "replace")   # una ventana atascada no arrastra al resto
        except Exception:  # noqa: BLE001
            return []
        out = []
        for bloque in _GI_BLOQUE.findall(r):
            campos = {}
            for dt, dd in _GI_DT.findall(bloque):
                k = _norm(re.sub(r"<[^>]+>", " ", dt))
                campos[k] = dd
            ent = re.sub(r"\s+", " ", _html.unescape(
                re.sub(r"<[^>]+>", " ", campos.get("entitat", "")))).strip()
            if _norm(ent) != ck:          # `entitat` casa por substring → igualdad aquí
                continue
            mp = _GI_PDF.search(campos.get("titol", ""))
            if not mp:
                continue
            tit = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", mp.group(2)))).strip()
            fecha = re.sub(r"\s+", " ", _html.unescape(
                re.sub(r"<[^>]+>", " ", campos.get("data", "")))).strip()
            d2, m2, y2 = (fecha.split("/") + ["", "", ""])[:3]
            ed = re.sub(r"\D", "", campos.get("numedicte", "")) or mp.group(1)[-12:]
            out.append({"url": mp.group(1), "titulo": tit, "cve": f"BOP-GI-{y2 or y0}-{ed}",
                        "fecha": fecha, "orden": f"{y2}{m2}{d2}" if y2 else str(y0),
                        "materia": q not in ("ordenanza", "ordenança")})
        return out

    vistos = {}

    def barrido(q, vs):
        with _cf.ThreadPoolExecutor(max_workers=max(1, len(vs))) as ex:
            for rs in ex.map(una, [(v, q) for v in vs]):
                for r in rs:
                    if r["cve"] in vistos:
                        vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                    else:
                        vistos[r["cve"]] = r

    # Dos fases: casi todo lo que se pregunta es reciente, así que primero las 2
    # últimas ventanas (~6 años, 1 tanda) y solo se barre el archivo entero —caro—
    # si de ahí no sale ninguna ordenanza de la materia.
    barrido(consultas[0], ventanas[-2:])
    util = [r for r in vistos.values() if _es_ordenanza(r["titulo"])
            and not _NO_NORMA.search(r["titulo"])]
    if not util:
        barrido(consultas[0], ventanas[:-2])
    if not vistos and len(consultas) > 1:
        barrido(consultas[1], ventanas[-3:])
    return list(vistos.values())


def _girona_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:                        # el PDF se descarga sin sesión ni cookies
        pdf = _getb(u, timeout=45)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = _pdf_bytes_texto(pdf)
    return (t, via) if len(t) > 400 else ("", "sin-texto")


# ---- backend ALMERÍA (archivo Pandora/DIGIBIS del propio BOP) ----------------
# NO se usa su app ZK (estado de desktop en servidor, inviable en serverless):
# el mismo host expone el archivo Pandora, que sirve el TEXTO PLANO del boletín
# entero. Sin PDF, sin OCR, sin sesión. El anuncio se recorta en local.
_AL_ART = re.compile(
    r"(?m)^\s*(?:Documento firmado electr[óo]nicamente[^\n]*|"
    r"[_\w.]*B[_\w.]*O[_\w.]*P[_\w.]*de[_\w.]*Alm[_\w.]*[^\n]*P[_\w.]*g\.[_\w. ]*\d+[^\n]*)$")
_AL_MARCA = re.compile(r"(?m)^\s*(\d{1,6}/\d{2})\s*\n\s*([A-ZÁÉÍÓÚÜÑ][^\n]{3,80})\s*$")


def _almeria_buscar(prov, texto, entidad=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not entidad:
        return []
    ck = _norm(entidad)
    consultas = [q for q in _consultas_materia(texto, None)][:3]

    def busca(q):
        # la proximidad ~150 es lo que hace utilizable la consulta: sin ella, el AND
        # casa cualquier aparición suelta en las ~50 páginas del boletín
        query = f'type:bulletin AND "{entidad} {q}"~150'
        body = urllib.parse.urlencode([("query", query), ("length", "6"), ("load", "true"),
                                       ("retain", "id"), ("retain", "filename")]).encode()
        try:                       # OJO: sin `sort` -> relevancia (con él, boletines de 1834)
            r = urllib.request.urlopen(urllib.request.Request(
                cfg["base"] + "/json/select.vm", data=body,
                headers={"User-Agent": _UA,
                         "Content-Type": "application/x-www-form-urlencoded"}), timeout=25).read()
            d = json.loads(r.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return []
        return [(x["id"][0], (x.get("filename") or [""])[0]) for x in d.get("documents", [])]

    boletines = {}
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        for rs in ex.map(busca, consultas):
            for ident, fn in rs:
                boletines.setdefault(ident, fn)

    def anuncios(par):
        ident, fn = par
        try:
            t = _madrid_get(f"{cfg['base']}/text.vm?id={ident}&view=boletines&lang=es"
                            f"&attachment=x.txt", timeout=30, intentos=1)
        except Exception:  # noqa: BLE001
            return []
        t = _AL_ART.sub("", t)          # pie legal y cabecera con guiones bajos intercalados
        marcas = list(_AL_MARCA.finditer(t))
        out = []
        for i, m in enumerate(marcas):
            ent = re.sub(r"\s+", " ", m.group(2)).strip()
            if _norm(ent) != ck:
                continue
            fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(t)
            cuerpo = t[m.end():fin].strip()
            if len(cuerpo) < 300:
                continue
            # el anuncio empieza con el edicto ("Al no haberse presentado alegaciones…"),
            # así que el nombre de la norma hay que sacarlo del cuerpo o el ranking
            # y el filtro `_es_ordenanza` se quedan sin señal
            plano = re.sub(r"\s+", " ", cuerpo[:2500])
            mo = re.search(r"((?:Ordenanza|Reglamento|Ordenanzas)[^.;]{5,160})", plano, re.I)
            titulo = (mo.group(1) if mo else
                      re.sub(r"(?i)^\s*A\s*N\s*U\s*N\s*C\s*I\s*O\s*", "", plano)[:220]).strip()
            fe = f"{fn[6:8]}/{fn[4:6]}/{fn[:4]}" if len(fn) == 8 else ""
            out.append({"url": f"{cfg['base']}/text.vm?id={ident}&view=boletines&lang=es",
                        "titulo": titulo, "cve": f"BOP-AL-{fn}-{m.group(1).replace('/', '-')}",
                        "fecha": fe, "orden": fn or "0", "texto": cuerpo, "materia": True})
        return out

    res = []
    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        for rs in ex.map(anuncios, list(boletines.items())[:8]):
            res.extend(rs)
    return res


def _almeria_texto(prov, m):
    """El texto ya viene recortado de la búsqueda: cero peticiones extra."""
    t = m.get("texto") if isinstance(m, dict) else ""
    return (t, "texto") if t and len(t) > 300 else ("", "sin-texto")


# ---- backend ILLES BALEARS (BOIB, webapp del Govern) -------------------------
# GET simple sin cookies. Tres reglas que salieron del sondeo: (1) sin rango de
# fechas solo busca el ÚLTIMO MES; (2) `texto` es FRASE LITERAL y sensible a
# tildes (no hay AND/OR) → una sola palabra catalana bien acentuada; (3)
# `organisme` casa por substring, así que "Sant Joan" arrastra "Sant Joan de
# Labritja" → el municipio se confirma por igualdad exacta en local.
_BA_BLOQUE = re.compile(r'<li>\s*<div class="caja">(.*?)</div>\s*</li>', re.S)
_BA_META = re.compile(r"BOIB\s+Núm\s+(\d+)/(\d{4})\s+de\s+(\d{2}/\d{2}/\d{4})\s*-\s*"
                      r"Número d'edicte:\s*(\d+)")
_BA_ENL = re.compile(r'href="(/eboibfront/[a-z]{2}/\d{4}/\d+/(\d+)/[^"]*)"')
_BA_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_BA_CUERPO = re.compile(r'(?s)<div[^>]+id="contenidoEdicto"[^>]*>(.*?)'
                        r'(?:<h3>\s*Documents adjunts|<div class="mensajeValidez|'
                        r'<div class="validez|<!-- /columna central|<script|<footer|\Z)')


def _baleares_buscar(prov, texto, organisme=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not organisme:
        return []
    ck = _norm(organisme)
    hoy = time.gmtime()
    fin = f"{hoy.tm_mday:02d}/{hoy.tm_mon:02d}/{hoy.tm_year}"
    ini = f"01/01/{cfg.get('indice_desde', 2012)}"

    def una(q):
        p = {"cerca": "Enviar", "lang": "ca", "organisme": str(organisme),
             "texto": q, "fec_ini": ini, "fec_fin": fin}
        try:
            h = _madrid_get(cfg["base"] + "/eboibfront/cercar?" + urllib.parse.urlencode(p),
                            timeout=30, intentos=1)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for bloque in _BA_BLOQUE.findall(h):
            enl = _BA_ENL.search(bloque)
            if not enl:
                continue
            ps = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()
                  for x in _BA_P.findall(bloque)]
            org = ps[0] if ps else ""
            if _norm(org) != ck:            # substring del buscador → igualdad aquí
                continue
            tit = ps[1] if len(ps) > 1 else ""
            me = _BA_META.search(re.sub(r"<[^>]+>", " ", bloque))
            fecha = me.group(3) if me else ""
            d, mo, y = (fecha.split("/") + ["", "", ""])[:3]
            out.append({"url": cfg["base"] + enl.group(1), "titulo": tit,
                        "cve": f"BOIB-{me.group(2)}-{me.group(4)}" if me else enl.group(2),
                        "fecha": fecha, "orden": f"{y}{mo}{d}" if y else "0",
                        "ident": enl.group(2), "materia": q != "ordenança"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        for rs in ex.map(una, _consultas_materia(texto, "ca", generico="ordenança")):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    return list(vistos.values())


def _baleares_texto(prov, m):
    cfg = PROVINCIAS[prov]
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        h = _madrid_get(u, timeout=30, intentos=1)
        mm = _BA_CUERPO.search(h)
        if mm:
            t = _html_a_texto(mm.group(1))
            if len(t) > 600:
                return t, "html"
    except Exception:  # noqa: BLE001
        pass
    ident = m.get("ident") if isinstance(m, dict) else None
    if ident:                       # ~5 %: el articulado va en un anexo PDF
        try:
            pdf = _getb(f"{cfg['base']}/eboibfront/pdf/VisPdf?action=VisEnviament"
                        f"&idEnviament={ident}&lang=ca", timeout=45)
            if pdf[:5] == b"%PDF-":
                return _pdf_bytes_texto(pdf)
        except Exception:  # noqa: BLE001
            pass
    return "", "sin-texto"


# ---- backend CÓRDOBA (Next.js SSR; resultados en el payload RSC) -------------
# Sin cookies ni captcha y muy rápido (0,3 s). Dos claves: `buscar=1` es
# OBLIGATORIO (sin él responde 200 sin resultados) y el buscador tiene stemming
# agresivo —"ordenanza" == "orden" == "ordenar"— así que consultar "ordenanza" es
# inútil: se consulta SOLO con el término distintivo y se rankea por título.
_CO_PAG = re.compile(r'"pagination":\{"page":(\d+),"pageSize":(\d+),"pageCount":(\d+),"total":(\d+)\}')
_CO_ITEM = re.compile(
    r'\["\$","li","(\d+)",\{"className":"announcement[^"]*","children":\['
    r'\["\$","h3",null,\{"children":"((?:[^"\\]|\\.)*)"\}\],'
    r'\["\$","p",null,\{"children":\[" ","((?:[^"\\]|\\.)*)"')
_CO_PDF = re.compile(r'"href":"(/visor-pdf/(\d{2}-\d{2}-\d{4})/(BOP-A-\d{4}-\d+)\.pdf)"')


def _co_flight(h):
    """Concatena y des-escapa el payload RSC donde Next.js mete los resultados."""
    partes = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)', h)
    out = []
    for p in partes:
        try:
            out.append(json.loads('"' + p + '"'))
        except Exception:  # noqa: BLE001
            pass
    return "".join(out)


def _cordoba_buscar(prov, texto, emisores=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not emisores:
        return []
    # 3 municipios tienen el histórico partido en dos anunciantes (el BOP los
    # renombró en 2023): el mapa admite ids separados por coma
    ids = [i.strip() for i in str(emisores).split(",") if i.strip()]
    consultas = [q for q in _consultas_materia(texto, None) if q != "ordenanza"] or ["ordenanza"]
    tareas = [(i, q) for i in ids for q in consultas]

    def una(t):
        idp, q = t
        p = {"buscar": "1", "texto": q.lower(), "emisor": idp,   # en MAYÚSCULAS da menos
             "ordenarPor": "1", "porPagina": str(max(rpp, 40))}
        try:
            h = _madrid_get(cfg["base"] + "/buscar?" + urllib.parse.urlencode(p),
                            timeout=25, intentos=1)
        except Exception:  # noqa: BLE001
            return []
        fl = _co_flight(h)
        out = []
        for trozo in re.split(r'(?=\["\$","li","\d+",\{"className":"announcement)', fl)[1:]:
            it = _CO_ITEM.search(trozo)
            pdf = _CO_PDF.search(trozo)
            if not (it and pdf):
                continue
            tit = re.sub(r"\s+", " ", _html.unescape(it.group(3))).strip()
            d, mo, y = pdf.group(2).split("-")
            out.append({"url": cfg["base"] + pdf.group(1), "titulo": tit,
                        "cve": pdf.group(3), "fecha": f"{d}/{mo}/{y}",
                        "orden": f"{y}{mo}{d}", "materia": q != "ordenanza"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        for rs in ex.map(una, tareas):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    return list(vistos.values())


def _cordoba_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        pdf = _getb(u, timeout=45)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = _pdf_bytes_texto(pdf)
    return (t, via) if len(t) > 400 else ("", "sin-texto")


# ---- backend CANTABRIA (BOC, Struts propio "boces") --------------------------
# El BOC es autonómico y hace de BOP de los 102 ayuntamientos. NO tiene filtro por
# municipio (su lista de entidades vuelve vacía), así que: se busca por TÍTULO
# (0,8 s; la búsqueda por cuerpo tarda 39 s y además arrastra municipios ajenos)
# y el municipio se confirma con el <h2>Ayuntamiento de X</h2> de cada resultado.
_CB_BLOQUE = re.compile(r"(?s)<h2>([^<]*Ayuntamiento[^<]*)</h2>(.*?)(?=<h2>|\Z)")
_CB_ANU = re.compile(r'verAnuncio(?:Partes)?Action\.do\?idAnuBlob=(\d+)(?:&orden=(\d+))?"'
                     r'[^>]*>\s*PDF \((BOC-\d{4}-\d+[\w_]*)\)', re.S)
_CB_TIT = re.compile(r"<p>(.*?)</p>", re.S)


def _cantabria_buscar(prov, texto, municipio=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not municipio:
        return []
    ck = _norm(municipio)
    hoy = time.gmtime()

    def una(q):
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        op.addheaders = [("User-Agent", _UA)]
        try:
            op.open(cfg["base"] + "/boces/menu.do?dir=/inicioBusquedaAnuncios.do", timeout=25).read()
            d = {"anuncioBean.entrad": q, "anuncioBean.tipoTexto": "0",   # 0 = título
                 "anuncioBean.tipoBusqueda": "todasPalabras", "anuncioBean.filtroFecha": "1",
                 "anuncioBean.fecDesdeString": f"01/01/{cfg.get('indice_desde', 2010)}",
                 "anuncioBean.fecHastaString": f"{hoy.tm_mday:02d}/{hoy.tm_mon:02d}/{hoy.tm_year}",
                 "idAdmin": "-1", "idEntidad": "-1", "organizacionText": "", "unidadText": "",
                 "anuncioBean.idSeccion": "-1", "anuncioBean.idSubseccion": "-1",
                 "anuncioBean.idTipAnu": "-1", "boton": "Buscar"}
            h = op.open(urllib.request.Request(
                cfg["base"] + "/boces/busquedaAnuncios.do",
                data=urllib.parse.urlencode(d, encoding="iso-8859-15", errors="replace").encode("ascii"),
                headers={"Content-Type": "application/x-www-form-urlencoded"}),
                timeout=60).read().decode("iso-8859-15", "replace")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for org, bloque in _CB_BLOQUE.findall(h):
            nom = re.sub(r"(?i)^\s*Ayuntamiento\s+(?:de\s+)?", "", _html.unescape(org)).strip()
            if _norm(nom) != ck:
                continue                       # el municipio se confirma AQUÍ
            an = _CB_ANU.search(bloque)
            tit = _CB_TIT.search(bloque)
            if not an:
                continue
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1) if tit else ""))).strip()
            cve = an.group(3)
            y = re.search(r"BOC-(\d{4})", cve)
            out.append({"url": f"{cfg['base']}/boces/verAnuncioAction.do?idAnuBlob={an.group(1)}",
                        "titulo": t, "cve": cve, "fecha": "",
                        "orden": (y.group(1) + "0000") if y else "0",
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


def _cantabria_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        pdf = _getb(u, timeout=45)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = _pdf_bytes_texto(pdf)
    return (t, via) if len(t) > 400 else ("", "sin-texto")


# ---- backend NAVARRA (BON, Liferay) ------------------------------------------
# Uniprovincial. Sin token ni captcha: una GET. El filtro NO es "Ayuntamiento de
# X" (devuelve 0) sino la LOCALIDAD a secas en `organoSolicitante`. El anuncio se
# sirve como HTML con el texto íntegro: ni PDF ni OCR.
_NA_P = "_es_navarra_bon_buscador_portlet_BuscadorPortlet_"
_NA_ROW = re.compile(
    r'<a href="(https://bon\.navarra\.es/es/anuncio/-/texto/(\d{4})/(\d+)/(\d+))"\s*title="([^"]*)">'
    r".*?<h6>([^<]*)</h6>.*?<p>(.*?)</p>", re.S)
_NA_CUERPO = re.compile(r'(?s)<div[^>]*class="[^"]*journal-content-article[^"]*"[^>]*>(.*?)</div>\s*</div>')


def _navarra_buscar(prov, texto, localidad=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not localidad:
        return []
    ck = _norm(localidad)

    def una(q):
        p = {"p_p_id": "es_navarra_bon_buscador_portlet_BuscadorPortlet",
             "p_p_lifecycle": "0", "p_p_state": "normal", "p_p_mode": "view",
             _NA_P + "mvcRenderCommandName": "buscar", _NA_P + "titulo": q,
             _NA_P + "organoSolicitante": str(localidad)}
        try:
            h = _madrid_get(cfg["base"] + "/es/busquedas?" + urllib.parse.urlencode(p),
                            timeout=30, intentos=1)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for url, y, bol, orden, titbol, loc, tit in _NA_ROW.findall(h):
            if _norm(loc) != ck:              # el servidor ya filtra; se confirma
                continue
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit))).strip()
            fe = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", _html.unescape(titbol))
            if fe:
                d, mes, an = fe.group(1), _MESES.get(fe.group(2).lower(), "01"), fe.group(3)
                fecha, ordn = f"{int(d):02d}/{mes}/{an}", f"{an}{mes}{int(d):02d}"
            else:
                fecha, ordn = "", y
            out.append({"url": url, "titulo": t, "cve": f"BON-{y}-{bol}-{orden}",
                        "fecha": fecha, "orden": ordn, "materia": q != "ordenanza"})
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


def _navarra_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        h = _madrid_get(u, timeout=30, intentos=1)
    except Exception:  # noqa: BLE001
        return "", "sin-texto"
    # la página repite la clase journal-content-article 7 veces (menús, cabecera,
    # pie…): el anuncio es el bloque MÁS LARGO, no el primero
    trozos = h.split('journal-content-article')
    mejor = ""
    for tr in trozos[1:]:
        t = _html_a_texto(tr[:400000])
        if len(t) > len(mejor):
            mejor = t
    if len(mejor) < 400:
        mejor = _html_a_texto(h)
    return (mejor, "html") if len(mejor) > 400 else ("", "sin-texto")


# ---- backend BARCELONA (BOPB, app Symfony de la Diputació) -------------------
# Clave de rendimiento: NO se usa texto libre (18-38 s) ni rango de fechas (7 s);
# se filtra por municipio + tipo de anuncio "Normativa" (40) —que es justo lo que
# buscamos— y se rankea en local. El listado va en orden ASCENDENTE por fecha, así
# que lo reciente está en las ÚLTIMAS páginas: se piden esas en paralelo.
# Sin cookies: con PHPSESSID compartido el servidor serializa (10,8 s vs 2,1 s).
_BCN_ITEM = re.compile(
    r'<a href="/anunci/(\d+)/([^"]+)" class="stretched-link[^"]*">(.*?)</a>\s*</h3>\s*'
    r"<p>(.*?)</p>(.*?)</div>", re.S)
_BCN_TOT = re.compile(r"S&#039;han trobat (\d+) resultats")
_BCN_BANDA = re.compile(r"(?m)^\s*(?:B\b|A\b|Butllet[íi] Oficial.*|Data\s*\d.*|CVE\s*\d+.*|"
                        r"P[àa]g\.\s*\d+.*|https?://bop\.diba\.cat.*)\s*$")


def _barcelona_buscar(prov, texto, ident=None, rpp=40, paginas=20):
    cfg = PROVINCIAS[prov]
    if not ident:
        return []
    base = (cfg["base"] + "/resultats-cerca", urllib.parse.urlencode({
        "bopb_cerca[tipologiaAnunciantBase]": str(ident),
        "bopb_cerca[tipusAnunciBase]": str(cfg.get("tipo_normativa", 40))}))

    def pagina(n):
        u = f"{base[0]}/{n}?{base[1]}" if n > 1 else f"{base[0]}?{base[1]}"
        try:
            return _madrid_get(u, timeout=30, intentos=1)
        except Exception:  # noqa: BLE001
            return ""

    h1 = pagina(1)
    if not h1:
        return []
    tot = _BCN_TOT.search(h1)
    ultima = max(1, -(-int(tot.group(1)) // 20)) if tot else 1
    quiero = [n for n in range(ultima, max(0, ultima - paginas), -1) if n > 1]
    _raw, _core, _soft = _familias(texto or "")
    _fam = _expandir_idioma({w for w in (set(_raw) | _core) if w not in _GENERICO},
                            cfg.get("idioma"))

    def parse(h):
        out = []
        for ident2, _slug, _anunciante, tit, cola in _BCN_ITEM.findall(h or ""):
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit))).strip()
            fe = re.search(r"Data de publicaci[óo]</span>:\s*([\d/]+)", cola)
            reg = re.search(r"Registre</span>:\s*(\S+)", cola)
            fecha = fe.group(1) if fe else ""
            d, m2, y = (fecha.split("/") + ["", "", ""])[:3]
            out.append({"url": f"{cfg['base']}/anunci/descarrega-pdf/{ident2}",
                        "titulo": t, "cve": (reg.group(1) if reg else ident2),
                        "fecha": fecha, "orden": f"{y}{m2}{d}" if y else "0",
                        "id": ident2,
                        # ¿el título habla de la materia? (si se marca todo como
                        # "materia", el desempate por fecha sube lo más reciente y
                        # la ordenanza buena nunca llega a verificarse)
                        "materia": bool(_fam) and any(_hit(w, _mnorm(t)) for w in _fam)})
        return out

    vistos = {r["cve"]: r for r in parse(h1)}
    if quiero:
        with _cf.ThreadPoolExecutor(max_workers=8) as ex:
            for h in ex.map(pagina, quiero):
                for r in parse(h):
                    vistos.setdefault(r["cve"], r)
    # Las páginas recientes cubren la inmensa mayoría, pero una ordenanza de
    # residuos puede ser de 2010: si la materia no aparece en ningún título, se
    # barre el resto del histórico (sigue siendo barato: 1 petición por página).
    # se amplía si no hay ninguna ORDENANZA de la materia (no basta con que
    # aparezca la palabra: "Pla local de prevenció de residus" no es normativa)
    if _fam and not any(r.get("materia") and _es_ordenanza(r["titulo"])
                        and not _NO_NORMA.search(r["titulo"]) for r in vistos.values()):
        resto = [n for n in range(2, ultima) if n not in quiero]
        if resto:
            with _cf.ThreadPoolExecutor(max_workers=8) as ex:
                for h in ex.map(pagina, resto[-60:]):
                    for r in parse(h):
                        vistos.setdefault(r["cve"], r)
    return list(vistos.values())


def _barcelona_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        pdf = _getb(u, timeout=60)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = _pdf_bytes_texto(pdf)
    t = _BCN_BANDA.sub("", t)          # banda lateral que fitz intercala por página
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    # avisos de una página que remiten a la sede: no son la ordenanza
    return (t, via) if len(t) > 900 else ("", "sin-texto")


# ---- backend VALENCIA (BOP de València, JSF/PrimeFaces) ----------------------
# Lo caro aquí es la sesión: hay que leer del HTML el ViewState y los ids
# autogenerados (j_idtNNN, cambian entre despliegues) y POSTear al action con la
# misma cookie sticky. Dos avisos medidos: sin filtro de municipio la búsqueda
# tarda 17,7 s (con filtro, 0,6 s) y sin rango de fechas devuelve CERO.
_VL_SES = {}


def _vl_sesion(prov, forzar=False):
    s = _VL_SES.get(prov)
    if s and not forzar and time.time() - s[0] < 420:
        return s
    cfg = PROVINCIAS[prov]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA), ("Accept-Language", "es-ES,es")]
    h = op.open(cfg["base"] + "/bop/xhtml/portal.xhtml", timeout=30).read().decode("utf-8", "replace")
    vs = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', h)
    form = re.search(r'<form id="(j_idt\d+)"[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*?id="buscador"', h, re.S)
    render = re.search(r'id="buscarBtn".*?u:&quot;([^&]+)&quot;', h, re.S)
    ent = re.search(r'<label id="(j_idt\d+):label:label"[^>]*title="Entitat"', h)
    if not (vs and form and ent):
        raise RuntimeError("BOP València: no encuentro ViewState/form/entidad")
    dat = (time.time(), op, vs.group(1), form.group(1), form.group(2),
           (render.group(1) if render else "messages boletines3 edictos"), ent.group(1))
    _VL_SES[prov] = dat
    return dat


def _valencia_buscar(prov, texto, ident=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not ident:
        return []
    hoy = time.gmtime()
    fin = f"{hoy.tm_mday:02d}/{hoy.tm_mon:02d}/{hoy.tm_year}"
    consultas = _consultas_materia(texto, cfg.get("idioma"))

    def una(q, reintento=False):
        _t, op, vs, form, action, render, ent = _vl_sesion(prov, forzar=reintento)
        d = {"javax.faces.partial.ajax": "true", "javax.faces.source": "buscarBtn",
             "javax.faces.partial.execute": "@all", "javax.faces.partial.render": render,
             "buscarBtn": "buscarBtn", form: form, "numeroRegistro:field": "",
             "filtroCalendarioIni_input": f"01/01/{cfg.get('indice_desde', 2002)}",
             "filtroCalendarioFin_input": fin, "buscador": q,
             f"{ent}:field_input": "", f"{ent}:field_hinput": str(ident),
             "javax.faces.ViewState": vs}
        try:
            r = op.open(urllib.request.Request(
                cfg["base"] + action if action.startswith("/") else action,
                data=urllib.parse.urlencode(d).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                         "Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest",
                         "Referer": cfg["base"] + "/bop/xhtml/portal.xhtml"}),
                timeout=40).read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return []
        if "ViewExpiredException" in r and not reintento:
            return una(q, reintento=True)
        nvs = re.search(r'<update id="j_id1:javax\.faces\.ViewState:0"><!\[CDATA\[(.*?)\]\]></update>', r, re.S)
        if nvs:                       # el ViewState se renueva en cada respuesta
            d0 = _VL_SES[prov]
            _VL_SES[prov] = (d0[0], d0[1], nvs.group(1), d0[3], d0[4], d0[5], d0[6])
        out = []
        for bloque in r.split('<div class="ui-datagrid-column')[1:]:
            tit = re.search(r'<div class="sumario"><a [^>]*>(.*?)</a>', bloque, re.S)
            reg = re.search(r"registre:\s*</span>\s*(\d{4}/\d+)", bloque)
            fe = re.search(r'title="Butllet[^"]*">(\d{2}/\d{2}/\d{4})<', bloque)
            if not (tit and reg):
                continue
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit.group(1)))).strip()
            fecha = fe.group(1) if fe else ""
            d2, m2, y2 = (fecha.split("/") + ["", "", ""])[:3]
            out.append({"url": f"{cfg['base']}/bop/downloads?anuncioNumReg={reg.group(1)}",
                        "titulo": t, "cve": reg.group(1), "fecha": fecha,
                        "orden": f"{y2}{m2}{d2}" if y2 else "0",
                        "materia": q not in ("ordenanza", "ordenança")})
        return out

    vistos = {}
    for q in consultas:               # secuencial: la sesión JSF no es concurrente
        for r in una(q):
            if r["cve"] in vistos:
                vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
            else:
                vistos[r["cve"]] = r
        if len(vistos) >= 30:
            break
    return list(vistos.values())


def _valencia_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:                              # el PDF se sirve sin sesión ni cookies
        pdf = _getb(u, timeout=45)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    return _pdf_bytes_texto(pdf)


# ---- backend ASTURIAS (BOPA, buscador de legislación por TÍTULO) -------------
# Una sola GET, sin cookies ni token. Los títulos vienen prefijados con el emisor
# ("AYUNTAMIENTO DE LLANES. ORDENANZA...") → el filtro por concejo es gratis.
# Cuidado con su WAF: baneó la IP ~15 min a ~9 req/s; techo práctico ≈3 req/s.
_AS_P = "pa_sede_bopadisposicionmateria_web_BopaDisposicionMateriaLegislacionWeb"
_AS_FILA = re.compile(r'<p class="tit-azulcl titulo_dispo">(.*?)</p>\s*'
                      r'<p class="resultado_legis">(.*?)</p>\s*<p>(.*?)</p>', re.S)
_AS_PDF = re.compile(r'href="([^"]+\.pdf)"')
_MESES = {"enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05",
          "junio": "06", "julio": "07", "agosto": "08", "septiembre": "09",
          "octubre": "10", "noviembre": "11", "diciembre": "12"}


def _asturias_buscar(prov, texto, formas=None, rpp=40):
    cfg = PROVINCIAS[prov]
    if not formas:
        return []
    variantes = [f.strip() for f in str(formas).split(",") if f.strip()]
    consultas = _consultas_materia(texto, None)
    # el OR del buscador está ROTO (devuelve solo el segundo término): consultas
    # separadas y unión en local. Y las frases multipalabra van entre comillas.
    # Su WAF banea la IP ~15 min por encima de ~3 peticiones/s (y el 2-sep-2026
    # dejó de aceptar el TLS de Python tras un banco intensivo): de 6 peticiones
    # por búsqueda se baja a 2-3 (una forma, dos consultas) y la segunda forma
    # (con/sin tilde) solo se prueba si la primera no devuelve nada.
    pares = [(variantes[0], q) for q in consultas[:2]] if variantes else []

    def una(par):
        forma, q = par
        consulta = f'"{forma}" AND {q}' if " " in forma else f"{forma} AND {q}"
        p = {"p_p_id": _AS_P, "p_p_lifecycle": "0", "p_p_state": "normal", "p_p_mode": "view",
             "p_r_p_bopaLegislacionTitle": consulta, "p_r_p_bopaLegislacionFromMini": "false",
             f"_{_AS_P}_bopaLegislacionIsSearch": "true",
             f"_{_AS_P}_bopaLegislacionScope": "LOCAL",
             f"_{_AS_P}_bopaLegislacionOnlyCurrent": "false",
             f"_{_AS_P}_delta": "100", f"_{_AS_P}_cur": "1", f"_{_AS_P}_resetCur": "false"}
        try:
            h = _madrid_get(cfg["base"] + "/bopa/legislacion?" + urllib.parse.urlencode(p),
                            timeout=30, intentos=1)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for tit, ref, bloque in _AS_FILA.findall(h):
            t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tit))).strip()
            # el concejo va en el propio título: filtro duro (si no, se cuelan
            # mancomunidades y otros concejos citados en el cuerpo del título)
            if not re.match(r"(?i)^AYUNTAMIENTOS? DE\s+" + re.escape(_quita_tildes(forma)) + r"\s*[\.,]",
                            _quita_tildes(t)):
                continue
            pdf = _AS_PDF.search(bloque)
            fe = re.search(r"-\s*(\d+) de (\w+) de (\d{4})", _html.unescape(ref))
            if fe:
                d, mes, y = fe.group(1), _MESES.get(fe.group(2).lower(), "01"), fe.group(3)
                fecha, orden = f"{int(d):02d}/{mes}/{y}", f"{y}{mes}{int(d):02d}"
            else:
                fecha, orden = "", "0"
            url = pdf.group(1) if pdf else ""
            out.append({"url": url if url.startswith("http") else cfg["base"] + url,
                        "titulo": re.sub(r"(?i)^AYUNTAMIENTO DE\s+[^.]+\.\s*", "", t),
                        "cve": (url.rsplit("/", 1)[-1].replace(".pdf", "") if url else t[:40]),
                        "fecha": fecha, "orden": orden, "materia": q != "ordenanza"})
        return out

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=2) as ex:      # su WAF castiga las ráfagas
        for rs in ex.map(una, pares):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    if not vistos and len(variantes) > 1:
        for q in consultas[:2]:
            for r in una((variantes[1], q)):
                vistos.setdefault(r["cve"], r)
    return list(vistos.values())


def _quita_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def _asturias_texto(prov, m):
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u.endswith(".pdf"):
        return "", "sin-url"
    try:
        pdf = _getb(u, timeout=45)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = _pdf_bytes_texto(pdf)
    # hay anuncios cuyo PDF apenas tiene capa de texto: mejor descartarlo y que el
    # motor pruebe el siguiente candidato que devolver dos líneas sueltas
    return (t, via) if len(t) > 400 else ("", "sin-texto")


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
           "estacionamiento": "estacionament", "subvenciones": "subvencions",
           "turístico": "turístic", "turístico": "turístic", "turísticos": "turístics",
           "turísticas": "turístiques", "alquiler": "lloguer", "veladores": "vetlladors",
           "velador": "vetllador", "patinete": "patinet", "patinetes": "patinets",
           "perros": "gossos", "perro": "gos", "bicicletas": "bicicletes", "bicicleta": "bicicleta",
           "fiestas": "festes", "feria": "fira", "publicidad": "publicitat", "tenencia": "tinença"}


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
    # los anuncios antiguos vienen enlazados a ssl4.gipuzkoa.net, que el 2-sep-2026
    # no responde (timeout de conexión); el mismo fichero vive en la sede nueva
    u = re.sub(r"^https?://ssl4\.gipuzkoa\.net/castell/bog/", "https://egoitza.gipuzkoa.eus/gao-bog/castell/bog/", u)
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


def _expandir_idioma(terminos, idioma):
    """Añade a un conjunto de términos su forma en la lengua del boletín (y al
    revés). Sin esto, el tesauro —que está en castellano— no casa con títulos en
    catalán o gallego: "residuos" nunca encuentra "residus"."""
    tabla = {"gl": _GALEGO, "ca": _CATALA, "va": _CATALA}.get(idioma or "")
    if not tabla:
        return set(terminos)
    out = set(terminos)
    for w in list(out):
        for cas, loc in tabla.items():
            if _norm(w) == _norm(cas):
                out.add(_mnorm(loc))
            elif _norm(w) == _norm(loc):
                out.add(_mnorm(cas))
    return out


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
           "alcantarillado": "sumidoiros", "escuela": "escola", "deportes": "deportes",
           "turístico": "turístico", "turísticos": "turísticos", "alquiler": "alugueiro",
           "perros": "cans", "perro": "can", "fiestas": "festas", "feria": "feira",
           "publicidad": "publicidade", "patinete": "patinete",
           "bajas emisiones": "baixas emisións", "emisiones": "emisións",
           "bienes inmuebles": "bens inmobles", "inmuebles": "inmobles"}


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
        fam = _expandir_idioma({w for w in (set(raw) | core) if w not in _GENERICO},
                               cfg.get("idioma"))
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


_CA_SLUGS = {}   # slug -> (anuncios de TODOS los órganos, ts)  (caché en memoria 10 min)


def _cadiz_anuncios(base, slug, organo):
    """Anuncios de un órgano en la página del boletín (/boletin/<slug>/), con el
    PDF del día y la página (#page=N) donde empieza cada uno. El número de anuncio
    va con puntos de millar y puede tener 1-3 cifras iniciales (2.283 / 258.517)."""
    c = _CA_SLUGS.get(slug)
    if c and time.time() - c[1] < 600:
        todos = c[0]
    else:
        try:
            page = _cadiz_get(base + "/boletin/" + slug).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return []
        todos = []
        for mm in re.finditer(r"(\d{1,3}(?:\.\d{3})*)\.-\s*(Ayuntamiento de [^.<]+?)\.\s*(.*?)\s*"
                              r'<a[^>]+href="([^"]+\.pdf#page=(\d+))"', page, re.S):
            tit = _html.unescape(re.sub(r"<[^>]+>", " ", mm.group(3))).strip().rstrip(".")
            pdf = mm.group(4)
            todos.append({"organo": mm.group(2), "titulo": re.sub(r"\s+", " ", tit),
                          "pdf": base + pdf if pdf.startswith("/") else pdf,
                          "cve": f"BOP-CA-{mm.group(1)}", "page": int(mm.group(5))})
        _CA_SLUGS[slug] = (todos, time.time())
    on = _norm(organo)
    return [dict(a) for a in todos if _norm(a["organo"]) == on]


_CA_ENTRY = re.compile(r"<div class=\"listWEntry content-box\">\s*<a href='([^']+)'>(.*?)</a>", re.S)


def _cadiz_slug_de(href):
    """'/boletin/Boletin-numero-010-del-ano-2023/' o el PDF del boletín
    '/.boletines_pdf/2023/01_enero/BOP010_17-01-23.pdf' (o su sumario SU-010_…)
    -> 'Boletin-numero-010-del-ano-2023' (el número va a 3 cifras)."""
    m = re.search(r"/boletin/(Boletin-numero-\d+-del-ano-\d+)", href)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{4})/[^/]+/(?:BOP|SU-)(\d{1,3})_\d{2}-\d{2}-(\d{2})\.pdf", href)
    if m:
        return f"Boletin-numero-{int(m.group(2)):03d}-del-ano-{m.group(1)}"
    return ""


_CA_IDX = {}   # índice empaquetado (ordenanzas_data/cadiz_indice.json): organo_norm -> [anuncios]


def _cadiz_indice():
    """Índice EMPAQUETADO de anuncios normativos por ayuntamiento (lo genera
    _gen_indice_cadiz.py recorriendo las páginas de todos los boletines desde 2010).
    Es la vía principal: exacto e instantáneo. El buscador en vivo queda para lo
    publicado después de la fecha del índice."""
    if _CA_IDX:
        return _CA_IDX
    fp = os.path.join(_DATA, "cadiz_indice.json")
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _CA_IDX["_meta"] = {}
        return _CA_IDX
    por = {}
    for a in d.get("anuncios", []):
        por.setdefault(_norm(a["o"]), []).append(a)
    por["_meta"] = d.get("meta", {})
    _CA_IDX.update(por)
    return _CA_IDX


def _cadiz_buscar(prov, texto, organo=None, rpp=40):
    """BOP de Cádiz (OpenCms). Desde agosto-2026 el listado list-inner.jsp IGNORA el
    parámetro `texto` (devolvía lo mismo para «terrazas» y «ordenanza»: los últimos
    boletines del órgano, y de ahí el «no encuentro» en Jerez, Cádiz, San Fernando…).
    El buscador nuevo es /buscador/index.html?q=…&sort=score desc: full-text sobre el
    PDF del boletín, 15 resultados por página, cada uno con enlace al boletín (página
    /boletin/<slug>/ o PDF del día). Se consulta con la materia + el municipio, se
    deducen los boletines y se listan en ellos los anuncios del órgano con su #page."""
    cfg = PROVINCIAS[prov]
    if not organo:
        return []
    muni = re.sub(r"(?i)^ayuntamiento de\s+", "", organo).strip()
    raw = _familias(texto or "ordenanza")[0]
    terminos = sorted(raw, key=len, reverse=True)[:3]
    # 1) ÍNDICE EMPAQUETADO (exacto, 0 red): todos los anuncios normativos del órgano
    idx = _cadiz_indice()
    base_idx = idx.get(_norm(organo)) or []
    out, vistos = [], set()
    for a in base_idx:
        m8 = re.search(r"numero-(\d+)-del-ano-(\d+)", a.get("slug", ""))
        cve = f"BOP-CA-{a['n']}"
        if cve in vistos:
            continue
        vistos.add(cve)
        out.append({"organo": a["o"], "titulo": a["t"], "pdf": a["p"], "url": a["p"], "page": a["pg"],
                    "cve": cve, "orden": (m8.group(2) + f"{int(m8.group(1)):03d}") if m8 else "0",
                    "fecha": f"boletín {int(m8.group(1))}/{m8.group(2)}" if m8 else ""})
    # 2) lo publicado DESPUÉS del índice: últimos boletines con anuncios del órgano
    if out and idx.get("_meta", {}).get("generado"):
        try:
            p = {"tipo_": cfg["tipo"], "ruta_": "/sites/default/.content/BOP_F/", "incluirFiltros_": "true",
                 "num_elements_": "20", "num_columns_": "1",
                 "listConfig": "/.content/Lista_L/Lista_L_00001.html", "usepagination": "true",
                 "page": "1", "organo_remitente": organo, "sortModifier": "desc"}
            r = _cadiz_get(cfg["base"] + "/system/modules/es.dipucadiz.listas/elements/list-inner.jsp?"
                           + urllib.parse.urlencode(p), timeout=15).decode("utf-8", "replace")
            recientes = list(dict.fromkeys(re.findall(r"/boletin/(Boletin-numero-\d+-del-ano-\d+)", r)))[:3]
            with _cf.ThreadPoolExecutor(max_workers=3) as ex:
                for i, ans in enumerate(ex.map(lambda s: _cadiz_anuncios(cfg["base"], s, organo), recientes)):
                    m8 = re.search(r"numero-(\d+)-del-ano-(\d+)", recientes[i])
                    for a in ans:
                        if a["cve"] in vistos:
                            continue
                        vistos.add(a["cve"])
                        a["orden"] = (m8.group(2) + f"{int(m8.group(1)):03d}") if m8 else "0"
                        a["fecha"] = f"boletín {int(m8.group(1))}/{m8.group(2)}" if m8 else ""
                        a["url"] = a["pdf"]
                        out.append(a)
        except Exception:  # noqa: BLE001
            pass
        return out
    if out:
        return out
    # La relevancia del buscador es floja (OR sobre todo el índice: "residuos
    # Cádiz" devuelve 19.816 boletines). Lo que SÍ indexa bien es el SUMARIO de
    # cada boletín ("112.150.- Ayuntamiento de Cádiz. Ordenanza municipal
    # reguladora de la Zona de Bajas Emisiones"): por eso la consulta lleva el
    # órgano entrecomillado + "ordenanza" + la materia, y cada resultado se
    # puntúa por lo que dice su extracto (¿nombra al municipio? ¿la materia?).
    # (sin comillas: la frase entrecomillada NO filtra y además diluye la relevancia)
    consultas = [f'{" ".join(terminos)} {muni}'.strip(), f'{muni} ordenanza {" ".join(terminos)}'.strip()]
    mn = _norm(muni)
    puntos = {}
    for qtxt in consultas:
        for pagina in (1, 2):
            q = {"reloaded": "", "q": qtxt, "sort": "score desc", "page": str(pagina)}
            try:
                r = _cadiz_get(cfg["base"] + "/buscador/index.html?" + urllib.parse.urlencode(q),
                               timeout=25).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                break
            filas = _CA_ENTRY.findall(r)
            for pos, (href, cuerpo) in enumerate(filas):
                s = _cadiz_slug_de(href)
                if not s:
                    continue
                cn = _mnorm(_html.unescape(re.sub(r"<[^>]+>", " ", cuerpo)))
                pts = 3.0 if mn in _norm(cn) else 0.0
                pts += sum(1.0 for w in terminos if _hit(_mnorm(w), cn))
                pts += 0.5 if re.search(r"ordenan|reglament", cn) else 0
                pts -= pos * 0.02
                puntos[s] = max(puntos.get(s, -1), pts)
            if len(filas) < 15:
                break
    slugs = [s for s, _ in sorted(puntos.items(), key=lambda x: -x[1])][:8]
    if not slugs:
        # respaldo: últimos boletines con anuncios del órgano (list-inner.jsp)
        p = {"tipo_": cfg["tipo"], "ruta_": "/sites/default/.content/BOP_F/", "incluirFiltros_": "true",
             "num_elements_": "20", "num_columns_": "1",
             "listConfig": "/.content/Lista_L/Lista_L_00001.html", "usepagination": "true",
             "page": "1", "organo_remitente": organo, "sortModifier": "desc"}
        try:
            r = _cadiz_get(cfg["base"] + "/system/modules/es.dipucadiz.listas/elements/list-inner.jsp?"
                           + urllib.parse.urlencode(p)).decode("utf-8", "replace")
            slugs = list(dict.fromkeys(re.findall(r"/boletin/(Boletin-numero-\d+-del-ano-\d+)", r)))[:4]
        except Exception:  # noqa: BLE001
            return []
    out, vistos = [], set()
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        for i, ans in enumerate(ex.map(lambda s: _cadiz_anuncios(cfg["base"], s, organo), slugs)):
            m8 = re.search(r"numero-(\d+)-del-ano-(\d+)", slugs[i])
            for a in ans:
                if a["cve"] in vistos:
                    continue
                vistos.add(a["cve"])
                a["orden"] = (m8.group(2) + f"{int(m8.group(1)):03d}") if m8 else "0"
                a["fecha"] = f"boletín {int(m8.group(1))}/{m8.group(2)}" if m8 else ""
                a["url"] = a["pdf"]
                out.append(a)
    return out


def _cadiz_texto(prov, m):
    """Texto del anuncio dentro del PDF del boletín del día: empieza en #page=N y se
    corta donde acaba el anuncio («Nº 272.189 ______» cierra cada uno)."""
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
    # Cada anuncio del boletín TERMINA con su número y una raya («Nº 2.283
    # ___________»); el #page del sumario no marca el principio del anuncio (una
    # ordenanza larga empieza páginas antes), así que se delimita por marcas: de
    # la marca del anuncio ANTERIOR a la marca de ESTE (su número va en el CVE).
    num = re.sub(r"^BOP-CA-", "", (m.get("cve") if isinstance(m, dict) else "") or "")
    todo = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    marca = re.compile(r"N[ºo]\s*([\d.]{3,})\s*\n_{5,}")
    txt = ""
    if num:
        fin = re.search(r"N[ºo]\s*" + re.escape(num) + r"\s*\n_{5,}", todo)
        if fin:
            prev = [mm.end() for mm in marca.finditer(todo, 0, fin.start())]
            ini = prev[-1] if prev else max(0, todo.rfind("ADMINISTRACION LOCAL", 0, fin.start()))
            txt = todo[ini:fin.start()]
    if not txt:
        a, b_ = max(0, page - 1), min(doc.page_count, page - 1 + 12)   # respaldo por páginas
        txt = "\n".join(doc[i].get_text() for i in range(a, b_))
        mfin = marca.search(txt, 400)
        if mfin:
            txt = txt[:mfin.start()]
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


_TXT_CACHE = os.path.join(tempfile.gettempdir(), "bop-textos")


def _txt_cache_get(clave):
    try:
        fp = os.path.join(_TXT_CACHE, re.sub(r"[^A-Za-z0-9_.-]", "_", clave))
        if time.time() - os.path.getmtime(fp) < 7 * 86400:
            return open(fp, encoding="utf-8").read()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _txt_cache_set(clave, texto):
    try:
        os.makedirs(_TXT_CACHE, exist_ok=True)
        with open(os.path.join(_TXT_CACHE, re.sub(r"[^A-Za-z0-9_.-]", "_", clave)), "w",
                  encoding="utf-8") as f:
            f.write(texto)
    except Exception:  # noqa: BLE001
        pass


def _malaga_texto(prov, m):
    """El BOP de Málaga protege edicto.php con Cloudflare Turnstile por RÁFAGAS:
    a partir de ~8 peticiones/minuto por IP devuelve una página de «Verificación
    de seguridad» (1,9 KB) en vez del edicto; unos segundos después vuelve a servir.
    Por eso: caché del texto en /tmp (7 días), reintentos con espera creciente y,
    si sigue bloqueado, una vía distinta de "sin texto" para explicárselo al abogado."""
    cfg = PROVINCIAS[prov]
    eid = m.get("eid") if isinstance(m, dict) else m
    if not eid:
        return "", "sin-id"
    # texto EMPAQUETADO en el repo (ordenanzas de los municipios >50k; lo genera
    # _gen_textos_malaga.py con calma para no disparar el Turnstile)
    try:
        fp = os.path.join(_DATA, "malaga_prov_textos", eid + ".txt.gz")
        if os.path.exists(fp):
            import gzip as _gz
            with _gz.open(fp, "rt", encoding="utf-8") as f:
                t = f.read()
            if len(t) > 200:
                return t, "html-empaquetado"
    except Exception:  # noqa: BLE001
        pass
    cacheado = _txt_cache_get("malaga_" + eid)
    if cacheado:
        return cacheado, "html-cache"
    bloqueado = False
    for espera in (0, 1.5, 3.0):
        if espera:
            time.sleep(espera)
        try:
            req = urllib.request.Request(cfg["base"] + "/edicto.php?edicto=" + eid,
                                         headers={"User-Agent": _UA, "Referer": cfg["base"] + "/buscar.php",
                                                  "Accept-Language": "es-ES,es"})
            html = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return "", f"err:{e}"
        # la página de bloqueo (1,9 KB) solo dice «Verificación necesaria»; los
        # edictos reales también cargan el script de Turnstile, así que no basta
        # con buscar esa palabra (un edicto corto de 2013 pesa 5,9 KB)
        if re.search(r"turnstile_gate|<title>\s*Verificaci[oó]n necesaria", html):
            bloqueado = True
            continue
        t = _html_a_texto(html)
        if len(t) > 200:
            _txt_cache_set("malaga_" + eid, t)
            return t, "html"
        return "", "sin-texto"
    return "", ("bloqueo-antibots" if bloqueado else "sin-texto")


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


# Normas municipales que NO se titulan "ordenanza/reglamento" pero regulan igual:
# modificaciones del PGOU, limitaciones (p.ej. del número de viviendas de uso
# turístico), bandos, instrucciones, normas urbanísticas, estatutos... Solo entran
# en el ranking cuando el título lleva un término DISTINTIVO de la materia pedida
# (así no cuelan anuncios sueltos). Caso real: «Aprobación definitiva de la
# propuesta de limitación del número máximo de viviendas de uso turístico en la
# ciudad» (Sevilla, BOP 28/10/2024) se descartaba por no decir "ordenanza".
_NORMA_AMPLIA = re.compile(
    r"aprobaci[oó]n definitiva|texto (?:[ií]ntegro|refundido|consolidado)|"
    r"normas? (?:urban[ií]stic|reguladora|subsidiaria)|plan (?:general|especial|de ordenaci)|"
    r"\bpgou\b|regulaci[oó]n|limitaci[oó]n|\bbando\b|instrucci[oó]n|bases reguladoras|"
    r"estatutos|normativa|r[eé]gimen|zona (?:de )?bajas emisiones|modificaci[oó]n puntual|"
    r"regulaci[oó]|aprovaci[oó] definitiva|modificaci[oó] puntual|normes urban", re.I)


def _es_norma_amplia(titulo, distintivos):
    if not distintivos or not _NORMA_AMPLIA.search(titulo or ""):
        return False
    if _NO_NORMA.search(titulo or ""):
        return False
    tm = _mnorm(titulo)
    return any(_hit(w, tm) for w in distintivos)


def _distintivos(raw, core=()):
    """Términos que de verdad identifican la materia (fuera los de relleno)."""
    return [w for w in list(raw) + [c for c in core if " " not in c] if w not in _GENERICO]


_STOPM = {"de", "la", "el", "los", "las", "del", "y", "o", "en", "por", "para", "un",
          "una", "sobre", "municipal", "municipales", "ordenanza", "ordenanzas",
          "reglamento", "reglamentos", "reguladora", "regulador", "norma", "normativa"}

# Tesauro v2: (patrón sobre la materia normalizada CON espacios, términos CORE
# —específicos, pesan—, términos SOFT —genéricos, solo desempatan—). Los alias
# multipalabra se comparan como subcadena del título normalizado.
_EXPANSION = [
    # viviendas de uso turístico / pisos turísticos (caso Sevilla, 2-sep-2026: la
    # limitación del número de VUT es una modificación del PGOU publicada en el BOP)
    (r"turistic|\bvut\b|vivienda vacacional|apartamento turistic|piso turistic|pisos turistic|"
     r"alquiler vacacional|alquiler turistic|alojamiento turistic|hospedaje",
     ["uso turistico", "turistic", "vivienda vacacional", "alojamiento turistico",
      "apartamento turistico", "vut", "alquiler vacacional", "hospedaje"],
     ["vivienda", "alojamiento", "turismo"]),
    (r"\bplaya|litoral|costa\b", ["playa", "litoral"], []),
    (r"urbanis|\bpgou\b|plan general|edificaci|\bsolar(es)?\b|ruina",
     ["urbanistic", "plan general", "pgou", "edificacion", "ruina", "solares"], ["obra", "licencia"]),
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
    # (castellano, gallego y catalán: «corrección de erros», «correcció d'errades»)
    (r"correcci[oó]n? de err(?:ores|os|ades)|correcci[oó] d'errades", None),
    (r"delegaci[oó]n? de", None),
    (r"derr?ogaci[oó]n?\b", None),
    # actos que llevan el nombre de la ordenanza en el título pero NO son la norma
    (r"padr[oó]n|cobranza|per[ií]odo voluntario", "padron"),
    (r"notificaci[oó]n?|expediente sancionador|incoaci[oó]n?|licitaci[oó]n?|adjudicaci[oó]n?", None),
    # publicación de una SENTENCIA que anula un artículo: no es la ordenanza (San Sebastián, VUT)
    (r"\bsentencia\b|\bsent[eè]ncia\b|recurso contencioso|ejecuci[oó]n de sentencia", "sentencia"),
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


def _ranquear(res, materia, idioma=None):
    """Candidatos tipo ordenanza ordenados por relevancia; solo pasan el gate los
    que llevan ≥1 término raw o core (con frontera de palabra) en el título.
    `idioma` (gl/ca/va): el gate admite también la forma en la lengua del
    boletín («residuos» ↔ «lixo»/«residus»), como ya hacía _mejor_verificado."""
    raw, core, soft = _familias(materia)
    dist = _distintivos(raw, core)
    if idioma:
        dist = list(_expandir_idioma(dist, idioma))
    cand = [r for r in res if _es_ordenanza(r["titulo"]) or _es_norma_amplia(r["titulo"], dist)]
    if not cand:
        return []
    cand.sort(key=lambda r: _puntuar(r, raw, core, soft), reverse=True)
    gate = set(raw) | core
    if idioma:
        gate = _expandir_idioma(gate, idioma)
    if not gate:
        return cand
    return [r for r in cand if any(_hit(w, _mnorm(r["titulo"])) for w in gate)]


def _mejor(res, materia, idioma=None):
    top = _ranquear(res, materia, idioma)
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
    dist = _distintivos(raw, core)
    cand = [r for r in res if (_es_ordenanza(r["titulo"]) or _es_norma_amplia(r["titulo"], dist))
            and _no_demote(r, fam)]
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
_NO_NORMA = re.compile(r"\bsentencia\b|\bsent[eè]ncia\b|recurso contencioso|"
                       r"extracto|convocat[oò]ria|convocatoria|atorgament|ajuts econ[oò]mics|borsa de treball|nomenament|delegaci[óo]n de funciones|"
                       r"oferta[s]? de empleo|bases (del )?proceso|proceso selectivo|"
                       r"plan especial|plan parcial|plan general|calificaci[óo]n (de )?suelo|"
                       r"expropiaci|nombramiento|cese\b|list[ao] (provisional|definitiv)|"
                       r"informaci[óo]n p[úu]blica de|estudio de detalle|convenio urban[íi]stico", re.I)


def _mejor_verificado(prov, res, materia, top_n=4, estricto=False):
    """Elige la ordenanza VERIFICANDO EL CONTENIDO, no solo el título.
    estricto=True (títulos genéricos): exige mucha más densidad de la materia y
    un presupuesto de tiempo corto, porque el candidato no dice nada en el título.

    Necesario cuando el boletín titula de forma genérica ('Alcobendas.
    Organización y funcionamiento. Ordenanza'): el título no dice la materia, así
    que se leen los N mejores candidatos (en PARALELO; en el BOCM el texto viene
    en JSON y cuesta ~1 s) y gana el que realmente habla de la materia pedida.
    El texto ganador viaja en m['text'] para no volver a descargarlo."""
    raw, core, soft = _familias(materia)
    dist0 = _distintivos(raw, core)
    cand = [r for r in res if (_es_ordenanza(r["titulo"]) or _es_norma_amplia(r["titulo"], dist0))
            and not _NO_NORMA.search(r["titulo"])]
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
    clave_core = _expandir_idioma(clave_core, PROVINCIAS[prov].get("idioma"))

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
            # sin OCR: verificar de qué va un candidato no puede costar 10-15 s de
            # visión por PDF (Saga). Si el PDF va cifrado (fuente sin ToUnicode),
            # se OCR-ea SOLO la primera página (~1,5 s): basta para leer el título
            # real y decidir; el texto completo se lee después si es el elegido.
            t, via = _texto(prov, r, ocr=False)
            if not t and via == "cifrado":
                t, _ = _texto(prov, r, ocr=True, max_pag=1)
                if t:
                    r = dict(r, _solo_portada=True)
        except Exception:  # noqa: BLE001
            t = ""
        return r, t

    # SECUENCIAL con salida temprana (no en paralelo): desde Vercel el boletín
    # estrangula las descargas simultáneas y una ráfaga acaba en timeout. Lo normal
    # es resolver con 1 lectura; solo si esa no convence se mira la siguiente.
    mejor, mejor_s, mejor_t, mejor_ok = None, float("-inf"), "", False
    limite = time.time() + (8 if estricto else 16)   # tope duro: nunca disparar la latencia
    min_h, min_d = (6, 0.25) if estricto else (3, 0.12)
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
            ok = bool(en_titulo(r)) or (hclave >= min_h and dclave >= min_d)
            if (ok, s) > (mejor_ok, mejor_s):
                mejor, mejor_s, mejor_t, mejor_ok = r, s, t, ok
            if ok and dclave >= 0.30:      # claramente es esta: no leo más
                break
            if time.time() > limite:       # se agotó el presupuesto de tiempo
                break
    if mejor is None or not mejor_ok:
        return None                   # honesto: no hay ordenanza de esa materia
    mejor = dict(mejor)
    if not mejor.pop("_solo_portada", False):
        mejor["text"] = mejor_t       # (si solo se OCR-eó la portada, se relee entero)
    return mejor


def _ranquear_fulltext(res, materia):
    raw, core, soft = _familias(materia)
    fam = set(raw) | core | soft
    dist = _distintivos(raw, core)
    cand = [r for r in res if (_es_ordenanza(r["titulo"]) or _es_norma_amplia(r["titulo"], dist))
            and _no_demote(r, fam)]
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
    # el OCR de un PDF cifrado cuesta 10-30 s y dinero: se guarda 7 días en /tmp
    # (misma instancia caliente de Vercel = misma lectura repetida gratis)
    clave = "saga_" + re.sub(r"[^A-Za-z0-9]+", "_", pdf_url)[-90:]
    if ocr:
        cacheado = _txt_cache_get(clave)
        if cacheado:
            return cacheado, "ocr-cache"
    t, via = _pdf_bytes_texto(_getb(pdf_url, 50), ocr, max_pag)
    if t and str(via).startswith("ocr"):
        _txt_cache_set(clave, t)
    return t, via


def _limpia(t):
    t = re.sub(r"P[áa]gina \d+ de(?: un total de)? \d+|N[ºo] \d+ - [\w ]+de \d+|"
              r"CVE:? ?BOP-[A-Z]{2}-[\d-]+|Documento firmado[^\n]*|C[óo]d\.? ?Validaci[óo]n[^\n]*|"
              r"Bolet[íi]n Oficial[^\n]*|de la provincia de \w+|HASH:[^\n]*|Fecha Firma:[^\n]*", " ", t)
    return re.sub(r"[ \t\xa0]+", " ", t).strip()


def _articulos(texto):
    """[(rubrica, cuerpo)] troceando por 'Artículo N'."""
    t = _limpia(texto)
    # «Artículo 5», «Art. 5», «Article 5» (catalán), «Artigo 5» (gallego)
    marcas = [(m.start(), m.group(1)) for m in re.finditer(
        r"(?im)(?:^|\n|\.)\s*((?:art[íi]cul[oe]|artigo|art\.?)\s+\d+[\wº.\-]{0,4}[.\-–—:]?)", t)]
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
            f"Fuente: {_nombre_boletin(prov)} (texto publicado; "
            "el boletín no consolida: verifica modificaciones posteriores)." + aviso)


# Boletines que NO se llaman «de la Provincia» (uniprovinciales y forales)
_BOLETINES = {
    "larioja": "Boletín Oficial de La Rioja (BOR)", "asturias": "Boletín Oficial del Principado de Asturias (BOPA)",
    "navarra": "Boletín Oficial de Navarra (BON)", "bizkaia": "Boletín Oficial de Bizkaia (BOB)",
    "gipuzkoa": "Boletín Oficial de Gipuzkoa (BOG)", "alava": "Boletín Oficial del Territorio Histórico de Álava (BOTHA)",
    "baleares": "Butlletí Oficial de les Illes Balears (BOIB)", "cantabria": "Boletín Oficial de Cantabria (BOC)",
    "cantabria2": "Boletín Oficial de Cantabria (BOC)", "madrid": "Boletín Oficial de la Comunidad de Madrid (BOCM)",
    "murcia_prov": "Boletín Oficial de la Región de Murcia (BORM)", "ceuta": "Boletín Oficial de la Ciudad de Ceuta (BOCCE)",
    "melilla": "Boletín Oficial de la Ciudad de Melilla (BOME)",
}


def _nombre_boletin(prov):
    cfg = PROVINCIAS.get(prov, {})
    return cfg.get("boletin") or _BOLETINES.get(prov) or f"Boletín Oficial de la Provincia de {cfg.get('nombre', prov)}"


def _nombre_muni(prov, municipio):
    _cargar_mapas()
    muni, _ = _parse_muni(municipio)
    _, k = _clave_muni(_norm(muni), prov, (muni,))
    return _NOMBRES.get(prov, {}).get(k or _norm(muni)) or muni.strip().title()


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
    # 2ª fase, SOLO si hace falta: sinónimos del tesauro como consultas propias.
    # «pisos turísticos» no aparece en ningún título, «uso turístico» sí (la
    # limitación de VUT de Sevilla, que además no se titula ordenanza y no sale en
    # los volcados). Se omite cuando ya hay un candidato con un término distintivo
    # en el título (en Salamanca cada consulta viva cuesta 4-8 s).
    if materia.strip():
        try:
            raw, core, _soft = _familias(materia)
            dist = _distintivos(raw, core)
            ya = any(any(_hit(w, _mnorm(r["titulo"])) for w in dist) for r in vistos.values())
            frases = [c for c in sorted(core, key=len) if " " in c and c not in _norm(materia)][:2]
            if not ya and frases:
                with _cf.ThreadPoolExecutor(max_workers=2) as ex:
                    for rs in ex.map(run, [(c, 40) for c in frases]):
                        for r in rs:
                            vistos.setdefault(r["url"], r)
        except Exception:  # noqa: BLE001
            pass
    return list(vistos.values())


def _aviso_indice(prov):
    d = PROVINCIAS[prov].get("indice_desde")
    return (f"el índice electrónico de este BOP solo cubre publicaciones desde ~{d}; "
            if d else "el índice electrónico de este BOP puede no cubrir publicaciones antiguas; ")


def _honesto(prov, nombre, consulta, supra_hits):
    lin = [f"No encuentro una ordenanza de «{consulta}» del Ayuntamiento de {nombre} en el "
           f"{_nombre_boletin(prov)}."]
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


_GEN_PREFIJO = re.compile(r"^(?:anuncio de |edicto de |aprobaci[oó]n (?:definitiva|inicial|provisional) "
                          r"(?:de (?:la |el |las |los )?)?|texto (?:[ií]ntegro|definitivo) de (?:la |el )?)+", re.I)


def _titulos_genericos(res):
    """Anuncios tipo ordenanza cuyo título NO dice la materia («Ordenanza reguladora»,
    «Aprobación definitiva de ordenanza», «Modificación de ordenanza fiscal»): tras
    quitar el prefijo administrativo quedan ≤3 palabras útiles. Más recientes primero."""
    out = []
    for r in res:
        t = r.get("titulo") or ""
        if not _es_ordenanza(t) or _NO_NORMA.search(t):
            continue
        resto = _GEN_PREFIJO.sub("", t.strip())
        utiles = [w for w in _mnorm(resto).split() if w not in _STOPM and w not in _GENERICO
                  and w not in ("fiscal", "fiscales", "modificacion", "definitiva", "definitivo",
                                "general", "texto", "integro", "aprobacion", "ayuntamiento", "municipio")]
        if not utiles:
            out.append(r)
    out.sort(key=lambda r: r.get("orden") or "0", reverse=True)
    return out


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
        ords = _ranquear(res, consulta, PROVINCIAS[prov].get("idioma"))
    else:
        ords = [r for r in res if _es_ordenanza(r["titulo"])]
        ords.sort(key=lambda r: r["orden"], reverse=True)
    ords = [r for r in ords if not re.search(r"correcci[oó]n? de err(?:ores|os|ades)|correcci[oó] d'errades|"
                                             r"delegaci[oó]n? de", r["titulo"], re.I)]
    if not ords:
        # títulos GENÉRICOS («Ordenanza reguladora», «Aprobación definitiva de
        # ordenanza»): el boletín no dice la materia, así que no se puede descartar
        # que sea la pedida; se listan para que leer_ordenanza las verifique por
        # contenido (o el abogado elija por CVE)
        genericos = _titulos_genericos(res)
        extra = ""
        if genericos and consulta.strip():
            lin = ["\n\nOJO: este ayuntamiento publica anuncios con título GENÉRICO (sin decir la "
                   "materia); alguno podría ser la norma buscada. leer_ordenanza los verifica por "
                   "contenido; también puedes leer uno por su CVE:"]
            for i, r in enumerate(genericos[:5], 1):
                lin.append(f"{i}. {r['titulo']}" + (f" · {r['cve']}" if r.get("cve") else "")
                           + (f" · pub. {r['fecha']}" if r.get("fecha") else ""))
            extra = "\n".join(lin)
        return _honesto(prov, nombre, consulta, _supra(prov, consulta)) + extra
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
        # referencias oficiales de los distintos boletines: BOP-SE-2024-091027,
        # BOCM-20260317-46, BOC-2024-1234, BOME-A-2024-439, BOP-SA-20240117-003,
        # BOP-LR-2025-123, BOP-BA-2024-3121R, BOP-CA-2.283…
        mcve = re.search(r"\b(?:BOP-[A-Z]{1,4}|BOCM|BOC|BOME-AX?|BOR|BOG|BOTHA|BOIB)-\d{4,8}-[\d.]+[A-Z]?\b",
                         ordenanza, re.I)
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
            m = _mejor(res, ordenanza, PROVINCIAS[prov].get("idioma"))
            if not m:
                # 2) volcado genérico del municipio + ranking local (recall profundo)
                res = _candidatos(prov, cat, ordenanza)
                m = _mejor(res, ordenanza, PROVINCIAS[prov].get("idioma"))
            if not m:
                # 3) títulos GENÉRICOS («Ordenanza reguladora», «Aprobación definitiva
                # de ordenanza»): se leen los 3 más recientes y gana el que de verdad
                # trata la materia (sin OCR y con tope de tiempo). Caso real:
                # Almuñécar publica «Ordenanza reguladora» a secas.
                genericos = _titulos_genericos(res)[:5] if PROVINCIAS[prov].get("genericos", True) else []
                if genericos:
                    m = _mejor_verificado(prov, genericos, ordenanza, top_n=5, estricto=True)
    except Exception as e:  # noqa: BLE001
        return f"Error buscando la ordenanza en el BOP de {PROVINCIAS[prov]['nombre']}: {e}"
    if not m:
        return _honesto(prov, nombre, ordenanza, _supra(prov, ordenanza))
    try:
        if m.get("text"):
            texto, via = m["text"], "verificado"     # ya leído al verificar el candidato
        else:
            texto, via = _texto(prov, m)
    except Exception as e:  # noqa: BLE001
        return f"Localicé la ordenanza «{m['titulo']}» ({m.get('cve','')}) pero no pude leer su PDF: {e}"
    if not texto:
        if str(via).startswith("bloqueo"):
            return (f"Localicé «{m['titulo']}» ({m.get('cve','')}) pero el boletín ha activado "
                    "temporalmente su verificación anti-robots y no sirve el texto ahora mismo. "
                    f"Reintenta en 1-2 minutos o consulta el enlace oficial: {m['url']}")
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
