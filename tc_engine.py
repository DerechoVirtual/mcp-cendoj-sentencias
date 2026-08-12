# -*- coding: utf-8 -*-
"""Motor de jurisprudencia del TRIBUNAL CONSTITUCIONAL (hj.tribunalconstitucional.es).

Fuente oficial: base de doctrina HJ del propio TC (~32.000 resoluciones: STC, ATC
y DTC desde 1980). Verificado en vivo 12-ago-2026: sin captcha, sin rate-limit,
sin bloqueo por User-Agent; fichas con el texto INTEGRO inline en el HTML.

Particularidades del buscador (todas descubiertas empiricamente):
  - Es ASP.NET MVC con token antiforgery: GET /es/Busqueda/Index da cookie+token
    (reutilizables entre busquedas de la misma sesion -> se cachean a nivel de
    modulo con un lock, valen para todo el warm start del lambda).
  - El POST /es/Busqueda/Buscar responde 302 y los resultados quedan en la SESION
    del servidor; se recogen con GET /es/Resolucion/List. Por eso cada busqueda
    completa se hace bajo lock (dos busquedas entrelazadas se pisarian la sesion).
  - BUSQUEDA_LIBRE a secas es rechazado por el ModelState: SIEMPRE debe ir
    acompanado de FECHA_DESDE/FECHA_HASTA (dd/mm/aaaa).

API publica (contrato de los motores auxiliares del conector):
  es_cita(cita)                      -> bool
  localizar(cita)                    -> list[dict] (docs; RuntimeError si red)
  buscar_docs(consulta, ...)         -> list[dict]
  leer_doc(d, parrafos, terminos, max_chars) -> (registro|None, error|None)

Los dicts de documento son compatibles con _formatear_lista/_fmt_resultado del
servidor: roj ("STC 105/2016"), ecli, fechares (AAAAMMDD), sala, ponente,
resumen, recurso; y llevan _motor="tc" + id_hj (id de la ficha).
"""
from __future__ import annotations

import html as _html
import re
import threading
import time

import httpx

BASE = "https://hj.tribunalconstitucional.es"
_TIMEOUT = 8.0
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jurisprudenciator"}

# El texto libre exige rango de fechas (ModelState). Rango total por defecto.
_FECHA_MIN = "01/01/1980"

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
          "noviembre": 11, "diciembre": 12}

# STC 105/2016 | ATC 105/2016 | DTC 1/2004 (tolera "STC nº 105/2016")
_RE_TC_ROJ = re.compile(r"\b([SAD])TC\s*(?:n[ºo.]*\s*)?(\d{1,4})\s*/\s*(\d{4})\b", re.I)
_RE_TC_ECLI = re.compile(r"ECLI:ES:TC:(\d{4}):(\d{1,4})([AD]?)\b", re.I)

_TIPO_LETRA = {"S": "SENTENCIA", "A": "AUTO", "D": "DECLARACION"}
_LETRA_TIPO = {"SENTENCIA": "S", "AUTO": "A", "DECLARACION": "D", "DECLARACIÓN": "D"}


# --------------------------------------------------------------------------
# Sesion (cookie + token antiforgery) cacheada por warm start
# --------------------------------------------------------------------------
# POOL de 3 sesiones independientes: los resultados de una busqueda viven en la
# SESION del servidor (cookie), asi que dos busquedas simultaneas sobre la misma
# cookie se pisan. Con 3 sesiones, la cascada de niveles (sintesis analitica /
# descriptiva / texto completo) se lanza EN PARALELO: 5-6 s -> ~2 s (medido).
_SES_TTL = 600  # 10 min; el token vive mas, pero renovar barato evita sorpresas
_POOL = [{"cliente": None, "token": "", "ts": 0.0, "lock": threading.Lock()}
         for _ in range(4)]
_lock = _POOL[0]["lock"]          # compat: localizar usa el slot 0


def _sesion_fresca() -> tuple[httpx.Client, str]:
    c = httpx.Client(timeout=_TIMEOUT, follow_redirects=False, headers=_UA)
    r = c.get(f"{BASE}/es/Busqueda/Index")
    if r.status_code != 200:
        raise RuntimeError(f"El Tribunal Constitucional respondio HTTP {r.status_code} "
                           "al abrir el buscador. Reintenta en unos minutos.")
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("El buscador del Tribunal Constitucional no devolvio el "
                           "token de sesion. Reintenta en unos minutos.")
    return c, m.group(1)


def _con_sesion(slot: int = 0):
    ses = _POOL[slot]
    if ses["cliente"] is None or time.time() - ses["ts"] > _SES_TTL:
        ses["cliente"], ses["token"] = _sesion_fresca()
        ses["ts"] = time.time()
    return ses["cliente"], ses["token"]


_precalentado = {"hecho": False}


def precalentar() -> None:
    """Abre las 4 sesiones del pool en un hilo daemon (le quita ~1 s a la
    PRIMERA busqueda tras un arranque en frio). Idempotente y best-effort."""
    if _precalentado["hecho"]:
        return
    _precalentado["hecho"] = True

    def _calienta():
        for i, ses in enumerate(_POOL):
            try:
                with ses["lock"]:
                    _con_sesion(i)
            except Exception:  # noqa: BLE001 - el precalentado jamas rompe nada
                pass

    threading.Thread(target=_calienta, daemon=True).start()


precalentar()


def _buscar_form(form: dict, maximo: int, slot: int = 0) -> tuple[list[dict], str]:
    """POST del formulario + GET de la lista, bajo el lock de SU slot de sesion
    (los resultados viven en la sesion del servidor). Devuelve (docs, total)."""
    ses = _POOL[slot]
    with ses["lock"]:
        for intento in range(2):
            try:
                c, token = _con_sesion(slot)
                data = {"__RequestVerificationToken": token,
                        "ResultadosPorPagina": "50", **form}
                r = c.post(f"{BASE}/es/Busqueda/Buscar", data=data,
                           headers={"Referer": f"{BASE}/es/Busqueda/Index"})
                # 302 a /es/Resolucion/List = busqueda aceptada; 302 a Busqueda/Index
                # o 200 = formulario rechazado (p.ej. texto libre sin fechas).
                loc = r.headers.get("location", "")
                if r.status_code != 302 or "Resolucion/List" not in loc:
                    if intento == 0:
                        ses["cliente"] = None  # token/cookie caducados: renovar
                        continue
                    return [], "0"
                # La lista pagina de 10 en 10 (el ResultadosPorPagina del POST se
                # ignora). La pagina 1 dice el total; las siguientes se piden EN
                # PARALELO (la sesion solo LEE el result set ya calculado).
                rl = c.get(f"{BASE}/es/Resolucion/List",
                           params={"sortOrder": "desc", "page": 1})
                if rl.status_code != 200:
                    return [], "0"
                mt = re.search(r"Se han encontrado\s*<b>([\d.]+)</b>", rl.text)
                total = mt.group(1) if mt else "?"
                docs = _parse_lista(rl.text)
                n_total = int(total.replace(".", "")) if total not in ("?", "") else 0
                objetivo = min(maximo, n_total) if n_total else maximo
                if docs and len(docs) < objetivo:
                    paginas = range(2, min(12, (objetivo - 1) // 10 + 1) + 1)
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=3) as ex:
                        partes = list(ex.map(
                            lambda p: c.get(f"{BASE}/es/Resolucion/List",
                                            params={"sortOrder": "desc", "page": p}),
                            paginas))
                    for rp in partes:
                        if rp.status_code == 200:
                            docs.extend(_parse_lista(rp.text))
                if docs:
                    docs[0]["_total"] = total
                return docs[:maximo], total
            except httpx.TransportError:
                ses["cliente"] = None
                if intento == 0:
                    continue
                raise RuntimeError(
                    "Error de red al consultar el Tribunal Constitucional (pasa a "
                    "veces por mantenimiento). Reintenta en unos minutos.")
    return [], "0"


# --------------------------------------------------------------------------
# Parseo de la lista de resultados
# --------------------------------------------------------------------------
def _fechares(dia: int, mes: int, anno: int) -> str:
    return f"{anno:04d}{mes:02d}{dia:02d}"


def _parse_lista(html: str) -> list[dict]:
    """Items: <a class="resolucion-item" href="/es/Resolucion/Show/ID">Sala X.
    SENTENCIA 105/2016, de 6 de junio (BOE ...)</a> + tabla con Tipo de Proceso
    y Sintesis Descriptiva/Analitica."""
    docs: list[dict] = []
    trozos = re.split(r'<a class="resolucion-item"', html)
    for t in trozos[1:]:
        m = re.search(r'href="/es/Resolucion/Show/(\d+)"\s*>([^<]+)</a>', t)
        if not m or m.group(1) == "0":
            continue
        id_hj, titulo = int(m.group(1)), _html.unescape(m.group(2)).strip()
        mt = re.search(r"\b(SENTENCIA|AUTO|DECLARACI\w+)\s+(\d{1,4})/(\d{4})", titulo, re.I)
        if not mt:
            continue
        tipo = mt.group(1).upper()
        letra = _LETRA_TIPO.get(tipo, tipo[:1])
        num, anno = int(mt.group(2)), int(mt.group(3))
        sala = titulo.split(".")[0].strip() if "." in titulo[:30] else ""
        fechares = ""
        mf = re.search(r"de\s+(\d{1,2})\s+de\s+(\w+)", titulo)
        if mf and mf.group(2).lower() in _MESES:
            fechares = _fechares(int(mf.group(1)), _MESES[mf.group(2).lower()], anno)
        # referencia BOE del titulo: "(BOE num. 172 de 16 de julio de 2010)" ->
        # permite leer las sentencias GIGANTES por el BOE (comprimido) en <1 s.
        boe_fecha = ""
        mb = re.search(r"BOE\s+n[uú]m\.\s*\d+\s+de\s+(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})",
                       titulo, re.I)
        if mb and mb.group(2).lower() in _MESES:
            boe_fecha = _fechares(int(mb.group(1)), _MESES[mb.group(2).lower()],
                                  int(mb.group(3)))
        # sintesis (descriptiva + analitica) y tipo de proceso, del bloque extra
        bloque = _html.unescape(re.sub(r"<[^>]+>", "|", t))
        bloque = re.sub(r"\s*\|[\s|]*", "|", bloque)   # "| | " -> "|"
        resumen = ""
        ms = re.search(r"S[ií]ntesis Descriptiva\|([^|]+)", bloque)
        if ms:
            resumen = ms.group(1).strip()
        ma = re.search(r"S[ií]ntesis Anal[ií]tica\|([^|]+)", bloque)
        if ma:
            resumen = (resumen + " — " if resumen else "") + ma.group(1).strip()
        recurso = ""
        mp = re.search(r"Tipo de Proceso\|([^|]+)", bloque)
        if mp:
            recurso = mp.group(1).strip()
        docs.append({
            "roj": f"{letra}TC {num}/{anno}",
            "ecli": f"ECLI:ES:TC:{anno}:{num}" + ("A" if letra == "A" else ""),
            "fechares": fechares, "sala": sala, "ponente": "",
            "resumen": resumen, "recurso": recurso, "boe_fecha": boe_fecha,
            "num": num, "anno": anno,
            "_motor": "tc", "id_hj": id_hj, "tipo": tipo,
            # hash/opt: exigidos por el dedup del servidor; unicos por ficha
            "hash": f"tc-{id_hj}", "opt": "",
        })
    return docs


# --------------------------------------------------------------------------
# API publica
# --------------------------------------------------------------------------
def es_cita(cita: str) -> bool:
    s = (cita or "").upper()
    return bool(_RE_TC_ECLI.search(s) or _RE_TC_ROJ.search(s))


def _partes_cita(cita: str):
    """-> (letra S/A/D o '', numero, anno) o None."""
    s = (cita or "").upper()
    m = _RE_TC_ECLI.search(s)
    if m:
        anno, num, suf = int(m.group(1)), int(m.group(2)), m.group(3)
        return ({"A": "A", "D": "D"}.get(suf, "S"), num, anno)
    m = _RE_TC_ROJ.search(s)
    if m:
        return (m.group(1).upper(), int(m.group(2)), int(m.group(3)))
    return None


def localizar(cita: str) -> list[dict]:
    """Localiza una resolucion del TC por su cita (STC/ATC/DTC n/aaaa o ECLI).
    Coincidencia exacta primero (STC y ATC comparten numero)."""
    p = _partes_cita(cita)
    if not p:
        return []
    letra, num, anno = p
    docs, _ = _buscar_form({"NUMERO_RESOLUCION": str(num),
                            "ANNO_RESOLUCION": str(anno)}, 10)
    exactos = [d for d in docs if d["roj"] == f"{letra}TC {num}/{anno}"]
    return exactos + [d for d in docs if d not in exactos]


_SIN_TILDES = str.maketrans("áéíóúüàèìòùÁÉÍÓÚÜ", "aeiouuaeiouAEIOUU")


def _rerank(docs: list[dict], consulta: str, maximo: int) -> list[dict]:
    """El buscador de HJ devuelve por FECHA desc, lo que ENTIERRA los casos
    lider (STC 140/2016 'tasas judiciales' no entraba en el top-10 por culpa
    de resoluciones recientes que solo citan el termino). Se trae un pool
    mayor y se reordena por relevancia local: terminos de la consulta en la
    sintesis/rubrica (prefijos, sin tildes) + preferencia por SENTENCIAS."""
    if len(docs) <= 1:
        return docs
    total = docs[0].get("_total")
    terms = [w.translate(_SIN_TILDES).lower()[:6]
             for w in re.findall(r"[\wÀ-ſ]{4,}", consulta or "")]
    puntuados = []
    for i, d in enumerate(docs):
        texto = " ".join([d.get("resumen") or "", d.get("recurso") or "",
                          d.get("sala") or ""]).translate(_SIN_TILDES).lower()
        aciertos = sum(1 for t in set(terms) if t in texto)
        score = aciertos * 4.0 + float(d.pop("_bonus", 0.0))
        if d["roj"].startswith("STC"):
            score += 2.0          # la doctrina la fijan las sentencias
        if "PLENO" in (d.get("sala") or "").upper():
            score += 2.0          # los hitos (inconstitucionalidad) son del Pleno
        score -= i * 0.05         # leve respeto al orden (recencia) original
        puntuados.append((score, i, d))
    puntuados.sort(key=lambda x: (-x[0], x[1]))
    out = [d for _, _, d in puntuados[:maximo]]
    for d in out:
        d.pop("_total", None)
    if out and total is not None:
        out[0]["_total"] = total
    return out


def buscar_docs(consulta: str, fecha_desde: str = "", fecha_hasta: str = "",
                tipo_resolucion: str = "", maximo: int = 20) -> list[dict]:
    """Busqueda de texto libre en toda la doctrina del TC (fechas obligatorias
    para el ModelState: si no se dan, rango completo 1980-hoy). Trae un pool
    de hasta 30 y reordena por relevancia (ver _rerank)."""
    hasta_def = time.strftime("31/12/%Y")
    form = {"BUSQUEDA_LIBRE": (consulta or "").strip(),
            "FECHA_DESDE": (fecha_desde or "").strip() or _FECHA_MIN,
            "FECHA_HASTA": (fecha_hasta or "").strip() or hasta_def}
    tr = (tipo_resolucion or "").strip().upper()
    if tr in ("SENTENCIA", "AUTO", "DECLARACION", "DECLARACIÓN"):
        form["TIPO_RESOLUCION"] = "DECLARACION" if tr.startswith("DECLARACI") else tr
    maximo = max(1, int(maximo))
    pool = max(maximo, 30)
    # CASCADA anti-entierro (medida 12-ago-2026): el texto completo (tipo 0)
    # devuelve CIENTOS de resoluciones que solo CITAN el termino y el caso
    # lider queda fuera del pool ('estado de alarma': 103 por fecha desc y la
    # STC 148/2021 enterrada). La SINTESIS ANALITICA (7) y la DESCRIPTIVA (6)
    # solo machean resoluciones que VAN de eso. Se consulta 7 -> 6 -> 0 hasta
    # reunir efectivos, con bonus por nivel para el rerank.
    from concurrent.futures import ThreadPoolExecutor

    def _nivel(args):
        tipo, bonus, slot = args
        f = {**form, "TIPO_BUSQUEDA_LIBRE": str(tipo)}
        # el texto completo (tipo 0) es puro relleno de recencia mas alla de la
        # primera pagina: con 10 basta y ahorra dos GETs
        try:
            nuevos, total = _buscar_form(f, pool if tipo else min(pool, 10),
                                         slot=slot)
        except RuntimeError:
            return [], "0", bonus
        return nuevos, total, bonus

    # Niveles: 7=sintesis analitica, 6=descriptiva, 3=fundamentos, 0=texto
    # completo. OJO: los tipos 6/7 RECHAZAN algunas frases multi-palabra (el
    # servidor parece validarlas contra su tesauro: 'tasas judiciales' y
    # 'plusvalia municipal' rebotan, 'estado de alarma' pasa) -> el nivel 3
    # (fundamentos juridicos) es el paracaidas curado que siempre acepta.
    # DOS OLAS para no pagar 4 busquedas cuando con 2 basta: (7+3) resuelven
    # la mayoria; (6+0) solo si la primera ola quedo corta.
    docs: list[dict] = []
    vistos: set = set()
    total_max = 0

    def _absorber(niveles):
        nonlocal total_max
        for nuevos, total, bonus in niveles:
            try:
                total_max = max(total_max, int(str(total).replace(".", "")))
            except ValueError:
                pass
            for d in nuevos:
                if d["id_hj"] in vistos:
                    continue
                vistos.add(d["id_hj"])
                d["_bonus"] = bonus
                docs.append(d)

    # DOS OLAS de 2 en paralelo. Lanzar los 4 niveles a la vez CONTENDIA en el
    # servidor (los POST pasaban de 1,2 a 2+ s cada uno): con 2+2 el caso comun
    # (la primera ola basta) queda en ~2,4 s y el raro en ~4 s.
    with ThreadPoolExecutor(max_workers=2) as ex:
        _absorber(ex.map(_nivel, [(7, 6.0, 0), (3, 2.5, 2)]))
    # 2a ola SOLO si la curada vino vacia: si sintesis/fundamentos ya traen
    # material, es EL relevante (el texto completo solo anade resoluciones que
    # citan el termino de pasada, y cuesta otros ~2,4 s).
    if not docs:
        with ThreadPoolExecutor(max_workers=2) as ex:
            _absorber(ex.map(_nivel, [(6, 3.0, 1), (0, 0.0, 3)]))
    docs = _rerank(docs, consulta, maximo) if docs else docs
    if docs:
        docs[0]["_total"] = str(total_max or len(docs))
    return docs


# --------------------------------------------------------------------------
# Lectura de la ficha (texto integro inline)
# --------------------------------------------------------------------------
def _texto_ficha(html: str) -> tuple[str, dict]:
    """Extrae el texto de la resolucion (desde el titulo hasta la Ficha Tecnica)
    y metadatos sueltos (ecli, ponente)."""
    meta: dict = {}
    m = re.search(r"ECLI:ES:TC:\d{4}:\d+[AD]?", html)
    if m:
        meta["ecli"] = m.group(0)
    # inicio: el titulo "SENTENCIA 105/2016, de ..." pegado al ECLI del documento
    ini = 0
    for mm in re.finditer(r"(SENTENCIA|AUTO|DECLARACI\w+)\s+\d{1,4}/\d{4}", html):
        ventana = html[mm.start():mm.start() + 600]
        if "ECLI:ES:TC" in ventana:
            ini = mm.start()
            break
    fin = html.find('id="ficha-tecnica"')
    if fin < 0:
        fin = len(html)
    cuerpo = html[ini:fin]
    # etiquetas de bloque -> saltos de parrafo
    cuerpo = re.sub(r"(?i)</(p|div|h\d|li|tr)>", "\n", cuerpo)
    cuerpo = re.sub(r"(?i)<br\s*/?>", "\n", cuerpo)
    cuerpo = re.sub(r"<[^>]+>", " ", cuerpo)
    cuerpo = _html.unescape(cuerpo)
    cuerpo = re.sub(r"[ \t]+", " ", cuerpo)
    cuerpo = re.sub(r" ?\n ?", "\n", cuerpo)
    cuerpo = re.sub(r"\n{3,}", "\n\n", cuerpo).strip()
    mp = re.search(r"Ponente el Magistrad\w+\s+(?:don|doña|dona)?\s*([^,.\n]{4,70})", cuerpo)
    if mp:
        meta["ponente"] = mp.group(1).strip()
    return cuerpo, meta


_lector: dict = {"c": None}


def _cliente_lector() -> httpx.Client:
    """Cliente keep-alive para las lecturas (ahorra el handshake TLS; las fichas
    grandes del Pleno llegan a 1,7 MB sin compresion y cada decima cuenta)."""
    if _lector["c"] is None:
        _lector["c"] = httpx.Client(timeout=_TIMEOUT + 7, headers=_UA,
                                    follow_redirects=False)
    return _lector["c"]


# Umbral de ficha "gigante" (los Plenos gordos llegan a 1,7-5 MB SIN comprimir:
# la STC 31/2010 tardaba 21 s). Por encima, si la resolucion esta en el BOE
# (sentencias y declaraciones), se lee de alli: el BOE si comprime (gzip).
_UMBRAL_GIGANTE = 1_200_000


def _leer_via_boe(d: dict) -> "str | None":
    """Texto integro de una STC/DTC via BOE: sumario del dia -> BOE-A -> txt."""
    fecha = d.get("boe_fecha")
    num, anno = d.get("num"), d.get("anno")
    if not (fecha and num and anno):
        return None
    try:
        r = None
        for _ in range(2):                     # el BOE tiene algun hipo puntual
            r = _cliente_lector().get(
                f"https://www.boe.es/datosabiertos/api/boe/sumario/{fecha}",
                headers={"Accept": "application/json"})
            if r.status_code == 200:
                break
        if r is None or r.status_code != 200:
            return None
        # localizar el item del TC: "...entencia 31/2010..." y su BOE-A cercano
        # (el JSON del BOE escapa las barras: "31\/2010" -> desescapar antes)
        cuerpo = r.text.replace("\\/", "/")
        m = re.search(rf"(?:Sentencia|Declaraci[oó]n)\s+{num}/{anno}\b", cuerpo)
        if not m:
            return None
        ventana = cuerpo[max(0, m.start() - 2500):m.start() + 2500]
        mid = re.search(r'"(BOE-A-\d{4}-\d+)"', ventana)
        if not mid:
            return None
        rt = _cliente_lector().get(
            f"https://www.boe.es/diario_boe/txt.php?id={mid.group(1)}")
        if rt.status_code != 200 or len(rt.text) < 5000:
            return None
        t = re.sub(r"(?i)</(p|div|h\d|li|tr)>", "\n", rt.text)
        t = re.sub(r"(?i)<br\s*/?>", "\n", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = _html.unescape(t)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r" ?\n ?", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()
    except httpx.TransportError:
        return None


def leer_doc(d: dict, parrafos: int = 0, terminos: str = "", max_chars: int = 0):
    """Lee la ficha Show/{id_hj}. Devuelve (registro, None) o (None, motivo)."""
    import server as _srv  # _extraer_parrafos (mismo extractor que el CENDOJ)
    id_hj = d.get("id_hj")
    if not id_hj:
        return None, "documento del TC sin id de ficha"
    texto, meta = None, {}
    try:
        gigante = False
        with _cliente_lector().stream(
                "GET", f"{BASE}/es/Resolucion/Show/{id_hj}") as r:
            if r.status_code != 200:
                return None, f"HTTP {r.status_code} del Tribunal Constitucional"
            largo = int(r.headers.get("content-length") or 0)
            if largo > _UMBRAL_GIGANTE and d.get("boe_fecha"):
                gigante = True                 # gigante y esta en el BOE: por alli
            else:
                cuerpo = r.read().decode("utf-8", "replace")
                texto, meta = _texto_ficha(cuerpo)
        if gigante:
            texto = _leer_via_boe(d)
            if texto is None:                  # el BOE fallo: descarga completa
                r2 = _cliente_lector().get(f"{BASE}/es/Resolucion/Show/{id_hj}",
                                           timeout=60)
                if r2.status_code != 200:
                    return None, f"HTTP {r2.status_code} del Tribunal Constitucional"
                texto, meta = _texto_ficha(r2.text)
    except httpx.TransportError as e:
        _lector["c"] = None
        return None, f"red: {e}"
    if len(texto) < 300:
        return None, "la ficha del TC llego sin texto"
    if meta.get("ecli"):
        d["ecli"] = meta["ecli"]
    if meta.get("ponente") and not d.get("ponente"):
        d["ponente"] = meta["ponente"]
    n_par = 0
    if parrafos and parrafos > 0:
        # En los Plenos gigantes (Estatut: 2,2 MB) el extractor de pasajes
        # tarda 16 s sobre el texto entero -> se centra en los FUNDAMENTOS
        # (donde vive la doctrina) con un tope de 1,5 MB.
        texto_par = texto
        if len(texto) > 1_500_000:
            mfj = re.search(r"(?i)fundamentos\s+jur[ií]d", texto)
            ini = mfj.start() if mfj else 0
            texto_par = texto[ini:ini + 1_500_000]
        par = _srv._extraer_parrafos(texto_par, terminos, parrafos)
        n_par = len(par)
        salida = ("\n\n   [...]\n\n".join(par) if par else
                  "[No se hallaron parrafos con los terminos; pide el texto "
                  "completo con parrafos=0 si lo necesitas.]")
    else:
        salida = texto
        if max_chars and len(salida) > max_chars:
            salida = salida[:max_chars] + f"\n[... recortado a {max_chars} ...]"
    paginas = max(1, len(texto) // 3200)  # aprox: la fuente es HTML, no PDF
    return {"doc": d, "ruta_pdf": "", "ruta_txt": "", "texto": salida,
            "paginas": paginas, "n_parrafos": n_par, "ok": True}, None
