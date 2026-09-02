# -*- coding: utf-8 -*-
"""Backend LLEIDA (familia «lleida») del motor de ordenanzas municipales.

eBOP de la Diputació de Lleida (ebop.diputaciolleida.cat/bop/cerca): JSF 2.x
Mojarra sobre Tomcat, el mismo producto eBOP que Girona (familia interna `girona`
de bop_engine.py, de la que este backend es una copia adaptada). Receta sondeada y
verificada en vivo el 27-jul-2026 (`_probe_lleida1..20.py`) y recomprobada al
implementar este backend (2-sep-2026).

BÚSQUEDA (GET /bop/cerca para sesión + ViewState, POST del form `formCercaContingut`;
1,7-4 s por consulta):
  * campos: titol (SUBSTRING sin acentos ni mayúsculas: «gual» casa «igualtat»),
    entitat (AND de tokens, SÍ distingue acentos: el valor del mapa lleva los acentos
    catalanes exactos y NO se normaliza), exerciciDesde/Fins, bopDesde/Fins…
  * resultados: div.resultat-cerca + dl/dt/dd (Entitat, Títol, Exercici, Bop, Data…),
    25 por página en orden de fecha DESC; paginación por postback JSF (form j_idt15,
    botón title="Pàgina següent") reutilizando sesión y ViewState.
  * el boletín está SOLO EN CATALÁN: castellano da 0 en casi todo el corpus →
    `B._consultas_materia(texto, "ca")` con la forma catalana PRIMERO.

CUATRO DIFERENCIAS con Girona (cada una, sola, deja el backend en 0 resultados):
  1. ViewState = `javax.faces.ViewState` (JSF 2.x), no `jakarta.faces.ViewState`.
  2. Los anuncios anteriores a mediados de 2023 traen el href del PDF RELATIVO y con
     otro nombre (/aplicacions/bop/bopV1/fitxers/pdf/<any>/<bop>/BOP-<id>.pdf); los
     nuevos son absolutos (…/<any><bop><edicte>.pdf). Exigir https:// deja fuera el
     72 % del histórico.
  3. La entidad del resultado llega como «AJUNTAMENT DE X (MUNICIPI)», «AJUNTAMENT DE
     X- DEPARTAMENT», «AJUNTAMENT DE X 331» o «PAERIA DE CERVERA. SERVEIS TÈCNICS»:
     se recorta el paréntesis, el departamento y los dígitos finales y se compara por
     IGUALDAD normalizada (un prefijo con separador cuela «AJUNTAMENT DE LES» en
     «AJUNTAMENT DE LES BORGES BLANQUES»).
  4. No hacen falta ventanas de ejercicio: la consulta sin acotar tarda 1,7-4 s.

LECTURA: PDF directo, sin sesión ni cookies; 12/12 PDFs probados (2020-2026, formato
viejo y nuevo) con capa de texto → sin OCR.

Referencia interna (cve): «BOP-LL-<año>-<nº de BOP en 3 dígitos><id del PDF>», con
<id> = dígitos del nombre del PDF (tras el año en el formato nuevo; el número tras
«BOP-» en el viejo). Se recuerda en memoria por si el motor relee por CVE; si la
memoria no lo tiene (otra instancia de Vercel), se relocaliza acotando ejercicio +
número de BOP + entidad (≤ 2 páginas) y casando el nombre del PDF.
"""
import concurrent.futures as _cf
import html as _html
import http.cookiejar
import re
import threading
import time
import urllib.parse
import urllib.request

import bop_engine as B

_BLOQUE = re.compile(r'<div class="resultat-cerca\s*"(.*?)</dl>', re.S)
_DT = re.compile(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", re.S)
_PDF = re.compile(r'<a href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.S)          # relativo O absoluto
_FORM = re.compile(r'(?s)<form id="formCercaContingut"[^>]*action="([^"]+)"(.*?)</form>')
_VS = re.compile(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"')
_SUBMIT1 = re.compile(r'name="(formCercaContingut:j_idt\d+)"[^>]*value="Cerca')
_SUBMIT2 = re.compile(r'type="submit"[^>]*name="(formCercaContingut:j_idt\d+)"')
_PAG_FORM = re.compile(r'(?s)<form id="(j_idt\d+)"[^>]*action="([^"]+)"')
_PAG_BTN = re.compile(r"title=\"P[àa]gina seg[üu]ent\"[^>]*onclick=\"mojarra\.jsfcljs\("
                      r"document\.getElementById\('([^']+)'\),\{'([^']+)':'([^']+)'\}")
_CVE = re.compile(r"(?i)\bBOP-LL-(\d{4})-(\d+)\b")
# «BALAGUER-Aprovació…», «LA SEU D'URGELL - Edicte…»: prefijo en MAYÚSCULAS + guion
_TIT_PREFIJO = re.compile(r"^([A-ZÀ-ÝÇ][A-ZÀ-ÝÇ'’\. ]{1,40}?)\s*[-–]\s*(.+)$")
_POR_PAGINA = 25
_MAX_PAGINAS = 3

_CVES = {}               # cve -> item (memoria del proceso; el motor relee por CVE)
_CVES_LOCK = threading.Lock()


def _txt(x):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


def _limpia_entidad(ent):
    """«AJUNTAMENT DE LLEIDA (LLEIDA)» / «…LLEIDA- URBANISME» / «…BALAGUER 331» /
    «PAERIA DE CERVERA. SERVEIS TÈCNICS» → «AJUNTAMENT DE LLEIDA» (ver diferencia 3)."""
    e = re.sub(r"\s*\([^)]*\)\s*$", "", ent or "")
    e = re.sub(r"\s*-\s+.*$|\s+-.*$", "", e)          # «X- DEPT», «X - DEPT» (no «BELL-LLOC»)
    e = re.sub(r"\.\s+.*$", "", e)                     # «PAERIA DE CERVERA. SERVEIS…»
    e = re.sub(r"[\s,:]+\d+\s*$", "", e)               # «BALAGUER 331»
    return e.strip()


def _casa(ent, clave):
    return B._norm(_limpia_entidad(ent)) == B._norm(clave)


def _id_pdf(url):
    """Dígitos identificativos del PDF: «…/2026/147/202614706716.pdf» → 14706716
    (sin el año); «…/2023/23/BOP-502424957.pdf» → 502424957."""
    nombre = url.rsplit("/", 1)[-1]
    m = re.match(r"(?i)^BOP-(\d+)\.pdf$", nombre)
    if m:
        return m.group(1)
    digitos = re.sub(r"\D", "", nombre)
    return digitos[4:] if len(digitos) > 8 else digitos


def _parse(base, raw, clave, q, materia):
    out = []
    for bloque in _BLOQUE.findall(raw):
        campos = {}
        for dt, dd in _DT.findall(bloque):
            campos[re.sub(r"[^a-z]", "", _txt(dt).lower())] = dd
        ent = _txt(campos.get("entitat", ""))
        mp = _PDF.search(campos.get("titol", ""))
        if not mp:
            continue
        tit = _txt(mp.group(2))
        # Fallo de datos del eBOP (anuncios de 2023): la entidad llega TRUNCADA
        # («AJUNTAMENT DE», «AJUNTAMENT DE VALLFOGONA DE») y el resto del nombre va
        # pegado al principio del título («BALAGUER-Aprovació definitiva Ordenança de
        # gestió de residus municipals»). Se recompone la entidad con ese prefijo en
        # mayúsculas y se limpia el título.
        mt = _TIT_PREFIJO.match(tit)
        if mt and (not clave or not _casa(ent, clave)):
            recompuesta = (ent + " " + mt.group(1)).strip()
            if not clave or _casa(recompuesta, clave):
                ent, tit = recompuesta, mt.group(2).strip()
        if clave and not _casa(ent, clave):
            continue
        u = _html.unescape(mp.group(1))
        if not u.startswith("http"):
            u = base + (u if u.startswith("/") else "/" + u)          # diferencia 2
        fecha = _txt(campos.get("data", ""))
        d2, m2, y2 = (fecha.split("/") + ["", "", ""])[:3]
        y2 = y2 or _txt(campos.get("exercici", ""))
        ident = _id_pdf(u)
        bop = re.sub(r"\D", "", _txt(campos.get("bop", "")))[:3]
        out.append({"url": u, "titulo": tit, "cve": f"BOP-LL-{y2 or '0000'}-{int(bop or 0):03d}{ident}",
                    "fecha": fecha, "orden": f"{y2}{m2}{d2}" if (y2 and m2 and d2) else f"{y2 or '0000'}0000",
                    "bop": _txt(campos.get("bop", "")), "entidad": _limpia_entidad(ent),
                    "id": ident, "materia": bool(materia)})
    return out


def _consulta(base, titol, entitat, y0="", y1="", paginas=1, timeout=22, bop=""):
    """Una búsqueda (sesión nueva) con hasta `paginas` páginas de resultados.
    Devuelve el HTML crudo de cada página (el parseo es de quien llama)."""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", B._UA), ("Accept-Language", "ca,es;q=0.9")]
    h = op.open(base + "/bop/cerca", timeout=timeout).read().decode("utf-8", "replace")
    fm = _FORM.search(h)
    if not fm:
        return []
    action, cuerpo = fm.group(1), fm.group(2)
    vs = _VS.findall(h)                                                  # diferencia 1
    sub = _SUBMIT1.search(cuerpo) or _SUBMIT2.search(cuerpo)
    if not (vs and sub):
        return []
    d = {"formCercaContingut": "formCercaContingut",
         "formCercaContingut:exerciciDesde": str(y0), "formCercaContingut:exerciciFins": str(y1),
         "formCercaContingut:edicteDesde": "", "formCercaContingut:edicteFins": "",
         "formCercaContingut:bopDesde": str(bop or ""), "formCercaContingut:bopFins": str(bop or ""),
         "formCercaContingut:dataDesde": "", "formCercaContingut:dataFins": "",
         "formCercaContingut:seccio": "", "formCercaContingut:titol": titol,
         "formCercaContingut:entitat": str(entitat or ""), "formCercaContingut:text": "",
         sub.group(1): "Cerca", "javax.faces.ViewState": vs[-1]}
    url = base + action if action.startswith("/") else action
    r = op.open(urllib.request.Request(
        url, data=urllib.parse.urlencode(d, encoding="utf-8").encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": base + "/bop/cerca"}),
        timeout=timeout)
    referer = r.geturl()
    raw = r.read().decode("utf-8", "replace")
    paginas_html = [raw]
    for _ in range(max(0, paginas - 1)):
        if len(_BLOQUE.findall(raw)) < _POR_PAGINA:
            break
        fm2, bt = _PAG_FORM.search(raw), _PAG_BTN.search(raw)
        if not (fm2 and bt):
            break
        vs2 = _VS.findall(raw)
        if not vs2:
            break
        formid, k, v = bt.group(1), bt.group(2), bt.group(3)
        d2 = {formid: formid, k: v, "javax.faces.ViewState": vs2[-1]}
        act2 = fm2.group(2)
        try:
            r = op.open(urllib.request.Request(
                base + act2 if act2.startswith("/") else act2,
                data=urllib.parse.urlencode(d2, encoding="utf-8").encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": referer}),
                timeout=timeout)
            raw = r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            break
        paginas_html.append(raw)
    return paginas_html


def _buscar_una(base, clave, q, materia, paginas=1):
    try:
        htmls = _consulta(base, q, clave, paginas=paginas)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for raw in htmls:
        out.extend(_parse(base, raw, clave, q, materia))
    return out


def _recordar(items):
    with _CVES_LOCK:
        if len(_CVES) > 2000:
            _CVES.clear()
        for it in items:
            _CVES.setdefault(it["cve"], dict(it))


def _por_cve(base, clave, anyo, digitos):
    """«BOP-LL-<año>-<bop 3 dígitos><id del PDF>»: memoria del proceso y, si no está
    (otra instancia), relocalización acotando ejercicio + número de BOP + entidad."""
    with _CVES_LOCK:
        it = _CVES.get(f"BOP-LL-{anyo}-{digitos}")
    if it:
        return [dict(it)]
    bop, ident = (int(digitos[:3] or 0), digitos[3:]) if len(digitos) > 3 else (0, digitos)
    try:
        htmls = _consulta(base, "", clave, y0=anyo, y1=anyo, bop=bop or "", paginas=2 if bop else _MAX_PAGINAS)
    except Exception:  # noqa: BLE001
        return []
    for raw in htmls:
        for it in _parse(base, raw, clave, "", False):
            if it["id"] == ident or it["id"] == digitos:
                _recordar([it])
                return [it]
    return []


def buscar(prov, texto, filtro, rpp=40):
    """Edictes del ayuntamiento `filtro` (valor del mapa, p.ej. «AJUNTAMENT DE TÀRREGA»
    o «PAERIA DE CERVERA») cuyo título contiene la materia (en catalán) + volcado
    genérico «ordenança» para que el motor verifique títulos genéricos por contenido.
    Sin filtro no se consulta el corpus entero (devuelve [])."""
    cfg = B.PROVINCIAS[prov]
    base = cfg["base"]
    if not filtro:
        return []
    texto = (texto or "").strip()
    m = _CVE.search(texto)
    if m:
        return _por_cve(base, filtro, m.group(1), m.group(2))
    # consultas de materia: forma catalana PRIMERO (el boletín está solo en catalán)
    locales = {B._norm(v) for v in B._CATALA.values()}
    qs = B._consultas_materia(texto, "ca")
    genericos = {B._norm(x) for x in ("ordenanza", "ordenança", "reglamento", "reglament", "tasa", "taxa")}
    materia = [q for q in qs if B._norm(q) not in genericos]
    materia = sorted(materia, key=lambda q: 0 if B._norm(q) in locales else 1)
    # dedup por forma normalizada, máximo 2 consultas de materia
    vistas, mq = set(), []
    for q in materia:
        if B._norm(q) not in vistas:
            vistas.add(B._norm(q))
            mq.append(q)
    mq = mq[:2]
    tareas = [(q, True, 2) for q in mq]
    # volcado genérico: siempre (títulos genéricos para verificar por contenido) salvo
    # que la propia consulta ya sea genérica («ordenanza», «tasa»)
    gen = B._CATALA.get(B._norm(texto), None) if B._norm(texto) in ("ordenanza", "tasa", "reglamento") else None
    if not mq:
        tareas.append((gen or "ordenança", False, 2))
    else:
        tareas.append(("ordenança", False, 1))
    vistos = {}
    with _cf.ThreadPoolExecutor(max_workers=min(3, len(tareas))) as ex:
        for rs in ex.map(lambda t: _buscar_una(base, filtro, t[0], t[1], t[2]), tareas):
            for r in rs:
                if r["cve"] in vistos:
                    vistos[r["cve"]]["materia"] = vistos[r["cve"]].get("materia") or r["materia"]
                else:
                    vistos[r["cve"]] = r
    out = list(vistos.values())
    out.sort(key=lambda r: (r.get("materia", False), r["orden"]), reverse=True)
    _recordar(out)
    return out[:max(int(rpp or 40), 40) + 10]


def texto(prov, m):
    """(texto, via) del edicte: PDF directo con capa de texto ('pdf'); sin OCR."""
    u = (m.get("url") if isinstance(m, dict) else m) or ""
    if not u:
        return "", "sin-url"
    try:
        pdf = B._getb(u, timeout=25)
    except Exception as e:  # noqa: BLE001
        return "", f"err:{type(e).__name__}"
    if pdf[:5] != b"%PDF-":
        return "", "sin-pdf"
    t, via = B._pdf_bytes_texto(pdf, ocr=False)
    t = re.sub(r"[ \t]+\n", "\n", t or "")
    if via == "cifrado" or len(t) < 300:
        return "", ("cifrado" if via == "cifrado" else "sin-texto")
    return t, "pdf"
