# -*- coding: utf-8 -*-
"""Backend ALBACETE (familia «albacete») del motor de ordenanzas municipales.

BOP de Albacete = SEDIPUALB@ (bop.dipualba.es), API AJAX pública sin captcha ni
cookies. Receta verificada en vivo el 27-jul-2026 y recomprobada al implementar este
backend (2-sep-2026).

LO QUE OFRECE EL PORTAL (todo GET, JSON {codigoError, descripcionError, yedata}):
  * /servicesajax/busquedaavanzadabop?a=<texto>&b=0&c=<dd/mm/aaaa>&d=<dd/mm/aaaa>
      Búsqueda FULL-TEXT sobre el texto de cada PÁGINA del boletín (AND de palabras,
      con lematización agresiva: «terrazas» casa «terrazo»/«terraza»). Devuelve
      páginas sueltas (fecha, número, página, `pid`), SIN título ni municipio, con un
      tope de 100 ordenadas por fecha DESC y paginación e=<cursor>&f=a|s. Con b=1
      busca por TITULARES = ENTIDADES (lista de boletines en que publica «Hellín»),
      no por títulos de anuncio.
  * /servicesajax/busquedapornumbop?a=<num>&b=<año>  (~0,1 s)
      SUMARIO del boletín: sección → entidad («Ayuntamientos» → «Hellín») → anuncios
      con TÍTULO, página y `pid` (<a href=".../descargararchivopaginaBOP/<pid>">).
  * /servicesajax/obtenerdiaspublicacion?a=<aaaa-mm-01>
      Calendario del mes (base64 JSON): número y fecha de cada boletín.
  * /servicesajax/descararchivopaginabop/<pid>  (0,3-0,5 s)
      PDF del ANUNCIO ENTERO que empieza en esa página (no solo la página), con
      capa de texto desde 2010 al menos: fitz lo lee, CERO OCR.

POR QUÉ UN ÍNDICE EMPAQUETADO: la búsqueda full-text NO sirve para localizar la
ordenanza de un municipio: «Hellín ordenanza terrazas» devuelve 100 páginas de bases
de oposiciones (temario: «Tema 10: Ordenanza de terrazas»), padrones y edictos que
citan las tres palabras, y la ordenanza real queda fuera del tope. En cambio el
SUMARIO trae entidad y título. Así que `_gen_bop_albacete_indice.py` recorre el
calendario y los sumarios desde 2010 y empaqueta en `ordenanzas_data/albacete_indice.json`
los anuncios de ayuntamientos (y mancomunidades/consorcios) cuyo título parece
normativa. Buscar = filtro LOCAL por entidad + término (0 red); los boletines
posteriores al último indexado se completan EN VIVO por número (memoria 10 min), así
que el índice no caduca. Si el índice no existe, queda un camino de respaldo en
vivo (full-text → sumarios de esos boletines → filtro por entidad), más lento y de
menos recall.

TRAMPAS:
  1. Un número de boletín inexistente NO da error: devuelve un sumario vacío (así se
     detecta el final de la numeración del año).
  2. El endpoint devuelve 500 esporádicos (6 de 60 llamadas en la sonda): reintentar
     con espera.
  3. En los sumarios el municipio es un nivel anidado (<ul>) bajo «Ayuntamientos»;
     Diputación, Junta, juzgados… van en otros niveles. Hay que llevar la pila de
     entidades por profundidad de <ul>, no quedarse con el último <span class="fw-bold">.
  4. El PDF lleva la cabecera de página («Miércoles, 15 de julio de 2026 / Página 42 /
     Número 81») y, en los antiguos, el pie de la administración del BOP en cada
     página: se limpian para que no ensucien los pasajes.
  5. «Fuenteálamo» en el sumario es «Fuente-Álamo» en el mapa (INE): se compara con
     `B._norm` (sin acentos ni guiones).

Referencia interna (cve): «BOP-AB-<año>-<pid>». Con ella el motor relee el anuncio
al instante; el enlace oficial es .../servicesajax/descararchivopaginabop/<pid>.
"""
import concurrent.futures as _cf
import datetime as _dt
import html as _html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import bop_engine as B

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDICE = os.path.join(_HERE, "ordenanzas_data", "albacete_indice.json")

_TOK = re.compile(r'(?s)(<ul[^>]*>|</ul>|<p class="h5">(.*?)</p>|<span class="fw-bold">(.*?)</span>|'
                  r'<div class="col-12">(.*?)</div>\s*<div class="col-12 text-end">\s*'
                  r'<a[^>]*descargararchivopaginaBOP/(\d+)"[^>]*title="Descargar p[áa]gina (\d+)")')
_H1 = re.compile(r"<h1>\s*Bolet[íi]n\s+N[úu]mero\s+(\d+)\s*\((\d{2}/\d{2}/\d{4})\)")
_HIT = re.compile(r'(?s)<div class="row mb-3"(.*?)(?=<div class="row mb-3"|<div class="row m-0 p-0">|\Z)')
_CVE = re.compile(r"(?i)\bBOP-AB-(\d{4})-(\d+)\b")
_SUPRA_ENT = re.compile(r"(?i)^(?:mancomunidad|consorcio)")
# Cómo titulan aquí lo que el tesauro del motor no cubre: en este BOP la venta
# ambulante es «actividades mercantiles fuera de (un) establecimiento (comercial)
# permanente» (Hellín, 2010-2026) y nunca dice «ambulante» ni «mercadillo».
_EXTRA_TITULO = {
    "ambulante": re.compile(r"(?i)fuera de (?:un )?establecimiento|no sedentari"),
}
# compilaciones que pueden contener la ordenanza pedida sin nombrarla en el título
_COMPILACION = re.compile(r"(?i)\b(?:diversas|varias|distintas)\s+ordenanzas|ordenanzas\s+fiscales\b(?!\s+(?:n[úu]mero|n[º°]|reguladora))")
# cabeceras y pies de página del PDF (se repiten en cada página)
_PIE = re.compile(r"(?im)^[ \t]*(?:Administraci[óo]n B\.O\.P\.:.*|Tfno:.*|P[áa]gina\s+\d+|N[úu]mero\s+\d+|"
                  r"(?:Lunes|Martes|Mi[ée]rcoles|Jueves|Viernes|S[áa]bado|Domingo),?\s+\d{1,2}\s+de\s+\w+,?\s+(?:de\s+)?\d{4}\s*)$\n?")

_ANUNCIO = "/servicesajax/descararchivopaginabop/{pid}"
_MAX_TOPUP = 80          # boletines nuevos como máximo por completado en vivo


def _txt(x):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


def _get(url, timeout=20, intentos=3):
    """GET con User-Agent y reintento (500/502/503 esporádicos, cortes de red)."""
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": B._UA, "Accept": "*/*",
                                                       "Accept-Language": "es-ES,es"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code not in (500, 502, 503, 504) or i + 1 >= intentos:
                raise
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if i + 1 >= intentos:
                raise
        time.sleep(0.6 * (i + 1))
    raise ultimo


def _ajax_json(base, ep, params, timeout=20):
    raw = _get(base + "/servicesajax/" + ep + "?" + urllib.parse.urlencode(params), timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {}


def _entidad_de(cadena):
    """Municipio (o mancomunidad/consorcio) de la cadena de entidades del sumario."""
    if not cadena:
        return ""
    if B._norm(cadena[0]) == "ayuntamientos":
        return cadena[1] if len(cadena) > 1 else ""
    if _SUPRA_ENT.match(cadena[0]):
        return cadena[0]
    return ""


def _parse_sumario(h):
    """[(pag, pid, titulo, cadena)] del HTML del sumario; ver trampa 3."""
    depth, pila, items = 0, {}, []
    for m in _TOK.finditer(h):
        t = m.group(1)
        if t.startswith("<ul"):
            depth += 1
        elif t.startswith("</ul"):
            depth -= 1
            pila = {d: v for d, v in pila.items() if d <= depth}
        elif m.group(2) is not None:          # nueva sección
            pila = {}
        elif m.group(3) is not None:          # entidad a esta profundidad
            pila = {d: v for d, v in pila.items() if d < depth}
            pila[depth] = _txt(m.group(3))
        else:
            items.append({"pag": int(m.group(6)), "pid": m.group(5), "titulo": _txt(m.group(4)),
                          "cadena": [pila[d] for d in sorted(pila)]})
    items.sort(key=lambda x: x["pag"])
    mh = _H1.search(h)
    fecha = mh.group(2) if mh else ""
    for it in items:
        it["fecha"] = fecha
    return items


_SUM = {}                # (anyo, num) -> items (memoria del proceso)
_SUM_LOCK = threading.Lock()


def _sumario_vivo(base, num, anyo, timeout=20):
    k = (int(anyo), int(num))
    with _SUM_LOCK:
        if k in _SUM:
            return _SUM[k]
    j = _ajax_json(base, "busquedapornumbop", {"a": str(num), "b": str(anyo)}, timeout=timeout)
    items = _parse_sumario(j.get("yedata") or "")
    with _SUM_LOCK:
        if len(_SUM) > 600:
            _SUM.clear()
        _SUM[k] = items
    return items


def _item(base, anyo, num, fecha, pid, pag, ent, tit):
    d, mo, y = (fecha.split("/") + ["", "", ""])[:3] if fecha else ("", "", str(anyo))
    return {"url": base + _ANUNCIO.format(pid=pid), "titulo": tit, "cve": f"BOP-AB-{y or anyo}-{pid}",
            "fecha": fecha, "orden": f"{y}{mo}{d}" if (y and mo and d) else f"{anyo}0000",
            "pid": str(pid), "pag": int(pag or 0), "num": int(num or 0), "anyo": int(anyo or 0),
            "entidad": ent}


def _es_normativa(titulo):
    return bool(B._es_ordenanza(titulo) or B._NORMA_AMPLIA.search(titulo))


# ---- índice empaquetado + completado en vivo -------------------------------
_IDX = {"ok": None, "items": [], "ultimo": (0, 0), "generado": ""}
_IDX_LOCK = threading.Lock()


def _indice(base):
    if _IDX["ok"] is not None:
        return _IDX
    with _IDX_LOCK:
        if _IDX["ok"] is not None:
            return _IDX
        try:
            d = json.load(open(_INDICE, encoding="utf-8"))
            meta = d.get("meta") or {}
            items = [_item(base, *r) for r in d.get("items") or []]
            u = meta.get("ultimo") or {}
            _IDX.update({"ok": True, "items": items, "generado": meta.get("generado", ""),
                         "ultimo": (int(u.get("anyo") or 0), int(u.get("num") or 0))})
        except Exception:  # noqa: BLE001
            _IDX["ok"] = False
    return _IDX


_TOPUP = {"ts": 0.0, "items": [], "ultimo": (0, 0)}
_TOPUP_LOCK = threading.Lock()


def _recientes(base, ultimo):
    """Anuncios de los boletines POSTERIORES al último indexado, por número, hasta
    encontrar 4 números seguidos sin sumario (memoria 10 min)."""
    with _TOPUP_LOCK:
        if _TOPUP["items"] is not None and time.time() - _TOPUP["ts"] < 600 and _TOPUP["ultimo"] == ultimo:
            return _TOPUP["items"]
    anyo, num = ultimo
    hoy = _dt.date.today().year
    out, hechos = [], 0
    if anyo and num:
        pendientes = [(anyo, n) for n in range(num + 1, num + 1 + _MAX_TOPUP)]
        if hoy > anyo:
            pendientes += [(a, n) for a in range(anyo + 1, hoy + 1) for n in range(1, 1 + _MAX_TOPUP)]
        i = 0
        while i < len(pendientes) and hechos < _MAX_TOPUP:
            tanda = pendientes[i:i + 4]
            i += 4
            with _cf.ThreadPoolExecutor(max_workers=4) as ex:
                res = list(ex.map(lambda k: _seguro(lambda: _sumario_vivo(base, k[1], k[0]), []), tanda))
            hechos += len(tanda)
            vacios = 0
            for (a, n), its in zip(tanda, res):
                if not its:
                    vacios += 1
                    continue
                for it in its:
                    ent = _entidad_de(it["cadena"])
                    if ent and _es_normativa(it["titulo"]):
                        out.append(_item(base, a, n, it.get("fecha", ""), it["pid"], it["pag"], ent, it["titulo"]))
            if vacios == len(tanda):
                # fin de la numeración de este año: saltar al siguiente año pendiente
                resto = [k for k in pendientes[i:] if k[0] != tanda[0][0]]
                pendientes, i = resto, 0
    with _TOPUP_LOCK:
        _TOPUP.update({"ts": time.time(), "items": out, "ultimo": ultimo})
    return out


def _seguro(fn, defecto):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return defecto


def _todos(base):
    idx = _indice(base)
    if not idx["ok"]:
        return None
    return idx["items"] + _recientes(base, idx["ultimo"])


# ---- respaldo en vivo (sin índice) -----------------------------------------
def _hits_fulltext(base, q, c="", d=""):
    j = _ajax_json(base, "busquedaavanzadabop", {"a": q, "b": "0", "c": c, "d": d}, timeout=25)
    out = []
    for b in _HIT.findall(j.get("yedata") or ""):
        cab = re.search(r"Bolet[íi]n \w+ (\d+) de (\d{2})/(\d{2})/(\d{4})", b)
        pag = re.search(r"de la p[áa]gina (\d+)", b)
        if cab and pag:
            out.append({"num": int(cab.group(1)), "anyo": int(cab.group(4)),
                        "fecha": f"{cab.group(2)}/{cab.group(3)}/{cab.group(4)}", "pag": int(pag.group(1))})
    return out


def _buscar_vivo(base, filtro, terminos):
    """Full-text (municipio + ordenanza + término) → boletines → sumarios → anuncios
    de ese municipio. Recall limitado por el tope de 100 páginas del buscador."""
    q = " ".join([filtro, "ordenanza"] + list(terminos)[:2]).strip()
    try:
        hits = _hits_fulltext(base, q)
    except Exception:  # noqa: BLE001
        return []
    bols = []
    for x in hits:
        if (x["anyo"], x["num"]) not in bols:
            bols.append((x["anyo"], x["num"]))
    bols = bols[:40]
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        sums = dict(zip(bols, ex.map(lambda k: _seguro(lambda: _sumario_vivo(base, k[1], k[0]), []), bols)))
    out, vistos, nf = [], set(), B._norm(filtro)
    for x in hits:
        its = sums.get((x["anyo"], x["num"]), [])
        cand = [i for i in its if i["pag"] <= x["pag"]]
        if not cand:
            continue
        it = cand[-1]
        if it["pid"] in vistos:
            continue
        vistos.add(it["pid"])
        ent = _entidad_de(it["cadena"])
        if B._norm(ent) != nf or not _es_normativa(it["titulo"]):
            continue
        out.append(_item(base, x["anyo"], x["num"], x["fecha"], it["pid"], it["pag"], ent, it["titulo"]))
    return out


# ---- contrato del backend ---------------------------------------------------
def _terminos(texto):
    """Términos (mnorm) que deben aparecer en el TÍTULO: los del abogado + el
    tesauro del motor (así «IBI» encuentra «bienes inmuebles»)."""
    raw, core, _soft = B._familias(texto)
    return [w for w in list(raw) + sorted(core) if len(w) >= 3]


def _por_pid(base, pid, todos):
    for it in todos or []:
        if it["pid"] == str(pid):
            return [dict(it)]
    try:                                   # no está en el índice: se lee la cabecera del PDF
        b = _get(base + _ANUNCIO.format(pid=pid), timeout=25)
        if b[:5] != b"%PDF-":
            return []
        t, _via = B._pdf_bytes_texto(b, ocr=False)
    except Exception:  # noqa: BLE001
        return []
    lineas = [ln.strip() for ln in t.splitlines() if ln.strip()]
    fe = re.search(r"(\d{1,2}) de (\w+),? (?:de )?(\d{4})", " ".join(lineas[:3]))
    num = re.search(r"N[úu]mero\s+(\d+)", " ".join(lineas[:6]))
    ent = next((re.sub(r"(?i)^ayuntamiento de\s+", "", ln).title() for ln in lineas[:8]
                if re.match(r"(?i)ayuntamiento de ", ln)), "")
    cuerpo = [ln for ln in lineas[:14] if not _PIE.match(ln + "\n") and not re.match(r"(?i)secci[óo]n|ayuntamiento de|anuncio$|edicto$", ln)]
    tit = " ".join(cuerpo)[:160] or f"Anuncio {pid}"
    meses = {"enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05", "junio": "06", "julio": "07",
             "agosto": "08", "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"}
    fecha = f"{int(fe.group(1)):02d}/{meses.get(fe.group(2).lower(), '00')}/{fe.group(3)}" if fe else ""
    anyo = int(fe.group(3)) if fe else 0
    it = _item(base, anyo, num.group(1) if num else 0, fecha, pid, 0, ent, tit)
    it["text"] = _limpia_pdf(t) if len(t) > 300 else ""
    return [it]


def buscar(prov, texto, filtro, rpp=40):
    """Anuncios normativos del ayuntamiento `filtro` (nombre del municipio, valor del
    mapa) cuyo título lleva `texto` (o su expansión del tesauro). Sin filtro se buscan
    mancomunidades/consorcios (normas supramunicipales)."""
    cfg = B.PROVINCIAS[prov]
    base = cfg["base"]
    texto = (texto or "").strip()
    todos = _todos(base)
    m = _CVE.search(texto) or re.search(r"(?i)\bpid[ :]*(\d{5,})\b", texto)
    if m:
        return _por_pid(base, m.group(m.lastindex), todos)
    terminos = _terminos(texto)
    if todos is None:                       # sin índice: respaldo en vivo
        return _buscar_vivo(base, filtro, terminos) if filtro else []
    if filtro:
        nf = B._norm(filtro)
        pool = [it for it in todos if B._norm(it["entidad"]) == nf]
    else:
        pool = [it for it in todos if _SUPRA_ENT.match(it["entidad"] or "")]
    pool.sort(key=lambda r: r["orden"], reverse=True)
    tope = max(int(rpp or 40), 40)
    if not terminos:                        # volcado genérico («ordenanza», «reglamento»)
        gen = B._norm(texto)
        if gen and gen not in ("ordenanza", "ordenanzas", "ordenanzamunicipal"):
            pool = [it for it in pool if B._hit(B._mnorm(texto), B._mnorm(it["titulo"]))] or pool
        return [dict(it, materia=False) for it in pool[:tope]]
    extras = [rx for w, rx in _EXTRA_TITULO.items() if any(w in t for t in terminos)]
    hits = [dict(it, materia=True) for it in pool
            if any(B._hit(w, B._mnorm(it["titulo"])) for w in terminos)
            or any(rx.search(it["titulo"]) for rx in extras)]
    if not hits:
        # ningún título dice la materia: solo quedan las COMPILACIONES («modificación
        # de diversas ordenanzas fiscales») y los títulos genéricos, que el motor
        # verifica por contenido (verifica_texto). Nada más: listar aquí ordenanzas
        # de otras materias haría creer que tratan de la pedida.
        gen = B._titulos_genericos(pool)
        comp = [it for it in pool if _COMPILACION.search(it["titulo"]) and B._es_ordenanza(it["titulo"])]
        vistos, out = set(), []
        for it in sorted(gen + comp, key=lambda r: r["orden"], reverse=True):
            if it["pid"] not in vistos:
                vistos.add(it["pid"])
                out.append(dict(it, materia=False))
        return out[:6]
    # títulos genéricos recientes de respaldo (el motor los verifica por contenido
    # solo si ninguno de los anteriores convence)
    vistos = {it["pid"] for it in hits}
    extra = [dict(it, materia=False) for it in pool if it["pid"] not in vistos
             and B._es_ordenanza(it["titulo"])][:8]
    return (hits + extra)[:tope + 8]


def _limpia_pdf(t):
    t = _PIE.sub("", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def texto(prov, m):
    """(texto, via) del anuncio: PDF del anuncio entero con capa de texto."""
    cfg = B.PROVINCIAS[prov]
    if not isinstance(m, dict):
        return "", "sin-texto"
    if m.get("text"):
        return m["text"], "pdf"
    pid = m.get("pid") or (re.search(r"/(\d+)\s*$", m.get("url") or "") or [None, ""])[1]
    if not pid:
        return "", "sin-id"
    try:
        b = _get(cfg["base"] + _ANUNCIO.format(pid=pid), timeout=25)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{type(e).__name__}"
    if b[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = B._pdf_bytes_texto(b, ocr=False)
    t = _limpia_pdf(t or "")
    if len(t) < 300:
        return "", ("cifrado" if via == "cifrado" else "sin-texto")
    return t, "pdf"
