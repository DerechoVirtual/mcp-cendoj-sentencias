"""
Servidor MCP - Buscar y leer sentencias del CENDOJ (poderjudicial.es).

Conecta el buscador oficial y GRATUITO de jurisprudencia del Poder Judicial
(CENDOJ) con un cliente MCP (Claude Desktop / Cowork). Ataca los endpoints HTTP
directamente y entrega texto integro + parrafos exactos + ECLI + metadatos.

>> VELOCIDAD: 30 sentencias en ~6 s, sin captchas. <<
El control "Control Descargas masivas" del CENDOJ es POR SESION (no por IP): salta
sobre la 6a-7a descarga de una misma sesion. El motor reparte las descargas entre
varias SESIONES FRESCAS (multi-sesion), de modo que casi nunca aparece; y si
aparece, se ESQUIVA abriendo una sesion nueva y reintentando, sin pausas ni
intervencion del usuario. (`continuar_lectura` queda como fallback historico.)

El CENDOJ es publico: NO tiene login. La sesion (cookie JSESSIONID) se obtiene
sola cargando el buscador, asi que el servidor funciona sin configurar nada.

Extraccion de texto: PyMuPDF (fitz) si esta instalado (mucho mas rapido); si no,
pypdf. Para volumen, usa el modo PARRAFOS (solo los pasajes relevantes).

Configuracion (.env - ver .env.example):
  CENDOJ_COOKIE  (opcional)  JSESSIONID propia.
  DOWNLOAD_DIR   (opcional)  carpeta para guardar PDFs/textos (solo si se pide).
  CENDOJ_BASE    (opcional)  base del buscador.

Herramientas:
  buscar_sentencias(consulta, ...)   -> lista filtrada con metadatos y resumen
  buscar_por_cita(cita)              -> localiza por ECLI o ROJ exacto
  opciones_busqueda(consulta, campo) -> facetas para refinar (anos/ponentes/organos)
  leer_sentencias(seleccion, ...)    -> texto integro o PARRAFOS exactos (sin guardar)
  continuar_lectura(texto)           -> fallback historico (normalmente innecesario)
  estado()                           -> diagnostico
"""

import io
import os
import re
import sys
import time
import datetime as _dt
import html as _html
import logging
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

try:
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
for _n in ("httpx", "httpcore", "pypdf", "fitz"):
    logging.getLogger(_n).setLevel(logging.ERROR)

import httpx
from pypdf import PdfReader
try:
    import fitz  # PyMuPDF (opcional): extraccion ~10x mas rapida que pypdf
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Image

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BASE = os.environ.get("CENDOJ_BASE", "https://www.poderjudicial.es/search").rstrip("/")
COOKIE_ENV = os.environ.get("CENDOJ_COOKIE", "").strip()
_DOM = "www.poderjudicial.es"
# Proxies de salida hacia el CENDOJ. Vacio = conexion directa (IP de Vercel, que el
# CENDOJ bloquea por VOLUMEN con 403). CENDOJ_PROXY admite UNA o VARIAS URLs de proxy
# separadas por coma (p.ej. "http://user:pass@host1:port,http://user:pass@host2:port").
# Se ROTA una al azar en cada sesion -> el volumen se reparte entre muchas IPs y el
# CENDOJ no rate-limita ninguna. Se activa SOLO con la env var.
import random as _random
_PROXIES = [p.strip() for p in os.environ.get("CENDOJ_PROXY", "").split(",") if p.strip()]


def _pick_proxy():
    """Un proxy al azar de la lista (rotacion por sesion), o None si no hay ninguno."""
    return _random.choice(_PROXIES) if _PROXIES else None

# Multi-sesion: el captcha salta sobre la 6a-7a descarga de UNA sesion -> usamos
# 5 por sesion (margen) y varias sesiones frescas en paralelo.
LOTE_SESION = 5
MAX_SESIONES = 8
REINTENTOS_DOC = 3   # esquives de captcha/red por documento antes de rendirse

_default_dir = os.path.join(os.path.expanduser("~"), "Documents", "sentencias-cendoj")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "").strip() or _default_dir
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": f"{BASE}/indexAN.jsp",
           "Accept-Language": "es-ES,es;q=0.9"}
AJAX = {"X-Requested-With": "XMLHttpRequest"}

mcp = FastMCP("Jurisprudenciator")

# --- Estado en memoria -----------------------------------------------------
_client: httpx.Client | None = None         # sesion persistente para BUSCAR
_ultima_busqueda: list[dict] = []
_ultima_consulta: str = ""                   # terminos, para el modo parrafos
_trabajo: dict | None = None                 # fallback de captcha por vision


# =========================================================================
# Sesiones
# =========================================================================
def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(headers=HEADERS, timeout=40.0, follow_redirects=False, proxy=_pick_proxy())
        if COOKIE_ENV:
            _client.cookies.set("JSESSIONID", COOKIE_ENV, domain=_DOM)
    return _client


def _todas_jsid(c) -> list:
    """Todas las cookies JSESSIONID del cliente (puede haber varias por path)."""
    try:
        return [ck for ck in c.cookies.jar if ck.name == "JSESSIONID"]
    except Exception:  # noqa: BLE001
        return []


def _jsid(c) -> str:
    """JSESSIONID de forma SEGURA aunque httpx tenga varias con ese nombre
    (evita 'CookieConflictError: Multiple cookies exist with name=JSESSIONID')."""
    try:
        return c.cookies.get("JSESSIONID") or ""
    except Exception:  # noqa: BLE001
        cks = _todas_jsid(c)
        for ck in cks:
            if (ck.path or "").startswith("/search"):
                return ck.value or ""
        return cks[-1].value if cks else ""


def _dedup_jsid(c) -> None:
    """Deja UNA sola cookie JSESSIONID (la del path /search) para no enviar cookies
    duplicadas al CENDOJ, que responde 403 si recibe varias."""
    cks = _todas_jsid(c)
    if len(cks) <= 1:
        return
    elegida = next((ck for ck in cks if (ck.path or "").startswith("/search")), cks[-1])
    val = elegida.value
    try:
        for ck in cks:
            c.cookies.jar.clear(ck.domain, ck.path, ck.name)
    except Exception:  # noqa: BLE001
        pass
    try:
        c.cookies.set("JSESSIONID", val, domain=_DOM, path="/search")
    except Exception:  # noqa: BLE001
        pass


def _abrir_sesion(c: httpx.Client) -> None:
    c.get(f"{BASE}/indexAN.jsp")
    _dedup_jsid(c)


def _asegurar_sesion(forzar: bool = False) -> httpx.Client:
    c = _get_client()
    _dedup_jsid(c)  # si el cliente global acumuló varias JSESSIONID, deja una sola
    if forzar:
        if COOKIE_ENV:
            c.cookies.set("JSESSIONID", COOKIE_ENV, domain=_DOM)
        else:
            _abrir_sesion(c)
    elif not _jsid(c):
        _abrir_sesion(c)
    return c


def _nueva_sesion(proxy=None, timeout: float = 40.0) -> httpx.Client:
    """Cliente NUEVO con su propia JSESSIONID (contador de captcha a cero).
    proxy: None = conexion DIRECTA (rapida, sin penalizacion); una URL = sale por
    ese proxy (mas lento, solo cuando el CENDOJ bloquea la IP directa con 403).
    El GET de apertura se REINTENTA si el CENDOJ corta la conexion (transitorio):
    un reintento con socket fresco casi siempre lo resuelve. Si agota intentos no
    lanza: el cliente sirve igual y el POST siguiente reabre la sesion."""
    c = httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=False, proxy=proxy)
    if COOKIE_ENV:
        c.cookies.set("JSESSIONID", COOKIE_ENV, domain=_DOM)
    else:
        for _intento in range(3):
            try:
                c.get(f"{BASE}/indexAN.jsp")
                break
            except httpx.TransportError:
                if _intento == 2:
                    break  # agotados: seguimos sin sesion previa (el POST la reabre)
    _dedup_jsid(c)
    return c


# =========================================================================
# Parseo de resultados
# =========================================================================
def _g(blk: str, pat: str, default: str = "") -> str:
    m = re.search(pat, blk, re.DOTALL)
    if not m:
        return default
    s = _html.unescape(m.group(1))
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def _resumen(blk: str) -> tuple[str, bool]:
    """(texto, es_automatico). 'Resumen Automatico:' = snippet rico del texto con los
    terminos (fiable). 'RESUMEN:' = etiqueta editorial (a veces generica)."""
    m = re.search(r'<div class="summary"[^>]*>(.*?)</div>', blk, re.DOTALL)
    if not m:
        return "", False
    raw = m.group(1)
    es_auto = bool(re.match(r"\s*Resumen\s+Autom", raw, re.I))
    mb = re.search(r'<b>(.*?)</b>', raw, re.DOTALL)
    txt = mb.group(1) if mb else raw
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = _html.unescape(txt)
    txt = re.sub(r"^\s*(?:RESUMEN|Resumen\s+Autom[aá]tico)\s*:\s*", "", txt, flags=re.I)
    return re.sub(r"\s+", " ", txt).strip(), es_auto


def _parse_resultados(html: str) -> list[dict]:
    out: list[dict] = []
    for blk in html.split('class="row searchresult doc"')[1:]:
        m = re.search(r'openDocument/([0-9a-fA-F]+)/(\d+)', blk)
        if not m:
            continue
        lis = re.findall(r'<li[^>]*>\s*<b>([^<]+)</b>', blk)
        sala = next((_html.unescape(x).strip() for x in lis
                     if not x.startswith("ECLI")), "")
        res_txt, res_auto = _resumen(blk)
        out.append({
            "hash": m.group(1), "opt": m.group(2),
            "roj": _g(blk, r'data-roj="([^"]*)"'),
            "ecli": _g(blk, r'(ECLI:[A-Z]{2}:[A-Z0-9]+:\d+:\d+A?)'),
            "fechares": _g(blk, r'data-fechares="([^"]*)"'),
            "ref": _g(blk, r'data-reference="([^"]*)"'),
            "sala": sala,
            "municipio": _g(blk, r'Municipio:\s*<b>([^<]*)</b>'),
            "ponente": _g(blk, r'Ponente:\s*<b>([^<]*)</b>'),
            "recurso": _g(blk, r'Recurso:\s*<b>([^<]*)</b>'),
            "resumen": res_txt, "resumen_auto": res_auto,
        })
    seen, uniq = set(), []
    for d in out:
        if d["hash"] not in seen:
            seen.add(d["hash"]); uniq.append(d)
    return uniq


def _fecha_legible(aaaammdd: str) -> str:
    if re.fullmatch(r"\d{8}", aaaammdd or ""):
        return f"{aaaammdd[6:8]}/{aaaammdd[4:6]}/{aaaammdd[0:4]}"
    return aaaammdd or "?"


def _slug(d: dict) -> str:
    base = d.get("roj") or d.get("ecli") or d.get("hash")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_") or d["hash"]


_ORGANOS = {
    "TS": "11|12|13|14|15|16", "TRIBUNAL SUPREMO": "11|12|13|14|15|16",
    "AP": "37", "AUDIENCIA PROVINCIAL": "37", "AUDIENCIAS PROVINCIALES": "37",
    "JURADO": "38", "AN": "22|23|24", "AUDIENCIA NACIONAL": "22|23|24",
    # TSJ = las 3 salas: 31 Civil y Penal · 33 Contencioso-Administrativo · 34 Social.
    # (Sin la 34, el filtro por jurisdiccion SOCIAL sobre TSJ daba CERO y "TSJ" a
    #  secas solo traia civil/penal + contencioso, sin lo laboral.)
    "TSJ": "31|33|34", "TRIBUNAL SUPERIOR DE JUSTICIA": "31|33|34",
    "JPI": "42", "PRIMERA INSTANCIA": "42", "JUZGADO DE PRIMERA INSTANCIA": "42",
    "INSTRUCCION": "41", "JM": "47", "MERCANTIL": "47", "JUZGADO DE LO MERCANTIL": "47",
    "JS": "44", "SOCIAL": "44", "JUZGADO DE LO SOCIAL": "44",
    "JP": "51", "PENAL": "51", "JUZGADO DE LO PENAL": "51",
    "CONTENCIOSO": "45", "MENORES": "53", "VIGILANCIA": "52",
}


def _resolver_organo(v: str) -> str:
    v = (v or "").strip().upper()
    if re.fullmatch(r"[0-9|]+", v):
        return v
    return _ORGANOS.get(v, v)


def _valor_provincia(p: str) -> str:
    return f"{(p or '').strip().upper()}(P)|"


# =========================================================================
# Extraccion de texto y de PARRAFOS exactos
# =========================================================================
def _extraer_texto(pdf_bytes: bytes) -> tuple[str, int]:
    if _HAS_FITZ:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                n = doc.page_count
                texto = "\n".join(p.get_text() for p in doc)
            finally:
                doc.close()
            # normalizar ligaduras (fi/fl) y quitar la marca de agua de paginacion
            # del CENDOJ ("<n> JURISPRUDENCIA") que se cuela en mitad del texto.
            texto = texto.translate({0xFB01: "fi", 0xFB02: "fl"})
            texto = re.sub(r"\s*\b\d+\s+JURISPRUDENCIA\b", " ", texto)
            return texto.strip(), n
        except Exception:  # noqa: BLE001
            pass
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        n = len(reader.pages)
        partes = [(p.extract_text() or "") for p in reader.pages]
        return "\n".join(partes).strip(), n
    except Exception as e:  # noqa: BLE001
        return f"[No se pudo extraer el texto del PDF: {e}]", 0


_STOP = {"para", "como", "este", "esta", "esto", "sobre", "entre", "segun",
         "donde", "cuando", "porque", "desde", "hasta", "ante", "tras", "del",
         "los", "las", "una", "unos", "unas", "con", "por", "que", "the"}

# Inicio/fin de la seccion de razonamiento juridico (para ignorar Antecedentes de
# Hecho y encabezados). Tolerante a tildes y a "F A L L A M O S" espaciado.
_INICIO_FUND = re.compile(
    r"FUNDAMENTOS?\s+DE\s+DERECHO|RAZONAMIENTOS?\s+JUR[IÍ]DICOS|FUNDAMENTOS?\s+JUR[IÍ]DICOS",
    re.I)
_FIN_FUND = re.compile(
    r"\bF\s*A\s*L\s*L\s*A?\s*M?\s*O?\s*S\b|PARTE\s+DISPOSITIVA|\bACUERDA\b|\bACORDAMOS\b",
    re.I)
# Pasajes meramente procesales / de relleno: se despriorizan (no son el fondo).
_RUIDO_PROCESAL = ("nulidad de actuaciones", "indefension", "costas procesales",
                   "admision a tramite", "antecedentes de hecho", "procurador",
                   "tuvo entrada", "suplico", "notifiquese", "diligencia de ordenacion",
                   "acta de la vista", "se tuvo por")
# Ordinales que encabezan cada fundamento (PRIMERO.-, DECIMO, VIGESIMO PRIMERO...),
# para partir el texto en fundamentos COMPLETOS y devolverlos enteros (sin cortes).
_ORDINALES_RE = re.compile(
    r"\b(?:PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|S[EÉ]PTIMO|OCTAVO|NOVENO|"
    r"D[EÉ]CIMO[A-ZÁÉÍÓÚ]*|UND[EÉ]CIMO|DUOD[EÉ]CIMO|VIG[EÉ]SIMO[A-ZÁÉÍÓÚ]*)\s*[.\-º:]",
    re.I)


def _frase_atras(txt: str, pos: int) -> int:
    """Inicio de la frase que contiene pos (tras el ultimo . ; : ! ? + espacio)."""
    ult = 0
    for m in re.finditer(r"[.;:!?]\s", txt[:pos]):
        ult = m.end()
    return ult


def _frase_adelante(txt: str, pos: int) -> int:
    """Fin (inclusive del punto) de la frase que contiene pos."""
    m = re.search(r"[.;:!?]\s", txt[pos:])
    return pos + m.start() + 1 if m else len(txt)


def _extraer_parrafos(texto: str, terminos: str, k: int = 3,
                      max_chars: int = 1800, min_chars: int = 450) -> list[str]:
    """Devuelve los k PASAJES relevantes de tamano ADAPTATIVO: agrupa las apariciones
    de los terminos en 'clusters' y devuelve, por cada uno, el bloque de FRASES
    COMPLETAS que lo cubre (nunca corta a media frase). Ni dos lineas sueltas (se
    expande hasta ~min_chars) ni el fundamento entero con relleno (se recorta en fin
    de frase a ~max_chars). Prioriza la seccion de Fundamentos (penaliza Antecedentes),
    despr ioriza lo procesal y deduplica."""
    txt = re.sub(r"-\n", "", texto)             # une palabras cortadas a fin de linea
    txt = re.sub(r"\s*\n\s*", " ", txt)          # une todas las lineas
    txt = re.sub(r"[ \t]{2,}", " ", txt).strip()
    palabras = [w for w in re.findall(r"\w{4,}", (terminos or "").lower())
                if w not in _STOP]
    if not palabras or len(txt) < 200:
        return []
    pesos = {w: 1.0 + len(w) / 8.0 for w in palabras}   # especificidad por longitud
    low = txt.lower()
    mi = _INICIO_FUND.search(txt)
    ini_fund = mi.start() if mi else 0
    hits = sorted(m.start() for w in palabras for m in re.finditer(re.escape(w), low))
    if not hits:
        return []
    # Agrupar apariciones cercanas (<=350 chars) en un mismo "parrafo relevante".
    clusters = [[hits[0]]]
    for h in hits[1:]:
        if h - clusters[-1][-1] <= 350:
            clusters[-1].append(h)
        else:
            clusters.append([h])
    cand = []
    for cl in clusters:
        ini = _frase_atras(txt, cl[0])
        fin = _frase_adelante(txt, cl[-1])
        guard = 0
        while fin - ini < min_chars and guard < 8:   # MINIMO: que no sean dos lineas
            nf = _frase_adelante(txt, fin + 1)
            if nf > fin:
                fin = nf
            else:
                na = _frase_atras(txt, max(0, ini - 2))
                if na < ini:
                    ini = na
                else:
                    break
            guard += 1
        if fin - ini > max_chars:                    # MAXIMO: ni el fundamento entero
            corte = txt.rfind(". ", ini + int(max_chars * 0.6), ini + max_chars)
            fin = corte + 1 if corte > ini else ini + max_chars
        seg = low[ini:fin]
        distintas = sum(1 for w in set(palabras) if w in seg)
        if not distintas:
            continue
        peso = sum(seg.count(w) * pesos[w] for w in palabras)
        densidad = peso / (len(seg) / 800.0 + 1.0)
        ruido = min(sum(seg.count(r) for r in _RUIDO_PROCESAL), 3)
        score = distintas * distintas * 5.0 + densidad - ruido * 2.0
        if cl[0] < ini_fund:                         # pasaje en Antecedentes de Hecho
            score -= 100.0
        cand.append((score, ini, fin))
    cand.sort(key=lambda x: -x[0])
    elegidas: list[tuple[int, int]] = []
    claves: list[str] = []                       # dedup
    for _, ini, fin in cand:
        if any(not (fin <= a or ini >= b) for a, b in elegidas):
            continue
        clave = re.sub(r"\W+", "", low[ini:fin])[:250]
        if any(SequenceMatcher(None, clave, c).ratio() > 0.7 for c in claves):
            continue
        elegidas.append((ini, fin)); claves.append(clave)
        if len(elegidas) >= k:
            break
    elegidas.sort()
    return [txt[ini:fin].strip() for ini, fin in elegidas]


# =========================================================================
# Descarga (un documento) + guardado/lectura
# =========================================================================
def _intento_descarga(c: httpx.Client, d: dict) -> tuple[str, bytes]:
    """('pdf', bytes) | ('captcha', b'') | ('error', mensaje_bytes)."""
    try:
        r = c.get(f"{BASE}/AN/openDocument/{d['hash']}/{d['opt']}")
    except Exception as e:  # noqa: BLE001
        return "error", f"red: {e}".encode()
    if r.status_code == 200 and r.content[:4] == b"%PDF":
        return "pdf", r.content
    if r.status_code in (301, 302, 303, 307):
        if "captcha" in r.headers.get("location", "").lower():
            return "captcha", b""
        return "error", f"redireccion a {r.headers.get('location','')}".encode()
    return "error", f"HTTP {r.status_code} ({r.headers.get('content-type','')})".encode()


def _construir_registro(d: dict, pdf_bytes: bytes, incluir_texto: bool,
                        guardar_pdf: bool, parrafos: int, terminos: str) -> dict:
    """Extrae texto/parrafos y (si se pide) guarda PDF+TXT. Devuelve el registro."""
    nombre = _slug(d)
    ruta_pdf = ruta_txt = ""
    if guardar_pdf:
        ruta_pdf = os.path.join(DOWNLOAD_DIR, nombre + ".pdf")
        with open(ruta_pdf, "wb") as f:
            f.write(pdf_bytes)
    texto_salida, paginas, n_par = "", 0, 0
    if incluir_texto:
        texto, paginas = _extraer_texto(pdf_bytes)
        if not d.get("ecli"):
            mm = re.search(r"ECLI:[A-Z]{2}:[A-Z0-9]+:\d+:\d+A?\b", texto)
            if mm:
                d["ecli"] = mm.group(0)
        if parrafos and parrafos > 0:
            par = _extraer_parrafos(texto, terminos, parrafos)
            n_par = len(par)
            texto_salida = ("\n\n   [...]\n\n".join(par) if par else
                            "[No se hallaron parrafos con los terminos; pide el texto "
                            "completo con parrafos=0 si lo necesitas.]")
        else:
            texto_salida = texto
        if guardar_pdf:
            ruta_txt = os.path.join(DOWNLOAD_DIR, nombre + ".txt")
            with open(ruta_txt, "w", encoding="utf-8") as f:
                f.write(texto)
    return {"doc": d, "ruta_pdf": ruta_pdf, "ruta_txt": ruta_txt,
            "texto": texto_salida, "paginas": paginas, "n_parrafos": n_par, "ok": True}


# =========================================================================
# MOTOR MULTI-SESION: descarga un lote esquivando captchas por rotacion de sesion
# =========================================================================
def _procesar_shard(shard: list[dict], incluir_texto: bool, guardar_pdf: bool,
                    parrafos: int, terminos: str) -> list[dict]:
    """Descarga los docs de un shard con UNA sesion fresca; si topa captcha o error
    transitorio, rota a otra sesion fresca y reintenta (hasta REINTENTOS_DOC)."""
    c = _nueva_sesion()
    regs, hechos_con_sesion = [], 0
    for d in shard:
        reg = None
        for intento in range(REINTENTOS_DOC):
            if hechos_con_sesion >= LOTE_SESION:        # rota antes de que salte
                c = _nueva_sesion(); hechos_con_sesion = 0
            tipo, payload = _intento_descarga(c, d)
            if tipo == "pdf":
                reg = _construir_registro(d, payload, incluir_texto, guardar_pdf,
                                          parrafos, terminos)
                hechos_con_sesion += 1
                break
            # captcha o error -> sesion nueva y reintento
            c = _nueva_sesion(); hechos_con_sesion = 0
        if reg is None:
            reg = {"doc": d, "ruta_pdf": "", "ruta_txt": "", "paginas": 0,
                   "texto": "", "n_parrafos": 0, "ok": False,
                   "error": "no se pudo tras reintentos (captcha persistente)"}
        regs.append(reg)
    return regs


def _fallo(d: dict, motivo: str) -> dict:
    return {"doc": d, "ruta_pdf": "", "ruta_txt": "", "paginas": 0, "texto": "",
            "n_parrafos": 0, "ok": False, "error": motivo}


def _descargar_lote(docs: list[dict], incluir_texto: bool, guardar_pdf: bool,
                    parrafos: int, terminos: str) -> list[dict]:
    """Pocos docs (<=LOTE_SESION): reutiliza la sesion de busqueda ya caliente (rapido,
    sin abrir sesiones de mas). Volumen: multi-sesion en paralelo (shards). Esquiva
    captchas rotando de sesion. Preserva el orden de 'docs'."""
    if len(docs) <= LOTE_SESION:
        c = _asegurar_sesion()
        regs = []
        for d in docs:
            reg = None
            for _ in range(REINTENTOS_DOC):
                tipo, payload = _intento_descarga(c, d)
                if tipo == "pdf":
                    reg = _construir_registro(d, payload, incluir_texto, guardar_pdf,
                                              parrafos, terminos)
                    break
                c = _nueva_sesion()                       # esquive de captcha/red
            regs.append(reg or _fallo(d, "no se pudo tras reintentos"))
        return regs
    shards = [docs[i:i + LOTE_SESION] for i in range(0, len(docs), LOTE_SESION)]
    workers = min(MAX_SESIONES, max(1, len(shards)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        partes = list(ex.map(
            lambda sh: _procesar_shard(sh, incluir_texto, guardar_pdf, parrafos, terminos),
            shards))
    return [reg for parte in partes for reg in parte]


# =========================================================================
# Formateo de salida
# =========================================================================
def _fmt_resultado(reg: dict) -> str:
    d = reg["doc"]
    cab = [
        f"=== {d.get('roj') or '?'}  |  {d.get('ecli') or 'ECLI ?'} ===",
        f"Fecha: {_fecha_legible(d.get('fechares',''))}"
        + (f"  |  {d['sala']}" if d.get("sala") else "")
        + (f"  |  Ponente: {d['ponente']}" if d.get("ponente") else ""),
    ]
    if d.get("recurso"):
        cab.append(f"N. Recurso: {d['recurso']}")
    if reg.get("ruta_pdf"):
        cab.append(f"PDF: {reg['ruta_pdf']}")
    if reg.get("ruta_txt"):
        cab.append(f"TXT: {reg['ruta_txt']}")
    if reg.get("texto"):
        etq = (f"--- PARRAFOS CLAVE ({reg['n_parrafos']}) ---"
               if reg.get("n_parrafos") else f"--- TEXTO ({reg.get('paginas','?')} pags) ---")
        cab.append(etq)
        cab.append(reg["texto"])
    return "\n".join(cab)


def _seleccionar(seleccion: str, docs: list[dict]) -> list[dict] | str:
    s = (seleccion or "todas").strip().lower()
    if s in ("", "todas", "todo", "all"):
        return list(docs)
    elegidos: list[dict] = []
    if re.search(r"[A-Za-z]{2,5}(?:\s+[A-Za-z]{1,4})?\s*\d+/\d{4}", seleccion):
        for r in seleccion.split(","):
            r_norm = re.sub(r"\s+", " ", r.strip().upper())
            m = next((d for d in docs
                      if re.sub(r"\s+", " ", d.get("roj", "").strip().upper()) == r_norm),
                     None)
            if m:
                elegidos.append(m)
        return elegidos or "No se reconocio ningun ROJ de la lista en la ultima busqueda."
    idxs: list[int] = []
    for tok in s.replace(" ", "").split(","):
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            if a.isdigit() and b.isdigit():
                idxs.extend(range(int(a), int(b) + 1))
        elif tok.isdigit():
            idxs.append(int(tok))
    for i in idxs:
        if 1 <= i <= len(docs):
            elegidos.append(docs[i - 1])
    return elegidos or "Seleccion vacia o fuera de rango. Usa 'todas', '1,3,5' o '1-5'."


# =========================================================================
# Priorizacion por RECENCIA (el CENDOJ no deja ordenar por fecha en origen:
# cualquier valor de 'sort' rompe la busqueda y los parametros de orden se
# ignoran; verificado en vivo). Reordenamos nosotros SIN descartar: recientes
# primero, y las muy antiguas al fondo (se recortan si hay suficientes recientes,
# pero un hito clasico sigue apareciendo mas abajo, no se pierde).
# =========================================================================
def _anio_de(d: dict):
    """Ano (int) de la fecha de resolucion 'AAAAMMDD', o None si no se pudo leer."""
    f = (d.get("fechares") or "").strip()
    return int(f[:4]) if len(f) >= 4 and f[:4].isdigit() else None


def _ordenar_por_fecha(docs: list[dict], anios: int = 7) -> list[dict]:
    """Reordena por RECENCIA conservando la relevancia del CENDOJ dentro de cada
    tramo. Tramos por antiguedad: (0) <= 'anios', (1) hasta 2x'anios', (2) resto y
    sin fecha. Es un sort estable, asi que no altera el orden relativo dentro del
    tramo. 'anios' <= 0 desactiva la reordenacion."""
    if anios <= 0 or not docs:
        return docs
    hoy = _dt.date.today().year

    def tramo(d: dict) -> int:
        a = _anio_de(d)
        if a is None:
            return 1  # sin fecha: tramo medio (no la hundimos del todo)
        edad = hoy - a
        if edad <= anios:
            return 0
        if edad <= anios * 2:
            return 1
        return 2

    return sorted(docs, key=tramo)


# =========================================================================
# Motor de busqueda (con paginacion para >50)
# =========================================================================
def _sanear_texto_cendoj(s: str) -> str:
    """El buscador del CENDOJ se cuelga (timeout) o devuelve HTTP 500 con
    apostrofos en la consulta ("Rob'S", "Jose´S Bar"). Se sustituyen por
    espacio; las comillas DOBLES (frase exacta) se conservan."""
    s = re.sub(r"['‘’‚‛´`]", " ", s or "")
    return re.sub(r"\s{2,}", " ", s).strip()


def _ejecutar_busqueda(data_base: dict, desc: str, maximo: int,
                       anios: int = 7, orden: str = "reciente") -> str:
    global _ultima_busqueda
    if data_base.get("TEXT"):
        data_base = {**data_base, "TEXT": _sanear_texto_cendoj(data_base["TEXT"])}
    c = _asegurar_sesion()
    docs: list[dict] = []
    start, total = 1, None
    reciente = (orden or "reciente").strip().lower() != "relevancia"
    # Con recencia traemos un pool mayor (min 50 = 1 pagina) para que las
    # recientes-relevantes tengan sitio antes de recortar a 'maximo'.
    pool = max(maximo, 50) if reciente else maximo
    while len(docs) < pool:
        data = {**data_base, "start": str(start), "maxresults": "50",
                "recordsPerPage": "50", "sort": ""}
        try:
            r = c.post(f"{BASE}/search.action", data=data, headers=AJAX)
        except Exception as e:  # noqa: BLE001
            return f"Error de red al buscar: {e}"
        if start == 1 and (r.status_code in (301, 302, 303, 307) or (
                "search.action" not in r.text and "searchresult" not in r.text)):
            c = _asegurar_sesion(forzar=True)
            r = c.post(f"{BASE}/search.action", data=data, headers=AJAX)
        if r.status_code != 200:
            return f"Jurisprudenciator respondio HTTP {r.status_code} a la busqueda."
        if "no es valida" in r.text.lower():
            return ("Jurisprudenciator rechazo la busqueda ('La busqueda no es valida'). "
                    "Suele ser por tildes/comillas o un filtro mal puesto: prueba sin "
                    "tildes, sin comillas, o revisa jurisdiccion/provincia.")
        if total is None:
            mt = re.search(r"([\d.]+)\s+resultados", r.text)
            total = mt.group(1) if mt else "?"
        nuevos = _parse_resultados(r.text)
        if not nuevos:
            break
        docs.extend(nuevos)
        if len(nuevos) < 50:
            break
        start += 50
    # dedup global por hash
    seen, uniq = set(), []
    for d in docs:
        if d["hash"] not in seen:
            seen.add(d["hash"]); uniq.append(d)
    if reciente:
        uniq = _ordenar_por_fecha(uniq, anios)  # recientes primero
    docs = uniq[:maximo]
    _ultima_busqueda = docs
    if not docs:
        return (f"Sin resultados para {desc}. Prueba sin tildes, con menos comillas, "
                "cambia la base a 'AN' o relaja los filtros.")
    n_auto = sum(1 for d in docs if d.get("resumen_auto"))
    orden_txt = ("ordenadas por RECIENTES primero (dentro de cada tramo, por relevancia)"
                 if reciente else "ordenadas por RELEVANCIA")
    lineas = [f"{len(docs)} resultados (total en Jurisprudenciator: {total}) para "
              f"{desc}, {orden_txt}:",
              f"{n_auto}/{len(docs)} con 'RESUMEN(auto)' = extracto del texto con tus "
              "terminos (senal de relevancia, fiable para elegir). 'MATERIA' = etiqueta "
              "generica: si es candidata, leela. Prioriza la jurisprudencia RECIENTE y "
              "elige la MAS relevante al caso (no por defecto la #1); recurre a una "
              "antigua solo si es el hito que fija la doctrina. Luego: leer_sentencias "
              "(texto) o leer_sentencias(parrafos=3) para los pasajes exactos.\n"]
    for i, d in enumerate(docs, 1):
        lineas.append(
            f"{i}. {d.get('roj') or '?'}  |  {d.get('ecli') or 'ECLI ?'}  |  "
            f"{_fecha_legible(d.get('fechares',''))}"
            + (f"  |  {d['sala']}" if d.get("sala") else "")
            + (f"  |  Pon: {d['ponente']}" if d.get("ponente") else ""))
        res = d.get("resumen", "")
        if res:
            etq = ("RESUMEN(auto)" if d.get("resumen_auto")
                   else "MATERIA" if (res.isupper() or len(res) < 45) else "RESUMEN")
            lineas.append(f"   {etq}: " + (res[:420] + " [...]" if len(res) > 420 else res))
    return "\n".join(lineas)


# =========================================================================
# HERRAMIENTAS MCP
# =========================================================================
@mcp.tool()
def buscar_sentencias(
    consulta: str, base: str = "TS", maximo: int = 20,
    fecha_desde: str = "", fecha_hasta: str = "", tipo_resolucion: str = "",
    jurisdiccion: str = "", provincia: str = "", tipo_organo: str = "",
    anios: int = 7, orden: str = "reciente",
) -> str:
    """Busca jurisprudencia oficial espanola y devuelve la lista con metadatos y
    resumen. NO descarga. Por defecto PRIORIZA la jurisprudencia RECIENTE:
    afina con los filtros y elige por el RESUMEN(auto), no la #1.

    Args:
        consulta: Texto libre. Comillas = frase exacta. Sensible a tildes (si da 0,
            prueba sin tildes/sin comillas).
        base: "TS" (Supremo) o "AN" (todo: TS, AN, TSJ, AP, juzgados). Con
            provincia/tipo_organo se fuerza "AN".
        maximo: Cuantos resultados traer. Admite >50 (pagina automaticamente).
        fecha_desde / fecha_hasta: dd/mm/aaaa (fecha de resolucion). Filtro DURO:
            usalo si quieres restringir de verdad (p.ej. materias reformadas hace poco).
        tipo_resolucion: "SENTENCIA" o "AUTO".
        jurisdiccion: "CIVIL", "PENAL", "CONTENCIOSO", "SOCIAL", "MILITAR",
            "ESPECIAL", "CONSTITUCIONAL".
        provincia: "Valladolid", "Alicante", "Barcelona"... (implica base AN).
        tipo_organo: "AP", "TS", "TSJ", "JPI", "JM", "JP"... o su codigo oficial.
        anios: Ventana de recencia (por defecto 7). Las de los ultimos 'anios' anos
            se muestran primero y las muy antiguas caen al fondo (no se excluyen).
            Para materias afectadas por reformas recientes, baja a 3-4.
        orden: "reciente" (por defecto, recientes primero) o "relevancia" (orden
            crudo de relevancia, sin priorizar fecha).

    Returns:
        Lista numerada con ROJ, ECLI, fecha, sala, ponente y RESUMEN. Luego usa
        leer_sentencias (texto completo) o leer_sentencias(parrafos=N) (pasajes exactos).
    """
    global _ultima_consulta
    consulta = (consulta or "").strip()
    if not consulta:
        return "Error: la consulta esta vacia."
    _ultima_consulta = consulta
    data = {"action": "query", "databasematch": (base or "TS").strip().upper(),
            "TEXT": consulta}
    if fecha_desde:
        data["FECHARESOLUCIONDESDE"] = fecha_desde
    if fecha_hasta:
        data["FECHARESOLUCIONHASTA"] = fecha_hasta
    if tipo_resolucion:
        data["TIPORESOLUCION"] = tipo_resolucion.strip().upper()
    if jurisdiccion:
        data["JURISDICCION"] = jurisdiccion.strip().upper()
    if tipo_organo:
        cod = _resolver_organo(tipo_organo)
        data["TIPOORGANOPUB"] = cod
        if data["databasematch"] == "TS" and cod != "11|12|13|14|15|16":
            data["databasematch"] = "AN"
    if provincia:
        data["VALUESCOMUNIDAD"] = _valor_provincia(provincia)
        data["databasematch"] = "AN"
    desc = f"{consulta!r} en base {data['databasematch']}"
    if provincia:
        desc += f", provincia {provincia}"
    if tipo_organo:
        desc += f", organo {tipo_organo}"
    return _ejecutar_busqueda(data, desc, max(1, int(maximo)),
                              anios=int(anios), orden=orden)


@mcp.tool()
def buscar_por_cita(cita: str) -> str:
    """Localiza una sentencia por su ECLI o ROJ EXACTO (verificar una cita o abrir una
    resolucion). La deja lista para leer_sentencias.

    Args:
        cita: ECLI ("ECLI:ES:TS:2014:4786") o ROJ ("STS 4786/2014", "SAP VA 1226/2014").
    """
    global _ultima_consulta
    cita = (cita or "").strip()
    if not cita:
        return "Error: indica un ECLI o un ROJ."
    _ultima_consulta = ""
    data = {"action": "query", "databasematch": "AN", "TEXT": ""}
    mE = re.search(r"ECLI:[A-Z]{2}:[A-Z0-9]+:\d+:\d+A?", cita.upper())
    mR = re.search(r"(?<![A-Za-z])[A-Za-z]{2,5}(?:\s+[A-Za-z]{1,4})?\s*\d+/\d{4}",
                   cita.upper())
    if mE:
        data["ECLI"] = mE.group(0); desc = f"ECLI {mE.group(0)}"
    elif mR:
        data["ROJ"] = re.sub(r"\s+", " ", mR.group(0)).strip(); desc = f"ROJ {data['ROJ']}"
    else:
        data["TEXT"] = cita; desc = f"cita {cita!r}"
    return _ejecutar_busqueda(data, desc, 10)


@mcp.tool()
def opciones_busqueda(consulta: str = "", campo: str = "organos", base: str = "AN") -> str:
    """Valores de una faceta para REFINAR la busqueda (organos, anos o ponentes).

    Args:
        consulta: Texto a refinar (opcional).
        campo: "organos", "anos" o "ponentes".
        base: "AN" o "TS".
    """
    field = {"organos": "TIPOORGANOPUB", "órganos": "TIPOORGANOPUB",
             "anos": "ANYO", "años": "ANYO", "ano": "ANYO",
             "ponentes": "PONENTE", "ponente": "PONENTE"}.get(
                 campo.lower().strip(), campo.upper())
    c = _asegurar_sesion()
    data = {"action": "getQueryAllTagValues", "field": field,
            "databasematch": (base or "AN").strip().upper(), "idtab": "jurisprudencia"}
    if consulta:
        data["TEXT"] = consulta
    try:
        r = c.post(f"{BASE}/search.action", data=data, headers=AJAX)
    except Exception as e:  # noqa: BLE001
        return f"Error de red: {e}"
    pares = re.findall(r'value="([^"]*)"[^>]*>([^<]+)<', r.text)
    if not pares:
        return f"Sin valores para el campo {campo!r}."
    vals = [f"{_html.unescape(l).strip()}" + (f"  [cod {v}]" if v != l else "")
            for v, l in pares]
    nota = ""
    if field == "PONENTE" and len(vals) > 60:
        nota = f"\n[... {len(vals)} ponentes; muestro 60. Filtra por nombre.]"
        vals = vals[:60]
    return (f"Valores de '{campo}'" + (f" para {consulta!r}" if consulta else "")
            + f" ({len(pares)}):\n- " + "\n- ".join(vals) + nota)


@mcp.tool(structured_output=False)
def leer_sentencias(seleccion: str = "todas", parrafos: int = 0,
                    terminos: str = "", max_chars: int = 0, guardar_pdf: bool = False):
    """Lee el TEXTO de las sentencias de la ultima busqueda, MUY rapido (multi-sesion:
    ~30 sentencias en pocos segundos, esquivando los captchas sin pausas). Por DEFECTO
    NO guarda nada en el disco (lee en memoria).

    Para VOLUMEN o para 'los parrafos exactos', usa `parrafos=N`: en vez del texto
    integro devuelve solo los N pasajes mas relevantes (los que contienen los terminos),
    rapido y sin saturar. Imprescindible para pedir 'los 5 parrafos clave de 30 sentencias'.

    Args:
        seleccion: "todas" (def.), indices "1,3,5", rango "1-30", o ROJs.
        parrafos: 0 = texto integro. >0 = solo los N parrafos mas relevantes por
            sentencia (recomendado 3-5 para volumen).
        terminos: palabras clave para elegir los parrafos. Vacio = la consulta de la
            ultima busqueda.
        max_chars: si parrafos=0, recorta el texto integro a esta longitud (0 = todo).
        guardar_pdf: False por defecto (no llena el disco). True guarda PDF + TXT.

    Returns:
        Por cada sentencia: ROJ, ECLI, fecha, ponente y los parrafos clave (o el texto).
    """
    global _trabajo
    if not _ultima_busqueda:
        return "No hay busqueda previa. Usa buscar_sentencias o buscar_por_cita."
    sel = _seleccionar(seleccion, _ultima_busqueda)
    if isinstance(sel, str):
        return sel
    if not sel:
        return "No se selecciono ninguna sentencia."
    terms = (terminos or "").strip() or _ultima_consulta
    t0 = time.time()
    regs = _descargar_lote(list(sel), incluir_texto=True, guardar_pdf=bool(guardar_pdf),
                           parrafos=int(parrafos), terminos=terms)
    # recorte de texto integro si procede
    if not parrafos and max_chars:
        for r in regs:
            if r.get("ok") and len(r["texto"]) > max_chars:
                r["texto"] = r["texto"][:max_chars] + f"\n[... recortado a {max_chars} ...]"
    oks = [r for r in regs if r.get("ok")]
    errs = [r for r in regs if not r.get("ok")]
    dt = time.time() - t0
    modo = f"parrafos clave (x{parrafos})" if parrafos else "texto integro"
    cab = f"{len(oks)} sentencia(s) leidas en {dt:.1f}s ({modo})."
    if guardar_pdf:
        cab += f" Guardadas en {DOWNLOAD_DIR}."
    if errs:
        cab += "\n" + f"{len(errs)} con incidencia: " + "; ".join(
            f"{e['doc'].get('roj','?')} ({e.get('error','?')})" for e in errs)
    cuerpo = "\n\n".join(_fmt_resultado(r) for r in oks)
    return cab + ("\n\n" + cuerpo if cuerpo else "")


@mcp.tool(structured_output=False)
def continuar_lectura(texto: str):
    """Fallback historico. Normalmente NO se necesita: el motor esquiva solo las
    comprobaciones de seguridad rotando de sesion, sin pausas. Existe por si en
    algun caso se devolviera una imagen con un codigo.

    Args:
        texto: los caracteres del codigo, si se te muestra una imagen.
    """
    global _trabajo
    texto = (texto or "").strip()
    if not _trabajo or not _trabajo.get("captcha_doc"):
        return ("No hay ninguna comprobacion pendiente (el motor las esquiva sola). "
                "Si querias leer sentencias, usa leer_sentencias.")
    c = _asegurar_sesion()
    d = _trabajo["captcha_doc"]
    try:
        r = c.post(f"{BASE}/contenidos.action", data={
            "action": "captcha", "prevaction": "accessToPDF",
            "nextaction": "accessToPDF", "encode": "true",
            "reference": d["hash"], "optimize": d["opt"], "tab": "AN",
            "embeded": "true", "captcha": texto}, headers=AJAX)
    except Exception as e:  # noqa: BLE001
        return f"Error de red al completar la comprobacion: {e}"
    if r.status_code == 200 and r.content[:4] == b"%PDF":
        reg = _construir_registro(d, r.content, _trabajo.get("incluir_texto", True),
                                  _trabajo.get("guardar_pdf", False),
                                  _trabajo.get("parrafos", 0), _trabajo.get("terminos", ""))
        _trabajo = None
        return "Comprobacion superada.\n\n" + _fmt_resultado(reg)
    return f"El codigo '{texto}' no fue aceptado. Reintenta leer_sentencias."


@mcp.tool()
def estado() -> str:
    """Diagnostico: extractor, sesion, ultima busqueda y carpeta."""
    c = _get_client()
    js = _jsid(c)
    return "\n".join([
        f"Base de jurisprudencia: {BASE}",
        f"Extractor: {'PyMuPDF (rapido)' if _HAS_FITZ else 'pypdf'}",
        f"Multi-sesion: {LOTE_SESION} descargas/sesion, hasta {MAX_SESIONES} sesiones",
        f"Sesion de busqueda: {'activa' if js else 'sin abrir'}",
        f"Cookie manual (.env): {'si' if COOKIE_ENV else 'no (auto)'}",
        f"Ultima busqueda: {len(_ultima_busqueda)} resultados | consulta: {_ultima_consulta!r}",
        f"Carpeta (solo si guardar_pdf=True): {DOWNLOAD_DIR}",
    ])


if __name__ == "__main__":
    mcp.run()
