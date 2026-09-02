# -*- coding: utf-8 -*-
"""Backend BOC de CANTABRIA (familia `cantabria2`) para el motor de ordenanzas.

El Boletín Oficial de Cantabria (boc.cantabria.es, Struts "boces") hace de BOP de
los 102 ayuntamientos. Receta verificada en vivo (27-jul y 2-sep-2026):

  * POST /boces/busquedaAnuncios.do, cuerpo y respuesta en ISO-8859-15. Funciona
    SIN cookies (0,2-0,6 s), así que no se abre sesión.
  * El filtro de municipio NO es el desplegable idEntidad (vacío) sino el campo de
    texto libre `organizacionText` = "Ayuntamiento de X" (valor del mapa). Se
    confirma además en local con el <h2> de cada resultado.
  * Búsqueda por TÍTULO (tipoTexto=0). Por cuerpo tarda 25-40 s: no se usa.
  * El buscador es LITERAL (sin stemming: "terraza" != "terrazas") pero acepta OR
    (`algunasPalabras`), así que TODAS las variantes de la materia van en UNA
    consulta. Insensible a mayúsculas y acentos.
  * VENENO: hay términos hiperfrecuentes en el índice GLOBAL que revientan la
    consulta ("???es_ES.mensaje.errorBusqueda???") antes de aplicar el filtro de
    municipio, aunque el municipio sea diminuto, y tardan 1-11 s en fallar. Un OR
    que contenga UNO de ellos falla entero. Verificados el 2-sep-2026: ordenanza,
    reguladora, fiscal, tasa(s), agua, basura, saneamiento, alcantarillado,
    subvenciones, municipal, aprobacion, definitiva, publico, publica, servicio(s),
    ocupacion, dominio, precio, actividades, vehiculos, obras (>300), matrimonio,
    apertura, domicilio (>300), vivienda, bienes, construccion, instalaciones,
    licencia, suministro, general (>300), aprovechamiento, especial. La lista
    _VENENO es estática + se APRENDE en caliente: si un OR falla, se trocea por
    término y el culpable queda vetado para el resto del proceso.
  * Anuncio = PDF directo (verAnuncioAction.do?idAnuBlob=N) con capa de texto en
    el 100 % de la muestra: se lee con fitz, sin OCR.
"""
import concurrent.futures as _cf
import html as _html
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

import bop_engine as B

_BUSCAR = "/boces/busquedaAnuncios.do"
_ANUNCIO = "/boces/verAnuncioAction.do?idAnuBlob="

_BLOQ = re.compile(r"(?s)<h2>(.*?)</h2>(.*?)(?=<h2>|</main>|\Z)")
_ANU = re.compile(r'verAnuncio(?:Partes)?Action\.do\?idAnuBlob=(\d+)[^>]*>\s*PDF \((BOC-(\d{4})-(\d+)[\w\-]*)\)')
_TIT = re.compile(r"<p>(.*?)</p>", re.S)
_TIPO = re.compile(r'<span class="spanH4">(.*?)</span>', re.S)
_ERR = re.compile(r'(?s)<div id="error">(.*?)</div>')
_CVE = re.compile(r"\bBOC-(\d{4})-(\d+)\b", re.I)
_FECHA = re.compile(r"(?:LUNES|MARTES|MI[ÉE]RCOLES|JUEVES|VIERNES|S[ÁA]BADO|DOMINGO),?\s+(\d{1,2})\s+DE\s+"
                    r"([A-ZÁÉÍÓÚ]+)\s+DE\s+(\d{4})")
_MESES = {"ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04", "MAYO": "05", "JUNIO": "06",
          "JULIO": "07", "AGOSTO": "08", "SEPTIEMBRE": "09", "SETIEMBRE": "09", "OCTUBRE": "10",
          "NOVIEMBRE": "11", "DICIEMBRE": "12"}

# Términos que revientan el buscador (ERRBUSQ) o desbordan los 300 resultados en
# cualquier municipio grande. Se amplía en caliente (ver _consulta).
_VENENO = {
    "ordenanza", "reguladora", "regulador", "fiscal", "fiscales", "tasa", "tasas", "agua", "basura",
    "saneamiento", "alcantarillado", "subvenciones", "municipal", "aprobacion", "definitiva",
    "definitivo", "publico", "publica", "servicio", "servicios", "ocupacion", "dominio", "precio",
    "actividades", "vehiculos", "obras", "matrimonio", "apertura", "domicilio", "vivienda", "bienes",
    "construccion", "instalaciones", "licencia", "suministro", "general", "aprovechamiento",
    "especial", "padron", "presupuesto", "modificacion", "modificaciones", "anuncio", "anuncios",
    "acuerdo", "acuerdos", "resolucion", "expediente", "expedientes", "convocatoria", "pleno",
    "ayuntamiento", "exposicion", "inicial", "provisional", "texto", "integro", "cobro", "periodo",
    "notificacion", "delegacion", "personal", "contratacion", "adjudicacion", "licitacion",
    "informacion", "bases", "concesion", "ayudas", "plan", "planes", "normas", "aprobado",
    "administracion", "administraciones",
}
_AMPLIO = set()          # (org_norm, término) que dan >300 resultados en ese municipio
_LOCK = threading.Lock()
# Términos genéricos SEGUROS para el volcado del municipio (86 anuncios en Santander, ~1,4 s)
_GENERICOS = ["ordenanzas", "reglamento"]
_ES_GENERICO = {"", "ordenanza", "ordenanzas", "reglamento", "reglamentos", "tasa", "tasas"}

# El tesauro del motor trae raíces truncadas ("acustic", "urbanistic"): en un
# buscador literal hay que desplegarlas.
_RAICES = {
    "acustic": ["acustica", "acustico", "acusticas"], "urbanistic": ["urbanistica", "urbanistico", "urbanisticas"],
    "turistic": ["turistica", "turistico", "turisticas", "turisticos"], "felin": ["felinas", "felina"],
    "canin": ["canina", "caninos", "canino"], "sonor": ["sonora", "sonoras"],
    "deportiv": ["deportivas", "deportivos", "deportiva"], "publicitari": ["publicitaria", "publicitarias"],
    "funerari": ["funerarios", "funerarias", "funerario"], "cultural": ["cultural", "culturales"],
}
_NO_TERMINO = set(B._GENERICO) | set(B._WEAK) | set(B._STOPM)
# palabras sueltas de los alias multipalabra del tesauro que, solas, casan con
# cualquier cosa ("terrenos" de «ocupación de terrenos de uso público» traía la
# plusvalía entera de Torrelavega)
_FLOJOS = {"terrenos", "terreno", "recogida", "gestion", "proteccion", "bienestar", "reserva", "entrada",
           "seguridad", "administracion", "electronica", "solidos", "urbanos", "vial", "integral", "ciclo",
           "punto", "limpio", "naturaleza", "control", "colonias", "retirada", "espacio", "espacios"}


def _variantes(w):
    """La palabra y su otro número (el BOC no hace stemming): terrazas<->terraza,
    velador<->veladores, animales->animal, inmuebles->inmueble."""
    if w in _RAICES:
        return list(_RAICES[w])
    out = [w]
    if len(w) < 4:
        return out
    if w.endswith("s"):
        if w.endswith("es") and len(w) > 5 and w[-3] in "rlndz" and w[-4] in "aeiou":
            out.append(w[:-2])                                        # veladores -> velador
        else:
            out.append(w[:-1])                                        # terrazas -> terraza
    elif w[-1] in "aeiou":
        out.append(w + "s")
    else:
        out.append(w + "es")
    return out


def _terminos(texto):
    """Dos grupos de términos (OR) para una materia: (1) las palabras del abogado
    y (2) el tesauro del motor, cada palabra en singular y plural, sin genéricos
    ni venenos. Las palabras sueltas de los alias multipalabra ("mesas y sillas")
    van al final del grupo 2 y sin flexionar. Grupos vacíos si la consulta es
    genérica ("ordenanza", "tasa")."""
    raw, core, _soft = B._familias(texto or "")
    g1 = [(w, True) for w in raw]
    g2 = [(c, True) for c in sorted(core, key=len) if " " not in c]
    g2 += [(w, False) for c in sorted(core, key=len) if " " in c and len(c.split()) <= 3
           for w in c.split() if len(w) >= 5 and w not in _FLOJOS]
    vistos, hechos, out = set(), set(), []
    for grupo, tope in ((g1, 4), (g2, 6)):
        terms = []
        for w, flexiona in grupo:
            if w in hechos or w in _NO_TERMINO or (len(w) < 4 and w not in raw):
                continue
            hechos.add(w)
            for v in (_variantes(w) if flexiona else [w]):
                if v not in vistos and v not in _VENENO:
                    vistos.add(v)
                    terms.append(v)
        out.append(terms[:tope])
    return out


# Anuncios que citan la ordenanza pero NUNCA traen su articulado: padrones y
# matrículas fiscales, periodos de cobro, notificaciones, sanciones... Se quitan
# de la lista para que el motor no los elija (ni los lea para verificar).
_RUIDO = re.compile(r"padr[oó]n|matr[ií]cula fiscal|lista cobratoria|per[ií]odo voluntario|periodo de cobro|"
                    r"notificaci[oó]n|sancionador|infracci[oó]n|correcci[oó]n de errores|cobranza|recibos|"
                    r"licitaci[oó]n|adjudicaci[oó]n|contrataci[oó]n|expropiaci|subasta", re.I)


def _post(cfg, extra, timeout=25):
    hoy = time.gmtime()
    d = {"anuncioBean.entrad": "", "anuncioBean.tipoTexto": "0", "anuncioBean.tipoBusqueda": "todasPalabras",
         "anuncioBean.filtroFecha": "1", "anuncioBean.fecDesdeString": f"01/01/{cfg.get('indice_desde', 2010)}",
         "anuncioBean.fecHastaString": f"{hoy.tm_mday:02d}/{hoy.tm_mon:02d}/{hoy.tm_year}",
         "idAdmin": "-1", "idEntidad": "-1", "organizacionText": "", "unidadText": "",
         "anuncioBean.idSeccion": "-1", "anuncioBean.idSubseccion": "-1", "anuncioBean.idTipAnu": "-1",
         "boton": "Buscar"}
    d.update(extra)
    body = urllib.parse.urlencode(d, encoding="iso-8859-15", errors="replace").encode("ascii")
    req = urllib.request.Request(cfg["base"] + _BUSCAR, data=body,
                                 headers={"User-Agent": B._UA, "Content-Type": "application/x-www-form-urlencoded",
                                          "Referer": cfg["base"] + "/boces/menu.do?dir=/inicioBusquedaAnuncios.do"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("iso-8859-15", "replace")


def _estado(h):
    m = _ERR.search(h)
    t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))) if m else ""
    if "300" in t:
        return "MAS300"
    if "errorBusqueda" in t:
        return "ERRBUSQ"
    if "No existen" in t:
        return "CERO"
    return "OK"


def _txt(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _org_ok(org_html, org):
    a, b = B._norm(org_html), B._norm(org)
    if a == b:
        return True
    # "Mancomunidad" a secas (consulta supramunicipal del motor) casa con cualquiera
    return b in ("mancomunidad", "mancomunidades") and a.startswith("mancomunidad")


def _parse(cfg, h, org, materia):
    out = []
    for org_html, bloque in _BLOQ.findall(h):
        if not _org_ok(_txt(org_html), org):
            continue
        an = _ANU.search(bloque)
        if not an:
            continue
        tt = _TIT.search(bloque)
        tp = _TIPO.search(bloque)
        blob, cve, anio, num = an.group(1), an.group(2), an.group(3), an.group(4)
        out.append({"url": cfg["base"] + _ANUNCIO + blob, "titulo": _txt(tt.group(1) if tt else ""),
                    "cve": cve, "fecha": "", "orden": f"{anio}{int(num):05d}", "materia": bool(materia),
                    "tipo": _txt(tp.group(1) if tp else ""), "organo": _txt(org_html)})
    return out


def _una(cfg, org, q, modo, extra=None):
    """(estado, filas) de UNA consulta. Nunca lanza: los fallos de red son 'EXC'."""
    d = {"anuncioBean.entrad": q, "anuncioBean.tipoBusqueda": modo, "organizacionText": org}
    if extra:
        d.update(extra)
    try:
        h = _post(cfg, d)
    except Exception:  # noqa: BLE001
        return "EXC", []
    st = _estado(h)
    return st, (_parse(cfg, h, org, True) if st == "OK" else [])


def _aprende(ok, t, st):
    if st == "ERRBUSQ":
        with _LOCK:
            _VENENO.add(t)
    elif st == "MAS300":
        with _LOCK:
            _AMPLIO.add((ok, t))


def _consulta(cfg, org, terminos, materia, extra=None, trocear=True, ruido=True):
    """Una consulta OR con todos los términos; si el BOC la rechaza (un término
    venenoso o >300 resultados), se trocea por término en paralelo (≤3) y se
    aprende cuál fue el culpable. Devuelve (estado, filas)."""
    ok = B._norm(org)
    terms = [t for t in terminos if t not in _VENENO and (ok, t) not in _AMPLIO]
    if not terms:
        return "VACIO", []
    st, rows = _una(cfg, org, " ".join(terms), "algunasPalabras" if len(terms) > 1 else "todasPalabras", extra)
    if st in ("OK", "CERO", "EXC") or len(terms) == 1 or not trocear:
        if len(terms) == 1:
            _aprende(ok, terms[0], st)
        return st, _marca(rows, materia, ruido)

    def uno(t):
        return t, _una(cfg, org, t, "todasPalabras", extra)

    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        for t, (st1, rs) in ex.map(uno, terms):
            _aprende(ok, t, st1)
            for r in rs:
                vistos.setdefault(r["cve"], r)
    return "OK", _marca(list(vistos.values()), materia, ruido)


def _materia(cfg, org, grupos):
    """Primero las palabras del abogado; el tesauro SOLO si con ellas no salen
    al menos 3 anuncios tipo ordenanza/reglamento. Medido en Santander: el grupo
    del tesauro de «residuos» ("basuras derribos escombros…") devuelve 200-250
    anuncios y tarda 5 s sin aportar nada cuando «residuos» ya trajo 81; en
    Laredo, en cambio, la tasa se titula «recogida de BASURAS» y solo la
    encuentra el tesauro. Dedup por CVE."""
    vistos = {}
    for g in grupos:
        if not g:
            continue
        _st, rows = _consulta(cfg, org, g, True)
        for r in rows:
            vistos.setdefault(r["cve"], r)
        if sum(1 for r in vistos.values() if B._es_ordenanza(r["titulo"])) >= 3:
            break
    return list(vistos.values())


def _marca(rows, materia, ruido=True):
    if ruido:
        rows = [r for r in rows if not _RUIDO.search(r["titulo"])]
    for r in rows:
        r["materia"] = bool(materia)
    return rows


def _por_cve(cfg, org, anio, cve):
    """Un anuncio concreto por su CVE (BOC-AAAA-N). El buscador no filtra por CVE:
    se lista el municipio en ese año (volcado genérico y, si no aparece, sin texto,
    que solo cabe en municipios pequeños por el tope de 300)."""
    ventana = {"anuncioBean.fecDesdeString": f"01/01/{anio}", "anuncioBean.fecHastaString": f"31/12/{anio}"}
    for rows in (_consulta(cfg, org, _GENERICOS, True, ventana, ruido=False)[1],
                 _una(cfg, org, "", "todasPalabras", ventana)[1]):
        for r in rows:
            if r["cve"].upper() == cve.upper():
                r["materia"] = True
                return [r]
    return []


_ALIAS_TESAURO = None


def _es_alias_tesauro(texto):
    """¿La consulta es un alias multipalabra del tesauro del motor ("punto
    limpio", "mesas y sillas")? El motor los lanza como consultas secundarias
    además de la materia; volver a expandirlos con el tesauro es redundante."""
    global _ALIAS_TESAURO
    if _ALIAS_TESAURO is None:
        _ALIAS_TESAURO = {B._mnorm(a) for _p, cs, _s in getattr(B, "_EXPANSION", []) for a in cs if " " in a}
    return B._mnorm(texto) in _ALIAS_TESAURO


_RES = {}                      # (prov, org, texto) -> (filas, ts): el chat llama buscar y leer seguidos
_RES_TTL = 300
_EN_VUELO = {}                 # clave -> Lock (consultas idénticas concurrentes)


def buscar(prov, texto, filtro, rpp=40):
    """Anuncios del municipio (filtro = 'Ayuntamiento de X') sobre la materia.
    Consulta genérica ("ordenanza") = volcado de ordenanzas/reglamentos."""
    cfg = B.PROVINCIAS[prov]
    texto = (texto or "").strip()
    org = filtro
    if not org:
        m = re.match(r"(?i)^mancomunidad(?:es)?\b\s*(.*)$", texto)
        if not m:
            return []
        org, texto = "Mancomunidad", m.group(1).strip()
    mc = _CVE.search(texto)
    if mc:
        return _por_cve(cfg, org, mc.group(1), mc.group(0))
    clave = (prov, B._norm(org), B._norm(texto))
    c = _RES.get(clave)
    if c and time.time() - c[1] < _RES_TTL:
        return [dict(r) for r in c[0]]
    # el motor lanza la misma consulta dos veces en paralelo (escalera de recall):
    # un cerrojo por clave hace que la segunda espere a la primera en vez de repetirla
    with _LOCK:
        cerrojo = _EN_VUELO.setdefault(clave, threading.Lock())
    with cerrojo:
        c = _RES.get(clave)
        if c and time.time() - c[1] < _RES_TTL:
            return [dict(r) for r in c[0]]
        rows = _buscar(cfg, org, texto)
        if len(_RES) > 64:
            _RES.clear()
        _RES[clave] = (rows, time.time())
    return [dict(r) for r in rows]


def _buscar(cfg, org, texto):
    tn = B._norm(texto)
    if tn in _ES_GENERICO:
        if tn in ("reglamento", "reglamentos", "tasa", "tasas"):
            return []                     # ya cubierto por el volcado de "ordenanza" (mismo coste, 0 peticiones)
        return _consulta(cfg, org, _GENERICOS, False)[1]
    grupos = _terminos(texto)
    if _es_alias_tesauro(texto):
        grupos = grupos[:1]        # el motor ya lanza los alias del tesauro como consultas propias
    res = _materia(cfg, org, grupos)
    if not res:
        # la materia no está en ningún título: volcado genérico para que el motor
        # verifique por contenido los títulos genéricos ("Ordenanza reguladora")
        res = _consulta(cfg, org, _GENERICOS, False)[1]
    return res


# ---- lectura ---------------------------------------------------------------
_TXT = {}                      # url -> (texto, fecha, ts)
_TXT_TTL = 600


def _limpia_boc(t):
    """Trampas medidas del texto de los PDF del BOC: control U+0003 como espacio
    (1 de cada 81), ligaduras fi/fl partidas ('deﬁ nitiva'), guion blando y el
    pie/cabecera de cada página."""
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", t or "")
    t = re.sub(r"([ﬁﬂ])[ \t]+(?=[a-záéíóúüñ])", r"\1", t)
    t = unicodedata.normalize("NFKC", t).replace("­", "")
    t = re.sub(r"(?m)^[ \t]*(?:i|boc\.cantabria\.es|P[áa]g\. ?\d+|\d{1,3}/\d{1,3}|CVE-\d{4}-\d+|"
               r"[A-ZÁÉÍÓÚ]+, \d{1,2} DE [A-ZÁÉÍÓÚ]+ DE \d{4} - BOC[^\n]*|\d{4}/\d+)[ \t]*\n", "", t)
    return t


def texto(prov, m):
    """(texto, via) del anuncio: PDF directo con capa de texto. Sin OCR (0/30 lo
    necesitaban): si el PDF fuese escaneado se responde honesto."""
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    c = _TXT.get(u)
    if c and time.time() - c[2] < _TXT_TTL:
        if isinstance(m, dict) and c[1] and not m.get("fecha"):
            m["fecha"] = c[1]
        return c[0], "pdf"
    try:
        pdf = B._getb(u, timeout=25)
    except Exception:  # noqa: BLE001
        return "", "sin-pdf"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = B._pdf_bytes_texto(pdf, ocr=False)
    if via != "directo" or len(t) < 200:
        return "", "sin-texto"
    fecha = ""
    mf = _FECHA.search(t[:600])
    if mf and mf.group(2).upper() in _MESES:
        fecha = f"{int(mf.group(1)):02d}/{_MESES[mf.group(2).upper()]}/{mf.group(3)}"
    t = _limpia_boc(t)
    if isinstance(m, dict) and fecha and not m.get("fecha"):
        m["fecha"] = fecha
    if len(_TXT) > 64:
        _TXT.clear()
    _TXT[u] = (t, fecha, time.time())
    return t, "pdf"
