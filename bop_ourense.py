# -*- coding: utf-8 -*-
"""Backend OURENSE (familia «ourense») del motor de ordenanzas municipales.

El BOP de Ourense (Deputación) es una SPA Angular sobre una API JSON pública:
https://bop.depourense.es/portalapi/api. Sin captcha, sin cookies, sin sesión.
Receta verificada en vivo el 27-jul-2026 (sondas `_probe_ourense*.py`) y
recomprobada al implementar este backend (2-sep-2026).

BÚSQUEDA (POST JSON, ~0,2 s por petición):
  POST {base}/edicto/busquedaDocumentos?page=0&size=N
  body {"texto": <consulta>, "tipo": "EDICTO", "idProcedente": "<id entidad>"}
  -> {content: [{idEdicto, edicto (título), fecha "AAAAMMDD", numeroBoletin,
                 seccion, idioma}], totalElements, ...}
  TRAMPAS: (1) idProcedente NO admite lista: el mapa guarda VARIOS ids por
  concello (Ourense capital publica por 17 departamentos) -> una petición por
  id (≤4 en paralelo) y unión; (2) cada resultado sale DOS veces (fila 'gl' y
  fila 'es') -> deduplicar por idEdicto; (3) el título viene SIEMPRE en gallego;
  (4) el índice es FULL-TEXT y BILINGÜE («basura» encuentra «lixo»), pero la
  consulta multipalabra es AND estricto y el orden NO es por relevancia -> se
  consulta palabra a palabra y se ranquea en local por el título;
  (5) tipo:'EDICTO' limita al índice con idEdicto (marzo-2024 en adelante);
  (6) la cabecera Content-Type: application/json es obligatoria (415 sin ella).

LECTURA (~0,2 s): GET {base}/edicto/descargar/html/{idEdicto}/es devuelve el
  edicto traducido al castellano. Si el HTML trae solo el encabezado (pasa con
  ordenanzas largas), el articulado está en el PDF del edicto
  ({base}/edicto/descargar/pdf/{idEdicto}/idioma/es), con capa de texto, sin OCR.

Referencia interna (cve): «BOP-OU-<año>-<idEdicto>»; con ella el motor relee el
edicto al instante (leer_ordenanza con el CVE).
"""
import concurrent.futures as _cf
import json
import re
import time
import urllib.request

import bop_engine as B

_WORKERS = 4                       # cortesía con el boletín: nada de ráfagas
_SIZE = 200                        # size sin tope práctico: una petición por id
_CVE = re.compile(r"(?i)\b(?:BOP-OU-\d{4}-|idEdicto\s*|edicto\s+)(\d{4,})\b")
_ART = re.compile(r"(?i)\bart(?:[íi]culo|igo)\s*\.?\s*\d+")
# Edictos que NUNCA son normativa pero pasan el filtro «ordenanza/taxa» del motor
# por su título («Edicto de cobranza do padrón do IBI… da taxa de…»). Se descartan
# salvo que el abogado pregunte precisamente por ellos.
_NO_NORMA = re.compile(r"(?i)padr[oó]n|cobranza|notificaci[oó]n|licitaci[oó]n|"
                       r"ad[xj]udicaci[oó]n|per[íi]odo voluntario|matr[ií]cula|"
                       r"listas? (?:provisional|definitiv)|nomeamento|nombramiento|"
                       r"contrataci[oó]n|bases (?:da|de la) convocatoria")
# Los patrones de rebaja del motor están en castellano («corrección de errores»,
# «derogación»); en gallego son «corrección de erros» y «derrogación» y no casan,
# así que una derogación ganaba a la ordenanza vigente. Estas filas se conservan
# (informan) pero sin la marca `materia` y al final: nunca ganan la verificación.
_REBAJA = re.compile(r"(?i)correcci[oó]n de err|derr?ogaci[oó]n|anulaci[oó]n|"
                     r"desistimiento|non aprobaci[oó]n")


def _ep(cfg, clave, **kw):
    return cfg["base"] + cfg["endpoints"][clave].format(**kw)


def _http(url, data=None, timeout=20, intentos=2):
    """GET/POST JSON con un reintento con espera. TLS sin verificar: las CA del
    sector público español no siempre están en certifi (igual que TEAC/REGCON)."""
    h = {"User-Agent": B._UA, "Accept": "application/json, text/html, */*"}
    body = None
    if data is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            return urllib.request.urlopen(req, timeout=timeout, context=B._SSL_NOVERIFY).read()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if i + 1 < intentos:
                time.sleep(0.8)
    raise ultimo


def _fila(cfg, x, materia=True):
    eid = x.get("idEdicto")
    if not eid:
        return None
    f8 = str(x.get("fecha") or "")
    fecha = f"{f8[6:8]}/{f8[4:6]}/{f8[:4]}" if len(f8) == 8 else ""
    tit = re.sub(r"\s+", " ", (x.get("edicto") or "").strip())
    return {"url": _ep(cfg, "pdf", idEdicto=eid, idioma="es"),
            "titulo": tit, "cve": f"BOP-OU-{f8[:4] or '0000'}-{eid}",
            "fecha": fecha, "orden": f8 if len(f8) == 8 else "0",
            "id": str(eid), "num": x.get("numeroBoletin") or "", "materia": materia}


def _es_normativa(titulo, consulta):
    """Descarta padrones, cobranzas y exposiciones de tasas (que no nombran ninguna
    ordenanza) salvo que el abogado pregunte por ellos."""
    if _NO_NORMA.search(consulta or ""):
        return True
    if _NO_NORMA.search(titulo):
        return False
    if re.search(r"(?i)exposici[oó]n p[úu]blica|per[íi]odo de cobro", titulo) \
            and not re.search(r"(?i)ordenanza|regulamento|reglamento", titulo):
        return False
    return True


def _consulta(cfg, ids, q, materia=True):
    """Una consulta en TODOS los ids del concello (≤4 en paralelo), unida y
    deduplicada por idEdicto."""
    def una(idp):
        try:
            j = json.loads(_http(_ep(cfg, "buscar", page=0, size=_SIZE),
                                 {"texto": q, "tipo": "EDICTO", "idProcedente": str(idp)})
                           .decode("utf-8", "replace"))
            return j.get("content") or []
        except Exception:  # noqa: BLE001
            return []
    filas = []
    if len(ids) == 1:
        filas = una(ids[0])
    else:
        with _cf.ThreadPoolExecutor(max_workers=min(_WORKERS, len(ids))) as ex:
            for out in ex.map(una, ids):
                filas.extend(out)
    vistos = {}
    for x in filas:
        r = _fila(cfg, x, materia)
        if r and r["id"] not in vistos:
            vistos[r["id"]] = r
    return vistos


def _etapas(texto):
    """Escalera de consultas (cada etapa solo si la anterior no dio nada):
    palabras distintivas en castellano -> variantes singular/plural -> formas
    gallegas del tesauro -> términos del tesauro de materias. Una consulta
    genérica («ordenanza», «tasa») o una sigla corta («IBI») va tal cual."""
    pal = [w for w in re.split(r"\W+", texto or "") if w]
    stop = {B._norm(x) for x in B._STOPM}
    utiles = [w for w in pal if B._norm(w) not in stop and len(w) >= 4]
    # las dos palabras más largas (más distintivas); sin ellas, la consulta tal cual
    a = sorted(utiles, key=len, reverse=True)[:2] if utiles else [texto.strip() or "ordenanza"]
    base = a if utiles else []
    b = []
    for w in base:
        v = w[:-1] if w.lower().endswith("s") else w + "s"
        if B._norm(v) not in {B._norm(x) for x in a}:
            b.append(v)
    c = []
    for w in base:
        g = B._GALEGO.get(w.lower())
        if g and B._norm(g) not in {B._norm(x) for x in a + b}:
            c.append(g)
    # términos del tesauro («plusvalía» -> «incremento de valor»): el índice es
    # full-text, así que la conjunción de esas palabras localiza la ordenanza aunque
    # el abogado use un nombre que no aparece literal en el texto
    raw, core, _soft = B._familias(texto)
    vistos = {B._norm(x) for x in a + b + c} | {B._norm(x) for x in raw}
    d = [t for t in sorted(core, key=len, reverse=True)
         if len(t) >= 4 and B._norm(t) not in vistos and t not in B._GENERICO][:2]
    return [e for e in (a, b, c, d) if e]


def _ordenar(cfg, res, texto):
    """El boletín no ordena por relevancia: se ranquea en local por el TÍTULO
    (que va en gallego, así que los términos se expanden a esa lengua)."""
    raw, core, soft = B._familias(texto)
    fuerte = B._expandir_idioma(set(raw) | core, cfg.get("idioma"))
    debil = B._expandir_idioma(soft, cfg.get("idioma"))

    def puntos(r):
        tm = B._mnorm(r["titulo"])
        s = 5.0 * sum(1 for w in fuerte if B._hit(w, tm)) + sum(1 for w in debil if B._hit(w, tm))
        if re.search(r"definitiv", r["titulo"], re.I):
            s += 2
        if B._es_ordenanza(r["titulo"]):
            s += 1
        return s
    return sorted(res, key=lambda r: (bool(r.get("materia")), puntos(r), r.get("orden") or "0"),
                  reverse=True)


def _por_id(cfg, ids, eid):
    """Un edicto concreto (referencia BOP-OU-<año>-<id>): título en castellano
    desde su HTML; fecha y número desde el índice si el edicto es del concello."""
    try:
        h = _http(_ep(cfg, "html", idEdicto=eid, idioma="es")).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    tit = re.search(r'(?s)class="bop-texto-sumario">(.*?)</span>', h)
    org = re.search(r'(?s)class="bop-texto-organismo">(.*?)</span>', h)
    if not tit:
        return []
    titulo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tit.group(1))).strip()
    fila = {"url": _ep(cfg, "pdf", idEdicto=eid, idioma="es"), "titulo": titulo,
            "cve": f"BOP-OU-0000-{eid}", "fecha": "", "orden": "0", "id": str(eid),
            "num": "", "materia": True,
            "organismo": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", org.group(1))).strip() if org else ""}
    if ids:
        pal = sorted([w for w in re.split(r"\W+", titulo) if len(w) >= 5], key=len, reverse=True)[:2]
        if pal:
            enc = _consulta(cfg, ids, pal[0]).get(str(eid))
            if enc:
                fila.update({k: enc[k] for k in ("cve", "fecha", "orden", "num")})
                fila["titulo"] = enc["titulo"] or titulo
    return [fila]


def buscar(prov, texto, filtro, rpp=40):
    """Edictos del concello (ids del mapa, separados por comas) que mencionan
    `texto` (índice full-text y bilingüe). Sin filtro no se consulta la provincia
    entera (devuelve [])."""
    cfg = B.PROVINCIAS[prov]
    if not filtro:
        return []
    ids = [i.strip() for i in str(filtro).split(",") if i.strip()]
    texto = (texto or "").strip()
    m = _CVE.search(texto)
    if m:
        return _por_id(cfg, ids, m.group(1))
    vistos = {}
    for etapa in _etapas(texto):
        for q in etapa:
            for k, r in _consulta(cfg, ids, q).items():
                if _es_normativa(r["titulo"], texto):
                    vistos.setdefault(k, r)
        if vistos:          # («IBI» solo casa padrones: se pasa a «bienes inmuebles»)
            break
    if not _REBAJA.search(texto):
        for r in vistos.values():
            if _REBAJA.search(r["titulo"]):
                r["materia"] = False
    return _ordenar(cfg, list(vistos.values()), texto)


def texto(prov, m):
    """(texto, via): HTML del edicto en castellano (via 'html'); si viene solo con
    el encabezado, el articulado está en el PDF del edicto (via 'pdf')."""
    cfg = B.PROVINCIAS[prov]
    if not isinstance(m, dict):
        return "", "sin-texto"
    eid = m.get("id") or ""
    if not eid:
        mm = _CVE.search(m.get("cve") or "")
        eid = mm.group(1) if mm else ""
    if not eid:
        return "", "sin-texto"
    t_html = ""
    try:
        h = _http(_ep(cfg, "html", idEdicto=eid, idioma="es")).decode("utf-8", "replace")
        t_html = _normaliza(B._html_a_texto(h))
    except Exception:  # noqa: BLE001
        t_html = ""
    if len(t_html) >= 3000 or (len(t_html) > 200 and _ART.search(t_html)):
        return t_html, "html"
    # HTML casi vacío (solo el encabezado): el texto íntegro está en el PDF
    try:
        pdf = _http(_ep(cfg, "pdf", idEdicto=eid, idioma="es"), timeout=25)
        t_pdf, via = B._pdf_bytes_texto(pdf, ocr=False)
        t_pdf = _normaliza(t_pdf or "")
        if len(t_pdf) > len(t_html):
            return t_pdf, "pdf"
    except Exception:  # noqa: BLE001
        pass
    return (t_html, "html") if t_html else ("", "sin-texto")


def _normaliza(t):
    """El HTML viene con CRLF y algunos concellos abrevian «Art. 1. Objeto»: sin
    esto el troceado por artículos del motor no encuentra ninguno."""
    t = t.replace("\r", "")
    t = re.sub(r"(?im)^[ \t]*Art\.\s*(\d+)", r"Artículo \1", t)
    return re.sub(r"[ \t]+", " ", t).strip()
