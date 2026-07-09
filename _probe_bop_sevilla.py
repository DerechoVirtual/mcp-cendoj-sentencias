# -*- coding: utf-8 -*-
"""Sonda del servicio SagaListado (Solr) del BOP de Sevilla. Offline (_*)."""
import http.cookiejar
import re
import sys
import time
import html as H
import urllib.parse
import urllib.request

S = ("C:/Users/carlo/AppData/Local/Temp/claude/"
     "C--Users-carlo-OneDrive-Documentos-antigravity-pinecone-y-jurisprudencia/"
     "ad383c1d-62e3-4444-9ede-1c61787ef4b4/scratchpad")
BASE = "https://bopsevilla.dipusevilla.es"


def parse_params(html_text):
    j = html_text.find("urlAjax")
    ini = html_text.rfind("{", 0, j)
    fin = html_text.find("};", ini)
    blob = html_text[ini + 1:fin]
    params = {}
    pat = re.compile(r"(\w+)\s*:\s*(?:'([^']*)'|\"([^\"]*)\"|([\w.\-]+))\s*,?")
    for m in pat.finditer(blob):
        k = m.group(1)
        v = m.group(2)
        if v is None:
            v = m.group(3)
        if v is None:
            v = m.group(4)
        params[k] = v
    return params


def buscar(buscar_texto, query_extra=None, rpp="10", pagina="1", timeout=30):
    t = open(f"{S}/bops_res.html", encoding="utf-8", errors="replace").read()
    params = parse_params(t)
    params["buscarTexto"] = buscar_texto
    params["ResultadosPorPagina"] = rpp
    params["paginaActual"] = pagina
    if query_extra is not None:
        params["QueryExtra"] = query_extra
    cj = http.cookiejar.MozillaCookieJar(f"{S}/bop_ck.txt")
    try:
        cj.load(ignore_discard=True)
    except Exception:
        pass
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [
        ("User-Agent", "Mozilla/5.0"),
        ("Referer", BASE + "/publica/buscador-anuncios/resultados-anuncios/"),
        ("X-Requested-With", "XMLHttpRequest"),
    ]
    url = BASE + params["urlAjax"]
    data = urllib.parse.urlencode(params).encode()
    t0 = time.time()
    resp = op.open(url, data=data, timeout=timeout).read().decode("utf-8", "replace")
    dt = time.time() - t0
    return dt, resp, params


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    consulta = sys.argv[1] if len(sys.argv) > 1 else "ordenanza terrazas"
    qe = sys.argv[2] if len(sys.argv) > 2 else None
    dt, resp, params = buscar(consulta, qe)
    print(f"[{dt:.2f}s] {len(resp)} chars | params: {len(params)}")
    open(f"{S}/saga_post2.html", "w", encoding="utf-8").write(resp)
    if "opencms.org" in resp:
        print("ERROR: pagina por defecto de OpenCms")
        sys.exit(1)
    vistos = 0
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', resp, re.S):
        lbl = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", H.unescape(m.group(2)))).strip()
        if lbl:
            print("  -", lbl[:88], "|", m.group(1)[:70])
            vistos += 1
        if vistos >= 10:
            break
    print("totalPages:", re.findall(r'totalPages" value="(\d+)"', resp)[:1],
          "| fechas:", re.findall(r"\d{2}/\d{2}/\d{4}", resp)[:3])
