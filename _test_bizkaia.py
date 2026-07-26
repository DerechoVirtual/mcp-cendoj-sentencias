# -*- coding: utf-8 -*-
"""Verifica EN VIVO el mapa de Bizkaia: cada municipio devuelve anuncios propios."""
import sys, json, time, re, gzip, ssl, html, os, urllib.request, urllib.parse
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
_SSL = ssl._create_unverified_context()
BASE, RES = "https://www.bizkaia.eus", "/es/bob/resultados"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_cj = http.cookiejar.CookieJar()
_op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj),
                                  urllib.request.HTTPSHandler(context=_SSL))
_op.addheaders = [("User-Agent", UA), ("Accept-Encoding", "gzip, deflate"),
                  ("Accept-Language", "es-ES,es;q=0.9")]


def _get(u, timeout=90, binary=False):
    t0 = time.time()
    r = _op.open(u, timeout=timeout)
    d = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        d = gzip.decompress(d)
    return (d if binary else d.decode("utf-8", "replace")), time.time() - t0


def url_busq(texto="", issuers="", delta=20, cur=1, extra=None):
    p = [("p_p_id", "IYBIWBCC"), ("p_p_lifecycle", "0"), ("p_p_state", "normal"),
         ("p_p_mode", "view"), ("_IYBIWBCC_text", texto)]
    for pre in ("dateFromBol", "dateToBol", "dateFromDisp", "dateToDisp"):
        for suf in ("Day", "Month", "Year"):
            p.append(("_IYBIWBCC_" + pre + suf, "0"))
    p += [("_IYBIWBCC_mvcRenderCommandName", "/search/filtros"),
          ("_IYBIWBCC_resetCur", "false"), ("_IYBIWBCC_delta", str(delta)),
          ("_IYBIWBCC_cur", str(cur)), ("_IYBIWBCC_issuersSelect", issuers)]
    if extra:
        p += extra
    return BASE + RES + "?" + urllib.parse.urlencode(p)


RE_TOT = re.compile(r'bipo_numero">([\d\.]+)</span>')
RE_ITEM = re.compile(r'<li class="row">.*?numberbob">([^<]+)</p>.*?fechabob">([^<]+)</p>.*?'
                     r'<div class="col-9 col-sm-7">\s*<p>(.*?)</p>.*?href="([^"]*Bao_bob[^"]*)"', re.S)


def _t(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", s))).strip()


def parsea(h):
    tot = RE_TOT.search(h)
    tot = int(tot.group(1).replace(".", "")) if tot else 0
    i = h.find("listado-resultados")
    seg = h[i:h.find("EmptyResultsMessage", i)] if i >= 0 else ""
    items, partes = [], re.split(r"<h3>(.*?)</h3>", seg, flags=re.S)
    for k in range(1, len(partes), 2):
        em = _t(partes[k])
        for m in RE_ITEM.finditer(partes[k + 1]):
            items.append({"emisor": em, "bob": m.group(1).strip(), "fecha": m.group(2).strip(),
                          "titulo": _t(m.group(3)), "pdf": html.unescape(m.group(4))})
    return tot, items


def _get_sin_sesion(u, timeout=90, binary=False):
    """SIN cookies: el portlet guarda la búsqueda en la sesión, así que compartir
    JSESSIONID entre hilos mezcla los resultados. Stateless = paralelizable."""
    t0 = time.time()
    r = urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Encoding": "gzip",
                                           "Accept-Language": "es-ES,es;q=0.9"}),
        timeout=timeout, context=_SSL)
    d = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        d = gzip.decompress(d)
    return (d if binary else d.decode("utf-8", "replace")), time.time() - t0


def prueba(arg):
    muni, iss = arg
    try:
        h, dt = _get_sin_sesion(url_busq("ordenanza", iss, delta=20))
        tot, items = parsea(h)
        ems = sorted({i["emisor"] for i in items})
        return muni, tot, len(items), ems, dt, None
    except Exception as e:  # noqa: BLE001
        return muni, -1, 0, [], 0, repr(e)[:90]


if __name__ == "__main__":
    _get(BASE + "/es/bob", timeout=45)
    mapa = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "ordenanzas_data", "bop_bizkaia_municipios.json"),
                          encoding="utf-8"))
    bruto = json.load(open("_bizkaia_mapa_bruto.json", encoding="utf-8"))
    # una prueba por MUNICIPIO (no por alias)
    casos = []
    for ofi, ent in bruto.items():
        iss = " o ".join('"%s"' % n for n in sorted(set(ent["nombres"])))
        casos.append((ofi, iss))
    ok = ko = 0
    t0 = time.time()
    nw = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    with ThreadPoolExecutor(max_workers=nw) as ex:
        for muni, tot, n, ems, dt, err in ex.map(prueba, casos):
            bien = tot > 0 and n > 0
            if bien:
                ok += 1
            else:
                ko += 1
                print("  FALLO", muni, tot, n, err or "")
    print("\nOK %d / KO %d  (%d municipios) en %.1fs" % (ok, ko, len(casos), time.time() - t0))
