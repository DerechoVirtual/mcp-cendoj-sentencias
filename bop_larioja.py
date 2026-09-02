# -*- coding: utf-8 -*-
"""Backend LA RIOJA (familia «larioja») del motor de ordenanzas municipales.

La Rioja es uniprovincial: su «BOP» es el Boletín Oficial de La Rioja (BOR),
publicado en web.larioja.org (portal Joomla + app «ckan-client» con un buscador
AJAX). Receta verificada en vivo el 27-jul-2026 (ver `_probe_larioja.py`) y
recomprobada al implementar este backend (2-sep-2026).

BÚSQUEDA (una GET, ~0,25 s):
  GET {base}/apps/ckan-client/public/bor/listaAJAX?filtros=<JSON>[&page=N]
    filtros: title (búsqueda POR TÍTULO: AND de todas las palabras, con
             lematización básica: «terraza» = «terrazas»; tokens de 3 letras
             como «IBI» NO casan), extras_creador_descripcion (organismo en texto
             libre = valor del mapa «Ayuntamiento de X»).
    Respuesta {"status":"success","content":"<HTML>"} con 20 <li> por página,
    orden por fecha DESC, sin parámetro rows. Cada <li>: <h6>ORGANISMO</h6>,
    <a href=PDF>TÍTULO</a>, «BOR nº N - Fecha: dd/mm/aaaa» y el enlace html
    /bor-portada/boranuncio?n=anu-<id>.
  TRAMPAS: (1) el filtro de organismo es DIFUSO («Ayuntamiento de Sotés» cuela
  «Soto en Cameros»): se confirma el municipio con el <h6> de cada <li>;
  (2) Cloudflare exige User-Agent no vacío (sin él, 403); (3) «text» (todos los
  campos) devuelve casi todo el corpus del organismo: no se usa.

LECTURA (~0,5 s, SIN OCR): el PDF oficial de cada anuncio
  https://ias1.larioja.org/boletin/Bor_Boletin_visor_Servlet?referencia=<ref>
  trae el anuncio COMPLETO con capa de texto. El BOR usa U+FFFF como separador
  de palabras y glifos privados U+F02D / U+F0B7 para «-» y «·»: se limpian.
  Respaldo: /bor-portada/boranuncio?n=anu-<id> (<div class="anuncio_texto">);
  en anuncios modernos solo trae la resolución, no el articulado anexo.
  Ventana útil del índice: 2012 -> hoy (antes no hay título en el listado).

Referencia interna (cve): «BOP-LR-<año>-<id de anuncio>». Con ella el motor
relee el anuncio al instante (leer_ordenanza con el CVE); en la web oficial el
anuncio es /bor-portada/boranuncio?n=anu-<id>.
"""
import html as _html
import json
import re
import time
import urllib.parse
import urllib.request

import bop_engine as B

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None

_AJAX = "/apps/ckan-client/public/bor/listaAJAX"
_ANUNCIO = "/bor-portada/boranuncio?n=anu-{id}"
_POR_PAGINA = 20
_MAX_PAGINAS = 5

_LI = re.compile(r"(?s)<li>\s*<h6>(.*?)</h6>(.*?)</li>")
_PDF = re.compile(r'href="(https://ias1\.larioja\.org/boletin/Bor_Boletin_visor_Servlet'
                  r'\?referencia=[^"]+)"[^>]*>(.*?)</a>', re.S)
_HTM = re.compile(r'href="/bor-portada/boranuncio\?n=anu-(\d+)"')
_BOR = re.compile(r"BOR n[ºo°]\s*(\d+)\s*-\s*Fecha:\s*(\d{2})/(\d{2})/(\d{4})")
_TOT = re.compile(r"total de <span>([\d.]+)</span>")
_CVE = re.compile(r"(?i)\b(?:BOP-LR-\d{4}-|anu-|anuncio\s+)(\d{4,})\b")
_PREF = re.compile(r"(?i)^\s*(?:excmo\.?\s+)?(?:ayuntamiento|ayto\.?)\s+de\s+(?:la\s+|el\s+)?")
# Anuncios que NUNCA son normativa pero pasan el filtro «ordenanza/tasa» del motor
# por su título («Padrón sobre tasa de ocupación… (terrazas de veladores)»,
# «Notificación de sanciones por infracción de la Ordenanza…»). El BOR publica
# decenas al año por ayuntamiento y se colaban por delante de la ordenanza real.
# Solo se conservan si el abogado pregunta precisamente por ellos.
_NO_NORMA = re.compile(r"(?i)padr[oó]n|notificaci[oó]n|licitaci[oó]n|adjudicaci[oó]n|"
                       r"cobranza|per[ií]odo voluntario|devoluci[oó]n de (?:fianza|garant)|"
                       r"lista (?:provisional|definitiva)|admitidos y excluidos|nombramiento|"
                       r"contrataci[oó]n|matr[ií]cula|expediente sancionador|sancionador|"
                       r"incoaci[oó]n|requerimiento|\bexpte\b|imposici[oó]n de sanci|"
                       r"emplazamiento|citaci[oó]n")


def _txt(x):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


def _get(url, timeout=20, intentos=2):
    """GET con User-Agent (Cloudflare devuelve 403 sin él) y un reintento con espera."""
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": B._UA,
                                                       "Accept-Language": "es-ES,es"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if i + 1 < intentos:
                time.sleep(0.8)
    raise ultimo


def _mismo_organismo(h6, filtro):
    return B._norm(_PREF.sub("", _txt(h6))) == B._norm(_PREF.sub("", filtro or ""))


def _item(cfg, org, cuerpo):
    p, hm, bo = _PDF.search(cuerpo), _HTM.search(cuerpo), _BOR.search(cuerpo)
    if not (p and hm):
        return None
    aid = hm.group(1)
    if bo:
        num, d, mo, y = bo.groups()
        fecha, orden = f"{d}/{mo}/{y}", f"{y}{mo}{d}"
    else:
        num, fecha, orden, y = "", "", "0", ""
    return {"url": cfg["base"] + _ANUNCIO.format(id=aid),
            "titulo": _txt(p.group(2)), "cve": f"BOP-LR-{y or '0000'}-{aid}",
            "fecha": fecha, "orden": orden, "id": aid, "num": num,
            "pdf": _html.unescape(p.group(1)), "organismo": _txt(org)}


def _lista(cfg, titulo, organismo, page=1):
    """Una página del buscador: ([items], total)."""
    f = {"title": titulo, "extras_creador_descripcion": organismo}
    qs = {"filtros": json.dumps(f, ensure_ascii=False)}
    if page > 1:
        qs["page"] = page
    raw = _get(cfg["base"] + _AJAX + "?" + urllib.parse.urlencode(qs))
    try:
        c = json.loads(raw.decode("utf-8", "replace")).get("content") or ""
    except Exception:  # noqa: BLE001
        return [], 0
    items = []
    for org, cuerpo in _LI.findall(c):
        if not _mismo_organismo(org, organismo):
            continue          # el filtro de organismo es difuso: se confirma con el <h6>
        it = _item(cfg, org, cuerpo)
        if it:
            items.append(it)
    t = _TOT.search(c)
    total = int(t.group(1).replace(".", "")) if t else len(items)
    return items, total


def _consultas(texto):
    """Consultas por TÍTULO para lo que pidió el abogado. El buscador hace AND de
    todas las palabras, así que con dos o más términos útiles se lanza la
    conjunción (precisa) y además cada término suelto (recall)."""
    pal = [w for w in re.split(r"\W+", texto or "") if w]
    stop = {B._norm(x) for x in B._STOPM}
    utiles = [w for w in pal if B._norm(w) not in stop and len(w) >= 4]
    if len(utiles) >= 2:
        qs = [" ".join(utiles[:3])] + sorted(utiles, key=len, reverse=True)[:2]
    else:
        qs = [texto.strip() or "ordenanza"]
    fuera, out = set(), []
    for q in qs:
        if B._norm(q) not in fuera:
            fuera.add(B._norm(q))
            out.append(q)
    return out


def _por_id(cfg, aid, filtro):
    """Un anuncio concreto por su id (referencia BOP-LR-<año>-<id> o anu-<id>):
    la página HTML del anuncio trae título, organismo, número, fecha y PDF."""
    try:
        h = _get(cfg["base"] + _ANUNCIO.format(id=aid)).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    tit = re.search(r'(?s)<p class="entradilla_anuncio">(.*?)</p>', h)
    org = re.search(r'(?s)<div class="anuncio_organo">(.*?)</div>', h)
    num = re.search(r'(?s)<div class="anuncio_num">\s*N[úu]m\.?\s*(\d+)', h)
    fe = re.search(r'/bor-portada/\?fecha=(\d{4})-(\d{2})-(\d{2})', h)
    pdf = re.search(r'(https://ias1\.larioja\.org/boletin/Bor_Boletin_visor_Servlet\?referencia=[^"\']+)', h)
    if not tit:
        return []
    if filtro and org and not _mismo_organismo(org.group(1), filtro):
        return []
    y, mo, d = fe.groups() if fe else ("", "", "")
    return [{"url": cfg["base"] + _ANUNCIO.format(id=aid), "titulo": _txt(tit.group(1)),
             "cve": f"BOP-LR-{y or '0000'}-{aid}", "fecha": f"{d}/{mo}/{y}" if fe else "",
             "orden": f"{y}{mo}{d}" if fe else "0", "id": aid, "num": num.group(1) if num else "",
             "pdf": _html.unescape(pdf.group(1)) if pdf else "",
             "organismo": _txt(org.group(1)) if org else ""}]


def buscar(prov, texto, filtro, rpp=40):
    """Anuncios del BOR del ayuntamiento `filtro` («Ayuntamiento de X») cuyo TÍTULO
    contiene `texto`. Sin filtro no se consulta el corpus entero (devuelve [])."""
    cfg = B.PROVINCIAS[prov]
    if not filtro:
        return []
    texto = (texto or "").strip()
    m = _CVE.search(texto)
    if m:
        return _por_id(cfg, m.group(1), filtro)
    consultas = _consultas(texto)
    # volcado genérico («ordenanza», «tasa»: Logroño tiene ~950 anuncios así) ->
    # 2 páginas bastan; consulta de materia -> hasta 3 (60 títulos con la palabra)
    generico = len(consultas) == 1 and B._norm(consultas[0]) in {B._norm(x) for x in B._STOPM} | {"tasa", "tasas"}
    paginas = 2 if generico else max(1, min(_MAX_PAGINAS, -(-int(rpp or 20) // _POR_PAGINA)))
    vistos = {}
    for q in consultas:
        for page in range(1, paginas + 1):
            try:
                items, total = _lista(cfg, q, filtro, page)
            except Exception:  # noqa: BLE001
                break
            for it in items:
                vistos.setdefault(it["id"], it)
            if page * _POR_PAGINA >= total or not items:
                break
    if not vistos:
        # el título no lleva la palabra del abogado («IBI» no casa: tokens de 3
        # letras): se prueba con los términos del tesauro («bienes inmuebles»)
        raw, core, _soft = B._familias(texto)
        extra = [c for c in sorted(core, key=len, reverse=True)
                 if len(c) >= 4 and B._norm(c) not in {B._norm(r) for r in raw}][:2]
        for q in extra:
            try:
                items, _total = _lista(cfg, q, filtro, 1)
            except Exception:  # noqa: BLE001
                continue
            for it in items:
                vistos.setdefault(it["id"], it)
    if not _NO_NORMA.search(texto):
        vistos = {k: v for k, v in vistos.items() if _es_normativa(v["titulo"])}
    return list(vistos.values())


def _es_normativa(titulo):
    """Fuera padrones y notificaciones; también los avisos de tasa por ejercicio
    («Tasa por ocupación de terrenos con mesas y sillas… Ejercicio 2025») cuando
    no nombran ninguna ordenanza o reglamento."""
    if _NO_NORMA.search(titulo):
        return False
    if re.search(r"(?i)\bejercicio\s+\d{4}", titulo) \
            and not re.search(r"(?i)ordenanza|reglamento", titulo):
        return False
    return True


def _limpia_bor(t):
    return (t.replace("￿", " ").replace("", "-").replace("", "·")
             .replace("", "·"))


def texto(prov, m):
    """(texto, via) del anuncio: PDF oficial con capa de texto (via 'pdf');
    respaldo HTML del anuncio (via 'html'). ("", "sin-texto") si no hay nada."""
    cfg = B.PROVINCIAS[prov]
    if not isinstance(m, dict):
        return "", "sin-texto"
    pdf_url = m.get("pdf") or ""
    if pdf_url and fitz is not None:
        try:
            b = _get(pdf_url, timeout=25)
            if b[:5] == b"%PDF-":
                doc = fitz.open(stream=b, filetype="pdf")
                t = _limpia_bor("\n".join(doc[i].get_text() for i in range(doc.page_count)))
                doc.close()
                t = re.sub(r"[ \t]+", " ", t).strip()
                if len(t) > 300:
                    return t, "pdf"
        except Exception:  # noqa: BLE001
            pass
    aid = m.get("id") or ""
    if aid:
        try:
            h = _get(cfg["base"] + _ANUNCIO.format(id=aid)).decode("utf-8", "replace")
            mm = re.search(r'(?s)<div class="anuncio_texto">(.*)', h)
            if mm:
                x = mm.group(1)
                j = x.find("anuncio_pie")
                t = B._html_a_texto(x[:j] if j > 0 else x)
                if len(t) > 300:
                    return t, "html"
        except Exception:  # noqa: BLE001
            pass
    return "", "sin-texto"
