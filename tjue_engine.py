# -*- coding: utf-8 -*-
"""Motor de jurisprudencia del TJUE (Tribunal de Justicia de la Union Europea).

Fuente oficial: CELLAR, la base documental de la Oficina de Publicaciones de la
UE (publications.europa.eu). Verificado en vivo 12-ago-2026: sin captcha, sin
API key, sin rate-limit; texto INTEGRO en espanol (98,4 % del corpus, incluso
Van Gend en Loos de 1963) en 0,3-0,6 s. CURIA (SPA Angular sin contenido
server-side) y la web de EUR-Lex (AWS WAF challenge que responde 202 vacio)
quedaron DESCARTADAS empiricamente.

Dos piezas:
  - SPARQL publico (webapi/rdf/sparql) para resolver ECLI / numero de asunto ->
    CELEX y para la busqueda por materia (bif:contains sobre el titulo ES, que
    incluye partes y descriptores "Procedimiento prejudicial - ..."). REGLA DE
    ORO medida: identificadores SIEMPRE con VALUES (0,2-0,5 s); FILTER/REGEX
    hace full scan (3-16 s) y esta prohibido en la ruta caliente.
  - REST /resource/celex/{CELEX} con Accept: application/xhtml+xml y
    Accept-Language: spa (ISO-639-3; "es" NO funciona) para el texto integro.

CELEX de jurisprudencia: 6 + anno(4) + organo (C=TJ, T=TG, F=TFP) + tipo
(J=sentencia, O=auto, C=conclusiones del AG, V=dictamen...) + numero(4).

API publica (contrato de los motores auxiliares del conector):
  es_cita(cita)                      -> bool
  localizar(cita)                    -> list[dict] (RuntimeError si red)
  buscar_docs(consulta, ...)         -> list[dict]
  leer_doc(d, parrafos, terminos, max_chars) -> (registro|None, error|None)
"""
from __future__ import annotations

import html as _html
import re
import time

import httpx

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR = "https://publications.europa.eu/resource/celex/"
_TIMEOUT = 8.0
_XSD_STR = "^^<http://www.w3.org/2001/XMLSchema#string>"
_LANG_SPA = "<http://publications.europa.eu/resource/authority/language/SPA>"
_PRE = "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"

# ECLI:EU:C:2020:981 (C=TJ, T=TG, F=TFP) | asunto "C-311/19", "T-778/16", "F-46/09"
_RE_EU_ECLI = re.compile(r"ECLI:EU:([CTF]):(\d{4}):(\d{1,4})\b", re.I)
_RE_EU_ASUNTO = re.compile(r"\b([CTF])\s*[-‑–]\s*(\d{1,4})\s*/\s*(\d{2,4})\b", re.I)

# Letras de tipo por las que se prueba un numero de asunto, en orden de interes
_TIPOS = {"C": ["CJ", "CO", "CC", "CV", "CB"],
          "T": ["TJ", "TO", "TC", "TB"],
          "F": ["FJ", "FO", "FB"]}
_TIPO_NOMBRE = {"J": "Sentencia", "O": "Auto", "C": "Conclusiones del AG",
                "V": "Dictamen", "B": "Auto", "P": "Toma de posicion"}
_ORGANO = {"C": "TJUE", "T": "Tribunal General", "F": "Trib. Funcion Publica"}

_lector: dict = {"c": None}


def _cliente() -> httpx.Client:
    if _lector["c"] is None:
        _lector["c"] = httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    return _lector["c"]


def _sparql(query: str, timeout: float = _TIMEOUT) -> list[dict]:
    try:
        # timeout=3000 (anytime de Virtuoso): si la query es cara, corta a los
        # 3 s y devuelve HTTP 206 con los resultados PARCIALES, que valen (los
        # fallbacks del caller completan). Con 9000 una query cara bloqueaba
        # 9-10 s la tool entera.
        r = _cliente().get(SPARQL, params={
            "default-graph-uri": "", "query": _PRE + query, "timeout": "3000",
            "format": "application/sparql-results+json"}, timeout=timeout)
    except httpx.TransportError as e:
        _lector["c"] = None
        raise RuntimeError(
            "Error de red al consultar la base del TJUE (pasa a veces por "
            f"mantenimiento). Reintenta en unos minutos. ({e})")
    if r.status_code not in (200, 206):
        raise RuntimeError(f"La base del TJUE respondio HTTP {r.status_code} "
                           "a la busqueda. Reintenta en unos minutos.")
    try:
        return r.json()["results"]["bindings"]
    except Exception:  # noqa: BLE001
        return []


def _v(row: dict, k: str) -> str:
    return row.get(k, {}).get("value", "")


# --------------------------------------------------------------------------
# Construccion de documentos
# --------------------------------------------------------------------------
_RE_CELEX_BASE = re.compile(r"^6(\d{4})([CTF])([A-Z])(\d{4})$")


def _asunto_de_celex(celex: str) -> str:
    m = _RE_CELEX_BASE.match(celex)
    if not m:
        return celex
    anno, org, _tipo, num = m.groups()
    return f"{org}-{int(num)}/{anno[2:]}"


def _doc_de(celex: str, ecli: str = "", fecha: str = "", titulo: str = "") -> dict:
    m = _RE_CELEX_BASE.match(celex)
    tipo = _TIPO_NOMBRE.get(m.group(3), "Resolucion") if m else "Resolucion"
    organo = _ORGANO.get(m.group(2), "TJUE") if m else "TJUE"
    asunto = _asunto_de_celex(celex)
    sala = ""
    ms = re.search(r"\((Gran Sala|Sala [^)]{2,25}|Pleno)\)", titulo)
    if ms:
        sala = ms.group(1)
    partes, resumen = "", ""
    if titulo:
        # Titulo Cellar: "Sentencia ... de fecha.#Partes.#Descriptores.#Asunto X."
        trozos = [t.strip() for t in titulo.split("#") if t.strip()]
        if len(trozos) >= 2:
            partes = trozos[1].rstrip(".")
        if len(trozos) >= 3:
            resumen = " — ".join(trozos[1:-1] if trozos[-1].lower().startswith("asunto")
                                 else trozos[1:])[:600]
        else:
            resumen = titulo[:300]
    etiqueta = tipo if tipo != "Sentencia" else ("Sentencia" if organo == "TJUE"
                                                 else f"Sentencia {organo}")
    return {
        "roj": f"{asunto} ({etiqueta})" if tipo != "Sentencia" else asunto,
        "ecli": ecli, "fechares": fecha.replace("-", "")[:8],
        "sala": (f"{organo}" + (f", {sala}" if sala else "")),
        "ponente": "", "resumen": (partes + (" — " if partes and resumen and
                                             not resumen.startswith(partes) else "")
                                   + (resumen if not resumen.startswith(partes) or not partes
                                      else resumen[len(partes):].lstrip(" —"))) or titulo[:300],
        "_motor": "tjue", "celex": celex, "tipo": tipo,
        "hash": f"tjue-{celex}", "opt": "",
    }


def _filtrar_variantes(rows: list[dict]) -> list[dict]:
    """Quita los CELEX con sufijo (_SUM/_RES/_INF) y los anuncios del DOUE
    (CA/TA/CN/TN), dedupe por CELEX base."""
    vistos, out = set(), []
    for r in rows:
        celex = _v(r, "celex") or _v(r, "c")
        if not _RE_CELEX_BASE.match(celex):
            continue
        if celex[5:7] in ("CA", "TA", "CN", "TN"):
            continue
        if celex in vistos:
            continue
        vistos.add(celex)
        out.append(r)
    return out


# --------------------------------------------------------------------------
# API publica
# --------------------------------------------------------------------------
def es_cita(cita: str) -> bool:
    s = (cita or "").upper()
    if _RE_EU_ECLI.search(s):
        return True
    m = _RE_EU_ASUNTO.search(s)
    # "C-311/19" es inequivoco; con "asunto 26/62" (sin letra) no nos metemos
    return bool(m)


def _anno4(a: str) -> int:
    n = int(a)
    if n >= 100:
        return n
    return 2000 + n if n <= (time.localtime().tm_year % 100) + 1 else 1900 + n


def localizar(cita: str) -> list[dict]:
    """ECLI o numero de asunto -> documentos, sentencia primero."""
    s = (cita or "").upper()
    m = _RE_EU_ECLI.search(s)
    if m:
        ecli = f"ECLI:EU:{m.group(1)}:{m.group(2)}:{int(m.group(3))}"
        rows = _sparql(f"""SELECT DISTINCT ?celex ?date ?title WHERE {{
  ?w cdm:case-law_ecli "{ecli}"{_XSD_STR} .
  ?w cdm:resource_legal_id_celex ?celex .
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?e cdm:expression_belongs_to_work ?w ;
                cdm:expression_uses_language {_LANG_SPA} ;
                cdm:expression_title ?title }}
}} LIMIT 10""")
        rows = _filtrar_variantes(rows)
        return [_doc_de(_v(r, "celex"), ecli, _v(r, "date"), _v(r, "title"))
                for r in rows]
    m = _RE_EU_ASUNTO.search(s)
    if not m:
        return []
    org, num, anno = m.group(1).upper(), int(m.group(2)), _anno4(m.group(3))
    tipos = list(_TIPOS[org])
    if "CONCLUSION" in s:  # "C-311/19 (Conclusiones del AG)" -> el AG primero
        tipos.sort(key=lambda t: 0 if t[1] == "C" else 1)
    candidatos = [f"6{anno}{t}{num:04d}" for t in tipos]
    values = " ".join(f'"{c}"{_XSD_STR}' for c in candidatos)
    rows = _sparql(f"""SELECT DISTINCT ?celex ?ecli ?date ?title WHERE {{
  VALUES ?celex {{ {values} }}
  ?w cdm:resource_legal_id_celex ?celex .
  OPTIONAL {{ ?w cdm:case-law_ecli ?ecli }}
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?e cdm:expression_belongs_to_work ?w ;
                cdm:expression_uses_language {_LANG_SPA} ;
                cdm:expression_title ?title }}
}} LIMIT 20""")
    rows = _filtrar_variantes(rows)
    orden = {t: i for i, t in enumerate(tipos)}
    rows.sort(key=lambda r: orden.get((_v(r, "celex"))[5:7], 9))
    return [_doc_de(_v(r, "celex"), _v(r, "ecli"), _v(r, "date"), _v(r, "title"))
            for r in rows]


_STOP_TJUE = {"del", "los", "las", "con", "por", "para", "una", "uno", "que",
              "sobre", "entre", "ante", "tribunal", "sentencia", "sentencias",
              "jurisprudencia", "tjue", "asunto"}


def _terminos_bif(consulta: str) -> list[str]:
    """Frases entre comillas se respetan; el resto, palabras con comodin de
    Virtuoso ('hipotec*' casa hipoteca/hipotecario — verificado, 15 vs 1
    resultados). OJO: el comodin solo funciona bien en palabras SIN tilde
    (el indice tokeniza los acentos aparte); las acentuadas van EXACTAS.
    Se devuelven ordenados de mas a menos especifico (longitud)."""
    consulta = (consulta or "").strip()
    frases = re.findall(r'"([^"]{3,80})"', consulta)
    resto = re.sub(r'"[^"]*"', " ", consulta)
    terminos = [f.strip() for f in frases if f.strip()]
    palabras = [w for w in re.findall(r"[\wÀ-ſ]{3,}", resto)
                if w.lower() not in _STOP_TJUE]
    palabras.sort(key=len, reverse=True)
    for w in palabras[:4]:
        if len(w) >= 6 and not re.search(r"[À-ſ]", w):
            terminos.append(w[:max(5, len(w) - 3)] + "*")
        else:
            terminos.append(w)
    return terminos


def buscar_docs(consulta: str, fecha_desde: str = "", fecha_hasta: str = "",
                tipo_resolucion: str = "", maximo: int = 20) -> list[dict]:
    """Busqueda por materia/partes sobre el titulo ES de Cellar (el titulo trae
    las partes y los descriptores de materia).

    RANKING POR CITAS (medido 12-ago-2026): ordenar por fecha ENTIERRA los
    casos lider (C-70/17 'vencimiento anticipado' caia al puesto 31); contar
    cuantas resoluciones citan cada work (cdm:work_cites_work) lo sube al #1
    en 0,5 s. Con filtro de fechas explicito se ordena por fecha (esa es la
    intencion del usuario). Si el AND completo no da nada, se relaja a los
    2-3 terminos mas especificos (nunca OR: ruido y lentitud medidos)."""
    consulta = (consulta or "").strip()
    if _RE_EU_ASUNTO.search(consulta.upper()) and len(consulta) < 40:
        docs = localizar(consulta)          # "C-311/19" pegado en la busqueda
        if docs:
            return docs[:max(1, int(maximo))]
    terminos = _terminos_bif(consulta)
    if not terminos:
        return []
    maximo = max(1, int(maximo))
    por_fecha = bool((fecha_desde or "").strip() or (fecha_hasta or "").strip())
    orden = "DESC(?date)" if por_fecha else "DESC(?nc) DESC(?date)"

    def _q(terms: "list[str] | None" = None, expr: str = "") -> list[dict]:
        """UNA sola query con el COUNT de citas INLINE y orden por citas (el
        caso lider primero: C-70/17 tiene 49 citas y por fecha caia al puesto
        31). Verificado rapido (0,3-1 s) tanto para el AND con comodines como
        para el OR-de-ANDs exacto; lo que SI agotaba el anytime de Virtuoso
        era separar el COUNT a un VALUES sucio (duplicados/sufijos)."""
        if expr == "":
            expr = " AND ".join(f"'{t}'" for t in terms)
        return _sparql(f"""SELECT ?celex (SAMPLE(?d) AS ?date) (SAMPLE(?t) AS ?title)
       (SAMPLE(?e2) AS ?ecli) (COUNT(DISTINCT ?citing) AS ?nc) WHERE {{
  ?e cdm:expression_uses_language {_LANG_SPA} ;
     cdm:expression_title ?t ;
     cdm:expression_belongs_to_work ?w .
  ?t bif:contains "{expr}" .
  ?w cdm:resource_legal_id_celex ?celex .
  ?w cdm:work_date_document ?d .
  OPTIONAL {{ ?w cdm:case-law_ecli ?e2 }}
  OPTIONAL {{ ?citing cdm:work_cites_work ?w }}
  FILTER(STRSTARTS(STR(?celex), "6"))
}} GROUP BY ?celex ORDER BY {orden} LIMIT {min(200, maximo * 6 + 20)}""", timeout=15)

    # LEAVE-ONE-OUT en UNA sola query (OR de ANDs): un termino "veneno" que no
    # aparece en el titulo mata el AND completo ('registro' no esta en el
    # titulo de C-55/18 aunque el caso VA de eso), y el AND de 4-5 terminos con
    # comodines agota el anytime-timeout de Virtuoso (HTTP 206 a los 9 s). El
    # OR de ANDs de n-1 terminos es SUPERCONJUNTO del AND completo y tarda
    # 0,5 s -> con >=4 terminos se va directo a el.
    # Para el leave-one-out hacen falta las PALABRAS ORIGINALES completas (el
    # stem con comodin agota a Virtuoso, y el stem a secas no machea nada).
    _crudas = [w for w in re.findall(r"[\wÀ-ſ]{3,}",
                                     re.sub(r'"[^"]*"', " ", consulta))
               if w.lower() not in _STOP_TJUE]
    _crudas.sort(key=len, reverse=True)
    _crudas = _crudas[:4]

    def _loo() -> list[dict]:
        # palabras EXACTAS (sin comodin): el OR de ANDs con comodines agotaba
        # el anytime de Virtuoso y el 206 parcial se comia el caso lider
        loo = " OR ".join(
            "(" + " AND ".join(f"'{t}'" for j, t in enumerate(_crudas) if j != i) + ")"
            for i in range(len(_crudas)))
        return _q(expr=loo)

    if len(terminos) >= 4:
        rows = _loo()
    else:
        rows = _q(terminos)
        if not rows and len(terminos) == 3:
            rows = _loo()
    if not rows and len(terminos) > 1:
        rows = _q(terminos[:2]) or _q(terminos[:1])
    rows = _filtrar_variantes(rows)
    # filtros cliente: tipo y fechas (el FILTER de fechas en SPARQL multiplica x4
    # la latencia, medido; en cliente es gratis)
    tr = (tipo_resolucion or "").strip().upper()
    if tr:
        letras = {"SENTENCIA": ("J",), "AUTO": ("O", "B"),
                  "CONCLUSIONES": ("C",)}.get(tr, ())
        if letras:
            rows = [r for r in rows if _v(r, "celex")[6] in letras]
    else:
        # por defecto, fuera las conclusiones del AG (se piden con tipo_resolucion)
        rows = [r for r in rows if _v(r, "celex")[6] in ("J", "O", "B", "V")]

    def _iso(f: str) -> str:
        f = (f or "").strip()
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", f)
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else f

    fd, fh = _iso(fecha_desde), _iso(fecha_hasta)
    if fd:
        rows = [r for r in rows if _v(r, "date") >= fd]
    if fh:
        rows = [r for r in rows if _v(r, "date") <= fh]
    total = len(rows)
    docs = []
    for r in rows[:maximo]:
        d = _doc_de(_v(r, "celex"), _v(r, "ecli"), _v(r, "date"), _v(r, "title"))
        nc = _v(r, "nc")
        if nc and nc.isdigit() and int(nc) > 0:
            d["resumen"] = (f"[citada por {nc} resoluciones UE posteriores] "
                            + (d.get("resumen") or ""))
        docs.append(d)
    if docs:
        docs[0]["_total"] = str(total) + ("+" if total >= maximo * 5 else "")
    return docs


# --------------------------------------------------------------------------
# Lectura del texto integro (Cellar, ES con fallback FR/EN)
# --------------------------------------------------------------------------
def _bajar_texto(celex: str) -> tuple[str, str]:
    """-> (texto, idioma) o RuntimeError. Prueba spa -> fra -> eng, y por cada
    idioma xhtml -> html (los documentos antiguos solo tienen 'html')."""
    for lang, nombre in (("spa", "es"), ("fra", "fr"), ("eng", "en")):
        for accept in ("application/xhtml+xml", "text/html"):
            try:
                r = _cliente().get(CELLAR + celex, headers={
                    "Accept": accept, "Accept-Language": lang})
            except httpx.TransportError as e:
                _lector["c"] = None
                raise RuntimeError(f"red: {e}")
            if r.status_code == 200 and len(r.content) > 2000:
                break
        if r.status_code == 200 and len(r.content) > 2000:
            html = r.text
            html = re.sub(r"(?i)</(p|div|h\d|li|tr|table)>", "\n", html)
            html = re.sub(r"(?i)<br\s*/?>", "\n", html)
            html = re.sub(r"<[^>]+>", " ", html)
            html = _html.unescape(html)
            html = re.sub(r"[ \t]+", " ", html)
            html = re.sub(r" ?\n ?", "\n", html)
            html = re.sub(r"\n{3,}", "\n\n", html).strip()
            return html, nombre
    raise RuntimeError(f"Cellar no tiene el texto de {celex} en ES/FR/EN "
                       f"(ultimo HTTP {r.status_code})")


def leer_doc(d: dict, parrafos: int = 0, terminos: str = "", max_chars: int = 0):
    """Lee el texto integro por CELEX. Devuelve (registro, None) o (None, motivo)."""
    import server as _srv
    celex = d.get("celex")
    if not celex:
        return None, "documento del TJUE sin CELEX"
    try:
        texto, idioma = _bajar_texto(celex)
    except RuntimeError as e:
        return None, str(e)
    aviso = ""
    if idioma != "es":
        aviso = (f"\n[AVISO: esta resolucion no tiene version en espanol en la "
                 f"fuente oficial; se entrega en {idioma.upper()}.]")
    n_par = 0
    if parrafos and parrafos > 0:
        par = _srv._extraer_parrafos(texto, terminos, parrafos)
        n_par = len(par)
        salida = ("\n\n   [...]\n\n".join(par) if par else
                  "[No se hallaron parrafos con los terminos; pide el texto "
                  "completo con parrafos=0 si lo necesitas.]")
    else:
        salida = texto
        if max_chars and len(salida) > max_chars:
            salida = salida[:max_chars] + f"\n[... recortado a {max_chars} ...]"
    paginas = max(1, len(texto) // 3200)
    return {"doc": d, "ruta_pdf": "", "ruta_txt": "", "texto": salida + aviso,
            "paginas": paginas, "n_parrafos": n_par, "ok": True}, None
