# -*- coding: utf-8 -*-
"""Backend BADAJOZ del motor de ordenanzas (bop_engine): Boletín Oficial de la
Provincia de Badajoz — https://www.dip-badajoz.es/bop (app PHP propia de la
Diputación). Receta verificada en vivo el 27-jul-2026 (sondas _probe_badajoz*.py)
e implementada el 2-sep-2026. 184 ayuntamientos (mapa bop_badajoz_municipios.json:
nombre -> id de `delegacion`).

Cómo funciona el buscador (index.php?Busqueda=1):
  * POST form-urlencoded: `asunto` (texto del TÍTULO del edicto), `delegacion`
    (id del ayuntamiento = valor del mapa), administracion=1, tipo_entidad=12,
    cantidad=99 + unidad=4 (= últimos 99 años; el índice arranca en 2006).
    `localidad` NO se usa (devuelve 0 resultados).
  * CSRF: cookie `__RequestVerificationToken` que ROTA en cada POST -> se relee
    del cookiejar justo antes de cada petición y se reenvía como parámetro del
    mismo nombre. Un token viejo no vale. Las sesiones se reutilizan EN SERIE
    (pool): cada búsqueda (POST + páginas NPS) usa una sesión en exclusiva.
  * `asunto` = coincidencia de PALABRA o FRASE completa, insensible a mayúsculas
    y tildes, pero NI prefijo NI subcadena («terraza» no encuentra «terrazas»,
    «ordenanz» no encuentra nada) y con MÍNIMO de 5 caracteres («agua», «vado»,
    «tasa», «IBI» -> «debe ser superior a 4 caracteres»). Por eso: términos
    largos del tesauro, equivalencias para los cortos (_CORTOS) y reintento
    singular<->plural si la primera forma no da nada.
  * Tres estados de la página: «Se han encontrado N edictos», «NO se ha
    encontrado edicto alguno» y «Se han obtenido demasiados resultados» (tope
    del servidor: término demasiado frecuente; se trata como vacío).
  * Resultados: 10 por página, cronológico inverso. Páginas siguientes por GET
    index.php?Busqueda=1&NPS=n&accion=Buscar (ligado a la sesión de la búsqueda;
    ~1,2 s cada una, así que se acotan).
  * Lectura: ventana_anuncio.php?id_anuncio=..&FechaSolicitada=.. devuelve el
    texto ÍNTEGRO en HTML (div.contenido_anuncio), sin PDF ni OCR, ~0,25 s.
  * El BOP no tiene CVE: el identificador oficial es «Anuncio N/AAAA». Se expone
    como cve «BOP-BA-AAAA-N» (el motor lo reconoce para leer por cita); como el
    buscador no permite buscar por ese número, se resuelve por caché y, si no
    está, rastreando los volcados genéricos del municipio.
  * Los padrones, anuncios de cobranza, notificaciones, licitaciones... contienen
    «tasa» en el título y pasarían por ordenanza en el ranking común: se filtran
    aquí (_RUIDO) salvo que el título diga ordenanza/reglamento.
"""
import concurrent.futures as _cf
import html as _html
import http.cookiejar
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import bop_engine as B

_SEM = threading.BoundedSemaphore(3)      # cortesía con el BOP: ≤3 peticiones simultáneas
_POOL_LOCK = threading.Lock()
_POOL = []                                # sesiones libres (se reutilizan en serie)
_SES_TTL = 420                            # s; la sesión PHP caduca: se renueva antes
_RES = {}                                 # (delegacion, consulta) -> (ts, resultados)
_RES_TTL = 600
_CVE = {}                                 # cve -> anuncio (lectura por cita)
_CVE_FILE = os.path.join(tempfile.gettempdir(), "bop-badajoz-cve.json")
_CVE_LOCK = threading.Lock()

_FILA = re.compile(
    r'<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*'
    r'<a href="([^"]*ventana_anuncio\.php[^"]*)"[^>]*>(.*?)</a>', re.S)
_CONTADOR = re.compile(r"Se han? encontrado\s+([\d\.]+)\s+edictos?", re.I)
_CERO = re.compile(r"NO se ha encontrado edicto alguno", re.I)
_TOPE = re.compile(r"demasiados resultados", re.I)
_CORTO = re.compile(r"superior a 4 caracteres", re.I)
_BOLETIN = re.compile(r"N\.?\s*[ºo°]?\s*(\d+)\s+(\d{4})-(\d{2})-(\d{2})")
# «[01171/2026]»; las correcciones de errores llevan letra: «[R03121/2024]»
_IDENT = re.compile(r"\[\s*([A-Z]?)0*(\d+)/(\d{4})\s*\]\s*$")
_CVE_RE = re.compile(r"(?i)\bBOP-BA-(\d{4})-0*(\d+)\b")
_NORMA = re.compile(r"ordenanza|reglamento|\bbando\b|estatutos?\b", re.I)
# anuncios que NO son normativa pero llevan «tasa»/«impuesto» en el título
_RUIDO = re.compile(
    r"padr[oó]n|cobranza|per[ií]odo voluntario|calendario fiscal|notificaci[oó]n|emplazamiento|"
    r"citaci[oó]n|licitaci[oó]n|adjudicaci[oó]n|concesi[oó]n (?:demanial|administrativa)|"
    r"convocatoria|bases (?:de la|del|para)|oferta de empleo|lista (?:provisional|definitiva)|"
    r"nombramiento|delegaci[oó]n de|expediente sancionador|subvenci|licencia (?:de|para)|"
    r"mapa estrat[eé]gico|declaraci[oó]n de zona|informaci[oó]n p[uú]blica de(?:l| la)? "
    r"(?:proyecto|solicitud|expediente)|matr[ií]cula|liquidaci[oó]n|cuenta general|"
    r"presupuesto general|modificaci[oó]n de cr[eé]ditos|plan econ[oó]mico|\bayudas?\b|"
    r"\bbecas?\b|premios?\b|concurso|bases reguladoras de", re.I)

# Equivalencias para términos que el buscador rechaza (< 5 caracteres) o que en
# los títulos del BOP aparecen con otra forma. Los de 5+ letras van tal cual.
_CORTOS = {
    "agua": ["de agua", "aguas"], "aguas": ["aguas", "de agua"], "vado": ["vados"],
    "tasa": ["la tasa"], "tasas": ["la tasa"], "ibi": ["bienes inmuebles"],
    "icio": ["construcciones"], "iae": ["actividades economicas"], "zbe": ["bajas emisiones"],
    "vmp": ["movilidad personal"], "ora": ["estacionamiento"], "bici": ["bicicletas"],
    "vut": ["uso turistico"], "gato": ["gatos"], "perro": ["perros"], "obra": ["obras"],
    "boda": ["bodas"], "grua": ["grua"], "taxi": ["taxi"], "ruido": ["ruido", "ruidos"],
}
_GENERICOS = {"ordenanza", "ordenanzas", "reglamento", "reglamentos"}


def _t(x):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


# ---- sesión (cookie CSRF rotatoria) --------------------------------------------
class _Sesion:
    def __init__(self, cfg):
        self.base = cfg["base"]
        self.url = cfg["base"] + cfg.get("endpoints", {}).get("buscador", "/index.php?Busqueda=1")
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.op.addheaders = [("User-Agent", B._UA), ("Accept-Language", "es-ES,es;q=0.9")]
        with _SEM:
            self.op.open(self.url, timeout=20).read()
        self.nacida = time.time()

    def token(self):
        return next((c.value for c in self.cj if c.name == "__RequestVerificationToken"), "")

    def post(self, campos, timeout=25):
        datos = dict(campos)
        datos["__RequestVerificationToken"] = self.token()      # ROTA en cada POST
        req = urllib.request.Request(
            self.url, data=urllib.parse.urlencode(datos, encoding="utf-8").encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": self.url,
                     "Origin": self.base.split("/bop")[0]})
        with _SEM:
            return self.op.open(req, timeout=timeout).read().decode("utf-8", "replace")

    def get(self, url, timeout=25):
        with _SEM:
            return self.op.open(urllib.request.Request(url, headers={"Referer": self.url}),
                                timeout=timeout).read().decode("utf-8", "replace")


def _tomar(cfg):
    with _POOL_LOCK:
        while _POOL:
            s = _POOL.pop()
            if time.time() - s.nacida < _SES_TTL:
                return s
    return _Sesion(cfg)


def _soltar(s):
    with _POOL_LOCK:
        if len(_POOL) < 4 and time.time() - s.nacida < _SES_TTL:
            _POOL.append(s)


# ---- parseo de resultados ----------------------------------------------------------
def _estado(h):
    m = _CONTADOR.search(h)
    if m:
        return "n", int(m.group(1).replace(".", ""))
    if _CERO.search(h):
        return "cero", 0
    if _TOPE.search(h):
        return "tope", 0
    if _CORTO.search(h):
        return "corto", 0
    return "?", 0


def _filas(cfg, h, materia):
    origen = "https://" + urllib.parse.urlparse(cfg["base"]).netloc
    out = []
    for bol, anunc, href, asunto in _FILA.findall(h):
        asunto = _t(asunto)
        href = _html.unescape(href)
        if href.startswith("/"):
            href = origen + href
        mid = re.search(r"id_anuncio=(\d+)", href)
        mi = _IDENT.search(asunto)
        letra, num, anio = (mi.group(1), int(mi.group(2)), mi.group(3)) if mi else ("", 0, "")
        titulo = _IDENT.sub("", asunto).strip()
        mb = _BOLETIN.search(_t(bol))
        if mb:
            nbol, fecha = mb.group(1), f"{mb.group(4)}/{mb.group(3)}/{mb.group(2)}"
            orden = mb.group(2) + mb.group(3) + mb.group(4)
        else:
            nbol, fecha, orden = "", "", "0"
        if not anio and orden != "0":
            anio = orden[:4]
        if _RUIDO.search(titulo) and not _NORMA.search(titulo):
            continue
        # el sufijo de letra («…-3121R») no lo reconoce el motor al leer por cita: cae al
        # anuncio original 3121/2024 (la norma), que es lo útil
        out.append({"url": href, "titulo": titulo, "cve": f"BOP-BA-{anio}-{num}{letra}" if num and anio else "",
                    "fecha": fecha, "orden": orden, "boletin": nbol, "id": mid.group(1) if mid else "",
                    "anunciante": _t(anunc), "materia": bool(materia)})
    return out


# ---- caché de CVE (lectura por cita) ---------------------------------------------
def _cve_disco():
    try:
        return json.load(open(_CVE_FILE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _cve_recordar(res):
    nuevos = {r["cve"]: r for r in res if r.get("cve") and r["cve"] not in _CVE}
    if not nuevos:
        return
    with _CVE_LOCK:
        _CVE.update(nuevos)
        try:
            d = _cve_disco()
            d.update(nuevos)
            if len(d) > 3000:                      # que no crezca sin tope
                d = dict(sorted(d.items())[-2000:])
            with open(_CVE_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass


# ---- una consulta al buscador (POST + páginas NPS) -------------------------------
def _consulta(cfg, filtro, q, rpp, materia, maxpag):
    clave = (filtro or "", q.lower())
    c = _RES.get(clave)
    if c and time.time() - c[0] < _RES_TTL:
        return c[1]
    campos = {"asunto": q, "anuncio": "", "administracion": "1", "tipo_entidad": "12",
              "delegacion": filtro or "", "provincia": "", "localidad": "",
              "cantidad": "99", "unidad": "4", "accion": "Buscar"}
    s = _tomar(cfg)
    try:
        h = s.post(campos)
        estado, total = _estado(h)
        if estado != "n":
            res = []
        else:
            res = _filas(cfg, h, materia)
            npag = min(-(-total // 10), max(1, -(-rpp // 10)), maxpag)
            if npag > 1:
                plantilla = cfg.get("endpoints", {}).get("pagina", "/index.php?Busqueda=1&NPS={pagina}&accion=Buscar")
                urls = [cfg["base"] + plantilla.format(pagina=p) for p in range(2, npag + 1)]

                def pagina(u):
                    try:
                        return s.get(u)
                    except Exception:  # noqa: BLE001
                        return ""
                with _cf.ThreadPoolExecutor(max_workers=min(3, len(urls))) as ex:
                    for hh in ex.map(pagina, urls):
                        res.extend(_filas(cfg, hh, materia))
    finally:
        _soltar(s)
    if estado in ("n", "cero", "tope", "corto"):
        _RES[clave] = (time.time(), res)
    _cve_recordar(res)
    return res


# ---- términos a consultar -----------------------------------------------------------
def _consultas(texto):
    """(consultas de 1ª oleada, consultas de 2ª oleada, es_materia). El buscador es
    de palabra/frase exacta con mínimo de 5 letras: la 1ª oleada son los términos
    del abogado (los más largos primero, traducidos si son cortos); la 2ª, las
    frases del tesauro («mesas y sillas», «entrada de vehiculos»), solo si la
    primera no da ninguna norma."""
    mn = B._mnorm(texto)
    if mn in _GENERICOS:
        return [mn], [], False
    if mn in ("tasa", "tasas"):
        return ["la tasa"], [], False
    raw, core, _soft = B._familias(texto)
    out = []
    for w in sorted(raw, key=len, reverse=True):
        if w in _CORTOS:
            cands = _CORTOS[w]
        elif len(w) >= 5:
            cands = [w]
        elif len(w) == 4:
            cands = [w + "s"]
        else:
            cands = []
        for q in cands:
            if q not in out:
                out.append(q)
    segunda = []
    for c in sorted(core, key=lambda c: (" " not in c, -len(c))):     # frases primero
        if c in out or c in segunda or len(c) < 5:
            continue
        if " " not in c and (re.search(r"(?:ic|ari|in)$", c) or any(c.startswith(r) or r.startswith(c) for r in out)):
            continue                     # raíces truncadas (acustic, turistic) y repetidos
        segunda.append(c)
    if not out:
        if segunda:
            return segunda[:3], [], True
        return ["ordenanza"], [], False
    return out[:3], segunda[:3], True


def _variante(q):
    """Forma singular<->plural para reintentar cuando la primera no da nada."""
    if " " in q:
        return ""
    if q.endswith("es") and len(q) > 6:
        v = q[:-2]
    elif q.endswith("s") and len(q) > 5:
        v = q[:-1]
    elif q[-1] in "aeiou":
        v = q + "s"
    else:
        v = q + "es"
    return v if len(v) >= 5 and v != q else ""


def _por_cve(cfg, filtro, anio, num):
    cve = f"BOP-BA-{anio}-{num}"
    r = _CVE.get(cve) or _cve_disco().get(cve)
    if r:
        return [r]
    if not filtro:
        return []
    # rastreo: volcados genéricos del municipio hasta dar con ese número de anuncio
    for q in ("ordenanza", "reglamento", "la tasa"):
        try:
            res = _consulta(cfg, filtro, q, 40, False, 4)
        except Exception:  # noqa: BLE001
            res = []
        for r in res:
            if r.get("cve") == cve:
                return [r]
    return []


# ================================================================ contrato bop_engine
def buscar(prov, texto, filtro=None, rpp=40):
    """Anuncios del ayuntamiento `filtro` (id de delegacion) cuyo título contiene
    los términos de `texto`. [{url,titulo,cve,fecha,orden,boletin,id,...}]"""
    cfg = B.PROVINCIAS[prov]
    m = _CVE_RE.search(texto or "")
    if m:
        return _por_cve(cfg, filtro, m.group(1), int(m.group(2)))
    consultas, segunda, materia = _consultas(texto or "ordenanza")
    # cada página NPS cuesta ~1,2 s: la materia se pagina (las ordenanzas viejas
    # importan), el volcado genérico («ordenanza», «reglamento») se queda en la 1ª
    maxpag = 3 if materia else 1

    def run(q):
        try:
            res = _consulta(cfg, filtro, q, rpp, materia, maxpag)
            if not res and materia:
                v = _variante(q)
                if v:
                    res = _consulta(cfg, filtro, v, rpp, materia, maxpag)
            return res
        except Exception:  # noqa: BLE001
            return []

    vistos = {}

    def oleada(qs):
        with _cf.ThreadPoolExecutor(max_workers=min(3, len(qs))) as ex:
            for rs in ex.map(run, qs):
                for r in rs:
                    vistos.setdefault(r["id"] or r["url"], r)

    oleada(consultas)
    if segunda and not any(_NORMA.search(r["titulo"]) for r in vistos.values()):
        oleada(segunda)              # el abogado dijo «terrazas»; el BOP titula «mesas y sillas»
    out = list(vistos.values())
    out.sort(key=lambda r: r["orden"], reverse=True)
    return out


def texto(prov, m):
    """(texto_plano, via) del anuncio: HTML íntegro de ventana_anuncio.php."""
    cfg = B.PROVINCIAS[prov]
    url = (m.get("url") if isinstance(m, dict) else m) or ""
    if not url:
        return "", "sin-url"
    ident = (m.get("id") if isinstance(m, dict) else "") or (re.search(r"id_anuncio=(\d+)", url) or [None, ""])[1]
    clave = "badajoz-" + (ident or re.sub(r"\W", "_", url)[-40:])
    t = B._txt_cache_get(clave)
    if t:
        return t, "html"
    s = _tomar(cfg)
    try:
        h = s.get(url, timeout=25)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{e}"
    finally:
        _soltar(s)
    cuerpo = (re.search(r'(?s)<div class="contenido_anuncio"[^>]*>(.*?)<footer class="pie_anuncio"', h)
              or re.search(r'(?s)<div class="contenido_anuncio"[^>]*>(.*?)</article>', h))
    if cuerpo:
        frag = cuerpo.group(1)
    else:
        frag = re.sub(r"(?is)^.*?</header>", "", h)
    t = B._html_a_texto(frag)
    if len(t) < 40:
        return "", "sin-texto"
    B._txt_cache_set(clave, t)
    return t, "html"
