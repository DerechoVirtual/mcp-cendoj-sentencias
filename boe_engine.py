# -*- coding: utf-8 -*-
"""
Motor BOE (legislación española consolidada) — versión SERVERLESS para el
conector Jurisprudenciator. Portado del MCP local `boe-mcp` (validado <1 s).

Diseño para Vercel (stateless, disco read-only salvo /tmp):
  * DATOS ESTÁTICOS empaquetados en boe_data/ (read-only):
      - catalogo.json  (12.327 normas consolidadas: id, número, título, rango, fecha)
      - indices/*.json (índices de bloques de ~60 leyes clave, pre-cacheados)
  * CACHÉ ESCRIBIBLE en /tmp (persiste entre warm starts):
      - bloques XML de artículos (TTL 24 h) e índices nuevos.
  * Resolución de ley 0-red por alias (boe_aliases.py, ~65 leyes verificadas);
    fallback por número(+fecha)/texto contra el catálogo local. SIN red para
    resolver: solo 1 GET al BOE por artículo (con hedge multi-oleada).

API del BOE (datos abiertos, sin clave):
  GET /legislacion-consolidada/id/{ID}/texto/bloque/{bloque}   (solo XML)
  GET /legislacion-consolidada/id/{ID}/texto/indice            (JSON)
La versión vigente de un bloque = última <version>; se filtran las notas
(nota_pie / cita_con_pleca). Ids de bloque inconsistentes ('a18', 'art1902',
'a4-5'...) y leyes con artículos en palabras (LOPJ) -> ver _bloque_articulo.
"""
import os
import re
import json
import time
import tempfile
import unicodedata
import datetime
import urllib.request
import urllib.error
import concurrent.futures as _cf
import xml.etree.ElementTree as ET

import boe_aliases as _aliases

API = "https://www.boe.es/datosabiertos/api"
LEG = API + "/legislacion-consolidada"

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "boe_data")              # read-only (repo)
CACHE_DIR = os.path.join(tempfile.gettempdir(), "boe-cache")  # /tmp en Vercel
IDX_CACHE = os.path.join(CACHE_DIR, "indices")
BLQ_CACHE = os.path.join(CACHE_DIR, "bloques")
for _d in (IDX_CACHE, BLQ_CACHE):
    os.makedirs(_d, exist_ok=True)
TTL_BLOQUE = 24 * 3600

# ---------------------------------------------------------------- utilidades
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("/", " / ")
    s = re.sub(r"[^a-z0-9/]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

ALIAS = _aliases.build_index(_norm)
_ID2NOMBRE = {bid: nombre for _, bid, nombre in _aliases.LEYES}

def _http(url: str, accept="application/json", timeout=8):
    req = urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": "jurisprudenciator-boe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return "ERR", str(e)

def _get_json(url):
    st, txt = _http(url, "application/json")
    if st == 200:
        try:
            return json.loads(txt)
        except Exception:
            return None
    return None

# ---------------------------------------------------------------- catálogo
_CAT = None
def _catalogo():
    global _CAT
    if _CAT is None:
        try:
            _CAT = json.load(open(os.path.join(DATA_DIR, "catalogo.json"), encoding="utf-8"))
        except Exception:
            _CAT = []
    return _CAT

_CATID = None
def _cat_by_id():
    global _CATID
    if _CATID is None:
        _CATID = {x["id"]: x for x in _catalogo()}
    return _CATID

def _norma_info(bid):
    x = _cat_by_id().get(bid)
    if x:
        return x.get("n", ""), x.get("r", ""), x.get("t", "")
    d = _get_json(f"{LEG}/id/{bid}/metadatos")
    if d:
        m = d["data"][0] if isinstance(d["data"], list) else d["data"]
        info = {"id": bid, "n": m.get("numero_oficial", ""),
                "r": m.get("rango", {}).get("texto", ""), "t": m.get("titulo", "")}
        _cat_by_id()[bid] = info
        return info["n"], info["r"], info["t"]
    return "", "", ""

_RANGO_ABBR = {"Ley Orgánica": "LO", "Real Decreto-ley": "RD-ley",
               "Real Decreto Legislativo": "RD Legislativo", "Real Decreto": "RD", "Ley": "Ley"}
def _norma_corta(bid):
    n, r, t = _norma_info(bid)
    ab = _RANGO_ABBR.get(r, r)
    return f"{ab} {n} ({bid})" if n else (f"{t[:45]} ({bid})" if t else bid)

_RANGO_HINT = [
    (r"\bl\.?o\.?\b|\bley organica\b", "Ley Orgánica"),
    (r"\br\.?d\.?l\.?e?g?\.?\b|real decreto legislativo", "Real Decreto Legislativo"),
    (r"\breal decreto ley\b|\brd ?ley\b|\brdl\b", "Real Decreto-ley"),
    (r"\breal decreto\b|\brd\b", "Real Decreto"),
    (r"\bley\b", "Ley"),
]
_MESES = {"enero": "01", "febrero": "02", "marzo": "03", "abril": "04", "mayo": "05",
          "junio": "06", "julio": "07", "agosto": "08", "septiembre": "09", "setiembre": "09",
          "octubre": "10", "noviembre": "11", "diciembre": "12"}

def _fecha_de_cita(txt, anio):
    m = re.search(r"\bde\s+(\d{1,2})\s+de\s+(" + "|".join(_MESES) + r")\b", txt.lower())
    return f"{anio}{_MESES[m.group(2)]}{int(m.group(1)):02d}" if m else None

def _buscar_candidatos(consulta: str, limite=10):
    q = _norm(consulta)
    cat = _catalogo()
    if not cat:
        return []
    mnum = re.search(r"(\d+)\s*/\s*(\d{4})", consulta)
    if mnum:
        num = f"{mnum.group(1)}/{mnum.group(2)}"; anio = mnum.group(2)
        hits = [x for x in cat if x["n"] == num]
        fdia = _fecha_de_cita(consulta, anio)
        if fdia:
            exact = [x for x in hits if x.get("f") == fdia]
            if exact:
                hits = exact
        rango_pref = None
        for pat, rg in _RANGO_HINT:
            if re.search(pat, q):
                rango_pref = rg
                break
        if rango_pref:
            pref = [x for x in hits if x["r"] == rango_pref]
            hits = pref + [x for x in hits if x not in pref]
        if hits:
            return [{"id": x["id"], "titulo": x["t"], "rango": x["r"], "num": x["n"]}
                    for x in hits[:limite]]
    pals = [p for p in q.split() if len(p) > 3 and p != "ley"]
    if not pals:
        return []
    res = []
    for x in cat:
        tn = _norm(x["t"])
        if all(p in tn for p in pals):
            res.append(x)
    res.sort(key=lambda x: len(x["t"]))
    return [{"id": x["id"], "titulo": x["t"], "rango": x["r"], "num": x["n"]}
            for x in res[:limite]]

def _resolver_ley(consulta: str):
    if not consulta:
        return None
    m = re.search(r"boe[-\s]?a[-\s]?(\d{4})[-\s]?(\d+)", consulta, re.I)
    if m:
        bid = f"BOE-A-{m.group(1)}-{m.group(2)}"
        return (bid, _ID2NOMBRE.get(bid, bid))
    q = _norm(consulta)
    if q in ALIAS:
        return ALIAS[q]
    q2 = re.sub(r"^(la|el|los|las|de|del)\s+", "", q).strip()
    if q2 in ALIAS:
        return ALIAS[q2]
    for a, val in ALIAS.items():
        if len(a) >= 3 and re.search(rf"(^|\s){re.escape(a)}(\s|$)", q):
            return val
    cands = _buscar_candidatos(consulta, limite=1)
    if cands:
        return (cands[0]["id"], _ID2NOMBRE.get(cands[0]["id"], cands[0]["titulo"]))
    return None

def _resolver_estricto(cand: str):
    """Solo ID BOE, alias o número (con fecha) — nunca texto libre suelto."""
    if not cand:
        return None
    m = re.search(r"boe[-\s]?a[-\s]?(\d{4})[-\s]?(\d+)", cand, re.I)
    if m:
        bid = f"BOE-A-{m.group(1)}-{m.group(2)}"
        return (bid, _ID2NOMBRE.get(bid, bid))
    q = _norm(cand)
    if q in ALIAS:
        return ALIAS[q]
    q2 = re.sub(r"^(la|el|los|las|de|del)\s+", "", q).strip()
    if q2 in ALIAS:
        return ALIAS[q2]
    for a, val in ALIAS.items():
        if len(a) >= 3 and re.search(rf"(^|\s){re.escape(a)}(\s|$)", q):
            return val
    if re.search(r"\d+\s*/\s*\d{4}", cand):
        cands = _buscar_candidatos(cand, limite=1)
        if cands:
            bid = cands[0]["id"]
            return (bid, _ID2NOMBRE.get(bid, cands[0]["titulo"]))
    return None

# ---------------------------------------------------------------- índice de una ley
def _indice(bid: str):
    fp_tmp = os.path.join(IDX_CACHE, bid + ".json")
    fp_pack = os.path.join(DATA_DIR, "indices", bid + ".json")
    for fp in (fp_tmp, fp_pack):
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            pass
    d = _get_json(f"{LEG}/id/{bid}/texto/indice")
    if not d:
        return []
    data = d.get("data")
    bloques = data[0]["bloque"] if isinstance(data, list) else data.get("bloque", [])
    out = [{"id": b["id"], "titulo": (b.get("titulo") or "").strip()} for b in bloques]
    try:
        json.dump(out, open(fp_tmp, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return out

# ---------------------------------------------------------------- texto vigente
def _hoy():
    return datetime.date.today().strftime("%Y%m%d")

def _texto_vigente(xml_str: str):
    try:
        root = ET.fromstring(xml_str)
    except Exception:
        return None, {}
    bloque = root.find(".//bloque")
    if bloque is None:
        return None, {}
    versions = bloque.findall("version")
    if not versions:
        return None, {}
    hoy = _hoy()
    elegido = None
    for v in versions:
        fv = v.get("fecha_vigencia", "")
        if fv and fv <= hoy:
            elegido = v
    if elegido is None:
        elegido = versions[-1]
    partes = []
    for p in elegido.iter("p"):
        cls = p.get("class", "")
        if cls.startswith("nota") or "cita" in cls:
            continue
        txt = "".join(p.itertext()).strip()
        if txt:
            partes.append(txt)
    meta = {
        "titulo_bloque": bloque.get("titulo", ""),
        "fecha_vigencia": elegido.get("fecha_vigencia", ""),
        "norma_version": elegido.get("id_norma", ""),
        "n_versiones": len(versions),
        "normas_todas": [v.get("id_norma", "") for v in versions],
    }
    return "\n".join(partes), meta

# ---------------------------------------------------------------- números en palabras
_SUF = "bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies"
_CARD = {"cero":0,"uno":1,"un":1,"una":1,"dos":2,"tres":3,"cuatro":4,"cinco":5,"seis":6,
 "siete":7,"ocho":8,"nueve":9,"diez":10,"once":11,"doce":12,"trece":13,"catorce":14,
 "quince":15,"dieciseis":16,"diecisiete":17,"dieciocho":18,"diecinueve":19,"veinte":20,
 "veintiuno":21,"veintiun":21,"veintidos":22,"veintitres":23,"veinticuatro":24,
 "veinticinco":25,"veintiseis":26,"veintisiete":27,"veintiocho":28,"veintinueve":29}
_DEC = {"treinta":30,"cuarenta":40,"cincuenta":50,"sesenta":60,"setenta":70,"ochenta":80,"noventa":90}
_CEN = {"cien":100,"ciento":100,"doscientos":200,"trescientos":300,"cuatrocientos":400,
 "quinientos":500,"seiscientos":600,"setecientos":700,"ochocientos":800,"novecientos":900}
_ORD = {"primero":1,"primera":1,"segundo":2,"segunda":2,"tercero":3,"tercera":3,"cuarto":4,
 "quinto":5,"sexto":6,"septimo":7,"octavo":8,"noveno":9,"decimo":10,"undecimo":11,
 "duodecimo":12,"decimotercero":13,"decimocuarto":14,"decimoquinto":15,"decimosexto":16,
 "decimoseptimo":17,"decimoctavo":18,"decimonoveno":19,"vigesimo":20}

def _palabras_num(s: str):
    toks = [t for t in s.split() if t != "y"]
    if not toks:
        return None
    if len(toks) == 1 and toks[0] in _ORD:
        return _ORD[toks[0]]
    total = cur = 0; visto = False
    for t in toks:
        if t == "mil":
            cur = (cur or 1) * 1000; total += cur; cur = 0; visto = True
        elif t in _CEN: cur += _CEN[t]; visto = True
        elif t in _DEC: cur += _DEC[t]; visto = True
        elif t in _CARD: cur += _CARD[t]; visto = True
        elif t in _ORD: cur += _ORD[t]; visto = True
        else:
            return None
    return (total + cur) if visto else None

def _titulo_a_clave(titulo: str):
    tn = _norm(titulo)
    m = re.match(r"^(?:art\w*|articulo)\s+(.*)$", tn)
    if not m:
        return None
    resto = m.group(1).strip()
    suf = ""
    ms = re.search(rf"\b({_SUF})\b", resto)
    if ms:
        suf = ms.group(1); resto = (resto[:ms.start()] + resto[ms.end():]).strip()
    md = re.match(r"^(\d+)$", resto)
    num = int(md.group(1)) if md else _palabras_num(resto)
    if num is None:
        return None
    return (str(num), suf)

_MAPAS = {}
def _mapa_articulos(bid: str):
    if bid in _MAPAS:
        return _MAPAS[bid]
    mp = {}
    for b in _indice(bid):
        k = _titulo_a_clave(b["titulo"])
        if k:
            mp.setdefault((k[0] + " " + k[1]).strip(), b["id"])
    _MAPAS[bid] = mp
    return mp

# ---------------------------------------------------------------- fetch de bloques
def _bloque_cache_path(bid, bloque_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{bid}__{bloque_id}")
    return os.path.join(BLQ_CACHE, safe + ".xml")

def _bloque_cache_get(bid, bloque_id):
    fp = _bloque_cache_path(bid, bloque_id)
    try:
        if time.time() - os.path.getmtime(fp) < TTL_BLOQUE:
            x = open(fp, encoding="utf-8").read()
            return x if "<bloque" in x else None
    except Exception:
        pass
    return None

def _bloque_cache_set(bid, bloque_id, xml):
    try:
        open(_bloque_cache_path(bid, bloque_id), "w", encoding="utf-8").write(xml)
    except Exception:
        pass

def _fetch_first_ok(bid: str, cands, hedge_after=0.4, max_waves=3, timeout=6):
    """(id, xml) de un bloque: caché /tmp -> candidatos en paralelo con hedge."""
    cands = list(dict.fromkeys(cands))
    for c in cands:
        x = _bloque_cache_get(bid, c)
        if x:
            return c, x
    urls = [(c, f"{LEG}/id/{bid}/texto/bloque/{c}") for c in cands]
    with _cf.ThreadPoolExecutor(max_workers=len(urls) * max_waves) as ex:
        pending = {ex.submit(_http, u, "application/xml", timeout): c for c, u in urls}
        waves = 1
        end = time.time() + timeout
        while pending and time.time() < end:
            done, _ = _cf.wait(list(pending), timeout=hedge_after,
                               return_when=_cf.FIRST_COMPLETED)
            for fut in done:
                c = pending.pop(fut)
                st, txt = fut.result()
                if st == 200 and "<bloque" in txt:
                    _bloque_cache_set(bid, c, txt)
                    return c, txt
            if not done and waves < max_waves:
                for c, u in urls:
                    pending[ex.submit(_http, u, "application/xml", timeout)] = c
                waves += 1
    return None, None

def _parse_art(articulo: str):
    a = _norm(articulo)
    a = re.sub(r"^(art\w*|articulo|precepto|num\w*)\s*", "", a).strip()
    m = re.match(rf"(\d+)\s*({_SUF})?", a)
    if not m:
        return (articulo.strip(), None, None)
    num, suf = m.group(1), (m.group(2) or "")
    return ((num + " " + suf).strip(), num, suf)

def _bloque_articulo(bid: str, articulo: str):
    etiqueta, num, suf = _parse_art(articulo)
    if num is None:
        st, txt = _http(f"{LEG}/id/{bid}/texto/bloque/{articulo.strip()}", "application/xml")
        if st == 200 and "<bloque" in txt:
            return articulo.strip(), txt, etiqueta
        return None, None, etiqueta
    cands = [f"a{num}{suf}", f"art{num}{suf}"]
    bloque_id, xml = _fetch_first_ok(bid, cands)
    if xml:
        return bloque_id, xml, etiqueta
    clave = (num + " " + suf).strip()
    bloque_id = _mapa_articulos(bid).get(clave)
    if bloque_id:
        _, txt = _fetch_first_ok(bid, [bloque_id])
        if txt:
            return bloque_id, txt, etiqueta
    return None, None, etiqueta

# ================================================================ API pública
def articulo(ley: str, articulo_num: str) -> str:
    """Texto VIGENTE de un artículo de una ley española (BOE consolidado)."""
    t0 = time.perf_counter()
    r = _resolver_ley(ley)
    if not r:
        return (f"No he identificado la ley «{ley}». Prueba con la sigla (LEC, CP, CC, "
                f"LOPJ...), el número («Ley 1/2000», «LO 1/2025») o el nombre completo.")
    bid, nombre = r
    bloque_id, xml, etiqueta = _bloque_articulo(bid, articulo_num)
    if not xml:
        return (f"No encuentro el artículo {etiqueta} en {nombre} ({bid}). Puede que no "
                f"exista con esa numeración o esté derogado.")
    texto, meta = _texto_vigente(xml)
    if not texto:
        return f"El artículo {etiqueta} de {nombre} no tiene texto vigente (posible derogación)."
    dt = (time.perf_counter() - t0) * 1000
    fv = meta.get("fecha_vigencia", "")
    fv_txt = f" · vigente desde {fv[6:8]}/{fv[4:6]}/{fv[0:4]}" if len(fv) == 8 else ""
    cab = f"【{nombre} — {meta.get('titulo_bloque') or ('Artículo ' + etiqueta)}】{fv_txt}"
    pie = (f"\n\nFuente: BOE consolidado {bid} · redacción vigente dada por "
           f"{_norma_corta(meta.get('norma_version',''))} · "
           f"https://www.boe.es/buscar/act.php?id={bid} · {dt:.0f} ms")
    return cab + "\n\n" + texto + pie

# --------------------------------------------------------------- verificador
_ART_CITA = re.compile(
    r"(art[íi]culos?|arts?\.)\s+"
    r"(\d+(?:\.\d+)?(?:\s*(?:bis|ter|qu[aá]ter|quinquies|sexies))?"
    r"(?:\s*(?:,|;|y|e|a|al)\s*\d+(?:\.\d+)?(?:\s*(?:bis|ter))?)*)", re.IGNORECASE)
_NUM_CITA = re.compile(r"(\d+)(?:\.(\d+))?(?:\s*(bis|ter|qu[aá]ter|quinquies|sexies))?", re.IGNORECASE)
_ATRIB = re.compile(
    r"redacci[óo]n\s+dada\s+por\s+(?:el|la)?\s*"
    r"(Ley Org[áa]nica|Ley|Real Decreto[-\s]?ley|Real Decreto Legislativo|Real Decreto)\s+(\d+/\d{4})",
    re.IGNORECASE)
_NORMA_CITA = re.compile(
    r"(Ley Org[áa]nica|Ley|Real Decreto[-\s]?ley|Real Decreto Legislativo|Real Decreto)\s+(\d+/\d{4})"
    r"(?:,?\s+de\s+\d{1,2}\s+de\s+[a-záéíóú]+)?",
    re.IGNORECASE)

def _ley_en_contexto(frag):
    m = re.match(r"\s*,?\s*(?:de\s+|del\s+)?(?:la\s+|el\s+|los\s+|las\s+)?"
                 r"((?:ley org[áa]nica|ley|real decreto[-\s]?ley|real decreto legislativo|"
                 r"real decreto)\s+\d+/\d{4}(?:,?\s+de\s+\d{1,2}\s+de\s+[a-záéíóú]+)?)", frag, re.I)
    if m:
        r = _resolver_estricto(m.group(1))
        if r:
            return r
    m = re.match(r"\s*,?\s*(?:de|del)\s+(?:la|el|los|las)?\s*([A-Za-zÁÉÍÓÚáéíóúñÑ0-9/ .\-]{2,45})", frag)
    cand = m.group(1) if m else frag[:40]
    cand = re.split(r"[.,;:)\n]| dado | en la | que | conforme| seg[úu]n", cand)[0].strip()
    return _resolver_estricto(cand)

def _extraer_citas(texto):
    citas = []; ultima = None
    for m in _ART_CITA.finditer(texto):
        frag = texto[m.end(): m.end() + 70]
        rl = _ley_en_contexto(frag)
        if rl:
            ultima = rl
        ley = rl or ultima
        atr = _ATRIB.search(texto[m.start(): m.end() + 130])
        atrib = (atr.group(1), atr.group(2)) if atr else None
        _ini = max(0, m.start() - 120)
        if _ini > 0:
            _sp = texto.find(" ", _ini)
            if 0 <= _sp < m.start():
                _ini = _sp + 1
        ctx = re.sub(r"\s+", " ", texto[_ini: m.end() + 150]).strip()
        for nm in _NUM_CITA.finditer(m.group(2)):
            art, apart, suf = nm.group(1), nm.group(2), (nm.group(3) or "")
            citas.append({"art": art, "apart": apart, "suf": suf,
                          "etq": art + (f".{apart}" if apart else "") + (f" {suf}" if suf else ""),
                          "ley": ley, "atrib": atrib, "ctx": ctx,
                          "cita": re.sub(r"\s+", " ", m.group(0) + frag).strip()[:90]})
    normas = list(dict.fromkeys((mm.group(1), mm.group(2), re.sub(r"\s+", " ", mm.group(0)).strip())
                                for mm in _NORMA_CITA.finditer(texto)))
    return citas, normas

_STOP = set("""el la los las un una unos unas de del al a ante bajo con contra desde durante en entre
hacia hasta mediante para por segun sin sobre tras y e o u que se su sus lo le les nos os me mi tu ti
este esta estos estas ese esa esos esas aquel aquella dicho dicha dichos dichas presente presentes
mismo misma mismos mismas cual cuales cuyo cuya cuyos cuyas cuando como donde sera seran son ser esta
estan estara haya habra hara puede pueden podra debe deben debera conforme dispuesto dispone disponen
establece establecido establecen regula regulado regulada regulan articulo articulos ley leyes norma
normas texto legal aplicable prevista previsto materia parte partes accion demanda parrafo apartado
numero letra tenor virtud efectos caso casos anterior citada citado vigente redaccion asimismo ademas
tambien ostenta relativo relativa parte todos toda todas""".split())
_LEY_WORDS_CACHE = None
def _ley_words():
    global _LEY_WORDS_CACHE
    if _LEY_WORDS_CACHE is None:
        base = ("ley organica codigo real decreto legislativo enjuiciamiento civil criminal penal "
                "constitucion espanola estatuto trabajadores general tributaria propiedad horizontal "
                "arrendamientos urbanos rusticos sociedades capital concursal hipotecaria mercantil "
                "procedimiento administrativo comun regimen juridico sector publico")
        _LEY_WORDS_CACHE = {w for w in _norm(base).split() if len(w) >= 4}
    return _LEY_WORDS_CACHE

def _terminos_sig(s):
    return {w for w in _norm(s).split() if len(w) >= 5 and w not in _STOP}

def _rubrica(txt):
    m = re.match(r"\s*art[íi]culo[^.\n]*?\.\s*([^.\n]+)", txt or "", re.I)
    return m.group(1).strip() if m else ""

def verificar(texto: str, incluir_texto: bool = False) -> str:
    """Verifica las citas legales de un escrito contra el BOE (3 detectores:
    inexistente/derogado, reforma mal atribuida, disonancia de contenido)."""
    citas, normas = _extraer_citas(texto)
    if not citas and not normas:
        return "No he detectado citas legales (artículos ni leyes N/AAAA) en el texto."
    vistos = {}; orden = []
    for c in citas:
        key = ((c["ley"][0] if c["ley"] else "?"), c["art"], c["suf"])
        if key not in vistos:
            vistos[key] = c; orden.append(key)

    def traer(key):
        c = vistos[key]
        if not c["ley"]:
            return key, c, None, None
        bid, _ = c["ley"]
        _, xml, _ = _bloque_articulo(bid, c["art"] + (f" {c['suf']}" if c["suf"] else ""))
        if not xml:
            return key, c, None, None
        txt, meta = _texto_vigente(xml)
        return key, c, txt, meta

    with _cf.ThreadPoolExecutor(max_workers=5) as ex:
        resultados = list(ex.map(traer, orden))

    out = [f"VERIFICACIÓN DE CITAS LEGALES — {len(orden)} artículos citados"
           f"{', ' + str(len(normas)) + ' normas mencionadas' if normas else ''}.\n"]
    avisos = []
    for i, (key, c, txt, meta) in enumerate(resultados, 1):
        ley = c["ley"][1] if c["ley"] else "(ley NO identificada)"
        if txt is None:
            out.append(f"[{i}] artículo {c['etq']} — {ley}\n"
                       f"    ❌ NO localizado: puede no existir con esa numeración, estar "
                       f"derogado, o la ley no se identificó. Revísalo.")
            avisos.append(f"art {c['etq']} de {ley}: no localizado / posible cita inexistente")
            continue
        fv = meta.get("fecha_vigencia", "")
        fvs = f"{fv[6:8]}/{fv[4:6]}/{fv[0:4]}" if len(fv) == 8 else "?"
        normav = meta.get("norma_version", "")
        rub = _rubrica(txt)
        cab = f"[{i}] artículo {c['etq']} — {ley}" + (f" · «{rub}»" if rub else "")
        linea = [cab,
                 f"    ✔ existe · vigente desde {fvs} · redacción vigente dada por {_norma_corta(normav)}"]
        if c["atrib"]:
            rango_a, num_a = c["atrib"]
            n_vig, _, _ = _norma_info(normav)
            nums_bloque = {_norma_info(nid)[0] for nid in meta.get("normas_todas", [])}
            if num_a == n_vig:
                linea.append(f"    ✔ atribución correcta: la redacción vigente la dio {rango_a} {num_a}.")
            elif num_a in nums_bloque:
                linea.append(f"    ⚠️ ATRIBUCIÓN: el escrito atribuye la redacción a {rango_a} {num_a}, "
                             f"pero la VIGENTE la dio {_norma_corta(normav)}.")
                avisos.append(f"art {c['etq']} {ley}: redacción vigente de {_norma_corta(normav)}, "
                              f"no de {rango_a} {num_a}")
            else:
                linea.append(f"    ⛔ ATRIBUCIÓN ERRÓNEA: el escrito dice «redacción dada por {rango_a} "
                             f"{num_a}», pero esa norma NO consta entre las que han modificado este "
                             f"artículo. Redacción vigente: {_norma_corta(normav)}.")
                avisos.append(f"art {c['etq']} {ley}: {rango_a} {num_a} NO modificó este artículo "
                              f"(atribución falsa). Vigente: {_norma_corta(normav)}")
        terms = _terminos_sig(c.get("ctx", "")) - _ley_words() - set(_MESES)
        art_norm = _norm(rub + " " + txt[:700])
        solapan = {t for t in terms if t in art_norm}
        if len(terms) >= 3 and not solapan:
            tema = ", ".join(sorted(terms, key=len, reverse=True)[:5])
            linea.append(f"    ⚠️ POSIBLE DISONANCIA DE CONTENIDO: el escrito invoca este artículo a "
                         f"propósito de [{tema}], pero el art. {c['etq']} trata de «{rub or '?'}». "
                         f"Comprueba que citas el artículo correcto.")
            avisos.append(f"art {c['etq']} {ley}: el escrito lo usa para [{tema}] pero el artículo "
                          f"trata de «{rub}» — posible cita equivocada")
        cuerpo = txt if incluir_texto else (txt[:280] + ("…" if len(txt) > 280 else ""))
        linea.append("    Texto vigente: " + re.sub(r"\s+", " ", cuerpo).strip())
        out.append("\n".join(linea))

    if normas:
        out.append("\nNORMAS MENCIONADAS:")
        for rango, num, full in normas:
            r = _resolver_estricto(full)
            det = f"{r[1]} ({r[0]})" if r else "no identificada con seguridad (número ambiguo sin fecha)"
            out.append(f"  {'✔' if r else '❓'} {rango} {num} → {det}")
    if avisos:
        out.append("\n⚠️ POSIBLES ERRORES DETECTADOS AUTOMÁTICAMENTE:")
        out += [f"  · {a}" for a in avisos]
    out.append("\nNota: verifica además que el CONTENIDO de cada artículo respalda lo que "
               "afirma el escrito (materia, umbrales, procedimiento).")
    return "\n".join(out)


# ================================================================ SUMARIO / BÚSQUEDA / ÍTEMS
# (Amplía el motor a "cualquier cosa del BOE": sumario diario completo -secciones
#  I..V-, BORME, búsqueda avanzada de legislación por texto/fecha, lectura de un
#  ítem suelto y barrido de novedades por rango de fechas. Todo con la misma API
#  de datos abiertos, sin clave, y reutilizando _http/_get_json.)
SUM = API + "/boe/sumario"
SUM_BORME = API + "/borme/sumario"

def _lst(x):
    return x if isinstance(x, list) else ([] if x is None else [x])

def _fecha8(s: str) -> str:
    """Normaliza fecha a AAAAMMDD desde AAAAMMDD, AAAA-MM-DD o DD/MM/AAAA."""
    s = (s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return s
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}{int(m.group(2)):02d}{int(m.group(1)):02d}"
    return ""

def _fleg(f8: str) -> str:
    return f"{f8[6:8]}/{f8[4:6]}/{f8[0:4]}" if len(f8) == 8 else (f8 or "")

def _item_url(it: dict) -> str:
    u = it.get("url_html") or it.get("url_xml") or ""
    p = it.get("url_pdf")
    if not u and isinstance(p, dict):
        u = p.get("texto", "")
    return u

def _items_sumario(d: dict):
    """[(sec_cod, sec_nom, dep_nom, epi_nom, item)] recorriendo el árbol del
    sumario del BOE. Defensivo ante item directo en sección/departamento."""
    out = []
    dd = (d or {}).get("data", {})
    sm = dd.get("sumario", dd) if isinstance(dd, dict) else {}
    for di in _lst(sm.get("diario")):
        for sec in _lst(di.get("seccion")):
            scod = str(sec.get("codigo", "")); snom = sec.get("nombre", "")
            for it in _lst(sec.get("item")):
                out.append((scod, snom, "", "", it))
            for dep in _lst(sec.get("departamento")):
                dnom = dep.get("nombre", "")
                for it in _lst(dep.get("item")):
                    out.append((scod, snom, dnom, "", it))
                for ep in _lst(dep.get("epigrafe")):
                    enom = ep.get("nombre", "")
                    for it in _lst(ep.get("item")):
                        out.append((scod, snom, dnom, enom, it))
    return out

def _match_sec(scod: str, filtro: str) -> bool:
    if not filtro:
        return True
    return scod.upper().startswith(filtro.strip().upper())

def _fmt_item(scod: str, it: dict, con_sec=True) -> str:
    ident = it.get("identificador", "?")
    tit = re.sub(r"\s+", " ", (it.get("titulo", "") or "")).strip()
    sec = f"[{scod}] " if (con_sec and scod) else ""
    url = _item_url(it)
    linea = f"· {sec}{ident} — {tit}"
    return linea + (f"\n   {url}" if url else "")

_SEC_SHORT = {"1": "I", "2A": "II-A", "2B": "II-B", "3": "III", "4": "IV", "5A": "V-A", "5B": "V-B"}
_SEC_LABEL = {"1": "I. Disposiciones generales", "2A": "II-A. Nombramientos",
              "2B": "II-B. Oposiciones y concursos", "3": "III. Otras disposiciones",
              "4": "IV. Administración de Justicia", "5A": "V-A. Anuncios · Contratación",
              "5B": "V-B. Anuncios · Otros"}
# Cuántos titulares mostrar por sección en el RESUMEN del día (compacto).
_SEC_TOP = {"1": 15, "2A": 6, "2B": 6, "3": 8, "4": 5}

def sumario(fecha: str, seccion: str = "", contiene: str = "") -> str:
    """Qué se publicó en el BOE de un día (secciones I-V).

    SIN filtro -> RESUMEN COMPACTO del día (recuentos por sección + titulares de
    las secciones sustantivas; los anuncios solo se cuentan): pensado para
    responder rapido en UNA sola llamada, sin leer entradas.
    CON seccion o contiene -> listado detallado de esas entradas (con enlace)."""
    f8 = _fecha8(fecha)
    if not f8:
        return "Indica la fecha (AAAA-MM-DD o DD/MM/AAAA)."
    d = _get_json(f"{SUM}/{f8}")
    if not d or (d.get("status", {}).get("code") not in ("200", 200, None)):
        return f"No hay sumario del BOE para el {_fleg(f8)} (¿festivo/domingo o fecha futura?)."
    items = _items_sumario(d)
    if not items:
        return f"El BOE del {_fleg(f8)} no devolvió disposiciones (¿festivo/domingo?)."
    from collections import Counter, OrderedDict
    cnt = Counter(s for (s, sn, dn, en, it) in items)
    orden = ["1", "2A", "2B", "3", "4", "5A", "5B"]
    resumen = " · ".join(f"{_SEC_SHORT.get(k, k)}:{cnt[k]}"
                         for k in orden if cnt.get(k)) or \
              " · ".join(f"{k}:{cnt[k]}" for k in sorted(cnt))
    idx = f"https://www.boe.es/boe/dias/{f8[:4]}/{f8[4:6]}/{f8[6:8]}/"
    cab = [f"BOE del {_fleg(f8)} — {len(items)} disposiciones. Índice oficial: {idx}",
           f"Por sección: {resumen}"]

    # --- Modo DETALLE (hay filtro): lista las entradas concretas, con enlace ---
    if seccion or contiene:
        qn = _norm(contiene) if contiene else ""
        sel = [(s, it) for (s, sn, dn, en, it) in items if _match_sec(s, seccion)
               and (not qn or qn in _norm(it.get("titulo", "")))]
        if not sel:
            cab.append(f"\n(Sin ítems para seccion={seccion!r} contiene={contiene!r}.)")
            return "\n".join(cab)
        cap = 60
        cab.append(f"\n{len(sel)} ítem(s)" + (f" (muestro {cap})" if len(sel) > cap else "") + ":")
        cab += [_fmt_item(s, it) for (s, it) in sel[:cap]]
        cab.append("\nPara leer una entera: leer_boe con su identificador.")
        return "\n".join(cab)

    # --- Modo RESUMEN (compacto): titulares de I-IV, anuncios solo contados ---
    porsec = OrderedDict()
    for (s, sn, dn, en, it) in items:
        porsec.setdefault(s, []).append(it)
    for code in ["1", "2A", "2B", "3", "4"]:
        its = porsec.get(code)
        if not its:
            continue
        n = _SEC_TOP[code]
        cab.append(f"\n▸ {_SEC_LABEL[code]} ({len(its)}" +
                   (f", muestro {n}" if len(its) > n else "") + "):")
        for it in its[:n]:
            t = re.sub(r"\s+", " ", (it.get("titulo", "") or "")).strip()
            if len(t) > 140:
                t = t[:140] + "…"
            cab.append(f"  · {it.get('identificador', '')} — {t}")
    na, nb = len(porsec.get("5A", [])), len(porsec.get("5B", []))
    if na or nb:
        cab.append(f"\n▸ V. Anuncios ({na + nb}): contratación {na}, otros {nb}. "
                   "(Para verlos: sumario_boe con seccion=\"5\", o filtra con contiene=\"…\".)")
    cab.append("\nEsto ya es el resumen del día: respóndelo directamente. Para el texto de "
               "una entrada concreta usa leer_boe(identificador); para filtrar por materia, "
               "sumario_boe(fecha, seccion, contiene).")
    return "\n".join(cab)

def sumario_borme(fecha: str, contiene: str = "") -> str:
    """Sumario del BORME (Registro Mercantil) de un día: actos inscritos por
    provincia y anuncios. Para el detalle de una entrada usa leer_boe(BORME-A-...)."""
    f8 = _fecha8(fecha)
    if not f8:
        return "Indica la fecha (AAAA-MM-DD o DD/MM/AAAA)."
    d = _get_json(f"{SUM_BORME}/{f8}")
    if not d:
        return f"No hay BORME para el {_fleg(f8)} (¿festivo/domingo o fecha futura?)."
    items = _items_sumario(d)
    if not items:
        return f"El BORME del {_fleg(f8)} no devolvió entradas."
    qn = _norm(contiene) if contiene else ""
    sel = [(s, it) for (s, sn, dn, en, it) in items
           if (not qn or qn in _norm(it.get("titulo", "")))]
    cab = [f"BORME del {_fleg(f8)} — {len(items)} entradas."]
    if not sel:
        cab.append(f"(Sin entradas que contengan {contiene!r}.)")
        return "\n".join(cab)
    cap = 80
    cab.append(f"{len(sel)} entrada(s)" + (f" (muestro {cap})" if len(sel) > cap else "") + ":")
    cab += [_fmt_item(s, it) for (s, it) in sel[:cap]]
    return "\n".join(cab)

def buscar(consulta: str, desde: str = "", hasta: str = "", limite: int = 15) -> str:
    """Búsqueda avanzada de LEGISLACIÓN consolidada por texto del título y rango de
    fechas de publicación. Ordena por más reciente. (Para el texto de un artículo
    concreto usa articulo(); esto es para localizar/listar normas.)"""
    import urllib.parse as _up
    consulta = (consulta or "").strip()
    if not consulta:
        return "Indica qué buscar (texto del título de la norma)."
    qtext = re.sub(r'["\\]', " ", consulta)
    q = {"query": {"query_string": {"query": f"titulo:({qtext})"}},
         "sort": [{"fecha_publicacion": "desc"}]}
    lim = max(1, min(int(limite), 50))
    fetch = min(lim * 4, 100)
    url = f"{LEG}?query={_up.quote(json.dumps(q, ensure_ascii=False))}&limit={fetch}"
    d = _get_json(url)
    rows = d.get("data") if isinstance(d, dict) else None
    if not isinstance(rows, list) or not rows:
        return (f"Sin resultados de legislación para «{consulta}». Prueba con menos "
                "palabras o términos del título de la norma.")
    de, ha = _fecha8(desde), _fecha8(hasta)

    def _ok(x):
        fp = x.get("fecha_publicacion", "") or x.get("fecha_disposicion", "")
        if de and fp and fp < de:
            return False
        if ha and fp and fp > ha:
            return False
        return True

    rows = [x for x in rows if _ok(x)][:lim]
    if not rows:
        return (f"Sin normas para «{consulta}» en ese rango de fechas. Amplía el rango "
                "o quita el filtro de fechas.")
    rango = ""
    if de or ha:
        rango = f" (publicadas {'desde ' + _fleg(de) if de else ''}{' hasta ' + _fleg(ha) if ha else ''})"
    out = [f"{len(rows)} norma(s) para «{consulta}»{rango}, más recientes primero:\n"]
    for i, x in enumerate(rows, 1):
        rango_t = (x.get("rango", {}) or {}).get("texto", "")
        fp = x.get("fecha_publicacion", "")
        out.append(f"{i}. {x.get('titulo', '?')}\n"
                   f"   {rango_t} · {x.get('numero_oficial', '')} · publicada {_fleg(fp)} · "
                   f"{x.get('identificador', '')}\n"
                   f"   {x.get('url_html_consolidada', '')}")
    out.append("\nPara el texto vigente de un artículo: articulo(ley, nº). Para el índice: leer con su ID.")
    return "\n".join(out)

def leer_item(identificador: str, max_chars: int = 6000) -> str:
    """Texto de un ítem suelto del BOE/BORME (anuncio, resolución, edicto,
    nombramiento, disposición) por su identificador (BOE-A-..., BOE-B-..., BORME-...)."""
    ident = (identificador or "").strip().upper()
    m = re.search(r"(BO(?:E|RME)-[A-Z]-\d{4}-\d+)", ident)
    if not m:
        return "Indica un identificador válido (p.ej. BOE-A-2024-10761 o BORME-A-2024-102-03)."
    ident = m.group(1)
    base = "diario_borme" if ident.startswith("BORME") else "diario_boe"
    st, txt = _http(f"https://www.boe.es/{base}/xml.php?id={ident}", "application/xml", timeout=10)
    if st != 200 or "<documento" not in txt:
        return f"No pude leer {ident} (HTTP {st})."
    try:
        root = ET.fromstring(txt)
    except Exception:
        return f"No pude interpretar el XML de {ident}."
    meta = root.find(".//metadatos")

    def _mv(tag):
        e = meta.find(tag) if meta is not None else None
        return (e.text or "").strip() if e is not None and e.text else ""

    titulo = _mv("titulo")
    dept = _mv("departamento")
    rango_t = _mv("rango")
    fpub = _mv("fecha_publicacion") or _mv("fecha_disposicion")
    partes = []
    # OJO: hay <texto> dentro de <analisis>/referencias; el CUERPO es el <texto>
    # hijo DIRECTO de <documento> (no usar .//texto, que coge el primero anidado).
    texto = root.find("texto")
    if texto is not None:
        for p in texto.iter("p"):
            t = "".join(p.itertext()).strip()
            if t:
                partes.append(t)
    cuerpo = "\n".join(partes).strip()
    if not cuerpo:
        cuerpo = "(Sin texto estructurado; consulta el PDF oficial.)"
    if len(cuerpo) > max_chars:
        cuerpo = cuerpo[:max_chars].rstrip() + " […]"
    cab = f"【{ident}】"
    if titulo:
        cab += f" {titulo}"
    meta_l = " · ".join(x for x in [rango_t, dept, ("publicado " + _fleg(fpub)) if fpub else ""] if x)
    pie = f"\n\nFuente: BOE oficial · https://www.boe.es/diario_boe/txt.php?id={ident}"
    return f"{cab}\n{meta_l}\n\n{cuerpo}{pie}"

def _rango_fechas(de: str, ha: str, max_dias: int):
    d0 = datetime.date(int(de[:4]), int(de[4:6]), int(de[6:8]))
    d1 = datetime.date(int(ha[:4]), int(ha[4:6]), int(ha[6:8]))
    if d1 < d0:
        d0, d1 = d1, d0
    out, cur = [], d0
    while cur <= d1 and len(out) < max_dias:
        out.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)
    return out

def novedades(contiene: str, desde: str, hasta: str, seccion: str = "",
              max_dias: int = 31, cap: int = 100) -> str:
    """Barre los sumarios del BOE entre dos fechas (máx 31 días) y devuelve los
    ítems cuyo título contiene el texto/NIF indicado (opcionalmente de una sección).
    Es la vía on-demand para 'vigilar' el BOE sin sistema de alertas."""
    contiene = (contiene or "").strip()
    if not contiene:
        return "Indica el texto o NIF a buscar en las novedades del BOE."
    de, ha = _fecha8(desde), _fecha8(hasta)
    if not de or not ha:
        return "Indica el rango de fechas: desde y hasta (AAAA-MM-DD)."
    fechas = _rango_fechas(de, ha, max_dias)
    qn = _norm(contiene)
    hits = []

    def _un_dia(f8):
        d = _get_json(f"{SUM}/{f8}")
        if not d:
            return f8, []
        res = [(s, it) for (s, sn, dn, en, it) in _items_sumario(d)
               if _match_sec(s, seccion) and qn in _norm(it.get("titulo", ""))]
        return f8, res

    with _cf.ThreadPoolExecutor(max_workers=8) as ex:
        for f8, res in ex.map(_un_dia, fechas):
            for (s, it) in res:
                hits.append((f8, s, it))
    nota = f"BOE del {_fleg(de)} al {_fleg(ha)} ({len(fechas)} días" + \
           (f", tope {max_dias}" if len(fechas) >= max_dias else "") + \
           f") · texto «{contiene}»" + (f" · sección {seccion}" if seccion else "") + ":\n"
    if not hits:
        return nota + "Sin coincidencias en ese periodo."
    hits.sort(key=lambda h: h[0], reverse=True)
    out = [nota + f"{len(hits)} coincidencia(s)" + (f" (muestro {cap})" if len(hits) > cap else "") + ":"]
    for (f8, s, it) in hits[:cap]:
        out.append(f"[{_fleg(f8)}] " + _fmt_item(s, it))
    out.append("\nPara leer una entera: leer_boe con su identificador.")
    return "\n".join(out)
