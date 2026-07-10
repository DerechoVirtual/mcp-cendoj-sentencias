# -*- coding: utf-8 -*-
"""Sonda GENÉRICA de BOP provincial (plataforma OpenCms/Saga) — offline (_*).

Uso:
  python _probe_provincia.py <id> <NombreProvincia> <base_url> [--resultados RUTA] [--outdir DIR] [--dry]

Hace, en orden:
  1. GET <base><resultados> -> cookies + objeto SagaListado (params del POST).
     Si no hay 'urlAjax' en la página => NO es Saga (estado no_saga).
  2. Varias búsquedas amplias -> extrae las FACETS de municipio
     (value="...(Ayuntamientos|Ajuntaments|Concellos|...)/<Norm>/" + <label>Nombre</label>).
  3. Búsqueda de prueba "ordenanza" filtrada por 2 municipios del mapa.
  4. Lee el primer anuncio: localiza PDF, lo descarga y extrae texto (fitz).
  5. Si todo OK escribe ordenanzas_data/bop_<id>_municipios.json y
     ordenanzas_data/bop_<id>_config.json y emite un informe JSON por stdout.

Emite SIEMPRE una última línea 'REPORT_JSON: {...}' parseable.
"""
import html as H
import http.cookiejar
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

try:
    import fitz
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
HERE = os.path.dirname(os.path.abspath(__file__))

# patrones de carpeta de entidad local en las facets (castellano/catalán/gallego/euskera)
ENT_PAT = r"(?:Ayuntamientos|Ajuntaments|Concellos|Concejos|Udalak|Udala|Entidades-Locales|EntidadesLocales)"


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


class Sonda:
    def __init__(self, base, resultados):
        self.base = base.rstrip("/")
        self.resultados = resultados
        cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        self.op.addheaders = [("User-Agent", UA), ("Accept-Language", "es-ES,es")]
        self.params = None
        self.pagina = ""

    def sesion(self):
        page = self.op.open(self.base + self.resultados, timeout=25).read().decode("utf-8", "replace")
        self.pagina = page
        j = page.find("urlAjax")
        if j < 0:
            return False
        ini = page.rfind("{", 0, j)
        fin = page.find("};", ini)
        self.params = {}
        for m in re.finditer(r"(\w+)\s*:\s*(?:'([^']*)'|\"([^\"]*)\"|([\w.\-]+))", page[ini + 1:fin]):
            self.params[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
        return "urlAjax" in self.params

    def post(self, texto, categoria=None, rpp=25):
        p = dict(self.params)
        p["buscarTexto"] = texto
        p["ResultadosPorPagina"] = str(rpp)
        p["paginaActual"] = "1"
        if categoria:
            p["buscarCategoria"] = categoria
            p["CategoriasAListar"] = categoria
        req = urllib.request.Request(
            self.base + p["urlAjax"], data=urllib.parse.urlencode(p).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "X-Requested-With": "XMLHttpRequest",
                     "Referer": self.base + self.resultados})
        return self.op.open(req, timeout=25).read().decode("utf-8", "replace")

    def getb(self, url, t=45):
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=t).read()


_NO_MUNI = re.compile(
    r"agencia|mancomunidad|consorcio|diputaci|patronato|organismo|instituto|"
    r"gerencia|empresa|sociedad|fundaci|consejo|junta de|epel|o\.a\.|autonoma|"
    r"autónoma|prodis|turismo|urbanismo|deporte|juventud|cultura|servicio", re.I)


def facets_municipios(html_txt):
    """{Nombre legible: categoria} desde las facets del buscador (solo municipios)."""
    out = {}
    for m in re.finditer(r'value="([^"]*' + ENT_PAT + r'/[^"/]+/)"', html_txt):
        cat = m.group(1)
        tail = html_txt[m.end():m.end() + 400]
        lab = re.search(r"<label[^>]*>\s*([^<]+?)\s*[\(<]", tail)
        if not lab:
            lab = re.search(r"<label[^>]*>\s*([^<]+?)\s*</label>", tail)
        if lab:
            nombre = H.unescape(lab.group(1)).strip()
            if nombre and len(nombre) < 60 and not _NO_MUNI.search(nombre):
                out[nombre] = cat
    return out


def parse_items(html_txt, base):
    out = []
    for m in re.finditer(r'<a href="([^"]*?/anuncio/[^"]+)"\s+title="([^"]+)"', html_txt):
        tail = html_txt[m.end():m.end() + 900]
        cve = re.search(r"BOP[A-Z]?-[A-Z]{2,4}-\d{4}-\d+", tail)
        fe = re.search(r"(\d{2})/(\d{2})/(\d{4})", tail)
        u = m.group(1)
        out.append({"url": (base + u) if u.startswith("/") else u,
                    "titulo": H.unescape(m.group(2)),
                    "cve": cve.group(0) if cve else "",
                    "fecha": fe.group(0) if fe else ""})
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = {a.split("=")[0].lstrip("-"): (a.split("=", 1)[1] if "=" in a else True)
            for a in sys.argv[1:] if a.startswith("--")}
    if len(args) < 3:
        print("uso: _probe_provincia.py <id> <Nombre> <base_url> [--resultados=RUTA] [--outdir=DIR] [--dry]")
        sys.exit(2)
    pid, nombre, base = args[0], args[1], args[2].rstrip("/")
    resultados = opts.get("resultados", "/publica/buscador-anuncios/resultados-anuncios/")
    outdir = opts.get("outdir", os.path.join(HERE, "ordenanzas_data"))
    rep = {"id": pid, "nombre": nombre, "base": base, "estado": "no_encontrado",
           "num_municipios": 0, "busqueda_ok": False, "lectura_ok": False, "notas": ""}

    def emit():
        print("\nREPORT_JSON: " + json.dumps(rep, ensure_ascii=False))

    s = Sonda(base, resultados)
    try:
        es_saga = s.sesion()
    except Exception as e:
        rep["notas"] = f"GET {base}{resultados} falló: {e}"
        emit(); return
    if not es_saga:
        rep["estado"] = "no_saga"
        rep["notas"] = "La página no contiene el objeto SagaListado (urlAjax). Plataforma distinta."
        emit(); return
    print(f"[1] Saga OK — urlAjax={s.params.get('urlAjax','')[:80]}")

    # --- facets de municipios (merge de varias búsquedas amplias + página GET)
    mapa = facets_municipios(s.pagina)
    for q in ("ordenanza", "aprobacion", "edicto", "presupuesto", "anuncio"):
        try:
            r = s.post(q, rpp=10)
            nuevos = facets_municipios(r)
            mapa.update(nuevos)
            print(f"[2] facets con «{q}»: +{len(nuevos)} (total {len(mapa)})")
            if len(mapa) > 30 and q != "ordenanza":
                break
        except Exception as e:
            print(f"[2] búsqueda «{q}» falló: {e}")
    rep["num_municipios"] = len(mapa)
    if not mapa:
        rep["estado"] = "saga_parcial"
        rep["notas"] = "Saga responde pero no se extraen facets de municipios (revisar patrón de categorías)."
        emit(); return

    # --- búsqueda de prueba filtrada por municipio (muestras repartidas)
    claves = sorted(mapa.keys())
    idxs = sorted({len(claves) // 4, len(claves) // 2, (3 * len(claves)) // 4, 1, len(claves) - 2})
    muestras = [claves[i] for i in idxs if 0 <= i < len(claves)][:5]
    items, muni_ok = [], None
    for muni in muestras:
        try:
            r = s.post("ordenanza", categoria=mapa[muni], rpp=15)
            it = parse_items(r, s.base)
            print(f"[3] «ordenanza» en {muni}: {len(it)} resultados")
            if it:
                items, muni_ok = it, muni
                break
        except Exception as e:
            print(f"[3] fallo buscando en {muni}: {e}")
    if not items:
        # sin filtro, para distinguir 'no responde' de 'municipio sin resultados'
        try:
            it = parse_items(s.post("ordenanza", rpp=15), s.base)
            print(f"[3b] «ordenanza» SIN filtro: {len(it)} resultados")
            if it:
                items = it
        except Exception as e:
            print(f"[3b] fallo: {e}")
    rep["busqueda_ok"] = bool(items)
    if not items:
        rep["estado"] = "saga_parcial"
        rep["notas"] = f"Facets OK ({len(mapa)} municipios) pero la búsqueda no devuelve anuncios parseables."
        emit(); return
    # prefijo real de los enlaces de anuncio
    pref = re.match(r"(.*/anuncio/)", urllib.parse.urlparse(items[0]["url"]).path)
    anuncio_href = pref.group(1) if pref else "/publica/buscador-anuncios/anuncio/"

    # --- lectura: anuncio -> PDF -> texto
    pdf_marker, texto_len = "", 0
    for it in items[:3]:
        try:
            det = s.getb(it["url"]).decode("utf-8", "replace")
            mm = re.search(r'href="([^"]+\.pdf)"', det)
            cand = re.findall(r'href="([^"]+\.pdf)"', det)
            cand.sort(key=lambda u: ("galleries" not in u, len(u)))
            if not cand:
                continue
            u = cand[0]
            pdf_url = (s.base + u) if u.startswith("/") else u
            seg = re.search(r"\.galleries/([^/]+)/", u)
            pdf_marker = seg.group(1) if seg else (re.search(r"/([^/]+)/[^/]+\.pdf$", u).group(1) if re.search(r"/([^/]+)/[^/]+\.pdf$", u) else "")
            if _HAS_FITZ:
                doc = fitz.open(stream=s.getb(pdf_url), filetype="pdf")
                texto = "\n".join(doc[i].get_text() for i in range(doc.page_count))
                texto_len = len(texto.strip())
                print(f"[4] PDF {pdf_url[:90]} -> {doc.page_count} pág, {texto_len} chars texto directo")
            else:
                texto_len = len(s.getb(pdf_url))
                print(f"[4] (sin fitz) PDF descargado {texto_len} bytes")
            if pdf_marker:
                break
        except Exception as e:
            print(f"[4] fallo leyendo {it['url'][:80]}: {e}")
    rep["lectura_ok"] = bool(pdf_marker)
    rep["anuncio_pdf"] = pdf_marker or "Documentos-Anuncios-en-PDF"
    rep["anuncio_href"] = anuncio_href
    rep["muestra"] = {"municipio": muni_ok or "", "titulo": items[0]["titulo"][:90],
                      "cve": items[0]["cve"], "texto_chars": texto_len}
    rep["estado"] = "saga_ok" if (rep["busqueda_ok"] and rep["lectura_ok"]) else "saga_parcial"
    if rep["estado"] == "saga_ok":
        rep["notas"] = f"OK · {len(mapa)} municipios · muestra {muni_ok}: «{items[0]['titulo'][:60]}»"
    else:
        rep["notas"] = "Búsqueda OK pero sin PDF legible en las muestras (revisar patrón de PDF)."

    # --- escribir artefactos
    if not opts.get("dry"):
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, f"bop_{pid}_municipios.json"), "w", encoding="utf-8") as f:
            json.dump(mapa, f, ensure_ascii=False, indent=1, sort_keys=True)
        cfg = {"id": pid, "base": s.base, "resultados": resultados,
               "anuncio_pdf": rep["anuncio_pdf"], "anuncio_href": anuncio_href,
               "mapa": f"bop_{pid}_municipios.json", "nombre": nombre}
        with open(os.path.join(outdir, f"bop_{pid}_config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        print(f"[5] escritos bop_{pid}_municipios.json ({len(mapa)}) y bop_{pid}_config.json")
    emit()


if __name__ == "__main__":
    main()
