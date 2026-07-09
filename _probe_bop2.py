# -*- coding: utf-8 -*-
"""Sonda 2 del BOP Sevilla: buscador Solr (element.jsp) SIN reCAPTCHA + lectura.
Offline (_*). El reCAPTCHA solo bloquea el frontend; el backend Solr responde
por POST con cookies de sesion frescas."""
import re
import sys
import time
import html as H
import urllib.parse
import urllib.request
import http.cookiejar

BASE = "https://bopsevilla.dipusevilla.es"
RESULTADOS = BASE + "/publica/buscador-anuncios/resultados-anuncios/"
_op = None
_params = None


def _sesion():
    """GET de la pagina de resultados -> cookies JSESSIONID + objeto de params."""
    global _op, _params
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
                     ("Accept-Language", "es-ES,es")]
    page = op.open(RESULTADOS, timeout=30).read().decode("utf-8", "replace")
    j = page.find("urlAjax"); ini = page.rfind("{", 0, j); fin = page.find("};", ini)
    blob = page[ini + 1:fin]
    params = {}
    for m in re.finditer(r"(\w+)\s*:\s*(?:'([^']*)'|\"([^\"]*)\"|([\w.\-]+))", blob):
        params[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    _op, _params = op, params
    return op, params


def buscar(texto, rpp=10, timeout=25):
    """Devuelve lista de dicts {titulo, url, cve, fecha}. ~0.6s."""
    if _op is None:
        _sesion()
    p = dict(_params)
    p["buscarTexto"] = texto
    p["ResultadosPorPagina"] = str(rpp)
    p["paginaActual"] = "1"
    url = BASE + p["urlAjax"]
    req = urllib.request.Request(url, data=urllib.parse.urlencode(p).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Requested-With": "XMLHttpRequest", "Referer": RESULTADOS})
    resp = _op.open(req, timeout=timeout).read().decode("utf-8", "replace")
    if "opencms.org" in resp:
        _sesion()  # sesion caducada -> reintentar 1 vez
        return buscar(texto, rpp, timeout)
    out = []
    # cada resultado: <a href=".../anuncio/slug/" title="Titulo"> ... CVE ... fecha
    for m in re.finditer(r'<a href="(/publica/buscador-anuncios/anuncio/[^"]+)"\s+title="([^"]+)"', resp):
        url_a = BASE + m.group(1)
        titulo = H.unescape(m.group(2))
        # CVE y fecha en el bloque siguiente
        tail = resp[m.end():m.end() + 900]
        cve = (re.search(r"BOP-SE-\d{4}-\d+", tail) or [None])
        cve = cve.group(0) if hasattr(cve, "group") else ""
        fecha = (re.search(r"\d{2}/\d{2}/\d{4}", tail) or [None])
        fecha = fecha.group(0) if hasattr(fecha, "group") else ""
        out.append({"titulo": titulo, "url": url_a, "cve": cve, "fecha": fecha})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    consultas = sys.argv[1:] or [
        "residuos Lora del Rio", "basuras Umbrete", "terrazas Dos Hermanas",
        "ordenanza reguladora limpieza Mairena", "ruido Alcala de Guadaira"]
    _sesion()
    for q in consultas:
        t0 = time.time()
        res = buscar(q, rpp=5)
        print(f"\n### '{q}'  [{time.time()-t0:.2f}s] {len(res)} resultados")
        for r in res[:5]:
            print(f"   {r['fecha']} {r['cve']} | {r['titulo'][:72]}")
