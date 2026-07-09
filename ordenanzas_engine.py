# -*- coding: utf-8 -*-
"""
Motor de ORDENANZAS Y REGLAMENTOS MUNICIPALES — conector Jurisprudenciator.

Diseño para Vercel (stateless, disco read-only salvo /tmp), espejo de los otros
motores (boe_engine / dgt_engine): lectura EN VIVO de la fuente oficial, catálogo
mínimo empaquetado por municipio, caché /tmp con TTL, errores blandos como texto
(nunca excepción al cliente).

ARQUITECTURA (multi-municipio, mismas 2 tools):
  * AdaptadorBase — catálogo (ordenanzas_data/<municipio>.json), búsqueda por
    scoring de tokens (0 red), resolución de norma (id / referencia / fuzzy).
  * Un adaptador por FUENTE:
      - _MadridAEBOE: Código electrónico AEBOE nº 329 (la sede del Ayuntamiento
        —Cibelex— bloquea bots vía Akamai; el código AEBOE está consolidado y al
        día y se sirve desde boe.es). El ePub trae un XHTML por norma y el
        servidor soporta HTTP Range -> "lazy zip" (~30-150 KB por norma).
      - _ZaragozaAPI: API JSON de la sede (servicio/normativa/<id>.json, campo
        `text` con el articulado en HTML).
      - AdaptadorWeb: genérico por catálogo (url por norma, HTML o PDF).
  * Parsers a BLOQUES [(clase, texto)] comunes: el troceo por artículos, los
    pasajes por términos y el texto íntegro trabajan siempre sobre bloques.

Añadir un municipio = ordenanzas_data/<municipio>.json + (adaptador o config
AdaptadorWeb) + alta en ADAPTADORES. Las tools NO cambian.
"""
import io
import json
import os
import re
import struct
import tempfile
import time
import unicodedata
import urllib.request
import urllib.error
import zipfile
import zlib
import html as _html

try:
    import fitz  # PyMuPDF (ya es dependencia del conector)
    _HAS_FITZ = True
except Exception:  # noqa: BLE001
    _HAS_FITZ = False
try:
    from pypdf import PdfReader
    _HAS_PYPDF = True
except Exception:  # noqa: BLE001
    _HAS_PYPDF = False

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "ordenanzas_data")            # read-only (repo)
CACHE_DIR = os.path.join(tempfile.gettempdir(), "ordenanzas-cache")  # /tmp
os.makedirs(CACHE_DIR, exist_ok=True)
TTL = 7 * 24 * 3600

_UA = {"User-Agent": "Mozilla/5.0 (compatible; jurisprudenciator-ordenanzas/2.0)"}


# ---------------------------------------------------------------- utilidades
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_STOP = {"para", "como", "este", "esta", "esto", "sobre", "entre", "segun",
         "donde", "cuando", "porque", "desde", "hasta", "ante", "tras", "del",
         "los", "las", "una", "unos", "unas", "con", "por", "que", "de", "la",
         "el", "en", "ordenanza", "ordenanzas", "reglamento", "reglamentos",
         "municipal", "municipales", "ayuntamiento"}


def _http(url: str, rng: str = "", timeout: int = 25, accept: str = ""):
    """GET con Range/Accept opcionales. (status, bytes, headers) o (código, b'', {})."""
    h = dict(_UA)
    if rng:
        h["Range"] = rng
    if accept:
        h["Accept"] = accept
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, b"", {}
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e).encode(), {}


def _cache_path(municipio: str, nombre: str) -> str:
    d = os.path.join(CACHE_DIR, municipio)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, re.sub(r"[^A-Za-z0-9_.-]", "_", nombre))


def _cache_get(municipio: str, nombre: str):
    fp = _cache_path(municipio, nombre)
    try:
        if time.time() - os.path.getmtime(fp) < TTL:
            return open(fp, "rb").read()
    except Exception:  # noqa: BLE001
        pass
    return None


def _cache_set(municipio: str, nombre: str, data: bytes):
    try:
        with open(_cache_path(municipio, nombre), "wb") as f:
            f.write(data)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------- lazy ZIP remoto (HTTP Range)
def _zip_dir_remoto(municipio: str, url: str):
    """Central directory del ZIP remoto: {nombre: [metodo, comp_size, offset_local]}.
    Se cachea en /tmp. Lanza excepción si el servidor no coopera (el caller hace
    fallback a descarga completa)."""
    cacheado = _cache_get(municipio, "_zipdir.json")
    if cacheado:
        return json.loads(cacheado.decode("utf-8"))
    st, cola, hdrs = _http(url, rng="bytes=-65536")
    if st == 200:               # el servidor ignoró el Range -> ya tenemos todo
        raise _RangeNoSoportado(cola)
    if st != 206:
        raise RuntimeError(f"HTTP {st} pidiendo la cola del ZIP")
    total = int(hdrs.get("Content-Range", "0/0").split("/")[-1])
    eocd = cola.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise RuntimeError("EOCD no encontrado (¿ZIP?)")
    cd_size, cd_off = struct.unpack("<II", cola[eocd + 12:eocd + 20])
    tail_ini = total - len(cola)
    if cd_off >= tail_ini:
        cd = cola[cd_off - tail_ini:cd_off - tail_ini + cd_size]
    else:
        st2, cd, _ = _http(url, rng=f"bytes={cd_off}-{cd_off + cd_size - 1}")
        if st2 != 206:
            raise RuntimeError(f"HTTP {st2} pidiendo el central directory")
    entradas, pos = {}, 0
    while pos + 46 <= len(cd) and cd[pos:pos + 4] == b"PK\x01\x02":
        metodo, = struct.unpack("<H", cd[pos + 10:pos + 12])
        comp, = struct.unpack("<I", cd[pos + 20:pos + 24])
        nlen, elen, clen = struct.unpack("<HHH", cd[pos + 28:pos + 34])
        off, = struct.unpack("<I", cd[pos + 42:pos + 46])
        nombre = cd[pos + 46:pos + 46 + nlen].decode("utf-8", "replace")
        entradas[nombre] = [metodo, comp, off]
        pos += 46 + nlen + elen + clen
    if not entradas:
        raise RuntimeError("central directory vacío")
    _cache_set(municipio, "_zipdir.json", json.dumps({"total": total, "e": entradas}).encode())
    return {"total": total, "e": entradas}


class _RangeNoSoportado(Exception):
    def __init__(self, cuerpo: bytes):
        super().__init__("range no soportado")
        self.cuerpo = cuerpo


def _zip_miembro_remoto(url: str, entrada, margen: int = 4096) -> bytes:
    """Descarga y descomprime UN miembro del ZIP remoto vía Range."""
    metodo, comp, off = entrada
    fin = off + 30 + 512 + margen + comp          # local header + nombre/extra + datos
    st, raw, _ = _http(url, rng=f"bytes={off}-{fin - 1}")
    if st != 206 or raw[:4] != b"PK\x03\x04":
        raise RuntimeError(f"HTTP {st} pidiendo miembro del ZIP")
    nlen, elen = struct.unpack("<HH", raw[26:30])
    ini = 30 + nlen + elen
    datos = raw[ini:ini + comp]
    if len(datos) < comp:
        raise RuntimeError("miembro incompleto (extra field mayor de lo previsto)")
    if metodo == 8:
        return zlib.decompress(datos, -15)
    if metodo == 0:
        return datos
    raise RuntimeError(f"método de compresión {metodo} no soportado")


# ==================================================================== PARSERS
# Todos producen BLOQUES: lista de (clase, texto) con clases: 'articulo',
# 'titulo_num', 'titulo_tit', 'anexo_tit', 'parrafo' (+ variantes 'parrafo_*').
_CORTES = ("articulo", "titulo_num", "titulo_tit", "anexo_tit")

_P_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.S)
_CLASS_RE = re.compile(r'class="([^"]+)"')
_IDX_RE = re.compile(r'<div id="textoindice">.*?</div>', re.S)


def _texto_plano(frag: str) -> str:
    frag = re.sub(r"<[^>]+>", " ", frag)
    return re.sub(r"\s+", " ", _html.unescape(frag)).strip()  # \s cubre el nbsp


def _bloques_aeboe(xhtml: str) -> list:
    """Bloques desde el XHTML de un Código AEBOE (clases propias del BOE)."""
    cuerpo = _IDX_RE.sub("", xhtml)
    out = []
    for m in _P_RE.finditer(cuerpo):
        cls = (_CLASS_RE.search(m.group(1)) or [None, ""])[1]
        txt = _texto_plano(m.group(2))
        if txt and cls not in ("pub", "dem", "tit", "imagen"):
            out.append((cls, txt))
    return out


_BLOCK_TAGS = re.compile(
    r"</?(?:p|div|li|ul|ol|table|tr|h[1-6]|section|article)\b[^>]*>|<br\s*/?>", re.I)

# Encabezado de artículo al inicio de línea: "Artículo 12.", "Art. 12 bis.-",
# "ARTÍCULO 12:", "Artículo 12º.-", y catalán "ARTICLE 11-1." (número compuesto).
_ART_LINEA = re.compile(
    r"^(?:Art(?:[íi]cul[eo]|[íi]cle)?|ART[IÍ]CUL[EO]|ART[IÍ]CLE)\s*\.?\s*(\d+(?:[-–.]\d+)*)\s*"
    r"(bis|ter|qu[aá]ter|quinquies|sexies)?\s*[ºª]?\s*(?:[.\-–—:]|$)", re.I)
_TIT_LINEA = re.compile(
    r"^(T[IÍ]TU?OL|T[IÍ]TULO|CAP[IÍ]TOL|CAP[IÍ]TULO|SECCI[OÓ]N?|SUBSECCI[OÓ]N?|"
    r"PRE[AÁ]MBUL[OE]|EXPOSICI[OÓ]N?\s+DE\s+MOTI[UV]S?|LIBRO|LLIBRE)\b", re.I)
_DISP_LINEA = re.compile(r"^Disposici(?:o|ó)n?(?:es|ons)?\b", re.I)
_ANEXO_LINEA = re.compile(r"^ANNEX(?:OS)?\b|^ANEXOS?\b", re.I)


def _html_a_texto(htm: str) -> str:
    """HTML genérico -> texto plano con saltos de línea en los límites de bloque."""
    htm = re.sub(r"<(script|style)\b.*?</\1>", " ", htm, flags=re.S | re.I)
    htm = _BLOCK_TAGS.sub("\n", htm)
    htm = re.sub(r"<[^>]+>", " ", htm)
    htm = _html.unescape(htm)
    lineas = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in htm.split("\n")]
    return "\n".join(ln for ln in lineas if ln)


def _bloques_desde_texto(texto: str) -> list:
    """Bloques desde texto plano (HTML convertido o PDF extraído).
    Clasifica cada línea y separa la rúbrica del artículo de su cuerpo cuando
    van en la misma línea ("Artículo 5. Horarios.- El horario será...")."""
    out = []
    for linea in texto.split("\n"):
        linea = linea.strip()
        if not linea:
            continue
        if re.search(r"\.{4,}\s*\d*\s*$", linea):
            continue  # linea de INDICE de PDF ("Articulo 5 .......... 81")
        if _ART_LINEA.match(linea):
            # rúbrica = hasta el primer punto tras el número (+rúbrica corta)
            m = re.match(
                r"^((?:Art(?:[íi]cul[eo]|[íi]cle)?|ART[IÍ]CUL[EO]|ART[IÍ]CLE)\s*\.?\s*\d+(?:[-–.]\d+)*\s*"
                r"(?:bis|ter|qu[aá]ter|quinquies|sexies)?\s*[ºª]?\s*"
                r"(?:[.\-–—:]\s*[^.:]{0,80}?)?\s*[.:])\s*[-–—]?\s*(.*)$", linea, re.I)
            if m and not m.group(2).strip() and len(m.group(1)) > 28:
                # la "rubrica" se trago una clausula entera ("Articulo 3º.- El
                # tipo de gravamen sera:"): re-partir en minimo tras el numero
                m = re.match(
                    r"^((?:Art(?:[íi]cul[eo]|[íi]cle)?|ART[IÍ]CUL[EO]|ART[IÍ]CLE)\s*\.?\s*"
                    r"\d+(?:[-–.]\d+)*\s*(?:bis|ter|qu[aá]ter|quinquies|sexies)?\s*[ºª]?\s*\.?)"
                    r"\s*[-–—]?\s*(.*)$", linea, re.I)
            if m:
                out.append(("articulo", m.group(1).strip()))
                if m.group(2).strip():
                    out.append(("parrafo", m.group(2).strip()))
            else:
                out.append(("articulo", linea))
        elif _TIT_LINEA.match(linea) and len(linea) < 160:
            out.append(("titulo_num", linea))
        elif _DISP_LINEA.match(linea) and len(linea) < 200:
            out.append(("titulo_num", linea))
        elif _ANEXO_LINEA.match(linea) and len(linea) < 160:
            out.append(("anexo_tit", linea))
        else:
            out.append(("parrafo", linea))
    return out


def _reparar_parrafos_pdf(texto: str) -> str:
    """Los PDF salen troceados en LINEAS visuales: re-une cada parrafo (una
    linea se pega a la anterior salvo que esta acabe en puntuacion de cierre o
    que la nueva parezca un encabezado o item de lista)."""
    out = []
    for ln in texto.split("\n"):
        ln = re.sub(r"[ \t\xa0]+", " ", ln).strip()
        if not ln:
            continue
        # ruido tipico de reimpresiones del BOP: cabeceras/pies y nº de pagina
        if re.match(r"^\d{1,3}$", ln) or (len(ln) < 120 and re.search(
                r"Bolet[ií]n Oficial de la provincia|Dep[oó]sito Legal|"
                r"^N[uú]mero\s+\d+\s*$", ln, re.I)):
            continue
        # lineas corruptas (fuentes CID sin unicode en PDFs viejos del BOP):
        # o caracteres raros, o "texto" ASCII sin apenas vocales (cifrado CID)
        raros = sum(1 for c in ln if not (c.isascii() or c in "áéíóúñÁÉÍÓÚÑüÜçÇ¿¡ºª€«»–—·àèìòùÀÈÌÒÙ ï·"))
        if len(ln) > 12 and raros / len(ln) > 0.25:
            continue
        letras = [c for c in ln.lower() if c.isalpha()]
        if len(letras) > 20:
            vocales = sum(1 for c in letras if c in "aeiouáéíóúàèìòùü")
            if vocales / len(letras) < 0.25:
                continue
        es_encabezado = (_ART_LINEA.match(ln) or _TIT_LINEA.match(ln)
                         or _DISP_LINEA.match(ln) or _ANEXO_LINEA.match(ln)
                         or re.match(r"^\d+\s*[.)-]\s", ln) or re.match(r"^[a-z]\)\s", ln, re.I))
        if out and not es_encabezado and not re.search(r"[.:;!?]\s*$", out[-1]) \
                and not (_ART_LINEA.match(out[-1]) and len(out[-1]) < 90):
            if out[-1].endswith("-"):
                out[-1] = out[-1][:-1] + ln          # palabra cortada a fin de linea
            else:
                out[-1] += " " + ln
        else:
            out.append(ln)
    return "\n".join(out)


def _pdf_a_texto(datos: bytes) -> str:
    texto = ""
    if _HAS_FITZ:
        try:
            doc = fitz.open(stream=datos, filetype="pdf")
            try:
                texto = "\n".join(p.get_text() for p in doc)
            finally:
                doc.close()
            texto = texto.translate({0xFB01: "fi", 0xFB02: "fl"}).strip()
        except Exception:  # noqa: BLE001
            texto = ""
    if not texto and _HAS_PYPDF:
        try:
            reader = PdfReader(io.BytesIO(datos))
            texto = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        except Exception as e:  # noqa: BLE001
            return f"[No se pudo extraer el texto del PDF: {e}]"
    if not texto:
        return "[Sin extractor de PDF disponible]"
    return _reparar_parrafos_pdf(texto)


def _clave_art(texto: str) -> str:
    """'Articulo 6 bis. Otorgamiento...' -> '6 bis'; 'ARTICLE 11-1. ...' -> '11 1'."""
    t = _norm(texto)
    m = re.match(r"art(?:iculo|icle|icul)?\s*(\d+(?:\s+\d+)*)\s*(bis|ter|quater|quinquies|sexies)?", t)
    if not m:
        return ""
    num = re.sub(r"\s+", " ", m.group(1)).strip()
    return (num + (" " + m.group(2) if m.group(2) else "")).strip()


def _extraer_articulo(bloques: list, articulo: str):
    """(rubrica, texto) del artículo pedido, o (None, opciones_cercanas)."""
    a = _norm(re.sub(r"^(art\w*\.?|articulo)\s*", "", articulo.strip(), flags=re.I))
    a = re.sub(r"[.\s]+", " ", a).strip()
    encontrados = []
    for i, (cls, txt) in enumerate(bloques):
        if cls == "articulo":
            encontrados.append((_clave_art(txt), i, txt))
    # puede haber VARIAS apariciones del mismo articulo: las del indice/TOC no
    # tienen cuerpo (se descartan) y las de OTRA norma posterior en el mismo
    # documento van despues -> gana la PRIMERA con cuerpo sustancial.
    mejor = None
    for clave, i, rubrica in encontrados:
        if clave == a:
            cuerpo = []
            for cls, txt in bloques[i + 1:]:
                if cls in _CORTES or re.match(r"^disposici(on|ones)\b", _norm(txt)):
                    break
                cuerpo.append(txt)
            candidato = (rubrica, "\n\n".join(cuerpo))
            if len(candidato[1]) >= 150:
                return candidato
            if mejor is None or len(candidato[1]) > len(mejor[1]):
                mejor = candidato
    if mejor is not None:
        return mejor
    cercanos = [r for c, _, r in encontrados
                if c and a and c.split()[0] == a.split()[0]]
    return None, cercanos[:5]


def _extraer_pasajes(bloques: list, terminos: str, k: int) -> str:
    """Los k párrafos más relevantes (con su artículo de contexto), en orden."""
    palabras = [w for w in _norm(terminos).split() if len(w) >= 4 and w not in _STOP]
    ultimo_art, filas = "", []
    for i, (cls, txt) in enumerate(bloques):
        if cls == "articulo":
            ultimo_art = txt
            continue
        if not cls.startswith("parrafo"):
            continue
        tn = _norm(txt)
        pts = sum(3 if re.search(rf"\b{re.escape(w)}\b", tn) else (1 if w in tn else 0)
                  for w in palabras)
        if pts:
            filas.append((pts, i, ultimo_art, txt))
    filas.sort(key=lambda x: -x[0])
    top = sorted(filas[:k], key=lambda x: x[1])   # de nuevo en orden de aparición
    if not top:
        return ""
    salida, vistos_art = [], set()
    for _, _, art, txt in top:
        pre = f"[{art}]\n" if art and art not in vistos_art else ""
        vistos_art.add(art)
        salida.append(pre + txt)
    return "\n\n[...]\n\n".join(salida)


def _texto_integro(bloques: list, max_chars: int) -> str:
    partes = []
    for cls, txt in bloques:
        if cls in ("titulo_num", "titulo_tit", "anexo_tit"):
            partes.append("\n" + txt.upper())
        elif cls == "articulo":
            partes.append("\n" + txt)
        else:
            partes.append(txt)
    texto = "\n\n".join(partes).strip()
    tope = max_chars if max_chars > 0 else 60000
    if len(texto) > tope:
        corte = texto.rfind("\n\n", 0, tope)
        texto = texto[:corte if corte > 0 else tope]
        texto += ("\n\n[TRUNCADO: la norma es más larga. Pide un articulo concreto "
                  "(articulo=\"N\"), usa parrafos=3 + terminos, o sube max_chars.]")
    return texto


def _indice_articulos(bloques: list) -> str:
    filas = [txt for cls, txt in bloques if cls in ("articulo", "titulo_num", "anexo_tit")]
    return "\n".join(filas)


# ================================================================ ADAPTADORES
class AdaptadorBase:
    """Catálogo empaquetado + búsqueda + resolución. Subclases: bloques()."""
    codigo = ""       # clave del registro y nombre del json (ordenanzas_data/)
    nombre = ""       # "Madrid"
    aliases = ()      # formas de nombrar el municipio

    def __init__(self):
        self._cat = None

    def catalogo(self) -> dict:
        if self._cat is None:
            with open(os.path.join(DATA_DIR, self.codigo + ".json"), encoding="utf-8") as f:
                self._cat = json.load(f)
        return self._cat

    def fuente_corta(self) -> str:
        m = self.catalogo()["meta"]
        act = f", act. {m['actualizado']}" if m.get("actualizado") else ""
        return f"{m.get('fuente', 'fuente oficial')}{act} · {m.get('url', '')}".strip(" ·")

    # -------- búsqueda (0 red: catálogo empaquetado)
    def buscar(self, consulta: str, limite: int) -> list:
        normas = self.catalogo()["normas"]
        q = [w for w in _norm(consulta).split() if w not in _STOP]
        if not q:
            return normas[:limite]
        puntuadas = []
        for n in normas:
            principal = _norm(n["titulo"]) + " | " + " | ".join(n.get("alias", []))
            secundario = _norm(n.get("cat", "")) + " | " + " | ".join(n.get("kw", []))
            pts = 0
            for w in q:
                if re.search(rf"\b{re.escape(w)}\b", principal):
                    pts += 3          # titulo/alias mandan
                elif w in principal:
                    pts += 1
                elif w in secundario:
                    pts += 1          # categoria/keywords solo desempatan
            if pts:
                tn = _norm(n["titulo"])
                # las ordenanzas/reglamentos por delante de decretos y tarifas
                if re.match(r"(ordenanza|ordenanca|reglamento|reglament)", tn):
                    pts += 1
                # los reglamentos de ORGANOS (consejos, comisiones...) al final
                if re.search(r"\b(consejo|consell|comision|comissio|observatorio|"
                             r"mesa) (sectorial|asesor|municipal|de)\b", tn):
                    pts -= 2
                puntuadas.append((pts, n))
        # a igualdad de puntos gana el titulo MAS CORTO: la ordenanza principal
        # ("Ordenanza de Movilidad") por delante de tasas de titulo kilometrico
        puntuadas.sort(key=lambda x: (-x[0], len(x[1]["titulo"])))
        return [n for _, n in puntuadas[:limite]]

    # -------- resolución de una norma concreta
    def resolver(self, ordenanza: str):
        s = (ordenanza or "").strip()
        normas = self.catalogo()["normas"]
        porid = {n["id"]: n for n in normas}
        if s in porid:
            return porid[s]
        m = re.search(r"(\d{2,7})", s)
        if m:
            for nid, n in porid.items():
                if nid.split("-")[-1] == m.group(1):
                    return n
        sn = _norm(s)
        for n in normas:
            if n.get("ref") and _norm(n["ref"]) == sn:
                return n
        candidatos = self.buscar(s, 3)
        return candidatos[0] if candidatos else None

    # -------- cada subclase produce los bloques de una norma
    def bloques(self, norma: dict) -> list:
        raise NotImplementedError

    def nota_extra(self, norma: dict) -> str:
        """Texto adicional al pie (p.ej. anexos en PDF)."""
        return ""


class _MadridAEBOE(AdaptadorBase):
    """Ordenanzas de MADRID capital desde el Código electrónico AEBOE nº 329."""
    codigo = "madrid"
    nombre = "Madrid"
    aliases = ("madrid", "ayuntamiento de madrid", "madrid capital",
               "villa de madrid", "ciudad de madrid", "madrid espana")

    def fuente_corta(self) -> str:
        m = self.catalogo()["meta"]
        return f"texto consolidado AEBOE (Codigo {m['codigo']}, act. {m['actualizado']}) · {m['url']}"

    def bloques(self, norma: dict) -> list:
        return _bloques_aeboe(self.texto_xhtml(norma))

    # -------- texto XHTML de una norma (caché -> lazy zip -> ePub completo)
    def texto_xhtml(self, norma: dict) -> str:
        partes, faltan = [], []
        for f in norma["ficheros"]:
            c = _cache_get(self.codigo, os.path.basename(f))
            partes.append(c.decode("utf-8", "replace") if c else None)
            if c is None:
                faltan.append(f)
        if faltan:
            url = self.catalogo()["meta"]["epub_url"]
            try:
                d = _zip_dir_remoto(self.codigo, url)
                for f in faltan:
                    datos = _zip_miembro_remoto(url, d["e"][f])
                    _cache_set(self.codigo, os.path.basename(f), datos)
                    partes[norma["ficheros"].index(f)] = datos.decode("utf-8", "replace")
            except _RangeNoSoportado as e:
                self._cachear_epub_completo(e.cuerpo if len(e.cuerpo) > 1e6 else None)
                return self.texto_xhtml(norma)
            except Exception:  # noqa: BLE001 — fallback: bajar el ePub entero
                self._cachear_epub_completo(None)
                return self.texto_xhtml(norma)
        return "\n".join(p for p in partes if p)

    def _cachear_epub_completo(self, cuerpo):
        url = self.catalogo()["meta"]["epub_url"]
        if cuerpo is None:
            st, cuerpo, _ = _http(url, timeout=60)
            if st != 200:
                raise RuntimeError(f"HTTP {st} descargando el ePub completo")
        z = zipfile.ZipFile(io.BytesIO(cuerpo))
        for n in z.namelist():
            if re.fullmatch(r"OEBPS/conso-\d+(_\d+)?\.xhtml", n):
                _cache_set(self.codigo, os.path.basename(n), z.read(n))


class _ZaragozaAPI(AdaptadorBase):
    """Ordenanzas de ZARAGOZA desde la API JSON de su sede electrónica
    (https://www.zaragoza.es/sede/servicio/normativa/<id>.json, campo `text`)."""
    codigo = "zaragoza"
    nombre = "Zaragoza"
    aliases = ("zaragoza", "ayuntamiento de zaragoza", "zaragoza capital",
               "ciudad de zaragoza")
    _DETALLE = "https://www.zaragoza.es/sede/servicio/normativa/{}.json"

    def bloques(self, norma: dict) -> list:
        nid = norma["id"].split("-")[-1]
        clave = f"det_{nid}.json"
        raw = _cache_get(self.codigo, clave)
        if raw is None:
            st, raw, _ = _http(self._DETALLE.format(nid), accept="application/json")
            if st != 200:
                raise RuntimeError(f"HTTP {st} pidiendo la norma a la sede de Zaragoza")
            _cache_set(self.codigo, clave, raw)
        d = json.loads(raw.decode("utf-8", "replace"))
        htm = d.get("text") or ""
        if len(_texto_plano(htm)) < 200:
            # sin articulado en la ficha: el texto está en un PDF (link o anexo)
            return self._bloques_pdf(norma)
        return _bloques_desde_texto(_html_a_texto(htm))

    def _bloques_pdf(self, norma: dict) -> list:
        if norma.get("url_pdf"):
            url = norma["url_pdf"]
        elif norma.get("anexos"):
            an = norma["anexos"][0]
            url = an["link"] if an["link"].startswith("http") else "https://www.zaragoza.es" + an["link"]
        else:
            raise RuntimeError("la ficha no publica el articulado (ni HTML ni PDF)")
        clave = "pdf_" + os.path.basename(url)
        datos = _cache_get(self.codigo, clave)
        if datos is None:
            st, datos, _ = _http(url, timeout=40)
            if st != 200:
                raise RuntimeError(f"HTTP {st} descargando el PDF de la norma")
            _cache_set(self.codigo, clave, datos)
        return _bloques_desde_texto(_pdf_a_texto(datos))

    def nota_extra(self, norma: dict) -> str:
        anexos = norma.get("anexos") or []
        if not anexos:
            return ""
        filas = [f"- {a['title']}: https://www.zaragoza.es{a['link']}"
                 if not a["link"].startswith("http") else f"- {a['title']}: {a['link']}"
                 for a in anexos[:10]]
        return "\nAnexos (PDF oficiales):\n" + "\n".join(filas)


class _BarcelonaAKN(AdaptadorBase):
    """Ordenanzas de BARCELONA desde el portal Norma (vLex): cada norma expone
    su texto consolidado en XML Akoma Ntoso (…/vid/<vid>/akn). El texto oficial
    del portal esta en CATALAN."""
    codigo = "barcelona"
    nombre = "Barcelona"
    aliases = ("barcelona", "ayuntamiento de barcelona", "ajuntament de barcelona",
               "barcelona capital", "ciudad de barcelona", "bcn")

    def bloques(self, norma: dict) -> list:
        vid = norma["id"].split("-")[-1]
        clave = f"akn_{vid}.xml"
        raw = _cache_get(self.codigo, clave)
        if raw is None:
            for intento in (1, 2):
                st, raw, _ = _http(norma["url"], timeout=30 * intento)
                if st == 200:
                    break
            if st != 200:
                raise RuntimeError(f"HTTP {st} pidiendo la norma al portal juridico")
            _cache_set(self.codigo, clave, raw)
        x = raw.decode("utf-8", "replace")
        m = re.search(r'<block name="main">(.*?)</block>', x, re.S)
        htm = _html.unescape(m.group(1)) if m else _html.unescape(x)
        htm = re.sub(r'<nav class="toc".*?</nav>', " ", htm, flags=re.S)  # fuera el TOC
        return _bloques_desde_texto(_html_a_texto(htm))

    def nota_extra(self, norma: dict) -> str:
        return "\nTexto oficial en catalan (portal juridico municipal): " + norma.get("web", "")


class AdaptadorWeb(AdaptadorBase):
    """Genérico: cada norma del catálogo trae url + formato ('html'|'pdf').
    Para portales que publican el texto por norma en una URL estable."""

    def __init__(self, codigo: str, nombre: str, aliases: tuple):
        super().__init__()
        self.codigo, self.nombre, self.aliases = codigo, nombre, aliases

    def _descargar(self, norma: dict) -> bytes:
        url = norma["url"]
        clave = "doc_" + re.sub(r"[^A-Za-z0-9]+", "_", url)[-80:]
        datos = _cache_get(self.codigo, clave)
        if datos is None:
            for intento in (1, 2):
                st, datos, _ = _http(url, timeout=25 * intento)
                if st == 200:
                    break
            if st != 200:
                raise RuntimeError(f"HTTP {st} descargando la norma de la fuente oficial")
            _cache_set(self.codigo, clave, datos)
        return datos

    @staticmethod
    def _recortar_por_titulo(texto: str, titulo: str) -> str:
        """Algunos ayuntamientos enlazan el BOP ENTERO del dia en vez de la norma
        suelta: si el documento es enorme, saltar a la PRIMERA aparicion del
        titulo pasada la zona de sumario (~6000 chars). Se prueba tambien la
        aguja sin el tipo de norma ("Estatutos de el..." -> "Instituto del...")."""
        if len(texto) < 100_000:
            return texto
        corto = re.sub(r"^(ordenanza|reglamento|estatutos|normas)\s+"
                       r"(municipal(?:es)?\s+)?(reguladora?s?\s+)?(de[l]?\s+|de\s+l[ao]s?\s+)?",
                       "", titulo, flags=re.I).strip()
        for aguja in (titulo[:40].strip(), corto[:40].strip()):
            if len(aguja) < 8:
                continue
            posiciones = [m.start() for m in re.finditer(re.escape(aguja), texto, re.I)]
            cuerpo = [p for p in posiciones if p > 6000]
            if cuerpo:
                texto = texto[cuerpo[0]:]
                # cortar la cola en la siguiente seccion institucional del BOP
                m = re.search(r"\n(OTRAS ENTIDADES|ANUNCIOS PARTICULARES|JUZGADOS DE|"
                              r"MANCOMUNIDAD DE|DIPUTACI[OÓ]N PROVINCIAL|"
                              r"ADMINISTRACI[OÓ]N DE JUSTICIA)\b", texto[1500:])
                if m:
                    texto = texto[:1500 + m.start()]
                return texto
        return texto

    def _texto_de(self, norma: dict, url: str) -> str:
        datos = self._descargar(dict(norma, url=url))
        if norma.get("formato") == "zip" and norma.get("miembro"):
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                datos = z.read(norma["miembro"])
        if norma.get("formato") in ("pdf", "zip") or datos[:5] == b"%PDF-":
            return self._recortar_por_titulo(_pdf_a_texto(datos), norma["titulo"])
        enc = "utf-8"
        m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', datos[:2000])
        if m:
            enc = m.group(1).decode("ascii", "replace")
        htm = datos.decode(enc, "replace")
        rec = self.catalogo()["meta"].get("recorte")
        if rec:
            m2 = re.search(rec, htm, re.S)
            if m2:
                htm = m2.group(1) if m2.groups() else m2.group(0)
        return _html_a_texto(htm)

    def bloques(self, norma: dict) -> list:
        # algunos portales ponen de primer documento una caratula/resumen: si el
        # texto es sospechosamente corto, probamos los siguientes candidatos y
        # nos quedamos con el mas largo.
        candidatas = [norma["url"]] + [u for u in norma.get("urls", []) if u != norma["url"]]
        mejor = ""
        for url in candidatas[:4]:
            try:
                texto = self._texto_de(norma, url)
            except Exception:  # noqa: BLE001
                continue
            if len(texto) > len(mejor):
                mejor = texto
            if len(mejor) > 5000:
                break
        if not mejor:
            raise RuntimeError("no se pudo descargar el texto de la fuente oficial")
        return _bloques_desde_texto(mejor)


_MADRID = _MadridAEBOE()
_ZARAGOZA = _ZaragozaAPI()
_BARCELONA = _BarcelonaAKN()
_VALENCIA = AdaptadorWeb("valencia", "Valencia",
                         ("valencia", "ayuntamiento de valencia", "ajuntament de valencia",
                          "valencia capital", "ciudad de valencia"))
_SEVILLA = AdaptadorWeb("sevilla", "Sevilla",
                        ("sevilla", "ayuntamiento de sevilla", "sevilla capital",
                         "ciudad de sevilla"))
_MALAGA = AdaptadorWeb("malaga", "Malaga",
                       ("malaga", "ayuntamiento de malaga", "malaga capital",
                        "ciudad de malaga"))
_MURCIA = AdaptadorWeb("murcia", "Murcia",
                       ("murcia", "ayuntamiento de murcia", "murcia capital",
                        "ciudad de murcia"))
_PALMA = AdaptadorWeb("palma", "Palma",
                      ("palma", "palma de mallorca", "ajuntament de palma",
                       "ayuntamiento de palma", "ciudad de palma"))
_LASPALMAS = AdaptadorWeb("laspalmas", "Las Palmas de Gran Canaria",
                          ("las palmas", "las palmas de gran canaria", "lpgc",
                           "ayuntamiento de las palmas",
                           "ayuntamiento de las palmas de gran canaria"))
ADAPTADORES = {a.codigo: a for a in (_MADRID, _ZARAGOZA, _BARCELONA, _VALENCIA,
                                     _SEVILLA, _MALAGA, _MURCIA, _PALMA, _LASPALMAS)}

# Cobertura AMPLIA por Boletín Oficial de la Provincia (BOP): cualquier
# ayuntamiento de una provincia cubierta (por ahora Sevilla) se resuelve
# buscando en su BOP en vivo. Motor separado (bop_engine); enrutado abajo.
try:
    import bop_engine as _bop
except Exception:  # noqa: BLE001
    _bop = None


def _resolver_municipio(municipio: str):
    q = _norm(municipio)
    for ad in ADAPTADORES.values():
        if q == ad.codigo or q in (_norm(a) for a in ad.aliases):
            return ad
    for ad in ADAPTADORES.values():  # "ordenanzas de madrid", "madrid (capital)"...
        if re.search(rf"\b{_norm(ad.nombre)}\b", q):
            return ad
    return None


def _no_cubierto(municipio: str) -> str:
    cubiertos = ", ".join(sorted(a.nombre.upper() for a in ADAPTADORES.values()))
    return (f"Municipio no cubierto (aun): «{(municipio or '').strip()}». Cubro las 9 mayores "
            f"ciudades ({cubiertos}) y TODOS los ayuntamientos de la provincia de SEVILLA (via "
            "su Boletin Oficial de la Provincia). Las ordenanzas de otros municipios se publican "
            "en el BOP de su provincia y en la web/sede del ayuntamiento; aun no los tengo. NO "
            "repitas esta llamada: informa de donde encontrarla y ofrece normativa estatal "
            "(buscar_articulo / buscar_boe) o jurisprudencia (buscar_sentencias) relacionada.")


# ================================================================ API pública
def buscar(municipio: str, consulta: str = "", limite: int = 15) -> str:
    t0 = time.perf_counter()
    ad = _resolver_municipio(municipio)
    if not ad:
        if _bop is not None and _bop.provincia_de(municipio):
            r = _bop.buscar(municipio, consulta, limite)   # cobertura por BOP
            if r is not None:
                return r
        return _no_cubierto(municipio)
    try:
        limite = max(1, min(int(limite or 15), 80))
        if not consulta.strip() and limite == 15:
            limite = 80                      # consulta vacia = catalogo entero
        normas = ad.buscar(consulta, limite)
        meta = ad.catalogo()["meta"]
        if not normas:
            todas = ad.catalogo()["normas"]
            cats = sorted({n.get("cat", "") for n in todas if n.get("cat")})
            return (f"Sin resultados para «{consulta}» en las ordenanzas de {ad.nombre} "
                    f"(catalogo con las {len(todas)} normas de {meta.get('fuente', 'la fuente oficial')}). "
                    "Prueba con otra materia o pide el catalogo entero (consulta vacia). "
                    "Categorias: " + "; ".join(cats) +
                    f". Si es una norma menor no incluida, estara en {meta.get('url', 'la web municipal')}.")
        lineas = [f"【Ordenanzas y reglamentos de {ad.nombre.upper()}"
                  + (f" — resultados para «{consulta}»】" if consulta.strip() else " — catalogo】")]
        for i, n in enumerate(normas, 1):
            extra = " · ".join(x for x in (n.get("pub", ""), f"ult. mod. {n['mod']}" if n.get("mod") else "") if x)
            lineas.append(f"\n{i}. {n['titulo']}\n   id: {n['id']}"
                          + (f" · {n['cat']}" if n.get("cat") else "")
                          + (f" · Ref. {n['ref']}" if n.get("ref") else "")
                          + (f"\n   {extra}" if extra else ""))
        dt = (time.perf_counter() - t0) * 1000
        lineas.append(f"\nSiguiente paso: leer_ordenanza(municipio=\"{ad.nombre}\", "
                      "ordenanza=\"<id>\", articulo=\"<num>\") para el texto de un articulo, "
                      "o parrafos=3 + terminos=\"...\" para los pasajes relevantes.")
        lineas.append(f"Fuente: {ad.fuente_corta()} · {dt:.0f} ms")
        return "\n".join(lineas)
    except Exception as e:  # noqa: BLE001
        return f"Error buscando ordenanzas de {municipio}: {e}"


def leer(municipio: str, ordenanza: str, articulo: str = "", parrafos: int = 0,
         terminos: str = "", max_chars: int = 0) -> str:
    t0 = time.perf_counter()
    ad = _resolver_municipio(municipio)
    if not ad:
        if _bop is not None and _bop.provincia_de(municipio):
            r = _bop.leer(municipio, ordenanza, articulo, parrafos, terminos, max_chars)
            if r is not None:
                return r
        return _no_cubierto(municipio)
    try:
        norma = ad.resolver(ordenanza)
        if not norma:
            return (f"No identifico la ordenanza «{ordenanza}» en {ad.nombre}. Usa el id que "
                    "devuelve buscar_ordenanzas, su referencia oficial o el titulo; o vuelve "
                    "a buscar con otra materia.")
        bloques = ad.bloques(norma)
        cab_extra = " · ".join(x for x in (norma.get("pub", ""),
                                           f"Ref. {norma['ref']}" if norma.get("ref") else "",
                                           f"Ultima modificacion: {norma['mod']}" if norma.get("mod") else "") if x)
        rotulo = ""
        if articulo.strip():
            rubrica, cuerpo = _extraer_articulo(bloques, articulo)
            if rubrica is None:
                pista = ("Articulos con ese numero: " + "; ".join(cuerpo)) if cuerpo else \
                        ("Indice de la norma:\n" + _indice_articulos(bloques)[:3000])
                return (f"No encuentro el articulo «{articulo}» en {norma['titulo']} "
                        f"({ad.nombre}). {pista}")
            rotulo, texto = f" — {rubrica}", cuerpo
        elif parrafos and int(parrafos) > 0:
            texto = _extraer_pasajes(bloques, terminos or ordenanza, int(parrafos))
            if not texto:
                return (f"Ningun pasaje de {norma['titulo']} ({ad.nombre}) machea "
                        f"terminos=«{terminos}». Prueba con otras palabras o pide un "
                        "articulo concreto. Indice:\n" + _indice_articulos(bloques)[:3000])
        else:
            texto = _texto_integro(bloques, int(max_chars or 0))
        dt = (time.perf_counter() - t0) * 1000
        cab = f"【{norma['titulo']} — Ayuntamiento de {ad.nombre}{rotulo}】"
        pie = ad.nota_extra(norma)
        pie += f"\n\nFuente: {ad.fuente_corta()} · {dt:.0f} ms"
        return cab + ("\n" + cab_extra if cab_extra else "") + "\n\n" + texto + pie
    except Exception as e:  # noqa: BLE001
        return f"Error leyendo la ordenanza en {municipio}: {e}"
