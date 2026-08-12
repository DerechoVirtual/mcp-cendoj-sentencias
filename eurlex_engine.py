# -*- coding: utf-8 -*-
"""Motor de NORMATIVA de la Union Europea (directivas, reglamentos, decisiones).

Fuente oficial: CELLAR (publications.europa.eu), la misma base sin captcha ni
rate-limit que ya usa tjue_engine (verificada 12-ago-2026). El BOE NO sirve
(su API de consolidada rechaza los ids DOUE) y la web de EUR-Lex esta tras un
WAF que responde 202 vacio.

Piezas verificadas en vivo:
  - Numero -> CELEX sector 3: 3 + anno(4) + letra (L=directiva, R=reglamento,
    D=decision, F=decision marco) + numero(4). "Directiva (UE) 2019/1024" ->
    32019L1024 (0,55 s).
  - Version CONSOLIDADA (la vigente, con reformas integradas): CELEX 0-prefijo
    "02016R0679-20160504". Se localiza por la propiedad INDEXADA
    cdm:act_consolidated_based_on_resource_legal (0,4 s; STRSTARTS = 6,5 s).
  - Texto integro en ESPANOL: GET /resource/celex/{CELEX} con Accept
    application/xhtml+xml + Accept-Language: spa (RGPD consolidado 0,5 MB en
    0,9 s; art. 17 extraible).
  - Busqueda por titulo con bif:contains + comodines (igual que el TJUE).

API publica:
  es_norma(ref)          -> bool (la referencia apunta a una norma UE)
  buscar(consulta, ...)  -> str | None (lista formateada)
  articulo(ref, art)     -> str | None (texto del articulo, consolidado)
  leer(ref, max_chars)   -> str | None (texto integro/resumen)
"""
from __future__ import annotations

import html as _html
import re
import time

import httpx

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR = "https://publications.europa.eu/resource/celex/"
_TIMEOUT = 9.0
_XSD = "^^<http://www.w3.org/2001/XMLSchema#string>"
_SPA = "<http://publications.europa.eu/resource/authority/language/SPA>"
_PRE = "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"

_TIPO_LETRA = {"DIRECTIVA": "L", "REGLAMENTO": "R", "DECISION": "D",
               "DECISIÓN": "D", "DECISION MARCO": "F", "DECISIÓN MARCO": "F"}
_LETRA_NOMBRE = {"L": "Directiva", "R": "Reglamento", "D": "Decisión",
                 "F": "Decisión marco"}

# Normas UE celebres que los abogados citan por su apodo (alias -> CELEX base).
_ALIAS = {
    "RGPD": "32016R0679", "GDPR": "32016R0679",
    "REGLAMENTO GENERAL DE PROTECCION DE DATOS": "32016R0679",
    "DSA": "32022R2065", "REGLAMENTO DE SERVICIOS DIGITALES": "32022R2065",
    "DMA": "32022R1925", "REGLAMENTO DE MERCADOS DIGITALES": "32022R1925",
    "REGLAMENTO DE INTELIGENCIA ARTIFICIAL": "32024R1689",
    "REGLAMENTO DE IA": "32024R1689", "AI ACT": "32024R1689", "RIA": "32024R1689",
    "MICA": "32023R1114", "DORA": "32022R2554",
    "NIS2": "32022L2555", "NIS 2": "32022L2555",
    "EIDAS": "32014R0910", "EIDAS2": "32024R1183", "EIDAS 2": "32024R1183",
    "BRUSELAS I BIS": "32012R1215", "BRUSELAS II TER": "32019R1111",
    "ROMA I": "32008R0593", "ROMA II": "32007R0864",
    "REGLAMENTO DE SUCESIONES": "32012R0650",
    "DIRECTIVA DE CLAUSULAS ABUSIVAS": "31993L0013",
    "DIRECTIVA DE VIAJES COMBINADOS": "32015L2302",
    "DIRECTIVA WHISTLEBLOWER": "32019L1937",
    "DIRECTIVA DE SECRETOS COMERCIALES": "32016L0943",
    "DIRECTIVA DE DATOS ABIERTOS": "32019L1024", "OPEN DATA": "32019L1024",
    "DIRECTIVA DE COPYRIGHT": "32019L0790",
    "DIRECTIVA DE COMERCIO ELECTRONICO": "32000L0031",
    "DIRECTIVA DE SERVICIOS": "32006L0123", "BOLKESTEIN": "32006L0123",
    "DIRECTIVA DE RETORNO": "32008L0115",
    "ORDEN EUROPEA DE DETENCION": "32002F0584", "EUROORDEN": "32002F0584",
    "REGLAMENTO DE PASAJEROS AEREOS": "32004R0261",
    "DIRECTIVA MARCO DEL AGUA": "32000L0060",
    "DIRECTIVA DE HABITATS": "31992L0043", "DIRECTIVA DE AVES": "32009L0147",
    "DIRECTIVA DE TIEMPO DE TRABAJO": "32003L0088",
    "DIRECTIVA DE DESPIDOS COLECTIVOS": "31998L0059",
    "DIRECTIVA DE CREDITO AL CONSUMO": "32023L2225",
    "DIRECTIVA DE CREDITO HIPOTECARIO": "32014L0017",
    "DIRECTIVA DE MOROSIDAD": "32011L0007",
    "DIRECTIVA EPRIVACY": "32002L0058",
    "DIRECTIVA DE ACCIONES DE REPRESENTACION": "32020L1828",
    "DIRECTIVA DE CONCILIACION": "32019L1158",
}

# "Directiva (UE) 2019/1024", "Directiva 93/13/CEE", "Reglamento (CE) nº 261/2004",
# "Decisión marco 2002/584/JAI", "Reglamento 2016/679"...
_RE_NORMA = re.compile(
    r"\b(directiva|reglamento|decisi[oó]n(?:\s+marco)?)\s*"
    r"(?:\(?(?:UE|CE|CEE|EU|Euratom)\)?\s*)?"
    r"(?:n[ºo.°]*\s*)?"
    r"(\d{1,4})\s*/\s*(\d{1,4})", re.I)
_RE_CELEX = re.compile(r"\b(0?3\d{4}[LRDF]\d{4,5})(?:-(\d{8}))?\b")

_lector: dict = {"c": None}


def _cliente() -> httpx.Client:
    if _lector["c"] is None:
        _lector["c"] = httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    return _lector["c"]


def _sparql(query: str, timeout: float = _TIMEOUT) -> list[dict]:
    try:
        r = _cliente().get(SPARQL, params={
            "default-graph-uri": "", "query": _PRE + query, "timeout": "3000",
            "format": "application/sparql-results+json"}, timeout=timeout)
    except httpx.TransportError as e:
        _lector["c"] = None
        raise RuntimeError(
            "Error de red al consultar la base de normativa de la UE (pasa a "
            f"veces por mantenimiento). Reintenta en unos minutos. ({e})")
    if r.status_code not in (200, 206):
        raise RuntimeError(f"La base de normativa de la UE respondio HTTP "
                           f"{r.status_code}. Reintenta en unos minutos.")
    try:
        return r.json()["results"]["bindings"]
    except Exception:  # noqa: BLE001
        return []


def _v(row: dict, k: str) -> str:
    return row.get(k, {}).get("value", "")


# --------------------------------------------------------------------------
# Referencia -> CELEX
# --------------------------------------------------------------------------
def _anno4(n: int) -> "int | None":
    """Un numero es 'anno plausible' si cae en 1952-2035 (o 52-99 en 2 cifras)."""
    if 1952 <= n <= 2035:
        return n
    if 52 <= n <= 99:
        return 1900 + n
    return None


def _celex_candidatos(ref: str) -> list[str]:
    """Deduce los CELEX base posibles de una referencia textual."""
    s = (ref or "").strip()
    m = _RE_CELEX.search(s.upper().replace(" ", ""))
    if m:
        return [m.group(1).lstrip("0") if m.group(1).startswith("03") else m.group(1)]
    su = re.sub(r"\s+", " ", s.upper())
    su = su.translate(str.maketrans("ÁÉÍÓÚÜÀÈÌÒÙ", "AEIOUUAEIOU"))
    for alias, celex in _ALIAS.items():
        if alias in su:
            return [celex]
    m = _RE_NORMA.search(s)
    if not m:
        return []
    tipo = re.sub(r"\s+", " ", m.group(1).upper().replace("Ó", "O"))
    letra = "F" if "MARCO" in tipo else _TIPO_LETRA.get(tipo.split()[0], "")
    if not letra:
        return []
    a, b = int(m.group(2)), int(m.group(3))
    out = []
    # "2019/1024" (anno/num, estilo actual y directivas antiguas 93/13) y
    # "261/2004" (num/anno, reglamentos antiguos): se prueban las dos lecturas.
    if _anno4(a) and not (1952 <= b <= 2035):
        out.append(f"3{_anno4(a)}{letra}{b:04d}")
    if _anno4(b) and not (1952 <= a <= 2035):
        out.append(f"3{_anno4(b)}{letra}{a:04d}")
    if not out and _anno4(a) and _anno4(b):     # ambiguo total: ambas
        out = [f"3{_anno4(a)}{letra}{b:04d}", f"3{_anno4(b)}{letra}{a:04d}"]
    return out


def es_norma(ref: str) -> bool:
    return bool(_celex_candidatos(ref))


def _resolver(ref: str) -> "dict | None":
    """Referencia -> {celex, celex_leer (consolidada si existe), titulo, fecha}."""
    cands = _celex_candidatos(ref)
    if not cands:
        return None
    values = " ".join(f'"{c}"{_XSD}' for c in cands)
    rows = _sparql(f"""SELECT DISTINCT ?c ?date ?title ?ccons WHERE {{
  VALUES ?c {{ {values} }}
  ?w cdm:resource_legal_id_celex ?c .
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?e cdm:expression_belongs_to_work ?w ;
                cdm:expression_uses_language {_SPA} ;
                cdm:expression_title ?title }}
  OPTIONAL {{ ?cons cdm:act_consolidated_based_on_resource_legal ?w ;
                    cdm:resource_legal_id_celex ?ccons }}
}} ORDER BY DESC(?ccons) LIMIT 40""")
    if not rows:
        return None
    # la fila con la consolidada MAS RECIENTE (orden DESC) manda
    mejor = rows[0]
    celex = _v(mejor, "c")
    ccons = ""
    for r in rows:
        if _v(r, "c") == celex and _v(r, "ccons") > ccons:
            ccons = _v(r, "ccons")
            mejor = r
    return {"celex": celex, "celex_leer": ccons or celex,
            "consolidado": bool(ccons),
            "titulo": _v(mejor, "title"), "fecha": _v(mejor, "date")}


# --------------------------------------------------------------------------
# Texto (Cellar XHTML -> texto plano con saltos)
# --------------------------------------------------------------------------
_LANGS = (("spa", "es"), ("fra", "fr"), ("eng", "en"))


def _bajar_texto(celex: str, langs=_LANGS) -> "tuple[str, str]":
    # Los actos modernos se sirven como xhtml; los antiguos (p.ej. 32004R0261)
    # SOLO tienen manifestacion 'html' -> se prueban ambos Accept por idioma.
    for lang, nombre in langs:
        for accept in ("application/xhtml+xml", "text/html"):
            try:
                r = _cliente().get(CELLAR + celex, headers={
                    "Accept": accept, "Accept-Language": lang},
                    timeout=_TIMEOUT + 8)
            except httpx.TransportError as e:
                _lector["c"] = None
                raise RuntimeError(f"red: {e}")
            if r.status_code == 200 and len(r.content) > 2000:
                break
        if r.status_code == 200 and len(r.content) > 2000:
            t = r.text
            t = re.sub(r"(?i)</(p|div|h\d|li|tr|table)>", "\n", t)
            t = re.sub(r"(?i)<br\s*/?>", "\n", t)
            t = re.sub(r"<[^>]+>", " ", t)
            t = _html.unescape(t)
            t = re.sub(r"[ \t ]+", " ", t)
            t = re.sub(r" ?\n ?", "\n", t)
            t = re.sub(r"\n{3,}", "\n\n", t).strip()
            return t, nombre
    raise RuntimeError(f"Cellar no tiene el texto de {celex} en ES/FR/EN "
                       f"(ultimo HTTP {r.status_code})")


_RE_ART = r"Art[ií]culo\s+{n}(?:\s|\n|\.)"


def _extraer_articulo(texto: str, art: str) -> "str | None":
    """Bloque desde 'Articulo N' hasta el siguiente 'Articulo'/'ANEXO'."""
    n = re.escape((art or "").strip().replace("art.", "").replace("art", "").strip())
    if not n:
        return None
    hits = [m for m in re.finditer(_RE_ART.format(n=n), texto)]
    if not hits:
        return None
    # el primero suele ser el indice/sumario; el bloque REAL es el que mas texto
    # tiene hasta el siguiente encabezado de articulo
    mejores = []
    for m in hits:
        sig = re.search(r"\n\s*Art[ií]culo\s+\d+|\n\s*ANEXO\b", texto[m.end():])
        fin = m.end() + (sig.start() if sig else min(12000, len(texto) - m.end()))
        mejores.append(texto[m.start():fin].strip())
    return max(mejores, key=len)


# --------------------------------------------------------------------------
# API publica
# --------------------------------------------------------------------------
def _texto_norma(info: dict) -> "tuple[str, str]":
    """Baja el texto priorizando el ESPANOL sobre la consolidacion: (1) la
    consolidada en ES, (2) el acto original en ES (hay consolidadas sin
    version ES o sin XHTML: 02022L2555, 02004R0261), (3) lo que haya en FR/EN."""
    intentos = [info["celex_leer"]]
    if info["celex"] != info["celex_leer"]:
        intentos.append(info["celex"])
    for celex in intentos:                       # pasada 1: solo espanol
        try:
            texto, idioma = _bajar_texto(celex, langs=_LANGS[:1])
        except RuntimeError:
            continue
        if celex != info["celex_leer"]:
            info["consolidado"] = False
            info["celex_leer"] = celex
        return texto, idioma
    for celex in intentos:                       # pasada 2: FR/EN
        try:
            texto, idioma = _bajar_texto(celex, langs=_LANGS[1:])
        except RuntimeError:
            continue
        if celex != info["celex_leer"]:
            info["consolidado"] = False
            info["celex_leer"] = celex
        return texto, idioma
    raise RuntimeError(f"Cellar no sirve el texto de {info['celex']} "
                       "(ni consolidado ni original) en ES/FR/EN.")


def articulo(ref: str, art: str) -> "str | None":
    """Texto vigente (consolidado si existe) de un articulo de una norma UE."""
    info = _resolver(ref)
    if not info:
        return None
    texto, idioma = _texto_norma(info)
    bloque = _extraer_articulo(texto, art)
    cab = [info["titulo"] or f"CELEX {info['celex']}",
           f"CELEX: {info['celex']}"
           + (f"  |  Version consolidada: {info['celex_leer']}"
              if info["consolidado"] else "  |  Version original (sin consolidar)")
           + (f"  |  [texto en {idioma.upper()}]" if idioma != "es" else "")]
    if not bloque:
        return ("\n".join(cab)
                + f"\n\nNo se encontro el articulo {art!r} en la norma. "
                "Comprueba el numero (los anexos van aparte; pide 'anexo I' con "
                "leer_boe/el identificador CELEX si lo necesitas).")
    return "\n".join(cab) + "\n\n" + bloque


def leer(ref: str, max_chars: int = 12000) -> "str | None":
    """Texto integro (consolidado si existe) de una norma UE, recortable."""
    info = _resolver(ref)
    if not info:
        return None
    texto, idioma = _texto_norma(info)
    if max_chars and len(texto) > max_chars:
        texto = texto[:max_chars] + f"\n[... recortado a {max_chars} chars; pide un articulo concreto con buscar_articulo ...]"
    cab = [info["titulo"] or f"CELEX {info['celex']}",
           f"CELEX: {info['celex']}"
           + (f"  |  Version consolidada: {info['celex_leer']}"
              if info["consolidado"] else "")
           + (f"  |  [texto en {idioma.upper()}]" if idioma != "es" else "")]
    return "\n".join(cab) + "\n\n" + texto


_STOP = {"del", "los", "las", "con", "por", "para", "una", "uno", "que", "sobre",
         "entre", "ante", "normativa", "norma", "directiva", "reglamento",
         "decision", "decisión", "europea", "europeo", "union", "unión"}


def _terminos(consulta: str) -> list[str]:
    frases = re.findall(r'"([^"]{3,80})"', consulta or "")
    resto = re.sub(r'"[^"]*"', " ", consulta or "")
    out = [f.strip() for f in frases if f.strip()]
    palabras = [w for w in re.findall(r"[\wÀ-ſ]{3,}", resto)
                if w.lower() not in _STOP]
    palabras.sort(key=len, reverse=True)      # los mas especificos primero
    for w in palabras[:5]:
        out.append(w[:max(5, len(w) - 3)] + "*" if len(w) >= 6 else w)
    return out


def buscar(consulta: str, desde: str = "", hasta: str = "", limite: int = 15) -> "str | None":
    """Busca normas UE por el titulo (partes + materia). Devuelve lista formateada."""
    # referencia directa ("Directiva 2019/1024", "RGPD") -> ficha resuelta
    info = _resolver(consulta) if es_norma(consulta) else None
    if info:
        return (f"1 norma UE localizada:\n1. {info['titulo'] or consulta}\n"
                f"   CELEX {info['celex']}"
                + (f"  |  consolidada {info['celex_leer']}" if info["consolidado"] else "")
                + f"  |  {info['fecha']}\n"
                "Para el texto de un articulo: buscar_articulo(la norma, N). "
                "Para el texto integro: leer_boe con el CELEX.")
    terminos = _terminos(consulta)
    if not terminos:
        return None

    def _q(op: str) -> list[dict]:
        expr = f" {op} ".join(f"'{t}'" for t in terminos[:6])
        return _sparql(f"""SELECT DISTINCT ?c ?date ?title WHERE {{
  ?e cdm:expression_uses_language {_SPA} ;
     cdm:expression_title ?title ;
     cdm:expression_belongs_to_work ?w .
  ?title bif:contains "{expr}" .
  ?w cdm:resource_legal_id_celex ?c .
  ?w cdm:work_date_document ?date .
  FILTER(STRSTARTS(STR(?c), "3"))
}} ORDER BY DESC(?date) LIMIT {min(150, int(limite) * 6 + 20)}""", timeout=15)

    rows = _q("AND")
    if not rows and len(terminos) > 2:
        terminos = terminos[:2]        # relajar a lo mas especifico (OR = ruido
        rows = _q("AND")               # y lentitud medidos: 8 s y resultados malos)
    # solo actos con letra de tipo conocida y sin sufijos raros
    vistos, docs = set(), []
    for r in rows:
        c = _v(r, "c")
        if not re.fullmatch(r"3\d{4}[LRDF]\d{4,5}(R\(\d+\))?", c) or c in vistos:
            continue
        vistos.add(c)
        docs.append(r)

    def _iso(f):
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", (f or "").strip())
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else (f or "").strip()

    fd, fh = _iso(desde), _iso(hasta)
    if fd:
        docs = [r for r in docs if _v(r, "date") >= fd]
    if fh:
        docs = [r for r in docs if _v(r, "date") <= fh]
    if not docs:
        return ("Sin normas UE cuyo TITULO contenga esos terminos. El buscador "
                "rastrea el titulo oficial de directivas/reglamentos/decisiones: "
                "usa el concepto clave con tildes ('datos abiertos', 'credito "
                "hipotecario') o el numero ('Directiva 2019/1024'). Si buscas "
                "una obligacion concreta, localiza la norma por su materia y "
                "pide luego el articulo con buscar_articulo.")
    lineas = [f"{min(len(docs), int(limite))} normas UE (titulo oficial en espanol) "
              f"para {consulta!r}, recientes primero. Para el texto: "
              "buscar_articulo(norma, articulo) o leer_boe con el CELEX:\n"]
    for i, r in enumerate(docs[:int(limite)], 1):
        titulo = _v(r, "title")
        lineas.append(f"{i}. {titulo[:240]}" + ("" if len(titulo) <= 240 else " [...]"))
        lineas.append(f"   CELEX {_v(r, 'c')}  |  {_v(r, 'date')}")
    return "\n".join(lineas)
