"""
Plan B de Jurisprudenciator: buscar jurisprudencia en la web abierta cuando la
fuente oficial (CENDOJ) no responde.

Regla de oro: NUNCA dejar al abogado sin nada. Si la fuente oficial se cae, se
le dan resoluciones reales localizadas en Internet, con su enlace, avisando
siempre de que NO es la fuente oficial y de que la cita debe verificarse.

Dos niveles, del mas barato al mas caro:

  1) Buscadores web publicos (DuckDuckGo / Bing), SIN clave y SIN LLM. Las
     referencias (ECLI / ROJ) se extraen con expresion regular del titulo, la
     URL y el extracto REALES del resultado, asi que no hay ni un solo dato
     inventado. Coste: 0.

  2) GPT-5.6 Luna con reasoning_effort "low" y su buscador web, solo si (1) no
     encuentra nada. Coste medido: ~0,5 centimos de tokens por consulta.

El nivel 2 se puede apagar con RESPALDO_LLM=0.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_H = {"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"}

TIMEOUT = float(os.environ.get("RESPALDO_TIMEOUT", "8"))
# Segundos totales del plan B. El usuario aguanta ~25 s en total y la deteccion
# de la caida ya se ha comido ~8, asi que aqui caben 14.
PRESUPUESTO = float(os.environ.get("RESPALDO_PRESUPUESTO", "14"))
MODELO_LLM = os.environ.get("RESPALDO_MODELO", "gpt-5.6-luna")
_LLM_ON = (os.environ.get("RESPALDO_LLM", "1").strip() != "0")

# Sitios que republican jurisprudencia espanola. Se usan para puntuar, no para
# excluir: un resultado de otro dominio tambien vale si trae ECLI o ROJ.
_FIABLES = {
    "poderjudicial.es": 6, "boe.es": 5, "tribunalconstitucional.es": 5,
    "hj.tribunalconstitucional.es": 5, "curia.europa.eu": 4,
    "vlex.es": 3, "iberley.es": 3, "noticias.juridicas.com": 3,
    "elderecho.com": 2, "laleydigital.es": 2, "legaltoday.com": 2,
    "economistjurist.es": 2, "diariolaley.es": 2,
}

_RE_ECLI = re.compile(r"ECLI:ES:[A-Z]{2,6}:\d{4}:\d+[A-Z]?", re.I)
_RE_ROJ = re.compile(
    r"\b(?:STS|SAP|STSJ|SAN|ATS|AAP|ATSJ|AAN)\s+[A-Z]{0,3}\s?\d+/\d{4}\b", re.I)
# "Sentencia 241/2013", "STS num. 123/2017"
_RE_NUM = re.compile(r"\b(?:sentencia|auto|STS|STSJ|SAP)\s+(?:n[uú]m\.?\s*)?"
                     r"(\d{1,5}/\d{4})\b", re.I)


# =========================================================================
# Buscadores web publicos (nivel 1: gratis, sin LLM)
# =========================================================================
def _limpiar_url(u: str) -> str:
    """DuckDuckGo envuelve los enlaces en /l/?uddg=<url>. Se desenvuelve."""
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if "duckduckgo.com/l/" in u:
        q = urllib.parse.urlparse(u).query
        real = urllib.parse.parse_qs(q).get("uddg", [""])[0]
        if real:
            u = real
    return u.split("&rut=")[0].strip()


def _sin_tags(s: str) -> str:
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _mojeek(consulta: str, proxy: str | None = None) -> list[dict]:
    """Mojeek: indice propio, sin clave y sin anti-bot. Es el motor principal.

    Marcado de cada resultado:
        <li class="rN"> ... <h2><a class="title" href="URL">TITULO</a></h2>
                            <p class="s">EXTRACTO</p> </li>
    """
    url = "https://www.mojeek.com/search?" + urllib.parse.urlencode({"q": consulta})
    with httpx.Client(headers=_H, timeout=TIMEOUT, follow_redirects=True, proxy=proxy) as c:
        h = c.get(url).text
    out = []
    for m in re.finditer(
            r'<h2><a class="title"[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>'
            r'\s*(?:<p class="s">(.*?)</p>)?', h, re.S):
        out.append({"url": _limpiar_url(m.group(1)), "titulo": _sin_tags(m.group(2)),
                    "extracto": _sin_tags(m.group(3) or "")[:300]})
    return out


def _ddg(consulta: str, proxy: str | None = None) -> list[dict]:
    """DuckDuckGo. Responde 202 (anti-bot) a bastantes servidores, asi que va de
    segundo: cuando entra, entra bien."""
    cab = {**_H, "Referer": "https://duckduckgo.com/",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    with httpx.Client(headers=cab, timeout=TIMEOUT, follow_redirects=True, proxy=proxy) as c:
        h = c.post("https://html.duckduckgo.com/html/", data={"q": consulta}).text
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
                         r'(?:.*?class="result__snippet"[^>]*>(.*?)</a>)?', h, re.S):
        out.append({"url": _limpiar_url(m.group(1)), "titulo": _sin_tags(m.group(2)),
                    "extracto": _sin_tags(m.group(3) or "")[:300]})
    return out


def _resultados_web(consulta: str, proxy: str | None = None) -> list[dict]:
    for motor in (_mojeek, _ddg):
        try:
            r = motor(consulta, proxy)
            if r:
                return r
        except Exception:  # noqa: BLE001 - un buscador caido no puede tumbar el plan B
            continue
    return []


_VACIAS = {"de", "del", "la", "el", "los", "las", "en", "por", "para", "con",
           "al", "y", "o", "un", "una", "que", "se", "su", "sus", "sobre",
           "ante", "es", "sin", "no", "a", "e", "lo", "mas"}


def _variantes(consulta: str) -> list[str]:
    """Escalera de consultas, de la mas rica a la mas corta.

    Mojeek exige TODOS los terminos (AND estricto): con 7-8 palabras devuelve
    cero. Asi que si la consulta larga no da nada, se va recortando a los
    terminos mas distintivos (los mas largos suelen serlo).
    """
    base = re.sub(r"[\"'`]", " ", consulta).strip()
    pal = [p for p in re.split(r"\s+", base) if p]
    fuertes = [p for p in pal if p.lower() not in _VACIAS and len(p) > 2]
    vs = [f"{base} sentencia"]
    if len(pal) > 3:
        vs.append(base)
    if len(fuertes) >= 3:
        vs.append(" ".join(sorted(fuertes, key=len, reverse=True)[:3]) + " sentencia")
    if len(fuertes) >= 2:
        vs.append(" ".join(sorted(fuertes, key=len, reverse=True)[:2]) + " sentencia ECLI")
    fuera = set()
    return [v for v in vs if not (v in fuera or fuera.add(v))]


def _buscar_varias(consulta: str, proxy: str | None = None,
                   fin: float | None = None) -> list[dict]:
    """Recorre la escalera hasta juntar resultados suficientes, sin pasarse del
    reloj: al abogado le sirve mas una respuesta en 20 s que la escalera entera."""
    import time as _t
    vistas, res = set(), []
    for q in _variantes(consulta):
        if fin is not None and _t.monotonic() > fin - 2:
            break            # no llegaria a tiempo de servir de nada
        for r in _resultados_web(q, proxy):
            u = r.get("url", "")
            if u and u not in vistas:
                vistas.add(u)
                res.append(r)
        if any(any(_refs(r)) for r in res):
            break            # ya hay referencia real: no se sigue gastando tiempo
    return res


# =========================================================================
# Extraccion de referencias REALES (sin LLM, sin invencion posible)
# =========================================================================
def _dominio(u: str) -> str:
    try:
        return urllib.parse.urlparse(u).netloc.lower().replace("www.", "")
    except Exception:  # noqa: BLE001
        return ""


def _refs(r: dict) -> tuple[str, str]:
    """ECLI y ROJ que aparecen LITERALMENTE en el resultado."""
    blob = f"{r.get('titulo','')} {r.get('extracto','')} " + \
           urllib.parse.unquote(r.get("url", ""))
    ecli = _RE_ECLI.search(blob)
    roj = _RE_ROJ.search(blob)
    return (ecli.group(0).upper() if ecli else "",
            re.sub(r"\s+", " ", roj.group(0).upper()) if roj else "")


def _puntuar(r: dict) -> int:
    ecli, roj = _refs(r)
    p = _FIABLES.get(_dominio(r.get("url", "")), 0)
    if ecli:
        p += 5
    if roj:
        p += 3
    if _RE_NUM.search(r.get("titulo", "")):
        p += 1
    if r.get("url", "").lower().endswith(".pdf"):
        p += 2
    return p


def _ordenar(res: list[dict]) -> list[dict]:
    vistos, out = set(), []
    for r in sorted(res, key=_puntuar, reverse=True):
        ecli, roj = _refs(r)
        clave = ecli or roj or r.get("url", "")
        if clave in vistos:
            continue
        vistos.add(clave)
        out.append(r)
    return out


# =========================================================================
# Nivel 2: GPT-5.6 Luna (low) con buscador web
# =========================================================================
def _luna(consulta: str, maximo: int, restante: float = 14.0) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or not _LLM_ON or restante < 4:
        return ""
    cuerpo = {
        "model": MODELO_LLM,
        "reasoning": {"effort": "low"},          # lo mas barato que razona algo
        # 'low' recorta el contenido web que entra al contexto: es el parametro
        # que manda en la factura (el grueso del coste son tokens de entrada).
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        # En la MISMA llamada pedimos lista y pasajes: dos llamadas costarian el
        # doble y tardarian el doble. 900 da para 5 resoluciones + 2 pasajes.
        "max_output_tokens": 900,
        "input": (
            "Busca en Internet resoluciones judiciales espanolas sobre: "
            f"{consulta}\n\n"
            "Responde EXACTAMENTE con estas dos secciones y nada mas:\n\n"
            "RESOLUCIONES\n"
            f"- una linea por resolucion (maximo {maximo}): organo, fecha y ECLI o "
            "numero de sentencia SOLO si aparece literal en la fuente; si no "
            "aparece, escribe 'referencia sin verificar'.\n\n"
            "PASAJES\n"
            "- para las 2 mas relevantes: su referencia y, entre comillas, el "
            "parrafo LITERAL de la resolucion que responde a la consulta, tal y "
            "como aparece en la fuente.\n"
            "- si de alguna no encuentras texto literal, escribe: (resumen, no "
            "literal) y una frase con su criterio.\n\n"
            "Prohibido inventar referencias o parrafos. Sin introduccion ni "
            "conclusiones ni consejo juridico."),
    }
    try:
        # Se corta con el reloj del plan B: mas vale devolver el aviso honesto
        # que tener al abogado esperando.
        with httpx.Client(timeout=restante) as c:
            r = c.post("https://api.openai.com/v1/responses", json=cuerpo,
                       headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return ""
        d = r.json()
    except Exception:  # noqa: BLE001
        return ""
    txt, urls = "", []
    for o in d.get("output", []):
        if o.get("type") != "message":
            continue
        for ct in o.get("content", []):
            txt += ct.get("text", "")
            for a in ct.get("annotations", []):
                u = a.get("url", "").split("?utm_source=")[0]
                if a.get("type") == "url_citation" and u and u not in urls:
                    urls.append(u)
    txt = txt.strip()
    if txt and urls:
        txt += "\n\nEnlaces consultados:\n" + "\n".join(f"  - {u}" for u in urls[:6])
    return txt


# =========================================================================
# Texto y pasajes: el abogado necesita el PARRAFO, no una lista de enlaces
# =========================================================================
def _texto_de(url: str, proxy: str | None = None) -> str:
    """Baja una URL y devuelve texto plano. Entiende PDF (via PyMuPDF) y HTML."""
    try:
        with httpx.Client(headers=_H, timeout=TIMEOUT, follow_redirects=True,
                          proxy=proxy) as c:
            r = c.get(url)
        if r.status_code != 200:
            return ""
        crudo = r.content
        tipo = r.headers.get("content-type", "").lower()
    except Exception:  # noqa: BLE001
        return ""
    if "pdf" in tipo or crudo[:5] == b"%PDF-":
        try:
            import fitz  # PyMuPDF, ya presente para las sentencias oficiales
            with fitz.open(stream=crudo, filetype="pdf") as doc:
                return "\n".join(p.get_text() for p in doc)
        except Exception:  # noqa: BLE001
            return ""
    h = crudo.decode(r.encoding or "utf-8", errors="replace")
    h = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)</(p|div|br|li|h[1-6])>", "\n", h)
    return re.sub(r"[ \t]{2,}", " ", _sin_tags(h))


def _pasajes(texto: str, terminos: list[str], cuantos: int = 3) -> list[str]:
    """Los parrafos que mas terminos de la consulta contienen."""
    if not texto:
        return []
    parr = [re.sub(r"\s+", " ", p).strip()
            for p in re.split(r"\n\s*\n|(?<=\.)\n", texto)]
    parr = [p for p in parr if 120 <= len(p) <= 2500]
    if not parr:
        return []
    claves = [t.lower() for t in terminos if len(t) > 3]

    def puntos(p: str) -> int:
        b = p.lower()
        n = sum(3 for t in claves if re.search(rf"\b{re.escape(t)}", b))
        if re.search(r"\b(fundamento|considerando|declaramos|estimamos|"
                     r"desestimamos|doctrina)\b", b):
            n += 1
        return n

    mejores = [p for p in sorted(parr, key=puntos, reverse=True) if puntos(p) > 0]
    return mejores[:cuantos]


def _con_pasajes(ref: str, consulta: str, proxy: str | None = None) -> tuple[str, list[str]]:
    """Localiza en la web una copia legible de 'ref' y saca sus parrafos."""
    cand = _ordenar(_resultados_web(f'"{ref}" sentencia', proxy))
    cand.sort(key=lambda r: 0 if r.get("url", "").lower().endswith(".pdf") else 1)
    terminos = re.split(r"\s+", consulta)
    for r in cand[:3]:
        txt = _texto_de(r.get("url", ""), proxy)
        if len(txt) < 400:
            continue
        ps = _pasajes(txt, terminos)
        if ps:
            return r.get("url", ""), ps
    return "", []


# =========================================================================
# API publica del modulo
# =========================================================================
_AVISO = (
    "AVISO: la fuente oficial (buscador del Poder Judicial) no responde en este "
    "momento, asi que esto NO viene de ella. Son resoluciones localizadas en "
    "Internet: sirven para orientarse y para leerlas en el enlace, pero la cita "
    "NO esta verificada contra la fuente oficial. Dilo al usuario, no presentes "
    "estas referencias como confirmadas y sugiere verificarlas con buscar_por_cita "
    "cuando la fuente oficial vuelva."
)


def buscar(consulta: str, maximo: int = 8, proxy: str | None = None,
           presupuesto: float = PRESUPUESTO) -> str:
    """Plan B de buscar_sentencias. Devuelve SIEMPRE algo util (o un aviso claro).

    presupuesto: segundos totales. La deteccion de la caida ya se ha comido una
    parte del tiempo que aguanta el usuario (~25 s), asi que el respaldo lleva
    reloj: si no le queda para el nivel gratuito, va directo al de pago.
    """
    import time as _t
    fin = _t.monotonic() + presupuesto
    consulta = (consulta or "").strip()
    if not consulta:
        return f"{_AVISO}\n\nNo hay consulta que buscar."
    # Las dos vias arrancan A LA VEZ. En serie no cabian: Luna necesita 10-15 s
    # y los buscadores gratuitos otros 5, y el usuario aguanta 25 contando la
    # deteccion de la caida. En paralelo el coste es el mismo (Luna se lanza
    # igual) y siempre queda red debajo si Luna no llega a tiempo.
    #   - Luna (de pago, ~0,5 centimos): unica que devuelve ECLIs reales.
    #   - Buscadores gratuitos: mas flojos, pero listos en 3-6 s.
    import concurrent.futures as _cf
    utiles: list[dict] = []
    texto_llm = ""
    with _cf.ThreadPoolExecutor(2) as _ex:
        f_llm = _ex.submit(_luna, consulta, maximo, max(0.0, fin - _t.monotonic()))
        f_web = _ex.submit(_buscar_varias, consulta, proxy, fin)
        try:
            texto_llm = f_llm.result(timeout=max(1.0, fin - _t.monotonic())) or ""
        except Exception:  # noqa: BLE001 - timeout o API caida: queda la web
            texto_llm = ""
        try:
            res = _ordenar(f_web.result(timeout=max(1.0, fin - _t.monotonic() + 3)))
        except Exception:  # noqa: BLE001
            res = []

    if texto_llm:
        refs = list(dict.fromkeys(_RE_ECLI.findall(texto_llm)
                                  or _RE_ROJ.findall(texto_llm)))[:maximo]
    else:
        utiles = [r for r in res if any(_refs(r))][:maximo] or res[:maximo]
        refs = [" | ".join(x for x in _refs(r)[::-1] if x) for r in utiles]
        if not utiles:
            return (f"{_AVISO}\n\nTampoco se ha encontrado nada por Internet para "
                    f"{consulta!r}. Reintenta en unos minutos: estas caidas de la "
                    "fuente oficial suelen durar poco.")

    lineas = [_AVISO, ""]
    if texto_llm:
        lineas += ["Resoluciones localizadas en Internet:", texto_llm, ""]
    else:
        lineas.append(f"{len(utiles)} resoluciones localizadas en Internet para "
                      f"{consulta!r}:")
        for i, r in enumerate(utiles, 1):
            lineas.append(f"{i}. {refs[i-1] or 'referencia sin verificar'}")
            lineas.append(f"   {r.get('titulo','(sin titulo)')[:150]}")
            lineas.append(f"   fuente: {_dominio(r.get('url',''))} -> {r.get('url','')}")
            if r.get("extracto"):
                lineas.append(f"   {r['extracto'][:220]}")
        lineas.append("")

    # --- Parrafos: lo que de verdad necesita el abogado. Cuando ha contestado el
    # nivel 2 los pasajes vienen ya en su respuesta; aqui solo se buscan copias
    # publicas descargables, que dan cita literal verificable.
    dianas = [] if texto_llm else [r for r in refs if r][:2]
    if dianas:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(len(dianas)) as ex:
            sacados = list(ex.map(lambda r: (r, *_con_pasajes(r, consulta, proxy)),
                                  dianas))
        hubo = False
        for ref, url, ps in sacados:
            if not ps:
                continue
            if not hubo:
                lineas.append("PASAJES (extraidos de la copia publicada en el enlace, "
                              "NO de la fuente oficial):")
                hubo = True
            lineas.append(f"* {ref}  [{_dominio(url)}]")
            for p in ps:
                lineas.append(f"    \"{p[:700]}\"")
        if not hubo:
            lineas.append("No se ha podido extraer el texto de ninguna copia publica: "
                          "abre los enlaces de arriba para leer la resolucion.")

    lineas.append("")
    lineas.append("Cuando la fuente oficial vuelva, buscar_por_cita confirma cada "
                  "referencia y leer_sentencias da el texto integro y literal.")
    return "\n".join(lineas)


def localizar(cita: str, terminos: str = "", proxy: str | None = None) -> str:
    """Plan B de leer_sentencias y buscar_por_cita: dar el TEXTO de una
    resolucion concreta cuando la fuente oficial no responde.

    Orden: copia publica descargable (cita literal verificable) -> enlaces ->
    GPT-5.6 Luna (contenido aproximado, marcado como tal).
    """
    cita = (cita or "").strip()
    if not cita:
        return f"{_AVISO}\n\nNo se ha indicado ninguna resolucion."

    # 1) Copia publica que se pueda descargar: es lo unico que da cita LITERAL.
    url, ps = _con_pasajes(cita, terminos or cita, proxy)
    if ps:
        lineas = [_AVISO, "",
                  f"Pasajes de {cita} tomados de la copia publicada en "
                  f"{_dominio(url)} ({url}). Texto literal de esa copia, no "
                  "cotejado con la fuente oficial:"]
        lineas += [f'  "{p[:900]}"' for p in ps]
        return "\n".join(lineas)

    # 2) Al menos, donde leerla.
    res = _ordenar(_resultados_web(f'"{cita}" sentencia texto completo', proxy))[:6]
    if res:
        lineas = [_AVISO, "", f"No se ha podido extraer el texto, pero {cita} "
                              "aparece publicada aqui:"]
        for i, r in enumerate(res, 1):
            lineas.append(f"{i}. {r.get('titulo','(sin titulo)')[:150]}")
            lineas.append(f"   {_dominio(r.get('url',''))} -> {r.get('url','')}")
        return "\n".join(lineas)

    # 3) Ultimo recurso: contenido aproximado, dicho con todas las letras.
    txt = _luna(f"la resolucion {cita}: que resuelve y cual es su doctrina", 3,
                PRESUPUESTO)
    if txt:
        return (f"{_AVISO}\n\nNo se ha localizado una copia del texto. Contenido "
                f"APROXIMADO de {cita} segun fuentes de Internet (NO literal, no "
                f"citable como tal):\n{txt}")
    return (f"{_AVISO}\n\nNo se ha localizado {cita!r} por Internet. "
            "Reintenta cuando la fuente oficial responda.")


def esta_disponible() -> bool:
    """Para la tool 'estado': el plan B siempre esta, con o sin clave de LLM."""
    return True
