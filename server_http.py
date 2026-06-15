"""
Servidor MCP REMOTO (Streamable HTTP) — Jurisprudenciator / CENDOJ.

Misma funcionalidad que server.py (stdio, Claude Desktop) pero servido por HTTP
para desplegar en Vercel y conectarlo como "conector personalizado" en Claude.

DISEÑO (claves del proyecto):
  * COSTE CERO para Derecho Virtual: aqui solo se hace scraping HTTP del CENDOJ
    (gratis) + parsing. NO se llama a ninguna API de pago. Desde la IP de Vercel
    (datacenter) el "Control Descargas masivas" del CENDOJ salta POR IP, asi que
    rotar sesiones NO basta: el captcha se RESUELVE con la VISION del modelo
    cliente (Claude del usuario), nunca con una API nuestra.
  * STATELESS: Vercel se apaga entre peticiones, asi que NO dependemos de estado
    en memoria. `leer_sentencias` es autonoma: recibe los ROJ/ECLI a leer y los
    vuelve a localizar (no necesita recordar la ultima busqueda). Cuando topa un
    captcha devuelve la IMAGEN + un TOKEN (base64 de JSON) que lleva todo el
    estado necesario (cookie JSESSIONID, reference, optimize y los parametros de
    lectura); `resolver_captcha(token, texto)` recrea la sesion desde ese token y
    valida el captcha SIN memoria de servidor.

FLUJO DEL CAPTCHA (verificado contra poderjudicial.es, 2026-06-14):
  1. GET {BASE}/AN/openDocument/{hash}/{opt}  -> 302 Location: /search/captcha.jsp
     ?prevaction=accessToPDF&nextaction=accessToPDF&encode=true&reference={hash}
     &optimize={opt}&tab=AN&embeded=true   (salta por IP desde la 1a descarga).
  2. GET de esa captcha.jsp (registra el captcha en la sesion) y luego
     GET {BASE}/stickyImg  -> image/png con el texto a leer (atado a la JSESSIONID).
  3. POST {BASE}/contenidos.action con form:
     action=captcha, prevaction=accessToPDF, nextaction=accessToPDF, encode=true,
     reference={hash}, optimize={opt}, tab=AN, embeded=true, captcha=<texto>.
     -> ACIERTO: 200 con el PDF directamente (Content-Type application/pdf, %PDF).
     -> FALLO:   302 Location: captcha.jsp?... (hay que reintentar con otra imagen).

Reutiliza el motor ya probado de server.py (sesiones, parseo, descarga y
extraccion de texto/parrafos) sin tocarlo.

Entrypoint para Vercel (pyproject):  [tool.vercel] entrypoint = "server_http:app"
"""
import os
import re
import json
import base64

# En Vercel el disco es de solo lectura salvo /tmp. server.py hace makedirs del
# DOWNLOAD_DIR al importarse -> lo apuntamos a /tmp ANTES de importarlo.
os.environ.setdefault("DOWNLOAD_DIR", "/tmp/sentencias-cendoj")

import server as eng  # motor ya probado (reutilizado, no duplicado)
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings

# Detras de Vercel el Host es el dominio del deployment: desactivamos la proteccion
# anti DNS-rebinding (servicio publico de scraping, sin datos sensibles). json_response
# encaja mejor con serverless que el streaming SSE.
_sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
# Icono de marca (robot-abogado) para que aparezca en clientes como Claude.
_ICON_URL = os.environ.get(
    "CONNECTOR_ICON_URL", "https://jurisprudenciator-mcp.vercel.app/icon.png")
try:
    from mcp.types import Icon as _Icon
    _ICONS = [_Icon(src=_ICON_URL, mimeType="image/png", sizes="512x512")]
except Exception:  # noqa: BLE001
    _ICONS = None
mcp = FastMCP("Jurisprudenciator", stateless_http=True, json_response=True,
              transport_security=_sec,
              website_url="https://jurisprudenciator.lexiaipro.org", icons=_ICONS)


# =========================================================================
# Busqueda -> DEVUELVE DOCS (version stateless de _ejecutar_busqueda)
# =========================================================================
def _buscar_docs(data_base: dict, maximo: int) -> list[dict]:
    """Ejecuta la busqueda en el CENDOJ con una sesion fresca y devuelve la lista
    de documentos (con hash/opt para poder descargarlos). Sin estado global."""
    c = eng._nueva_sesion()
    docs: list[dict] = []
    start, total = 1, None
    while len(docs) < maximo:
        data = {**data_base, "start": str(start), "maxresults": "50",
                "recordsPerPage": "50", "sort": ""}
        try:
            r = c.post(f"{eng.BASE}/search.action", data=data, headers=eng.AJAX)
            r.encoding = "utf-8"  # el CENDOJ sirve UTF-8 pero no siempre lo declara
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Error de red al buscar: {e}")
        if start == 1 and (r.status_code in (301, 302, 303, 307) or (
                "search.action" not in r.text and "searchresult" not in r.text)):
            c = eng._nueva_sesion()
            r = c.post(f"{eng.BASE}/search.action", data=data, headers=eng.AJAX)
            r.encoding = "utf-8"
        if r.status_code != 200:
            raise RuntimeError(f"El CENDOJ respondio HTTP {r.status_code}.")
        if "no es valida" in r.text.lower():
            return []
        if total is None:
            mt = re.search(r"([\d.]+)\s+resultados", r.text)
            total = mt.group(1) if mt else "?"
        nuevos = eng._parse_resultados(r.text)
        if not nuevos:
            break
        docs.extend(nuevos)
        if len(nuevos) < 50:
            break
        start += 50
    seen, uniq = set(), []
    for d in docs:
        if d["hash"] not in seen:
            seen.add(d["hash"]); uniq.append(d)
    out = uniq[:maximo]
    if out:
        out[0]["_total"] = total  # para mostrar el total del CENDOJ
    return out


def _formatear_lista(docs: list[dict], desc: str) -> str:
    if not docs:
        return (f"Sin resultados para {desc}. Prueba sin tildes, con menos comillas, "
                "cambia la base a 'AN' o relaja los filtros.")
    total = docs[0].get("_total", "?")
    n_auto = sum(1 for d in docs if d.get("resumen_auto"))
    lineas = [f"{len(docs)} resultados (total CENDOJ: {total}) para {desc}:",
              f"{n_auto}/{len(docs)} con 'RESUMEN(auto)' = extracto del texto con tus "
              "terminos (senal de relevancia fiable). Elige la MAS relevante al caso, "
              "no la #1. Para leerlas: leer_sentencias con sus ROJ o ECLI "
              "(p.ej. 'STS 1177/2014, STS 1226/2014'), o parrafos=3 para los pasajes.\n"]
    for i, d in enumerate(docs, 1):
        lineas.append(
            f"{i}. {d.get('roj') or '?'}  |  {d.get('ecli') or 'ECLI ?'}  |  "
            f"{eng._fecha_legible(d.get('fechares',''))}"
            + (f"  |  {d['sala']}" if d.get("sala") else "")
            + (f"  |  Pon: {d['ponente']}" if d.get("ponente") else ""))
        res = d.get("resumen", "")
        if res:
            etq = ("RESUMEN(auto)" if d.get("resumen_auto")
                   else "MATERIA" if (res.isupper() or len(res) < 45) else "RESUMEN")
            lineas.append(f"   {etq}: " + (res[:420] + " [...]" if len(res) > 420 else res))
    return "\n".join(lineas)


def _localizar(cita: str) -> list[dict]:
    """Localiza por ECLI o ROJ exacto (para leer_sentencias stateless)."""
    cita = (cita or "").strip()
    if not cita:
        return []
    data = {"action": "query", "databasematch": "AN", "TEXT": ""}
    if cita.upper().startswith("ECLI"):
        data["ECLI"] = cita.upper()
    elif re.match(r"[A-Za-z]{2,4}\s*\d+/\d{4}", cita):
        data["ROJ"] = cita.upper()
    else:
        data["TEXT"] = cita
    return _buscar_docs(data, 3)


# =========================================================================
# CAPTCHA por VISION, STATELESS (el estado viaja en un token base64)
# =========================================================================
# Constantes del flujo de captcha del CENDOJ (verificadas contra poderjudicial.es).
_IMG_CAPTCHA = f"{eng.BASE}/stickyImg"          # imagen PNG del captcha (atada a la sesion)
_VALIDA_CAPTCHA = f"{eng.BASE}/contenidos.action"  # POST que valida el captcha
_CAPTCHA_JSP = f"{eng.BASE}/captcha.jsp"           # pagina que "registra" el captcha
_DOM = "www.poderjudicial.es"                     # dominio de la cookie JSESSIONID
_COOKIE_PATH = "/search"                           # path de la cookie JSESSIONID


def _cookie_sesion(c) -> str:
    """JSESSIONID de un cliente httpx, robusto ante varias cookies con ese nombre."""
    eng._dedup_jsid(c)
    return eng._jsid(c)


def _cliente_con_jsid(jsid: str):
    """Recrea un cliente httpx con una JSESSIONID concreta (sin abrir sesion nueva):
    es la pieza que hace el captcha STATELESS — el 'estado' es solo esa cookie."""
    c = httpx_client_factory()
    if jsid:
        c.cookies.set("JSESSIONID", jsid, domain=_DOM, path=_COOKIE_PATH)
    return c


def httpx_client_factory():
    """Cliente httpx con los mismos headers/timeout que el motor, sin seguir redirects
    (necesitamos VER el 302 del captcha)."""
    import httpx
    return httpx.Client(headers=eng.HEADERS, timeout=40.0, follow_redirects=False)


def _bajar_imagen_captcha(c) -> bytes:
    """Registra el captcha en la sesion (GET captcha.jsp) y baja la imagen PNG.
    Devuelve los bytes del PNG (o b'' si falla)."""
    try:
        # Visitar la pagina del captcha ayuda a que stickyImg sirva la imagen vigente.
        c.get(_CAPTCHA_JSP, params={
            "prevaction": "accessToPDF", "nextaction": "accessToPDF",
            "encode": "true", "tab": "AN", "embeded": "true"})
        ri = c.get(_IMG_CAPTCHA)
        if ri.status_code == 200 and ri.content[:4] == b"\x89PNG":
            return ri.content
    except Exception:  # noqa: BLE001
        pass
    return b""


def _codificar_token(d: dict, jsid: str, parrafos: int, terminos: str,
                     max_chars: int) -> str:
    """Empaqueta (base64 de JSON) el estado minimo para validar el captcha sin
    memoria de servidor: la cookie de sesion, el documento (hash/opt + metadatos
    para formatear el resultado) y los parametros de lectura."""
    payload = {
        "jsid": jsid,
        "hash": d.get("hash", ""), "opt": d.get("opt", ""),
        "doc": {k: d.get(k, "") for k in (
            "hash", "opt", "roj", "ecli", "fechares", "ref", "sala",
            "municipio", "ponente", "recurso")},
        "parrafos": int(parrafos or 0),
        "terminos": terminos or "",
        "max_chars": int(max_chars or 0),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decodificar_token(token: str) -> dict | None:
    try:
        raw = base64.urlsafe_b64decode((token or "").strip().encode("ascii"))
        d = json.loads(raw.decode("utf-8"))
        if isinstance(d, dict) and d.get("hash") and d.get("opt"):
            return d
    except Exception:  # noqa: BLE001
        pass
    return None


def _mensaje_captcha(token: str, png: bytes, reintento: bool = False) -> list:
    """Construye la respuesta MULTIPARTE (texto + imagen + texto-con-token) que el
    SDK de FastMCP convierte en bloques de contenido (texto + imagen) para que la
    VISION del modelo cliente lea el captcha. Stateless: el token lleva el estado."""
    intro = (
        "El CENDOJ ha pedido un CAPTCHA para descargar esta sentencia (control "
        "antidescargas, salta por la IP del servidor). " +
        ("El intento anterior no fue aceptado; aqui tienes una imagen NUEVA.\n\n"
         if reintento else "\n\n") +
        "PASOS: 1) Lee el texto de la imagen de abajo (letras y numeros, suele ser "
        "minusculas; ignora la raya que la cruza). 2) Llama a la herramienta "
        "resolver_captcha con EXACTAMENTE estos argumentos:\n"
        "   - texto = <lo que leas en la imagen>\n"
        "   - token = el token largo que aparece tras la imagen (copialo tal cual).")
    cola = (
        "\n\nTOKEN (no lo modifiques; pasalo intacto a resolver_captcha):\n"
        f"{token}")
    partes: list = [intro]
    if png:
        partes.append(Image(data=png, format="png"))
    else:
        partes.append("[No se pudo recuperar la imagen del captcha; vuelve a intentar "
                      "leer_sentencias para obtener una nueva.]")
    partes.append(cola)
    return partes


def _resolver_con_vision(png: bytes) -> str:
    """Lee los caracteres del codigo de la imagen con un modelo de vision (OpenAI
    gpt-4o-mini). Devuelve el texto (minusculas, solo alfanumerico) o '' si no hay
    clave o falla. Es lo que permite que el cliente NUNCA tenga que tratar el
    control antidescargas: lo resuelve el servidor a demanda del usuario."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or not png:
        return ""
    import urllib.request
    data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Devuelve UNICAMENTE los caracteres "
             "alfanumericos que aparecen escritos en la imagen, en minusculas, sin "
             "espacios ni puntuacion ni explicacion. Ignora la linea que los cruza."},
            {"type": "image_url", "image_url": {"url": data_uri}}]}],
        "max_tokens": 16, "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        txt = data["choices"][0]["message"]["content"]
        return re.sub(r"[^a-z0-9]", "", txt.strip().lower())
    except Exception:  # noqa: BLE001
        return ""


def _validar_captcha(c, d: dict, texto: str) -> bytes:
    """POST de validacion del codigo con la sesion c. Devuelve el PDF (bytes) si
    acierta, o b'' si falla."""
    try:
        r = c.post(_VALIDA_CAPTCHA, data={
            "action": "captcha", "prevaction": "accessToPDF",
            "nextaction": "accessToPDF", "encode": "true",
            "reference": d["hash"], "optimize": d["opt"], "tab": "AN",
            "embeded": "true", "captcha": texto}, headers=eng.AJAX)
    except Exception:  # noqa: BLE001
        return b""
    if r.status_code == 200 and r.content[:4] == b"%PDF":
        return r.content
    return b""


def _descargar_o_captcha(d: dict, parrafos: int, terminos: str, max_chars: int):
    """Descarga UN documento. Si el CENDOJ bloquea con su control antidescargas (lo
    normal desde IP de datacenter), el SERVIDOR resuelve el codigo de la imagen por
    vision y reintenta, de modo que el cliente (Claude o la web) recibe la sentencia
    ya leida sin tener que tratar nada. Devuelve ("pdf", bytes) | ("error", mensaje).
    """
    ultimo_err = ""
    for _ in range(eng.REINTENTOS_DOC):
        c = eng._nueva_sesion()
        tipo, payload = eng._intento_descarga(c, d)
        if tipo == "pdf":
            return "pdf", payload
        if tipo == "captcha":
            # Resolver en el servidor por vision, varios intentos con imagen nueva
            # en la MISMA sesion (cada imagen va atada a su JSESSIONID).
            for _intento in range(4):
                png = _bajar_imagen_captcha(c)
                if not png:
                    break
                texto = _resolver_con_vision(png)
                if not texto:
                    ultimo_err = "sin OPENAI_API_KEY o la vision fallo"
                    break
                pdf = _validar_captcha(c, d, texto)
                if pdf:
                    return "pdf", pdf
            ultimo_err = ultimo_err or "no se pudo resolver el codigo por vision"
            continue
        ultimo_err = payload.decode(errors="replace") if isinstance(payload, bytes) else str(payload)
    return "error", ultimo_err or "no se pudo descargar"


# =========================================================================
# HERRAMIENTAS MCP (stateless)
# =========================================================================
@mcp.tool()
def buscar_sentencias(
    consulta: str, base: str = "TS", maximo: int = 20,
    fecha_desde: str = "", fecha_hasta: str = "", tipo_resolucion: str = "",
    jurisdiccion: str = "", provincia: str = "", tipo_organo: str = "",
) -> str:
    """Busca jurisprudencia en el CENDOJ (Tribunal Supremo y demas organos) y
    devuelve la lista con ROJ, ECLI, fecha, ponente y resumen. NO descarga.

    Para leer las que elijas, llama luego a leer_sentencias con sus ROJ o ECLI.

    Args:
        consulta: Texto libre. Comillas = frase exacta. Si da 0, prueba sin tildes.
        base: "TS" (Supremo) o "AN" (todo). Con provincia/tipo_organo se fuerza "AN".
        maximo: Cuantos resultados (admite >50, pagina solo).
        fecha_desde / fecha_hasta: dd/mm/aaaa.
        tipo_resolucion: "SENTENCIA" o "AUTO".
        jurisdiccion: "CIVIL", "PENAL", "CONTENCIOSO", "SOCIAL", "MILITAR".
        provincia: "Valladolid", "Madrid"... (implica base AN).
        tipo_organo: "AP", "TS", "TSJ", "JPI", "JM", "JP"... o codigo CENDOJ.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return "Error: la consulta esta vacia."
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
        cod = eng._resolver_organo(tipo_organo)
        data["TIPOORGANOPUB"] = cod
        if data["databasematch"] == "TS" and cod != "11|12|13|14|15|16":
            data["databasematch"] = "AN"
    if provincia:
        data["VALUESCOMUNIDAD"] = eng._valor_provincia(provincia)
        data["databasematch"] = "AN"
    desc = f"{consulta!r} en base {data['databasematch']}"
    if provincia:
        desc += f", provincia {provincia}"
    if tipo_organo:
        desc += f", organo {tipo_organo}"
    try:
        docs = _buscar_docs(data, max(1, int(maximo)))
    except RuntimeError as e:
        return str(e)
    return _formatear_lista(docs, desc)


@mcp.tool()
def buscar_por_cita(cita: str) -> str:
    """Localiza una sentencia por su ECLI o ROJ EXACTO (verificar una cita o abrir
    una resolucion). Deja la lista lista para leer_sentencias.

    Args:
        cita: ECLI ("ECLI:ES:TS:2014:4786") o ROJ ("STS 4786/2014", "SAP VA 1226/2014").
    """
    cita = (cita or "").strip()
    if not cita:
        return "Error: indica un ECLI o un ROJ."
    try:
        docs = _localizar(cita)
    except RuntimeError as e:
        return str(e)
    return _formatear_lista(docs, f"cita {cita!r}")


@mcp.tool()
def opciones_busqueda(consulta: str = "", campo: str = "organos", base: str = "AN") -> str:
    """Valores de una faceta para REFINAR la busqueda (organos, anos o ponentes).

    Args:
        consulta: Texto a refinar (opcional).
        campo: "organos", "anos" o "ponentes".
        base: "AN" o "TS".
    """
    field = {"organos": "TIPOORGANOPUB", "organos ": "TIPOORGANOPUB",
             "anos": "ANYO", "ano": "ANYO",
             "ponentes": "PONENTE", "ponente": "PONENTE"}.get(
                 campo.lower().strip(), campo.upper())
    c = eng._nueva_sesion()
    data = {"action": "getQueryAllTagValues", "field": field,
            "databasematch": (base or "AN").strip().upper(), "idtab": "jurisprudencia"}
    if consulta:
        data["TEXT"] = consulta
    try:
        r = c.post(f"{eng.BASE}/search.action", data=data, headers=eng.AJAX)
        r.encoding = "utf-8"
    except Exception as e:  # noqa: BLE001
        return f"Error de red: {e}"
    import html as _html
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
def leer_sentencias(citas: str, parrafos: int = 0, terminos: str = "",
                    max_chars: int = 0):
    """Lee el TEXTO de sentencias concretas del CENDOJ. Stateless: indica las
    sentencias por su ROJ o ECLI.

    Para 'los parrafos exactos' o para volumen, usa parrafos=N: en vez del texto
    integro devuelve solo los N pasajes mas relevantes (los que contienen los
    terminos). Imprescindible para 'los 5 parrafos clave de varias sentencias'.

    El servidor gestiona automaticamente el control antidescargas del CENDOJ y te
    entrega el texto ya extraido; no necesitas hacer nada especial.

    Args:
        citas: ROJ o ECLI separados por coma. P.ej. "STS 1177/2014, STS 1226/2014"
            o "ECLI:ES:TS:2014:4786". (Los ves en el resultado de buscar_sentencias.)
        parrafos: 0 = texto integro. >0 = solo los N parrafos mas relevantes.
        terminos: palabras clave para elegir los parrafos (recomendado al usar parrafos).
        max_chars: si parrafos=0, recorta el texto integro a esta longitud (0 = todo).
    """
    lista = [c.strip() for c in (citas or "").split(",") if c.strip()]
    if not lista:
        return "Indica una o varias sentencias por su ROJ o ECLI (separados por coma)."
    docs: list[dict] = []
    no_encontradas: list[str] = []
    for cita in lista:
        try:
            ds = _localizar(cita)
        except RuntimeError as e:
            return str(e)
        if ds:
            docs.append(ds[0])
        else:
            no_encontradas.append(cita)
    if not docs:
        return ("No se localizo ninguna de esas citas en el CENDOJ: "
                + ", ".join(no_encontradas))

    parr = int(parrafos or 0)
    terms = (terminos or "").strip()
    mc = int(max_chars or 0)
    oks: list[dict] = []
    errs: list[dict] = []
    for d in docs:
        tipo, payload = _descargar_o_captcha(d, parr, terms, mc)
        if tipo == "pdf":
            reg = eng._construir_registro(d, payload, incluir_texto=True,
                                          guardar_pdf=False, parrafos=parr, terminos=terms)
            if not parr and mc and reg.get("ok") and len(reg["texto"]) > mc:
                reg["texto"] = reg["texto"][:mc] + f"\n[... recortado a {mc} ...]"
            oks.append(reg)
        else:
            errs.append(eng._fallo(d, payload or "no se pudo descargar"))

    modo = f"parrafos clave (x{parr})" if parr else "texto integro"
    cab = f"{len(oks)} sentencia(s) leidas ({modo})."
    if errs:
        cab += "\n" + f"{len(errs)} con incidencia: " + "; ".join(
            f"{e['doc'].get('roj','?')} ({e.get('error','?')})" for e in errs)
    if no_encontradas:
        cab += "\nNo localizadas: " + ", ".join(no_encontradas)
    cuerpo = "\n\n".join(eng._fmt_resultado(r) for r in oks)
    return cab + ("\n\n" + cuerpo if cuerpo else "")


@mcp.tool(structured_output=False)
def resolver_captcha(token: str, texto: str):
    """Valida el captcha que devolvio leer_sentencias y entrega el texto de la
    sentencia. STATELESS: recrea la sesion a partir del token (no hay memoria de
    servidor). Llamala con el TOKEN exacto que te dio leer_sentencias y el TEXTO
    que has leido en la imagen del captcha.

    Args:
        token: el token largo (base64) que acompanaba a la imagen del captcha.
        texto: los caracteres que se leen en la imagen del captcha.
    """
    st = _decodificar_token(token)
    if not st:
        return ("Token de captcha invalido o caducado. Vuelve a llamar a "
                "leer_sentencias con el ROJ/ECLI para obtener un captcha nuevo.")
    texto = (texto or "").strip()
    if not texto:
        return "Indica el texto que se ve en la imagen del captcha."

    d = st.get("doc") or {"hash": st["hash"], "opt": st["opt"]}
    d.setdefault("hash", st["hash"])
    d.setdefault("opt", st["opt"])
    parr = int(st.get("parrafos", 0) or 0)
    terms = (st.get("terminos") or "").strip()
    mc = int(st.get("max_chars", 0) or 0)

    c = _cliente_con_jsid(st.get("jsid", ""))
    try:
        r = c.post(_VALIDA_CAPTCHA, data={
            "action": "captcha", "prevaction": "accessToPDF",
            "nextaction": "accessToPDF", "encode": "true",
            "reference": st["hash"], "optimize": st["opt"], "tab": "AN",
            "embeded": "true", "captcha": texto}, headers=eng.AJAX)
    except Exception as e:  # noqa: BLE001
        return f"Error de red al validar el captcha: {e}"

    # ACIERTO: el POST devuelve el PDF directamente.
    if r.status_code == 200 and r.content[:4] == b"%PDF":
        reg = eng._construir_registro(d, r.content, incluir_texto=True,
                                      guardar_pdf=False, parrafos=parr, terminos=terms)
        if not parr and mc and reg.get("ok") and len(reg["texto"]) > mc:
            reg["texto"] = reg["texto"][:mc] + f"\n[... recortado a {mc} ...]"
        return "Captcha validado.\n\n" + eng._fmt_resultado(reg)

    # FALLO: 302 de vuelta a captcha.jsp -> servimos una imagen NUEVA + token nuevo.
    es_captcha = (r.status_code in (301, 302, 303, 307)
                  and "captcha" in r.headers.get("location", "").lower())
    if es_captcha or r.status_code == 200:
        png = _bajar_imagen_captcha(c)
        nuevo_token = _codificar_token(d, _cookie_sesion(c), parr, terms, mc)
        return _mensaje_captcha(nuevo_token, png, reintento=True)

    return (f"El CENDOJ respondio HTTP {r.status_code} al validar el captcha. "
            "Vuelve a llamar a leer_sentencias para reintentar.")


@mcp.tool()
def estado() -> str:
    """Diagnostico del servidor remoto (extractor de PDF y base del CENDOJ)."""
    return "\n".join([
        "Jurisprudenciator - conector de jurisprudencia (remoto, stateless).",
        f"Extractor PDF: {'PyMuPDF (rapido)' if eng._HAS_FITZ else 'pypdf'}",
        "Flujo: buscar_sentencias -> leer_sentencias (por ROJ/ECLI). El control "
        "antidescargas se resuelve automaticamente en el servidor.",
    ])


# App ASGI para Vercel (Streamable HTTP). El endpoint MCP queda en /mcp.
app = mcp.streamable_http_app()

# --- Icono de marca: servir /favicon.ico y /icon.png (robot-abogado) para que
# los clientes (p.ej. Claude) muestren el logo del conector. ---
import pathlib as _pathlib
from starlette.responses import Response as _Response
_DIR = _pathlib.Path(__file__).parent
_ICON_PNG = (_DIR / "robot-icon.png").read_bytes() if (_DIR / "robot-icon.png").exists() else b""
_ICON_ICO = (_DIR / "favicon.ico").read_bytes() if (_DIR / "favicon.ico").exists() else _ICON_PNG


async def _favicon_ico(request):
    return _Response(_ICON_ICO, media_type="image/x-icon",
                     headers={"Cache-Control": "public, max-age=86400"})


async def _icon_png(request):
    return _Response(_ICON_PNG, media_type="image/png",
                     headers={"Cache-Control": "public, max-age=86400"})


app.add_route("/favicon.ico", _favicon_ico, methods=["GET"])
app.add_route("/icon.png", _icon_png, methods=["GET"])


if __name__ == "__main__":
    # Ejecucion local de prueba: uvicorn server_http:app --port 8000
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
