# -*- coding: utf-8 -*-
"""Backend del BOP de CIUDAD REAL (SIGEM de la Diputación) para bop_engine — familia «ciudadreal».

Contrato: buscar(prov, texto, filtro, rpp) -> [{url, titulo, cve, fecha, orden, …}] y
texto(prov, m) -> (texto_plano, via). Lo despacha bop_engine._buscar_raw/_texto por importlib.

Cómo funciona (receta verificada 27-jul-2026 y 2-sep-2026):
  * El buscador del BOP (/buscador) solo indexa el TÍTULO y el título nunca dice el
    municipio; su filtro `entidad` está roto. Por eso la vía principal es un ÍNDICE
    EMPAQUETADO (ordenanzas_data/ciudadreal_indice.json, lo genera
    _gen_indice_ciudadreal.py recorriendo el SUMARIO DIARIO /bop/AAAA/MM/DD desde 2013,
    que agrupa los anuncios por municipio EXACTO). Búsqueda = 0 red.
  * Lo publicado DESPUÉS de la fecha del índice se completa leyendo en vivo los sumarios
    diarios posteriores (0,4 s/día, ≤4 en paralelo, caché en memoria; tope 21 días).
  * El texto de un anuncio es su PDF (getDocument.do?entidad=005&doc=N): capa de texto
    real (fitz), sin OCR; se limpian las cabeceras/pies de página del SIGEM.
  * El motor decide por contenido (config `verifica_texto`): aquí se marca `materia`
    cuando el título ya lleva la materia (camino rápido de _mejor_verificado).
  * CVE propio: BOP-CR-<año>-<nº de anuncio> (el número de anuncio se reinicia cada año).
"""
import concurrent.futures as _cf
import datetime as _dt
import html as _html
import json
import os
import re
import threading
import time
import urllib.request

import bop_engine as B

_HERE = os.path.dirname(os.path.abspath(__file__))
_IDX_FP = os.path.join(_HERE, "ordenanzas_data", "ciudadreal_indice.json")
_MAPA_FP = os.path.join(_HERE, "ordenanzas_data", "bop_ciudadreal_municipios.json")
BASE = "https://bop.dipucr.es"
PDF_URL = "https://se1.dipucr.es:4443/SIGEM_BuscadorDocsWeb/getDocument.do?entidad=005&doc={doc}"
_CVE = re.compile(r"(?i)\bBOP-CR-(\d{4})-(\d{1,5})\b")
_MAX_DIAS_VIVOS = 21


def _get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": B._UA}),
                                  timeout=timeout, context=B._SSL_NOVERIFY).read()


# ---- filtro «¿es normativa municipal?» sobre el TÍTULO (compartido con Burgos) ------
NORMATIVO = re.compile(r"ordenan[zç]a|reglament|\btasas?\b|precios? p[uú]blicos?|prestaci[oó]n patrimonial|"
                       r"aprobaci[oó]n definitiva|texto (?:[ií]ntegro|refundido|consolidado)|\bbando\b|"
                       r"normas? urban|plan (?:general|especial|de ordenaci)|\bpgou\b|\bpom\b|"
                       r"limitaci[oó]n|regulaci[oó]n|estatutos|c[oó]digo (?:de conducta|[eé]tico)|normativa",
                       re.I)
NO_NORMATIVO = re.compile(
    r"padr[oó]n|notificaci[oó]n|licitaci[oó]n|contrataci[oó]n|adjudicaci[oó]n|"
    r"bases (?:de|para|del|que|reguladoras)|convocatoria|nombramiento|delegaci[oó]n|"
    r"lista (?:provisional|definitiva)|oferta de empleo|expediente sancionador|emplazamiento|"
    r"subasta|formalizaci[oó]n|cobranza|per[ií]odo de cobro|calificaci[oó]n ambiental|licencia de|"
    r"extracto|presupuest|cr[eé]dito|cuenta general|plantilla|relaci[oó]n de puestos|"
    r"oposici[oó]n|concurso|subvenci|encomienda|caducidad|baja de oficio|matrimonio|"
    r"proyecto de|obras? de|expropiaci|responsabilidad patrimonial|veh[ií]culos? abandonad|"
    r"selecci[oó]n|bolsa de (?:trabajo|empleo)|inscripci[oó]n indebida|rectificaci[oó]n de errores|"
    r"correcci[oó]n de errores|lista cobratoria|liquidaci|recursos humanos|estudio de detalle|"
    r"unidad de actuaci|proyecto b[aá]sico|incoaci[oó]n|alteraci[oó]n de trazado|deslinde|"
    r"aprovechamientos? forestal|coto de caza", re.I)
_FUERTE = re.compile(r"ordenan[zç]a|reglament", re.I)


def es_normativo(titulo):
    """Un anuncio entra en el índice si su título habla de ordenanza/reglamento/tasa…
    y no es un acto de gestión (padrón, licitación, presupuesto…) — salvo que diga
    expresamente ordenanza/reglamento (p.ej. «bases… de la ordenanza»)."""
    t = titulo or ""
    return bool(NORMATIVO.search(t)) and (not NO_NORMATIVO.search(t) or bool(_FUERTE.search(t)))


# ---- entidad del sumario -> municipio del mapa ---------------------------------------
# variantes sucias vistas en los sumarios -> clave del mapa (el resto se resuelve por normalización)
ALIAS = {
    "ARGAMASILLA ALBA": "Argamasilla de Alba", "VILLARUBIA DE LOS OJOS": "Villarrubia de los Ojos",
    "VALDEMANCO DEL ESTERA": "Valdemanco del Esteras", "TORRE JUAN DE ABAD": "Torre de Juan Abad",
    "FONTANEREJO": "Fontanarejo", "ALCOBA": "Alcoba de los Montes", "ROBLEDO": "El Robledo",
    "CORTIJOS": "Los Cortijos", "LABORES": "Las Labores", "SOLANA": "La Solana",
    "POZUELOS DE CALATRAVA": "Los Pozuelos de Calatrava", "PUEBLA DEL PRÍNCIPE": "Puebla del Príncipe",
    "PUEBLA DEL PRINCIPE": "Puebla del Príncipe",
    # erratas vistas en el crawl 2013-2026
    "VISO DEL MAQUÉS": "Viso del Marqués", "MIGUELTURA": "Miguelturra", "ALBADALEJO": "Albaladejo",
    "VILLARRRUBIA DE LOS OJOS": "Villarrubia de los Ojos",
}
_STOP = re.compile(r"\b(?:de|del|la|los|las|el|y)\b")
_MAPA = None
_NORM2KEY, _SINSTOP2KEY = {}, {}
_ALIAS_N = {B._norm(k): v for k, v in ALIAS.items()}


def _sin_stop(s):
    return re.sub(r"\s+", "", _STOP.sub(" ", B._mnorm(s)))


def _mapa():
    global _MAPA
    if _MAPA is None:
        try:
            m = json.load(open(_MAPA_FP, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            m = {}
        for k, v in m.items():
            _NORM2KEY[B._norm(k)] = k
            _NORM2KEY[B._norm(v)] = k
            _SINSTOP2KEY[_sin_stop(k)] = k
        _MAPA = m
    return _MAPA


def resolver_entidad(ent):
    """'CIUDAD REAL - PATRONATO MUNICIPAL DE DEPORTES' -> ('Ciudad Real', 'PATRONATO…');
    'VILLARUBIA DE LOS OJOS' -> ('Villarrubia de los Ojos', ''); EATIM/otros -> (None, ent)."""
    _mapa()
    ent = re.sub(r"\s+", " ", _html.unescape(ent or "")).strip(" .:-")
    sub = ""
    m = re.match(r"^(.*?)\s+[-–]\s+(.+)$", ent)
    if m:
        ent, sub = m.group(1).strip(), m.group(2).strip()
    base = re.sub(r"^(?:EXCMO\.?\s+)?AYUNTAMIENTO\s+DE\s+", "", ent, flags=re.I).strip()
    n = B._norm(base)
    k = _NORM2KEY.get(n) or _ALIAS_N.get(n) or _SINSTOP2KEY.get(_sin_stop(base))
    if not k and len(n) >= 6 and not re.search(r"(?i)e\.?a\.?t\.?i\.?m|patronato|instituto|fundaci|"
                                                r"organismo|consorcio|mancomunidad|empresa|gerencia", base):
        # erratas de tecleo del sumario («MIGUELTURA», «ALBADALEJO»): cierre difuso muy exigente
        import difflib
        cerca = difflib.get_close_matches(n, list(_NORM2KEY), n=1, cutoff=0.88)
        if cerca:
            k = _NORM2KEY[cerca[0]]
    return k, sub


# ---- sumario diario /bop/AAAA/MM/DD ------------------------------------------------------
_SECC = re.compile(r'<h3 class="admons">\s*(.*?)\s*</h3>(.*?)(?=<h3 class="admons">|\Z)', re.S)
_ENT = re.compile(r'<p class="clasificaciones">(.*?)</p>(.*?)(?=<p class="clasificaciones">|\Z)', re.S)
_LI = re.compile(r'<li id="(\d+)">(.*?)</li>', re.S)
_A = re.compile(r'<a href="([^"]*getDocument\.do[^"]*doc=(\d+)[^"]*)"[^>]*>(.*?)</a>', re.S)
_NUM = re.compile(r"Anuncio N[ºo]\s*(\d+)")
_BOL = re.compile(r"<span>\s*N[uú]mero\s+(\d+)\s*·")


def parse_sumario(html):
    """-> (nº boletín | None, [ {ent, t, d, n} ] de la(s) sección(es) AYUNTAMIENTOS)."""
    mb = _BOL.search(html)
    num = int(mb.group(1)) if mb else None
    out = []
    for cab, cuerpo in _SECC.findall(html):
        if "AYUNTAMIENTO" not in cab.upper():
            continue
        for ent, bloque in _ENT.findall(cuerpo):
            ent = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", ent))).strip()
            for _li, item in _LI.findall(bloque):
                ma = _A.search(item)
                if not ma:
                    continue
                tit = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", ma.group(3)))).strip(" .")
                mn = _NUM.search(item)
                out.append({"ent": ent, "t": tit[:240], "d": ma.group(2), "n": int(mn.group(1)) if mn else 0})
    return num, out


# ---- índice empaquetado ----------------------------------------------------------------
_IDX = {}          # organo_norm -> [filas]; "_meta" -> meta; "_por_cve" -> {cve: fila}
_LOCK = threading.Lock()


def _fila(o, t, n, d, f, e=""):
    f = f or ""
    # CVE = año + nº de anuncio (se reinicia cada año); si el sumario no trae el nº, el doc
    # id del PDF (7 cifras: no colisiona con los números de anuncio, que no pasan de 4-5)
    return {"url": PDF_URL.format(doc=d), "titulo": (f"{t} [{e.title()}]" if e else t),
            "cve": f"BOP-CR-{f[:4]}-{int(n or 0) or int(d)}", "fecha": f"{f[6:8]}/{f[4:6]}/{f[:4]}" if len(f) == 8 else "",
            "orden": f if len(f) == 8 else "0", "doc": str(d), "organo": o}


def _indice():
    if _IDX:
        return _IDX
    with _LOCK:
        if _IDX:
            return _IDX
        try:
            d = json.load(open(_IDX_FP, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            d = {"meta": {}, "anuncios": []}
        por, por_cve = {}, {}
        for a in d.get("anuncios", []):
            r = _fila(a["o"], a["t"], a.get("n"), a["d"], a.get("f"), a.get("e", ""))
            por.setdefault(B._norm(a["o"]), []).append(r)
            por_cve[r["cve"]] = r
        por["_meta"] = d.get("meta", {})
        por["_por_cve"] = por_cve
        _IDX.update(por)
    return _IDX


# ---- lo publicado después del índice: sumarios en vivo ----------------------------------
_DIAS = {}         # iso -> (ts, [filas de TODOS los ayuntamientos, ya normativas])
_DLOCK = threading.Lock()


def _dia_vivo(iso):
    c = _DIAS.get(iso)
    reciente = iso >= (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    if c and (not reciente or time.time() - c[0] < 600):
        return c[1]
    try:
        html = _get(f"{BASE}/bop/{iso[:4]}/{iso[5:7]}/{iso[8:10]}", timeout=12).decode("iso-8859-1", "replace")
        _num, anuncios = parse_sumario(html)
    except Exception:  # noqa: BLE001
        return []                      # no se cachea el fallo: se reintenta en la próxima
    filas = []
    for a in anuncios:
        if not es_normativo(a["t"]):
            continue
        k, sub = resolver_entidad(a["ent"])
        if k:
            filas.append(_fila(k, a["t"], a["n"], a["d"], iso.replace("-", ""), sub))
    _DIAS[iso] = (time.time(), filas)
    return filas


def _recientes():
    """Filas normativas de los días posteriores a la fecha del índice (tope 21 días)."""
    meta = _indice().get("_meta", {})
    hasta = meta.get("hasta") or ""
    if not hasta:
        return []
    try:
        d0 = _dt.date.fromisoformat(hasta) + _dt.timedelta(days=1)
    except Exception:  # noqa: BLE001
        return []
    hoy = _dt.date.today()
    if d0 > hoy:
        return []
    d0 = max(d0, hoy - _dt.timedelta(days=_MAX_DIAS_VIVOS - 1))
    dias = [(d0 + _dt.timedelta(days=i)).isoformat() for i in range((hoy - d0).days + 1)]
    with _DLOCK:                       # una sola ráfaga aunque el motor lance 4 consultas a la vez
        pend = [d for d in dias if d not in _DIAS]
        if pend:
            with _cf.ThreadPoolExecutor(max_workers=min(4, len(pend))) as ex:
                list(ex.map(_dia_vivo, pend))
    out = []
    for d in dias:
        out.extend(_DIAS.get(d, (0, []))[1])
    return out


# ---- contrato ---------------------------------------------------------------------------
def buscar(prov, texto, filtro, rpp=40):
    """filtro = valor del mapa del municipio («CIUDAD REAL»). Devuelve TODOS los anuncios
    normativos del municipio (índice + días posteriores); el motor ranquea y verifica."""
    idx = _indice()
    m = _CVE.search(texto or "")
    if m:
        cve = f"BOP-CR-{m.group(1)}-{int(m.group(2))}"
        r = idx["_por_cve"].get(cve)
        if r:
            return [dict(r)]
        return [dict(r) for r in _recientes() if r["cve"] == cve]
    if not filtro:
        return []
    on = B._norm(filtro)
    out, vistos = [], set()
    # dedup por doc (no por CVE: en 2013 el sumario repitió una docena de números de anuncio)
    for r in list(idx.get(on) or []) + [r for r in _recientes() if B._norm(r["organo"]) == on]:
        if r["doc"] in vistos:
            continue
        vistos.add(r["doc"])
        out.append(dict(r))
    raw, core, _soft = B._familias(texto or "")
    fam = {w for w in (set(raw) | core) if w not in B._GENERICO}
    for r in out:
        tm = B._mnorm(r["titulo"])
        r["materia"] = bool(fam) and any(B._hit(w, tm) for w in fam)
    out.sort(key=lambda r: r["orden"], reverse=True)
    return out


_RUIDO = re.compile(r"(?m)^[ \t]*(?:S e d e\s+e l e c t r[^\n]*|B O P[ \t]*|Ciudad Real[ \t]*|"
                    r"Documento firmado electr[oó]nicamente[^\n]*|reflejado al margen[^\n]*|"
                    r"N[úu]mero \d+ ·[^\n]*|Firmado por [^\n]*|El documento consta de [^\n]*|"
                    r"administraci[oó]n local[ \t]*)\n")
_PALABRAS = re.compile(r"\b(?:de|la|el|los|las|que|del|por|con|para)\b")


def _limpiar(t):
    t = _RUIDO.sub("", t)
    t = re.sub(r"(\w) ?-\n(?=[a-záéíóúñü])", r"\1", t)     # «Calatra -\nva» -> «Calatrava»
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def texto(prov, m):
    """(texto, via) del anuncio: PDF con capa de texto del SIGEM (sin OCR), con caché en disco."""
    doc = (m.get("doc") if isinstance(m, dict) else "") or ""
    if not doc:
        mm = re.search(r"doc=(\d+)", (m.get("url") if isinstance(m, dict) else m) or "")
        doc = mm.group(1) if mm else ""
    if not doc:
        return "", "sin-pdf"
    clave = f"ciudadreal-{doc}"
    t = B._txt_cache_get(clave)
    if t:
        return t, "pdf-cache"
    try:
        data = _get(PDF_URL.format(doc=doc), timeout=25)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{str(e)[:60]}"
    t, via = B._pdf_bytes_texto(data, ocr=False)
    if via == "sin-pdf":
        return "", "sin-pdf"
    t = _limpiar(t or "")
    # «cifrado» = la heurística del motor no ve español; un anuncio corto también cae ahí
    if via == "cifrado" and len(_PALABRAS.findall(t[:20000])) < 8:
        return "", "sin-texto"
    if len(t) < 120:
        return "", "sin-texto"
    B._txt_cache_set(clave, t)
    return t, "pdf"
