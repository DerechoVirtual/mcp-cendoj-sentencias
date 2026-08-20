# -*- coding: utf-8 -*-
"""
Motor CATASTRO — Direccion General del Catastro (Ministerio de Hacienda).

Servicios web OVC (Oficina Virtual del Catastro): PUBLICOS, GRATIS, SIN clave,
SIN captcha y rapidos (~0,2-1 s). Coste CERO para nosotros.

  * Callejero    https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx
      ConsultaProvincia / ConsultaMunicipio / ConsultaVia / ConsultaNumero
      Consulta_DNPRC  (por referencia catastral)
      Consulta_DNPLOC (por direccion: urbana)
      Consulta_DNPPP  (por poligono/parcela: rustica)
  * Coordenadas  https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCoordenadas.asmx
      Consulta_RCCOOR / Consulta_RCCOOR_Distancia (que hay en un punto)
      Consulta_CPMRC (coordenadas de una referencia catastral)
  * INSPIRE WFS  https://ovc.catastro.meh.es/INSPIRE/wfsCP.aspx  (superficie
      grafica de la parcela y su geometria; dato que NO da el servicio anterior)

ALCANCE REAL (honesto, va escrito en la salida para que nadie se confunda):
  ✔ DATOS NO PROTEGIDOS (art. 51-53 TRLCI, publicos para cualquiera):
    referencia catastral, localizacion, clase (urbano/rustico), uso, superficie
    construida, año de construccion, coeficiente de participacion, desglose de
    construcciones por planta/puerta/destino y, en rustica, los cultivos por
    subparcela con su superficie.
  ✘ DATOS PROTEGIDOS: TITULAR (nombre/NIF) y VALOR CATASTRAL. La ley solo los
    da al propio titular o a quien acredite interes legitimo -> se identifica en
    la Sede con certificado/Cl@ve. Aqui NUNCA se devuelven ni se estiman.
  ✘ PAIS VASCO (Araba, Bizkaia, Gipuzkoa) y NAVARRA: catastro FORAL propio, no
    esta en el Catastro estatal. Se avisa con el enlace de su Diputacion.

El texto que se devuelve es markdown compacto pensado para que el modelo lo lea
y lo cite: los numeros van con sus unidades y la fuente siempre es el Catastro.
"""
import os
import re
import json
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

_CALLEJERO = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/"
_COORD = "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCoordenadas.asmx/"
_WFS_CP = "https://ovc.catastro.meh.es/INSPIRE/wfsCP.aspx"
_SEDE = "https://www1.sedecatastro.gob.es"
_NS = "{http://www.catastro.meh.es/}"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_TIMEOUT = float(os.environ.get("CATASTRO_TIMEOUT", "20"))

# Provincias con catastro FORAL (fuera del Catastro estatal) y su enlace.
_FORALES = {
    "araba": ("Araba/Álava", "https://web.araba.eus/es/catastro"),
    "alava": ("Araba/Álava", "https://web.araba.eus/es/catastro"),
    "bizkaia": ("Bizkaia", "https://www.bizkaia.eus/es/catastro"),
    "vizcaya": ("Bizkaia", "https://www.bizkaia.eus/es/catastro"),
    "gipuzkoa": ("Gipuzkoa", "https://www.gipuzkoa.eus/es/web/ogasuna/catastro"),
    "guipuzcoa": ("Gipuzkoa", "https://www.gipuzkoa.eus/es/web/ogasuna/catastro"),
    "navarra": ("Navarra", "https://catastro.navarra.es"),
    "nafarroa": ("Navarra", "https://catastro.navarra.es"),
}
# Municipios forales frecuentes: si alguien los pide sin provincia, se explica
# por que no aparecen en vez de decir "no encontrado".
_MUN_FORALES = {
    "bilbao": "Bizkaia", "barakaldo": "Bizkaia", "getxo": "Bizkaia",
    "portugalete": "Bizkaia", "santurtzi": "Bizkaia", "basauri": "Bizkaia",
    "leioa": "Bizkaia", "galdakao": "Bizkaia", "durango": "Bizkaia",
    "sestao": "Bizkaia", "erandio": "Bizkaia", "gernika lumo": "Bizkaia",
    "vitoria gasteiz": "Araba/Álava", "vitoria": "Araba/Álava",
    "laudio": "Araba/Álava", "llodio": "Araba/Álava", "amurrio": "Araba/Álava",
    "donostia": "Gipuzkoa", "donostia san sebastian": "Gipuzkoa",
    "san sebastian": "Gipuzkoa", "irun": "Gipuzkoa", "errenteria": "Gipuzkoa",
    "eibar": "Gipuzkoa", "zarautz": "Gipuzkoa", "arrasate": "Gipuzkoa",
    "mondragon": "Gipuzkoa", "hernani": "Gipuzkoa", "tolosa": "Gipuzkoa",
    "pamplona": "Navarra", "iruna": "Navarra", "tudela": "Navarra",
    "barañain": "Navarra", "baranain": "Navarra", "burlada": "Navarra",
    "estella": "Navarra", "lizarra": "Navarra", "tafalla": "Navarra",
    "zizur mayor": "Navarra", "ansoain": "Navarra",
}

# Tipos de via del Catastro (sigla oficial) reconocidos en texto libre.
_TIPOS_VIA = [
    ("CL", ("calle", "c", "c/", "cl", "calles")),
    ("AV", ("avenida", "avda", "avd", "av", "avinguda", "avgda")),
    ("PZ", ("plaza", "plza", "pza", "pl", "pz", "placa", "praza", "plaça")),
    ("PS", ("paseo", "pso", "ps", "passeig", "paseig")),
    ("CR", ("carretera", "ctra", "cr", "carrer")),
    ("CM", ("camino", "cno", "cm", "cami")),
    ("TR", ("travesia", "trav", "tr", "travessera")),
    ("RD", ("ronda", "rda", "rd")),
    ("GL", ("glorieta", "gta", "gl")),
    ("PJ", ("pasaje", "psje", "pje", "pj", "passatge")),
    ("GV", ("gran via", "granvia", "gv")),
    ("RU", ("rua", "rúa")),
    ("BO", ("barrio", "bo", "bº")),
    ("UR", ("urbanizacion", "urb", "ur")),
    ("PG", ("poligono industrial", "poligono", "pol", "pg")),
    ("LG", ("lugar", "lg")),
    ("CJ", ("callejon", "callejuela", "cj", "calleja")),
    ("SD", ("senda", "sd")),
    ("RB", ("rambla", "rbla", "rb")),
    ("CT", ("cuesta", "costanilla", "ct")),
    ("SU", ("subida", "su")),
    ("BJ", ("bajada", "bj")),
    ("AL", ("alameda", "al")),
    ("PQ", ("parque", "pq")),
    ("JR", ("jardines", "jardin", "jr")),
    ("ML", ("muelle", "ml")),
    ("MZ", ("manzana", "mz")),
    ("CS", ("caserio", "cs")),
    ("DS", ("diseminado", "diseminados", "ds")),
    ("PB", ("poblado", "pb")),
    ("VR", ("vereda", "vr")),
    ("CH", ("chalet", "ch")),
    ("FN", ("finca", "fn")),
]
_SIGLAS = {s for s, _ in _TIPOS_VIA}

# Codigos de error del OVC que conviene traducir a lenguaje util.
_ERR_UTIL = {
    "1": "no existe ese municipio en la provincia indicada",
    "4": "la referencia catastral no está correctamente formada",
    "5": "esa referencia catastral no existe",
    "8": "no existe ese número en la vía indicada",
    "12": "no existe esa vía en el municipio",
    "13": "no existe ese inmueble (escalera/planta/puerta)",
    "16": "en esas coordenadas no hay ninguna parcela catastral",
    "18": "la referencia catastral debe tener 14 posiciones",
}


# ---------------------------------------------------------------- utilidades
def _norm(s):
    """minusculas, sin acentos y sin signos: para comparar nombres."""
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s)
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _mayus_sin_acentos(s):
    """'Alcalá' -> 'ALCALA'. El Catastro guarda las vias SIN acentos, asi que
    mandarlas con tilde hace que 'la via no existe'."""
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def _get(base, op, **kw):
    """GET al servicio OVC. Devuelve el Element raiz o lanza RuntimeError."""
    url = base + op + "?" + urllib.parse.urlencode(kw, encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    ultimo = None
    for _ in range(2):  # un reintento: el OVC da 500 esporadicos
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                return ET.fromstring(r.read())
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise RuntimeError("El servicio del Catastro no responde (%s)" % (
        repr(ultimo)[:120],))


def _t(el, tag):
    """Texto del primer descendiente <tag> (a cualquier profundidad)."""
    if el is None:
        return ""
    n = el.find(".//" + _NS + tag)
    return (n.text or "").strip() if n is not None and n.text else ""


def _errores(root):
    """Lista [(cod, descripcion)] de <lerr>; vacia si la respuesta es buena."""
    out = []
    for err in root.iter(_NS + "err"):
        out.append((_t(err, "cod"), _t(err, "des")))
    return out


def _msg_error(root, contexto=""):
    errs = _errores(root)
    if not errs:
        return ""
    cod, des = errs[0]
    util = _ERR_UTIL.get(cod)
    txt = util or (des or "").capitalize()
    return ("**Catastro: %s.**%s" % (txt, (" " + contexto) if contexto else ""))


# ------------------------------------------------------- catalogo municipios
_CAT = None


def _catalogo():
    """{'provincias': [...], 'municipios': [...]} empaquetado en el repo."""
    global _CAT
    if _CAT is None:
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "catastro_data", "municipios.json")
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                _CAT = json.load(f)
        except Exception:  # noqa: BLE001
            _CAT = {"provincias": [], "municipios": []}
        for m in _CAT["municipios"]:
            m["_n"] = _norm(m["nm"])
            m["_np"] = _norm(m["np"])
    return _CAT


def _variantes_municipio(n):
    """'la coruña' -> {'la coruna','coruna','a coruna'}: nombres con articulo,
    bilingues y con el articulo pospuesto ('CORUÑA (A)')."""
    v = {n}
    m = re.match(r"^(el|la|los|las|a|o|as|os|es|s|sa) (.+)$", n)
    if m:
        v.add(m.group(2))
        v.add(m.group(2) + " " + m.group(1))
    for art in ("a", "o", "el", "la", "los", "las", "as", "os"):
        v.add(art + " " + n)
    for sep in ("/", "-"):
        if sep in n:
            v.update(p.strip() for p in n.split(sep) if p.strip())
    return {x for x in v if x}


def resolver_municipio(municipio, provincia=""):
    """Devuelve (candidatos, aviso). candidatos = [dict municipio] ordenados por
    calidad de coincidencia. aviso = texto si es territorio foral."""
    nm, npr = _norm(municipio), _norm(provincia)
    if not nm and not npr:
        return [], ""
    for clave, (nombre, url) in _FORALES.items():
        if clave in npr.split() or npr == clave:
            return [], ("El Catastro estatal **no incluye %s** (tiene catastro "
                        "foral propio). Consulta: %s" % (nombre, url))
    if nm in _MUN_FORALES:
        terr = _MUN_FORALES[nm]
        url = [u for k, (n, u) in _FORALES.items() if n == terr]
        return [], ("**%s** está en **%s**, con catastro foral propio: no figura "
                    "en el Catastro estatal. Consulta: %s"
                    % (municipio.strip().title(), terr,
                       url[0] if url else "la Diputación Foral"))
    cat = _catalogo()
    variantes = _variantes_municipio(nm)
    exactos, empiezan, contienen = [], [], []
    for m in cat["municipios"]:
        if npr and npr not in m["_np"] and m["_np"] not in npr:
            continue
        if not nm:
            continue
        if m["_n"] in variantes or nm == m["_n"]:
            exactos.append(m)
        elif m["_n"].startswith(nm) or any(m["_n"].startswith(v) for v in variantes):
            empiezan.append(m)
        elif nm in m["_n"]:
            contienen.append(m)
    if not nm and npr:  # solo provincia: no resolvemos municipio
        return [], ""
    return (exactos + empiezan + contienen)[:12], ""


# ------------------------------------------------------ parseo de direcciones
_RE_RC = re.compile(r"[0-9A-Z]{7}[0-9A-Z]{7}(?:[0-9A-Z]{6})?$")


def limpiar_rc(rc):
    """Normaliza una referencia catastral: sin espacios/puntos, en mayusculas."""
    return re.sub(r"[^0-9A-Za-z]", "", rc or "").upper()


def es_rc(rc):
    r = limpiar_rc(rc)
    return len(r) in (14, 20) and bool(_RE_RC.match(r))


def parsear_direccion(texto):
    """'Avda. de la Constitución 12, 3º B' ->
    {'sigla':'AV','calle':'CONSTITUCION','numero':'12','planta':'3','puerta':'B'}
    Devuelve tambien 'calle_larga' (con articulos) para reintentos."""
    t = " " + re.sub(r"\s+", " ", (texto or "").strip()) + " "
    out = {"sigla": "", "calle": "", "numero": "", "bloque": "", "escalera": "",
           "planta": "", "puerta": "", "calle_larga": ""}
    # escalera / planta / puerta al final: "3º B", "esc 2, 4 C", "bajo A"
    m = re.search(r"[,\s]esc(?:alera)?\.?\s*([0-9A-Za-z]{1,3})\b", t, re.I)
    if m:
        out["escalera"] = m.group(1).upper()
        t = t[:m.start()] + " " + t[m.end():]
    m = re.search(r"[,\s](?:pl(?:anta)?\.?\s*)?(\d{1,2})\s*[ºo°ª]\s*"
                  r"(?:pta\.?|puerta\s*)?([A-Za-z0-9]{1,3})?\b", t)
    if m:
        out["planta"] = m.group(1)
        if m.group(2):
            out["puerta"] = m.group(2).upper()
        t = t[:m.start()] + " " + t[m.end():]
    else:
        m = re.search(r"[,\s](bajo|entresuelo|entlo|principal|atico|ático|sotano|sótano)"
                      r"\s*([A-Za-z0-9]{1,3})?\b", t, re.I)
        if m:
            out["planta"] = {"bajo": "00", "entresuelo": "EN", "entlo": "EN",
                             "principal": "PR", "atico": "AT", "ático": "AT",
                             "sotano": "-1", "sótano": "-1"}[m.group(1).lower()]
            if m.group(2):
                out["puerta"] = m.group(2).upper()
            t = t[:m.start()] + " " + t[m.end():]
    m = re.search(r"[,\s]pta\.?\s*([A-Za-z0-9]{1,3})\b", t, re.I)
    if m and not out["puerta"]:
        out["puerta"] = m.group(1).upper()
        t = t[:m.start()] + " " + t[m.end():]
    out["sigla"], tl = _detectar_tipo_via(t.strip())
    # numero: ultimo numero suelto (admite "45", "nº 45", "45 bis", "s/n")
    # OJO: el "nº" exige el simbolo o 'num'. Una 'n' suelta NO vale, o "Colón 1"
    # se leeria como "Coló" + "nº 1" (pasaba con Colón, León, Constitución...).
    m = re.search(r"(?:\bn[ºo°]\.?\s*|\bn[uú]m(?:\.|ero)?\s*)?\b(\d+)\s*"
                  r"(bis|dup|duplicado)?\s*[,.]?\s*$", tl.strip())
    if m:
        out["numero"] = m.group(1)
        tl = tl[:m.start()]
    else:
        m = re.search(r"(?:,|\s)\s*(?:\bn[ºo°]\.?\s*|\bn[uú]m(?:\.|ero)?\s*)?(\d+)\b", tl)
        if m:
            out["numero"] = m.group(1)
            tl = tl[:m.start()] + " " + tl[m.end():]
    calle = re.sub(r"[,;]+", " ", tl).strip(" ,.-")
    calle = re.sub(r"\s*\b(s\s*/\s*n|sin\s+n[uú]mero)\b\.?\s*$", "", calle,
                   flags=re.I)   # "s/n" no forma parte del nombre de la via
    out["calle_larga"] = _mayus_sin_acentos(calle)
    # el Catastro guarda la via sin articulos iniciales ("DE LA CONSTITUCION")
    corta = re.sub(r"^(?:de\s+la|de\s+los|de\s+las|del|de|la|el|los|las)\s+",
                   "", calle.strip(), flags=re.I)
    out["calle"] = _mayus_sin_acentos(corta)
    return out


def _detectar_tipo_via(texto):
    """('Avda. de la Constitución 12') -> ('AV', 'de la Constitución 12').
    Si al quitar el tipo no queda NOMBRE (solo el numero), se revierte: en
    'Gran Vía 1' la vía se llama GRAN VIA, no es un tipo de via."""
    t = (texto or "").strip()
    n = _norm(t)
    mejor = None
    for sigla, formas in _TIPOS_VIA:
        for f in formas:
            fn = _norm(f)
            if fn and (n == fn or n.startswith(fn + " ")):
                nw = len(fn.split())
                if mejor is None or nw > mejor[0]:
                    mejor = (nw, sigla)
    if not mejor:
        return "", t
    nw, sigla = mejor
    resto = t
    for _ in range(nw):
        resto = re.sub(r"^\s*\S+\s*", "", resto, count=1)
    resto = resto.lstrip(" .,/-")
    if not re.sub(r"[\d\s.,ºª°/-]", "", resto):   # no queda nombre -> revertir
        return "", t
    return sigla, resto


# ------------------------------------------------------------ enriquecimiento
def _coordenadas(rc14, provincia="", municipio=""):
    """(lat, lon) de una parcela por su RC de 14, o (None, None)."""
    try:
        root = _get(_COORD, "Consulta_CPMRC", Provincia=provincia,
                    Municipio=municipio, SRS="EPSG:4326", RC=rc14)
        x, y = _t(root, "xcen"), _t(root, "ycen")
        return (float(y), float(x)) if x and y else (None, None)
    except Exception:  # noqa: BLE001
        return (None, None)


def _superficie_parcela(rc14):
    """Superficie grafica de la parcela (m2) por WFS INSPIRE, o None."""
    try:
        url = (_WFS_CP + "?service=wfs&version=2.0.0&request=getfeature"
               "&STOREDQUERIE_ID=GetParcel&refcat=" + rc14 + "&srsname=EPSG::4326")
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            txt = r.read().decode("utf-8", "replace")
        m = re.search(r'areaValue[^>]*>\s*([0-9.]+)\s*<', txt)
        return int(float(m.group(1))) if m else None
    except Exception:  # noqa: BLE001
        return None


def _enlaces(rc, clase="U"):
    """Enlaces oficiales: ficha de datos no protegidos y visor cartografico."""
    rc = limpiar_rc(rc)
    ur = "R" if clase == "R" else "U"
    ficha = ("%s/CYCBienInmueble/OVCConCiud.aspx?UrbRus=%s&RefC=%s&from=OVCBusqueda"
             "&pest=rc&RCCompleta=%s" % (_SEDE, ur, rc, rc))
    mapa = "%s/Cartografia/mapa.aspx?refcat=%s" % (_SEDE, rc[:14])
    return ficha, mapa


# ----------------------------------------------------------------- formateo
_PLANTAS = {"00": "baja", "-1": "sótano -1", "-2": "sótano -2", "-3": "sótano -3",
            "EN": "entresuelo", "PR": "principal", "AT": "ático", "SM": "semisótano",
            "CU": "cubierta", "OD": "(varias plantas)", "ST": "sótano"}


def _planta(p):
    p = (p or "").strip()
    if not p:
        return ""
    if p in _PLANTAS:
        return _PLANTAS[p]
    if p.lstrip("-").isdigit():
        n = int(p)
        return "baja" if n == 0 else ("%dª" % n if n > 0 else "sótano %d" % n)
    return p


def _num(v):
    """'15786' -> '15.786'"""
    try:
        return "{:,}".format(int(float(str(v).replace(",", ".")))).replace(",", ".")
    except Exception:  # noqa: BLE001
        return str(v or "")


def _rc_de(el):
    """Reconstruye la referencia catastral de un nodo <rc> o <pc>."""
    if el is None:
        return ""
    return "".join(_t(el, k) for k in ("pc1", "pc2", "car", "cc1", "cc2"))


def _direccion_legible(dt):
    """Texto de la localizacion a partir de un nodo <dt>."""
    if dt is None:
        return ""
    lourb = dt.find(".//" + _NS + "lourb")
    if lourb is not None:
        d = lourb.find(_NS + "dir")
        partes = [_t(d, "tv"), _t(d, "nv")]
        pnp = _t(d, "pnp")
        if pnp and pnp != "0":
            partes.append(pnp)
        base = " ".join(p for p in partes if p)
        li = lourb.find(_NS + "loint")
        extra = []
        if li is not None:
            for tag, et in (("bq", "Bl."), ("es", "Esc."), ("pt", "Pl."), ("pu", "Pta.")):
                v = _t(li, tag)
                if v and v not in ("T", "OD", "OS", "0", "00 "):
                    extra.append("%s %s" % (et, _planta(v) if tag == "pt" else v))
        dp = _t(lourb, "dp")
        cola = " ".join(extra)
        return ("%s%s%s" % (base, (", " + cola) if cola else "",
                            (" — %s" % dp) if dp else "")).strip()
    lorus = dt.find(".//" + _NS + "lorus")
    if lorus is not None:
        pol, par = _t(lorus, "cpo"), _t(lorus, "cpa")
        npa = _t(lorus, "npa")
        return ("Polígono %s Parcela %s%s" % (pol, par, (" — %s" % npa) if npa else "")).strip()
    return ""


def _ficha(bico, provincia="", municipio="", con_extras=True):
    """Ficha completa de UN inmueble (nodo <bico> o <bi>) en markdown."""
    bi = bico.find(_NS + "bi") if bico.find(_NS + "bi") is not None else bico
    rc = _rc_de(bi.find(".//" + _NS + "rc"))
    clase = _t(bi, "cn") or "UR"
    dt = bi.find(_NS + "dt")
    np_ = _t(dt, "np") or provincia
    nm = _t(dt, "nm") or municipio
    ldt = _t(bico, "ldt") or _t(bi, "ldt") or _direccion_legible(dt)
    debi = bi.find(_NS + "debi")
    uso = _t(debi, "luso")
    sfc = _t(debi, "sfc")
    cpt = _t(debi, "cpt")
    ant = _t(debi, "ant")

    L = []
    L.append("## Referencia catastral %s" % rc)
    L.append("- **Localización:** %s" % (ldt or _direccion_legible(dt)))
    if nm:
        L.append("- **Municipio:** %s (%s)" % (nm.title(), np_.title()))
    L.append("- **Clase:** %s" % ("Rústico" if clase == "RU" else "Urbano"))
    if uso:
        L.append("- **Uso principal:** %s" % uso)
    if sfc and sfc not in ("0",):
        L.append("- **Superficie construida:** %s m²" % _num(sfc))
    if ant:
        L.append("- **Año de construcción:** %s" % ant)
    if cpt:
        try:
            c = float(str(cpt).replace(",", "."))
            L.append("- **Coeficiente de participación:** %s %%" %
                     ("{:g}".format(round(c, 6))))
        except Exception:  # noqa: BLE001
            L.append("- **Coeficiente de participación:** %s %%" % cpt)

    # Construcciones (desglose por planta/puerta/destino)
    cons = list(bico.iter(_NS + "cons"))
    if cons:
        filas = []
        total = 0
        for c in cons:
            destino = _t(c, "lcd")
            li = c.find(".//" + _NS + "loint")
            pt = _planta(_t(li, "pt")) if li is not None else ""
            pu = _t(li, "pu") if li is not None else ""
            stl = _t(c, "stl")
            dcons = c.find(_NS + "dfcons")
            anio = _t(c, "dt") and ""
            try:
                total += int(stl)
            except Exception:  # noqa: BLE001
                pass
            filas.append((destino or "—", pt or "—", pu or "—",
                          (_num(stl) + " m²") if stl else "—", dcons is not None and anio or ""))
        L.append("")
        L.append("**Construcciones (%d):**" % len(filas))
        L.append("")
        L.append("| Destino | Planta | Puerta | Superficie |")
        L.append("|---|---|---|---|")
        for d, pt, pu, s, _a in filas[:60]:
            L.append("| %s | %s | %s | %s |" % (d, pt, pu, s))
        if len(filas) > 60:
            L.append("| … | | | _(%d más)_ |" % (len(filas) - 60))
        if total:
            L.append("")
            L.append("_Suma de las superficies del desglose: %s m²._" % _num(total))

    # Rustica: cultivos por subparcela
    sprs = list(bico.iter(_NS + "spr"))
    if sprs:
        L.append("")
        L.append("**Cultivos / aprovechamientos (%d subparcelas):**" % len(sprs))
        L.append("")
        L.append("| Subparcela | Cultivo | Intensidad | Superficie |")
        L.append("|---|---|---|---|")
        tot = 0
        for s in sprs[:40]:
            ssp = _t(s, "ssp")
            try:
                tot += int(ssp)
            except Exception:  # noqa: BLE001
                pass
            L.append("| %s | %s | %s | %s m² |" % (
                _t(s, "cspr") or "—", _t(s, "dcc") or "—", _t(s, "ip") or "—",
                _num(ssp)))
        if tot:
            L.append("")
            L.append("_Superficie total de cultivo: %s m² (%s ha)._" % (
                _num(tot), "{:g}".format(round(tot / 10000.0, 4))))

    if con_extras and rc:
        rc14 = rc[:14]
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_co = ex.submit(_coordenadas, rc14, np_, nm)
            f_sp = ex.submit(_superficie_parcela, rc14)
            lat, lon = f_co.result()
            sup_par = f_sp.result()
        if sup_par:
            L.append("- **Superficie de la parcela (cartografía):** %s m²" % _num(sup_par))
        if lat and lon:
            L.append("- **Coordenadas (EPSG:4326):** %.6f, %.6f — "
                     "[ver en mapa](https://www.google.com/maps?q=%.6f,%.6f)"
                     % (lat, lon, lat, lon))
        ficha, mapa = _enlaces(rc, "R" if clase == "RU" else "U")
        L.append("- **Ficha oficial:** %s" % ficha)
        L.append("- **Cartografía:** %s" % mapa)
    return "\n".join(L)


_NOTA_PROTEGIDOS = (
    "\n\n> **Titular y valor catastral no son datos públicos** (arts. 51-53 del "
    "texto refundido de la Ley del Catastro Inmobiliario): solo se facilitan al "
    "titular o a quien acredite interés legítimo, identificándose en la Sede "
    "Electrónica del Catastro con certificado o Cl@ve. Todo lo anterior son "
    "datos catastrales NO protegidos, de acceso libre.\n"
    "_Fuente: Dirección General del Catastro (Sede Electrónica del Catastro)._")


def _lista(root, titulo, limite=25):
    """Lista compacta de inmuebles (<lrcdnp>) con su RC y su localizacion."""
    filas = []
    for rcd in root.iter(_NS + "rcdnp"):
        rc = _rc_de(rcd.find(_NS + "rc"))
        dt = rcd.find(_NS + "dt")
        filas.append((rc, _t(rcd, "ldt") or _direccion_legible(dt)))
    if not filas:
        return ""
    L = ["## %s" % titulo, "",
         "| # | Referencia catastral | Localización |", "|---|---|---|"]
    for i, (rc, d) in enumerate(filas[:limite], 1):
        L.append("| %d | `%s` | %s |" % (i, rc, d))
    if len(filas) > limite:
        L.append("")
        L.append("_Hay %d inmuebles en total; se muestran los %d primeros._"
                 % (len(filas), limite))
    L.append("")
    L.append("Para la ficha completa de uno: `consultar_catastro` con su "
             "referencia catastral.")
    return "\n".join(L)


# --------------------------------------------------------------------- tools
def _por_rc(rc, provincia="", municipio=""):
    rc = limpiar_rc(rc)
    root = _get(_CALLEJERO, "Consulta_DNPRC", Provincia=provincia,
                Municipio=municipio, RC=rc)
    err = _msg_error(root)
    if err:
        if len(rc) not in (14, 20):
            err += ("\n\nUna referencia catastral tiene **20 caracteres** "
                    "(inmueble) o **14** (parcela). La recibida tiene %d." % len(rc))
        elif any(c == "4" for c, _ in _errores(root)):
            err += ("\n\nLa longitud es correcta, así que probablemente esté mal "
                    "copiada: los **dos últimos caracteres son dígitos de "
                    "control** y no cuadran con el resto. Compruébala en la "
                    "escritura o en el recibo del IBI.")
        return err
    lista = _lista(root, "Inmuebles de la parcela %s" % rc[:14])
    if lista:
        return lista + _NOTA_PROTEGIDOS
    bico = root.find(_NS + "bico")
    if bico is None:
        return "**Catastro: sin resultados** para la referencia %s." % rc
    return _ficha(bico, provincia, municipio) + _NOTA_PROTEGIDOS


def _sugerir_vias(np_, nm, calle, sigla=""):
    """Vias parecidas para cuando la direccion no casa. [(tv, nv)]"""
    try:
        root = _get(_CALLEJERO, "ConsultaVia", Provincia=np_, Municipio=nm,
                    TipoVia=sigla or "", NombreVia=calle)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in root.iter(_NS + "calle"):
        d = c.find(_NS + "dir")
        out.append((_t(d, "tv"), _t(d, "nv")))
    return out


_VACIAS = {"de", "del", "la", "el", "los", "las", "y", "d"}


def _clave_via(s):
    """Nombre de via comparable: el Catastro pospone el articulo entre
    parentesis ('CONSTITUCION (DE LA)'), asi que se ignoran esos y los
    articulos sueltos."""
    s = re.sub(r"\([^)]*\)", " ", s or "")
    return " ".join(w for w in _norm(s).split() if w not in _VACIAS)


def _mejor_via(vias, calle, sigla):
    """Elige la via mas parecida: igualdad > empieza por > contiene; a igualdad,
    la del tipo de via pedido."""
    n = _norm(calle)
    k = _clave_via(calle)

    def punt(v):
        tv, nv = v
        m, mk = _norm(nv), _clave_via(nv)
        base = 0 if (m == n or mk == k) else (
            1 if (m.startswith(n) or mk.startswith(k)) else (2 if n in m else 3))
        return (base, 0 if (sigla and tv == sigla) else 1, len(m))
    return sorted(vias, key=punt)[0] if vias else None


def _existe_numero(np_, nm, sigla, calle, n):
    """True si ese numero de policia existe en la via (ConsultaNumero)."""
    try:
        root = _get(_CALLEJERO, "ConsultaNumero", Provincia=np_, Municipio=nm,
                    TipoVia=sigla, NomVia=calle, Numero=str(n))
        return not _errores(root) and root.find(".//" + _NS + "nump") is not None
    except Exception:  # noqa: BLE001
        return False


def _numeros_cercanos(np_, nm, sigla, calle, numero):
    """Numeros REALES proximos al pedido. El servicio no sabe listar la via
    entera (exige un numero concreto, de 4 digitos como mucho), asi que se
    tantea un abanico alrededor en paralelo (~0,3 s)."""
    try:
        obj = int(str(numero).strip())
    except Exception:  # noqa: BLE001
        return []
    if obj > 9999:
        return []
    cand = [obj + d for d in (-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6)
            if 0 < obj + d <= 9999]
    with ThreadPoolExecutor(max_workers=6) as ex:
        vivos = list(ex.map(
            lambda n: (n, _existe_numero(np_, nm, sigla, calle, n)), cand))
    return [n for n, ok in sorted(vivos, key=lambda t: abs(t[0] - obj)) if ok][:8]


def _por_direccion(direccion, municipio, provincia, planta="", puerta="",
                   escalera="", bloque="", maximo=25):
    d = parsear_direccion(direccion)
    planta = planta or d["planta"]
    puerta = puerta or d["puerta"]
    escalera = escalera or d["escalera"]
    bloque = bloque or d["bloque"]
    if not d["calle"]:
        return ("**Falta la vía.** Indica la dirección, p. ej. "
                "`direccion=\"Calle Alcalá 45\"`, `municipio=\"Madrid\"`.")
    cands, aviso = resolver_municipio(municipio, provincia)
    if aviso:
        return aviso
    if not cands:
        return ("**No encuentro el municipio «%s»** en el Catastro%s.\n\n"
                "Prueba con el nombre oficial (o añade la provincia). Si el "
                "inmueble está en el País Vasco o Navarra, su catastro es foral "
                "y no figura en el Catastro estatal."
                % (municipio or provincia,
                   " de %s" % provincia if provincia else ""))
    if len(cands) > 1 and _norm(cands[0]["nm"]) != _norm(municipio):
        opciones = "\n".join("- %s (%s)" % (c["nm"].title(), c["np"].title())
                             for c in cands[:8])
        return ("**Hay varios municipios que encajan con «%s»**. Indica la "
                "provincia o el nombre exacto:\n\n%s" % (municipio, opciones))
    m = cands[0]
    np_, nm = m["np"], m["nm"]
    if d["numero"] and len(d["numero"]) > 4:
        return ("**El número de policía «%s» no es válido**: en el Catastro "
                "tiene 4 dígitos como máximo. Revisa la dirección."
                % d["numero"])

    def dnploc(sig, calle):
        return _get(_CALLEJERO, "Consulta_DNPLOC", Provincia=np_, Municipio=nm,
                    Sigla=sig, Calle=calle, Numero=d["numero"] or "",
                    Bloque=bloque, Escalera=escalera, Planta=planta,
                    Puerta=puerta)

    # 1) intento directo (el 90 % de las veces acierta a la primera)
    root = dnploc(d["sigla"], d["calle"])
    if not _errores(root):
        return _formatear_loc(root, np_, nm, planta, puerta, maximo)
    ultimo = root
    # 2) resolver la via de verdad en el callejero y reintentar
    vias = _sugerir_vias(np_, nm, d["calle"], d["sigla"])
    if not vias and d["calle_larga"] != d["calle"]:
        vias = _sugerir_vias(np_, nm, d["calle_larga"], "")
    if not vias and len(d["calle"]) > 5:      # nombre mal escrito: por prefijo
        vias = _sugerir_vias(np_, nm, d["calle"][:5], "")
    mejor = _mejor_via(vias, d["calle"], d["sigla"])
    if mejor:
        tv, nv = mejor
        if (tv, nv) != (d["sigla"], d["calle"]):
            root = dnploc(tv, nv)
            if not _errores(root):
                nota = ("" if _clave_via(nv) == _clave_via(d["calle"])
                        else "Se ha entendido «%s %s» por «%s»." % (
                            tv, nv, direccion.strip()))
                return _formatear_loc(root, np_, nm, planta, puerta, maximo, nota)
            ultimo = root
        # 3) la via existe pero el numero no -> numeros reales de esa via
        if _norm(nv).startswith(_norm(d["calle"])[:5]):
            cerc = _numeros_cercanos(np_, nm, tv, nv, d["numero"])
            cola = ("\n\nNúmeros cercanos que sí existen: %s. Repite la consulta "
                    "con uno de ellos." % ", ".join(str(x) for x in cerc)) if cerc \
                else ("\n\nComprueba el número: puede que la numeración de esa "
                      "vía no llegue tan lejos, que el portal esté en otra vía "
                      "o que sea un número con letra (bis, duplicado).")
            return ("**La vía %s %s existe en %s (%s), pero no consta el número "
                    "%s.**%s" % (tv, nv, nm.title(), np_.title(),
                                 d["numero"] or "—", cola))
    if vias:
        op = "\n".join("- %s %s" % (tv, nv) for tv, nv in vias[:10])
        return ("**No encuentro «%s» en %s (%s)**. Vías parecidas en ese "
                "municipio:\n\n%s" % (d["calle"], nm.title(), np_.title(), op))
    ctx = ("Vía buscada: %s %s%s en %s (%s)."
           % (d["sigla"] or "?", d["calle"],
              (" nº %s" % d["numero"]) if d["numero"] else "", nm.title(),
              np_.title()))
    return _msg_error(ultimo, ctx) or "**Catastro: sin resultados.**"


def _formatear_loc(root, np_, nm, planta="", puerta="", maximo=25, nota=""):
    """Convierte la respuesta de Consulta_DNPLOC en ficha o lista, filtrando por
    planta/puerta cuando el usuario las ha dado."""
    pre = ("_%s_%s" % (nota, "\n\n")) if nota else ""
    bico = root.find(_NS + "bico")
    if bico is not None:
        return pre + _ficha(bico, np_, nm) + _NOTA_PROTEGIDOS
    if planta or puerta:
        elegidos = []
        for rcd in root.iter(_NS + "rcdnp"):
            li = rcd.find(".//" + _NS + "loint")
            pt = _t(li, "pt") if li is not None else ""
            pu = _t(li, "pu") if li is not None else ""
            ok_pt = (not planta) or _norm(pt).lstrip("0") == _norm(planta).lstrip("0") \
                or _norm(pt) == _norm(planta)
            ok_pu = (not puerta) or _norm(pu).lstrip("0") == _norm(puerta).lstrip("0") \
                or _norm(pu) == _norm(puerta)
            if ok_pt and ok_pu:
                elegidos.append(_rc_de(rcd.find(_NS + "rc")))
        if len(elegidos) == 1:
            return pre + _por_rc(elegidos[0], np_, nm)
    lista = _lista(root, "Inmuebles en esa dirección", maximo)
    if lista:
        return pre + lista + _NOTA_PROTEGIDOS
    return "**Catastro: sin resultados** para esa dirección."


def _por_parcela(provincia, municipio, poligono, parcela):
    cands, aviso = resolver_municipio(municipio, provincia)
    if aviso:
        return aviso
    if not cands:
        return ("**No encuentro el municipio «%s»** en el Catastro. Indica el "
                "nombre oficial y, si puedes, la provincia." % (municipio or provincia))
    m = cands[0]
    root = _get(_CALLEJERO, "Consulta_DNPPP", Provincia=m["np"], Municipio=m["nm"],
                Poligono=str(poligono).strip(), Parcela=str(parcela).strip())
    err = _msg_error(root, "Polígono %s, parcela %s de %s (%s)."
                     % (poligono, parcela, m["nm"].title(), m["np"].title()))
    if err:
        return err
    bico = root.find(_NS + "bico")
    if bico is not None:
        return _ficha(bico, m["np"], m["nm"]) + _NOTA_PROTEGIDOS
    lista = _lista(root, "Inmuebles del polígono %s parcela %s (%s)"
                   % (poligono, parcela, m["nm"].title()))
    return (lista + _NOTA_PROTEGIDOS) if lista else "**Catastro: sin resultados.**"


def _ordenar_latlon(a, b):
    """Acepta 'lat,lon' y tambien 'lon,lat' (como los copia Google Maps al
    reves). En España lat va de 27 a 44 y lon de -19 a 5, asi que no hay duda."""
    es_lat = lambda v: 27.0 <= v <= 44.5      # noqa: E731
    es_lon = lambda v: -19.0 <= v <= 5.0      # noqa: E731
    if es_lat(a) and es_lon(b):
        return a, b
    if es_lat(b) and es_lon(a):
        return b, a
    return a, b


def _por_coordenadas(coordenadas, radio=0):
    """'40.4168,-3.7038' -> parcela(s) en ese punto o en un radio de N metros."""
    vals = []
    for p in re.split(r"[;,\s]+", (coordenadas or "").strip()):
        try:
            vals.append(float(p.replace(",", ".")))
        except ValueError:
            pass
    if len(vals) < 2:
        return ("**Coordenadas no válidas.** Indícalas como `40.416775,-3.703790` "
                "(latitud, longitud en grados decimales, WGS84).")
    lat, lon = _ordenar_latlon(vals[0], vals[1])
    try:
        r = float(radio or 0)
    except Exception:  # noqa: BLE001
        r = 0
    if r > 0:
        root = _get(_COORD, "Consulta_RCCOOR_Distancia", SRS="EPSG:4326",
                    Coordenada_X="%.7f" % lon, Coordenada_Y="%.7f" % lat,
                    Distancia=str(int(r)))
        err = _msg_error(root)
        if err:
            return err
        filas = []
        for pcd in root.iter(_NS + "pcd"):
            filas.append((_rc_de(pcd.find(_NS + "pc")), _t(pcd, "ldt"), _t(pcd, "dis")))
        if not filas:
            return ("**En %.6f, %.6f no hay ninguna parcela catastral** (ni "
                    "colindante). Suele ocurrir con viales, plazas y demás "
                    "dominio público, y en el País Vasco y Navarra, cuyo "
                    "catastro es foral. Comprueba el punto o consulta por "
                    "dirección." % (lat, lon))
        L = ["## Parcelas a menos de %d m de %.6f, %.6f" % (int(r), lat, lon), "",
             "| Referencia (parcela) | Localización | Distancia |", "|---|---|---|"]
        for rc, ldt, dis in filas[:25]:
            L.append("| `%s` | %s | %s m |" % (rc, ldt, dis or "0"))
        L.append("")
        L.append("Para los datos de una: `consultar_catastro` con su referencia.")
        return "\n".join(L) + _NOTA_PROTEGIDOS
    root = _get(_COORD, "Consulta_RCCOOR", SRS="EPSG:4326",
                Coordenada_X="%.7f" % lon, Coordenada_Y="%.7f" % lat)
    errs = _errores(root)
    if errs:
        # Sin parcela justo en el punto: se amplia a 50 m automaticamente.
        return _por_coordenadas("%.7f,%.7f" % (lat, lon), 50)
    rc14 = _rc_de(root.find(".//" + _NS + "pc"))
    if not rc14:
        return _por_coordenadas("%.7f,%.7f" % (lat, lon), 50)
    ldt = _t(root, "ldt")
    cabecera = ("_Punto %.6f, %.6f → parcela `%s`%s._\n\n"
                % (lat, lon, rc14, (" (%s)" % ldt) if ldt else ""))
    return cabecera + _por_rc(rc14)


# ------------------------------------------------------------------- publico
def consultar(referencia_catastral="", direccion="", municipio="", provincia="",
              planta="", puerta="", escalera="", bloque="", poligono="",
              parcela="", coordenadas="", radio=0, maximo=25):
    """Punto de entrada unico de la tool `consultar_catastro`."""
    try:
        rc = limpiar_rc(referencia_catastral)
        if rc:
            return _por_rc(rc, "", "")
        if coordenadas:
            return _por_coordenadas(coordenadas, radio)
        if poligono and parcela:
            return _por_parcela(provincia, municipio, poligono, parcela)
        if direccion:
            return _por_direccion(direccion, municipio, provincia, planta,
                                  puerta, escalera, bloque, maximo)
        # 'direccion' vacia pero el municipio trae la calle dentro
        if municipio and re.search(r"\d", municipio):
            return _por_direccion(municipio, "", provincia, planta, puerta,
                                  escalera, bloque, maximo)
        return ("**Dime qué inmueble.** Puedes identificarlo de cuatro maneras:\n"
                "- `referencia_catastral=\"1047206VK4714G0001ZH\"`\n"
                "- `direccion=\"Calle Alcalá 45, 3º B\"` + `municipio=\"Madrid\"`\n"
                "- `municipio=\"Santa Cruz de Mudela\"` + `poligono=\"10\"` + "
                "`parcela=\"25\"` (finca rústica)\n"
                "- `coordenadas=\"40.419372,-3.696322\"` (y `radio` en metros "
                "para ver las parcelas del entorno)")
    except RuntimeError as e:
        return "**%s.** Vuelve a intentarlo en unos segundos." % str(e).rstrip(".")
    except Exception as e:  # noqa: BLE001
        return ("**No he podido completar la consulta al Catastro** (%s). "
                "Revisa los datos e inténtalo de nuevo." % repr(e)[:120])


def callejero(municipio="", provincia="", via="", numero=""):
    """Tool `callejero_catastro`: municipios, vias y numeros que existen."""
    try:
        if not municipio and not provincia:
            return ("Indica al menos un municipio o una provincia, p. ej. "
                    "`municipio=\"Getafe\"` o `provincia=\"Madrid\"`.")
        cands, aviso = resolver_municipio(municipio, provincia)
        if aviso:
            return aviso
        if not municipio and provincia:
            cat = _catalogo()
            npn = _norm(provincia)
            mun = [m for m in cat["municipios"] if npn in m["_np"] or m["_np"] in npn]
            if not mun:
                return "**No encuentro la provincia «%s»** en el Catastro." % provincia
            L = ["## Municipios de %s en el Catastro (%d)"
                 % (mun[0]["np"].title(), len(mun)), ""]
            L.append(", ".join(m["nm"].title() for m in mun[:400]))
            if len(mun) > 400:
                L.append("\n_(se muestran los 400 primeros)_")
            return "\n".join(L)
        if not cands:
            return ("**No encuentro el municipio «%s»**%s. Si está en el País "
                    "Vasco o Navarra, su catastro es foral y no figura en el "
                    "Catastro estatal." % (municipio,
                                           " en %s" % provincia if provincia else ""))
        if len(cands) > 1 and not via:
            L = ["## Municipios que encajan con «%s»" % municipio, ""]
            for c in cands[:12]:
                L.append("- **%s** (%s)" % (c["nm"].title(), c["np"].title()))
            return "\n".join(L)
        m = cands[0]
        np_, nm = m["np"], m["nm"]
        if not via:
            return ("**%s (%s)** consta en el Catastro. Indica también `via` "
                    "para ver las calles con ese nombre, o consulta un inmueble "
                    "con `consultar_catastro`." % (nm.title(), np_.title()))
        d = parsear_direccion(via)
        nombre = d["calle"] or via.strip().upper()
        vias = _sugerir_vias(np_, nm, nombre, d["sigla"]) or \
            _sugerir_vias(np_, nm, d["calle_larga"] or nombre, "")
        if not vias:
            return "**No hay ninguna vía que contenga «%s» en %s (%s).**" % (
                nombre, nm.title(), np_.title())
        if numero or d["numero"]:
            tv, nv = _mejor_via(vias, nombre, d["sigla"])
            nums = _numeros_cercanos(np_, nm, tv, nv, numero or d["numero"])
            return ("## %s %s — %s (%s)\n\nNúmeros existentes (más cercanos al "
                    "%s): %s" % (tv, nv, nm.title(), np_.title(),
                                 numero or d["numero"],
                                 ", ".join(str(x) for x in nums) or "—"))
        L = ["## Vías con «%s» en %s (%s)" % (nombre, nm.title(), np_.title()), "",
             "| Tipo | Nombre |", "|---|---|"]
        for tv, nv in vias[:40]:
            L.append("| %s | %s |" % (tv, nv))
        if len(vias) > 40:
            L.append("")
            L.append("_(%d vías en total)_" % len(vias))
        return "\n".join(L)
    except RuntimeError as e:
        return "**%s.**" % str(e).rstrip(".")
    except Exception as e:  # noqa: BLE001
        return "**No he podido consultar el callejero del Catastro** (%s)." % repr(e)[:120]
