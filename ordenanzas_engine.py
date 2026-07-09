# -*- coding: utf-8 -*-
"""
Motor de ORDENANZAS Y REGLAMENTOS MUNICIPALES — conector Jurisprudenciator.

Diseño para Vercel (stateless, disco read-only salvo /tmp), espejo de los otros
motores (boe_engine / dgt_engine): lectura EN VIVO de la fuente oficial, catálogo
mínimo empaquetado, caché /tmp con TTL, errores blandos como texto (nunca
excepción al cliente).

Municipios: un ADAPTADOR por municipio registrado en ADAPTADORES. Añadir un
municipio = catálogo en ordenanzas_data/<municipio>.json + adaptador + alias.

MADRID (único cubierto de momento): fuente = Código electrónico AEBOE nº 329
"Normativa del Ayuntamiento de Madrid" (~47 normas CONSOLIDADAS y al día por la
propia AEBOE; la sede del Ayuntamiento —Cibelex— bloquea bots vía Akamai, y el
código AEBOE se sirve desde boe.es, que ya consumimos a diario).
  * Catálogo empaquetado (ordenanzas_data/madrid.json, ~21 KB): búsqueda 0-red.
  * Lectura: el ePub del código (~24 MB) contiene un XHTML limpio por norma
    (<p class="articulo">, <p class="parrafo">...). El servidor soporta HTTP
    Range (206) -> "lazy zip": se baja SOLO el miembro comprimido de la norma
    (~30-150 KB) parseando el central directory del ZIP. Fallback: descarga
    completa del ePub cacheando todas las normas en /tmp de una vez.
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

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "ordenanzas_data")            # read-only (repo)
CACHE_DIR = os.path.join(tempfile.gettempdir(), "ordenanzas-cache")  # /tmp
os.makedirs(CACHE_DIR, exist_ok=True)
TTL = 7 * 24 * 3600  # el código AEBOE se actualiza ~mensualmente

_UA = {"User-Agent": "jurisprudenciator-ordenanzas/1.0"}


# ---------------------------------------------------------------- utilidades
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_STOP = {"para", "como", "este", "esta", "esto", "sobre", "entre", "segun",
         "donde", "cuando", "porque", "desde", "hasta", "ante", "tras", "del",
         "los", "las", "una", "unos", "unas", "con", "por", "que", "de", "la",
         "el", "en", "ordenanza", "ordenanzas", "reglamento", "municipal",
         "municipales", "ayuntamiento"}


def _http(url: str, rng: str = "", timeout: int = 25):
    """GET con Range opcional. Devuelve (status, bytes, headers) o (código, b'', {})."""
    h = dict(_UA)
    if rng:
        h["Range"] = rng
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


# ---------------------------------------------------------------- adaptadores
class _MadridAEBOE:
    """Ordenanzas de MADRID capital desde el Código electrónico AEBOE nº 329."""
    codigo = "madrid"
    nombre = "Madrid"
    aliases = ("madrid", "ayuntamiento de madrid", "madrid capital",
               "villa de madrid", "ciudad de madrid", "madrid espana")

    def __init__(self):
        self._cat = None

    def catalogo(self) -> dict:
        if self._cat is None:
            with open(os.path.join(DATA_DIR, "madrid.json"), encoding="utf-8") as f:
                self._cat = json.load(f)
        return self._cat

    def fuente_corta(self) -> str:
        m = self.catalogo()["meta"]
        return f"texto consolidado AEBOE (Codigo {m['codigo']}, act. {m['actualizado']}) · {m['url']}"

    # -------- búsqueda (0 red: catálogo empaquetado)
    def buscar(self, consulta: str, limite: int) -> list:
        normas = self.catalogo()["normas"]
        q = [w for w in _norm(consulta).split() if w not in _STOP]
        if not q:
            return normas[:max(limite, len(normas)) if limite <= 0 else limite]
        puntuadas = []
        for n in normas:
            texto = _norm(n["titulo"]) + " | " + " | ".join(n["alias"]) + " | " + _norm(n["cat"])
            pts = 0
            for w in q:
                if re.search(rf"\b{re.escape(w)}\b", texto):
                    pts += 3
                elif w in texto:
                    pts += 1
            if pts:
                puntuadas.append((pts, n))
        puntuadas.sort(key=lambda x: -x[0])
        return [n for _, n in puntuadas[:limite]]

    # -------- resolución de una norma concreta
    def resolver(self, ordenanza: str):
        s = (ordenanza or "").strip()
        porid = {n["id"]: n for n in self.catalogo()["normas"]}
        m = re.search(r"(?:conso-)?(\d{4,6})\b", s)
        if m and f"conso-{m.group(1)}" in porid:
            return porid[f"conso-{m.group(1)}"]
        sn = _norm(s)
        for n in self.catalogo()["normas"]:
            if n["ref"] and _norm(n["ref"]) == sn:
                return n
        candidatos = self.buscar(s, 3)
        return candidatos[0] if candidatos else None

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


_MADRID = _MadridAEBOE()
ADAPTADORES = {_MADRID.codigo: _MADRID}


def _resolver_municipio(municipio: str):
    q = _norm(municipio)
    for ad in ADAPTADORES.values():
        if q == ad.codigo or q in (_norm(a) for a in ad.aliases):
            return ad
    for ad in ADAPTADORES.values():  # "ordenanzas de madrid", "madrid (capital)"...
        if re.search(rf"\b{ad.codigo}\b", q):
            return ad
    return None


def _no_cubierto(municipio: str) -> str:
    cubiertos = ", ".join(sorted(a.nombre.upper() for a in ADAPTADORES.values()))
    return (f"Municipio no cubierto (aun): «{(municipio or '').strip()}». Ordenanzas municipales "
            f"disponibles SOLO de: {cubiertos}. Las de otros municipios se publican en el "
            "Boletin Oficial de su PROVINCIA (BOP) y en la web/sede electronica del "
            "ayuntamiento; no las tengo en linea. NO repitas esta llamada: informa al usuario "
            "de donde encontrarla y ofrece normativa estatal (buscar_articulo / buscar_boe) o "
            "jurisprudencia (buscar_sentencias) relacionada.")


# ------------------------------------------------------- parseo del XHTML AEBOE
_P_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.S)
_CLASS_RE = re.compile(r'class="([^"]+)"')
_IDX_RE = re.compile(r'<div id="textoindice">.*?</div>', re.S)
_CORTES = ("articulo", "titulo_num", "titulo_tit", "anexo_tit")  # fin de un artículo


def _texto_plano(frag: str) -> str:
    frag = re.sub(r"<[^>]+>", " ", frag)
    return re.sub(r"\s+", " ", _html.unescape(frag)).replace(" ", " ").strip()


def _bloques(xhtml: str) -> list:
    """[(clase, texto)] del cuerpo de la norma (sin el índice)."""
    cuerpo = _IDX_RE.sub("", xhtml)
    out = []
    for m in _P_RE.finditer(cuerpo):
        cls = (_CLASS_RE.search(m.group(1)) or [None, ""])[1]
        txt = _texto_plano(m.group(2))
        if txt and cls not in ("pub", "dem", "tit", "imagen"):
            out.append((cls, txt))
    return out


def _clave_art(texto: str) -> str:
    """'Articulo 6 bis. Otorgamiento...' -> '6 bis' (normalizado)."""
    t = _norm(texto)
    m = re.match(r"articulo\s+([0-9]+)(?:\s*[.\s]\s*)?(bis|ter|quater|quinquies|sexies)?", t)
    if not m:
        return ""
    return (m.group(1) + (" " + m.group(2) if m.group(2) else "")).strip()


def _extraer_articulo(bloques: list, articulo: str):
    """(rubrica, texto) del artículo pedido, o (None, opciones_cercanas)."""
    a = _norm(re.sub(r"^(art\w*\.?|articulo)\s*", "", articulo.strip(), flags=re.I))
    a = re.sub(r"[.\s]+", " ", a).strip()
    encontrados = []
    for i, (cls, txt) in enumerate(bloques):
        if cls == "articulo":
            encontrados.append((_clave_art(txt), i, txt))
    for clave, i, rubrica in encontrados:
        if clave == a:
            cuerpo = []
            for cls, txt in bloques[i + 1:]:
                if cls in _CORTES or re.match(r"^disposici(on|ones)\b", _norm(txt)):
                    break
                cuerpo.append(txt)
            return rubrica, "\n\n".join(cuerpo)
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


# ================================================================ API pública
def buscar(municipio: str, consulta: str = "", limite: int = 15) -> str:
    t0 = time.perf_counter()
    ad = _resolver_municipio(municipio)
    if not ad:
        return _no_cubierto(municipio)
    try:
        limite = max(1, min(int(limite or 15), 60))
        if not consulta.strip() and limite == 15:
            limite = 60                       # consulta vacia = catalogo entero
        normas = ad.buscar(consulta, limite)
        meta = ad.catalogo()["meta"]
        if not normas:
            todas = ad.catalogo()["normas"]
            cats = sorted({n["cat"] for n in todas})
            return (f"Sin resultados para «{consulta}» en las ordenanzas de {ad.nombre} "
                    f"(catalogo consolidado con las {len(todas)} normas principales del "
                    f"Codigo AEBOE {meta['codigo']}). Prueba con otra materia o pide el "
                    "catalogo entero (consulta vacia). Categorias: " + "; ".join(cats) +
                    ". Si es una norma menor no incluida, estara en sede.madrid.es.")
        lineas = [f"【Ordenanzas y reglamentos de {ad.nombre.upper()}"
                  + (f" — resultados para «{consulta}»】" if consulta.strip() else " — catalogo】")]
        for i, n in enumerate(normas, 1):
            extra = " · ".join(x for x in (n.get("pub", ""), f"ult. mod. {n['mod']}" if n.get("mod") else "") if x)
            lineas.append(f"\n{i}. {n['titulo']}\n   id: {n['id']} · {n['cat']}"
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
        return _no_cubierto(municipio)
    try:
        norma = ad.resolver(ordenanza)
        if not norma:
            return (f"No identifico la ordenanza «{ordenanza}» en {ad.nombre}. Usa el id que "
                    "devuelve buscar_ordenanzas (p.ej. conso-66304), su referencia oficial o "
                    "el titulo; o vuelve a buscar con otra materia.")
        xhtml = ad.texto_xhtml(norma)
        bloques = _bloques(xhtml)
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
        pie = f"\n\nFuente: {ad.fuente_corta()} · {dt:.0f} ms"
        return cab + ("\n" + cab_extra if cab_extra else "") + "\n\n" + texto + pie
    except Exception as e:  # noqa: BLE001
        return f"Error leyendo la ordenanza en {municipio}: {e}"
