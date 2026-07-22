# -*- coding: utf-8 -*-
"""
Motor Registro Mercantil (BORME por EMPRESA) vía el índice abierto openmercantil.es.

La API de datos abiertos del BOE solo permite el BORME por FECHA exacta (y los
títulos del sumario son provincias, no empresas). Para buscar una sociedad por
NOMBRE o CIF a lo largo del tiempo se usa openmercantil.es (JSON, sin clave, sin
captcha), que reindexa el BORME oficial.

Alcance REAL (honesto):
  ✔ existencia, CIF, estado, tipo y provincia de la sociedad.
  ✔ historial de ACTOS inscritos (constitución, nombramientos/ceses de
    administradores y apoderados, ampliaciones de capital, cambios de domicilio,
    disolución…) con fecha y referencia BORME.
  ✔ administradores/apoderados vigentes e históricos.
  ✘ NO el depósito de cuentas anuales (fecha fiable) ni su contenido financiero
    (eso es de pago en el Registro Mercantil).
  ✘ Sin valor de fe pública (dato reempaquetado por un tercero): para prueba,
    nota/certificación oficial del Registro Mercantil.
"""
import re
import json
import unicodedata
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://openmercantil.es/api/v1"


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Formas societarias: colapsar "s a"/"s l"/"s l u"… para que «Telefónica, S.A.»
# case con «TELEFONICA SA» (la coma/puntos rompen la comparación normal).
_SUFIJOS = [(r"\bs a u\b", "sau"), (r"\bs l u\b", "slu"), (r"\bs a\b", "sa"),
            (r"\bs l\b", "sl"), (r"\bs c\b", "sc"), (r"\bs coop\b", "scoop")]


def _norm_emp(s: str) -> str:
    n = _norm(s)
    for pat, rep in _SUFIJOS:
        n = re.sub(pat, rep, n)
    return n


def _forma_societaria(consulta: str) -> str:
    """Si la consulta acaba en una forma societaria explícita, devuelve la base
    sin ella ('Telefónica SA' -> 'Telefónica'); si no, cadena vacía."""
    m = re.match(r"^(.{3,}?)[\s,]+(s\.?\s?a\.?u?|s\.?\s?l\.?u?|s\.?\s?coop\.?)\.?\s*$",
                 (consulta or "").strip(), re.I)
    return m.group(1).strip(" ,.") if m else ""


def _get_json(path: str, timeout=10):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "jurisprudenciator-mercantil/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            d = json.loads(r.read().decode("utf-8", "replace"))
            # La API rechaza parámetros no admitidos con {"error": ...} y HTTP 200;
            # tratarlo como fallo para que el llamante pueda degradar/reintentar.
            if isinstance(d, dict) and d.get("error"):
                return None
            return d
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    except Exception:  # noqa: BLE001
        return None


def _es_cif(q: str) -> bool:
    q = (q or "").strip().upper().replace("-", "").replace(" ", "")
    return bool(re.fullmatch(r"[A-Z]\d{7}[A-Z0-9]", q))


def _cif_norm(q: str) -> str:
    return (q or "").strip().upper().replace("-", "").replace(" ", "")


_FUENTE = ("Fuente: índice del BORME (dato público reempaquetado, sin valor de fe "
           "pública). Para prueba, pide nota oficial del Registro Mercantil.")

# --------------------------------------------------------------------------
# FALLBACK 1: índice mercantil empresia.es (reindexa la Sección A del BORME:
# constituciones, cargos, actos, con enlace al PDF oficial). Cubre las pymes
# que faltan en openmercantil (p.ej. sociedades de A Coruña como DERECHO
# VIRTUAL SL). HTML server-side, GET simple, sin captcha.
# --------------------------------------------------------------------------
_EMPRESIA = "https://www.empresia.es"
_UA_NAV = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _get_html_ua(url: str, timeout=12) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA_NAV, "Accept": "text/html",
        "Accept-Language": "es-ES,es;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            cs = r.headers.get_content_charset() or "utf-8"
            try:
                return raw.decode(cs)
            except (LookupError, UnicodeDecodeError):
                return raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _limpia(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&nbsp;", " ").replace("&euro;", "EUR").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def _empresia_buscar(consulta: str):
    """Busca en empresia.es. Devuelve [(slug, nombre)] sin duplicados."""
    h = _get_html_ua(f"{_EMPRESIA}/busqueda/?q={urllib.parse.quote(consulta)}")
    out, vistos = [], set()
    for slug, nom in re.findall(r'href="(/empresa/[^"/#?]+/)"[^>]*>([^<]{2,90})', h):
        nom = _limpia(nom)
        if slug not in vistos and nom:
            vistos.add(slug)
            out.append((slug, nom))
    return out


def _empresia_ficha(slug: str, nombre: str = "") -> str:
    """Ficha completa desde la página de empresa de empresia.es."""
    h = _get_html_ua(f"{_EMPRESIA}{slug}")
    if not h or "Informe de la empresa" not in h:
        return ""
    m = re.search(r"<title>(.*?)\s*-\s*Informe", h)
    nombre = _limpia(m.group(1)) if m else (nombre or "?")
    txt = _limpia(re.sub(r"<(script|style).*?</\1>", "", h, flags=re.S))

    def campo(pat):
        mm = re.search(pat, txt, re.I)
        return _limpia(mm.group(1)) if mm else ""

    estado = campo(r"ESTADO ([A-ZÁÉÍÓÚÜÑ]{4,})")
    cif = campo(r"\bCIF ([A-Z]\d{7}[A-Z0-9])\b")
    objeto = campo(r"Objeto social (.+?) (?:CIF|Fecha constituci)")
    fconst = campo(r"Fecha constituci[oó]n (\d{2}/\d{2}/\d{4})")
    capital = campo(r"Capital social ([\d.,]+)")
    registro = campo(r"Registro ((?:[A-ZÁÉÍÓÚÜÑ][\w()/.-]*\s?){1,4}?)(?= [ÚU]ltimas)")
    organo = campo(r"[ÓO]rgano administraci[oó]n (.+?) (?:Informe|Seguir|Balances)")
    domicilio = campo(r"Datos de (.+?)\s*Ver mapa")
    if domicilio:
        # la cabecera repite la denominación antes de la dirección: quitarla
        patron_nombre = re.escape(nombre).replace(r"\ ", r"\s+")
        domicilio = re.sub(patron_nombre, " ", domicilio, flags=re.I)
        domicilio = re.sub(r"\s+", " ", domicilio).strip(" .,")

    cab = [f"【{nombre}】"
           + (f" · CIF {cif}" if cif else "")
           + (f" · {estado.capitalize()}" if estado else "")]
    linea2 = []
    if fconst:
        linea2.append(f"constituida el {fconst}")
    if capital:
        linea2.append(f"capital {capital} EUR")
    if registro:
        linea2.append(f"Registro Mercantil de {registro}")
    if organo:
        linea2.append(organo.lower())
    if linea2:
        cab.append(" · ".join(linea2))
    if domicilio:
        cab.append(f"Domicilio: {domicilio}")
    if objeto:
        cab.append(f"Objeto social: {objeto[:180]}")

    # Cargos y representantes (tabla EntidadRelacion del HTML crudo)
    filas = re.findall(
        r'<tr><td\s+class="td-relent-entidad">.*?title="Ver directivo ([^"]+)".*?'
        r'class="td-relent-relacion">([^<]*)<.*?'
        r'td-relent-desde">(?:<a[^>]*>)?([^<]*)(?:</a>)?</td>'
        r'<td class="td-relent-hasta">(?:<a[^>]*>)?([^<]*)',
        h, re.S)
    vigentes = [(n, r, d) for n, r, d, hasta in filas if not _limpia(hasta)]
    cesados = len(filas) - len(vigentes)
    if vigentes:
        cab.append("\nCargos vigentes:")
        for n, r, d in vigentes[:10]:
            desde = f" (desde {_limpia(d)})" if _limpia(d) else ""
            cab.append(f"  · {_limpia(n)} — {_limpia(r)}{desde}")
        if cesados:
            cab.append(f"  (+{cesados} cargos históricos/cesados)")

    # Cronología de actos (timeline) con referencia al PDF oficial del BORME
    actos = re.findall(
        r'<time class="icon" datetime="(\d{4}-\d{2}-\d{2})".*?<h3>(.*?)</h3>\s*<p>(.*?)</p>'
        r'(.*?)(?=<time class="icon"|</ul>)', h, re.S)
    if actos:
        cab.append(f"\nActos publicados ({min(len(actos), 10)} de {len(actos)}):")
        for fecha, tipo, cuerpo, cola in actos[:10]:
            det = _limpia(cuerpo)
            if len(det) > 160:
                det = det[:160] + "…"
            ref = re.search(r"(BORME-A-\d{4}-\d+-\d+)", cola)
            cab.append(f"  · {fecha} · {_limpia(tipo).rstrip('.')} — {det}"
                       + (f" [{ref.group(1)}]" if ref else ""))

    cab.append("\nFuente: índice mercantil sobre el BORME oficial (dato público "
               "reempaquetado, sin valor de fe pública). Para prueba, nota oficial "
               "del Registro Mercantil.")
    return "\n".join(cab)


def _ficha_empresia(consulta: str) -> str:
    """Localiza la sociedad en empresia.es y devuelve su ficha, o ''."""
    res = _empresia_buscar(consulta)
    base = _forma_societaria(consulta)
    if not res and base:
        res = _empresia_buscar(base)
    if not res:
        return ""
    nqe = _norm_emp(consulta)
    best = next(((s, n) for s, n in res if _norm_emp(n) == nqe), None)
    if not best:
        # todas las palabras de la consulta en el nombre y sin rivales
        cw = [(s, n) for s, n in res
              if all(w in _norm(n) for w in _norm(base or consulta).split())]
        if len(cw) == 1:
            best = cw[0]
    if not best and len(res) == 1:
        best = res[0]
    if not best:
        return ""
    return _empresia_ficha(*best)


# --------------------------------------------------------------------------
# FALLBACK OFICIAL: buscador de anuncios del BORME en boe.es (anborme.php).
# El índice openmercantil.es tiene lagunas (p.ej. Banco Santander, Inditex,
# El Corte Inglés no están). El buscador oficial cubre TODAS las sociedades
# con anuncios en la Sección C desde 2001 (fusiones, escisiones, convocatorias
# de junta, reducciones de capital…), buscables por denominación en el título.
# --------------------------------------------------------------------------
_ANBORME = "https://www.boe.es/buscar/anborme.php"


def _get_html(url: str, timeout=12) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "jurisprudenciator-mercantil/1.0", "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            cs = r.headers.get_content_charset() or "utf-8"
            try:
                return raw.decode(cs)
            except (LookupError, UnicodeDecodeError):
                return raw.decode("latin-1", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _borme_oficial_buscar(nombre: str, hits: int = 50):
    """Busca anuncios del BORME (Sección C) por denominación en el título.
    Devuelve lista de dicts {ref, fecha, titulo} ordenada por fecha desc."""
    qs = urllib.parse.urlencode({
        "campo[0]": "TITULO", "dato[0]": nombre, "operador[0]": "and",
        "campo[1]": "DOC", "dato[1]": "", "operador[1]": "and",
        "campo[2]": "NBO", "dato[2]": "",
        "operador[3]": "and", "campo[3]": "FPU",
        "dato[3][0]": "", "dato[3][1]": "",
        "page_hits": str(hits),
        "sort_field[0]": "FPU", "sort_order[0]": "desc", "accion": "Buscar"})
    h = _get_html(f"{_ANBORME}?{qs}")
    if not h:
        return []
    out = []
    for it in re.findall(r'<li class="resultado-busqueda">(.*?)</li>', h, re.S):
        ref = re.search(r"(BORME-[A-C]-\d{4}-\d+)", it)
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", it)).replace("&#13;", " · ").strip()
        m = re.match(r"BORME \d+ de (\d{2}/\d{2}/\d{4})\s+(.*?)\s*Ir al documento", txt)
        out.append({
            "ref": ref.group(1) if ref else "",
            "fecha": m.group(1) if m else "",
            "titulo": re.sub(r"\s*·\s*$", "", m.group(2)).strip() if m else txt[:160],
        })
    return out


def _ficha_borme_oficial(consulta: str) -> str:
    """Ficha mínima desde el buscador OFICIAL del BORME (boe.es) cuando la
    sociedad no está en el índice enriquecido."""
    if _es_cif(consulta):
        return ""  # el buscador oficial no indexa por CIF
    # Variantes: frase exacta tal cual; con la forma societaria en puntuación
    # canónica del BORME («, S.A.»); y por último sin comillas.
    variantes = [f'"{consulta}"']
    base = _forma_societaria(consulta)
    if base:
        forma = _norm_emp(consulta)[len(_norm_emp(base)):].strip()
        canon = {"sa": "S.A.", "sau": "S.A.U.", "sl": "S.L.",
                 "slu": "S.L.U.", "scoop": "S. COOP."}.get(forma)
        if canon:
            variantes.append(f'"{base}, {canon}"')
    variantes.append(consulta)
    anuncios = []
    for v in variantes:
        anuncios = _borme_oficial_buscar(v)
        if anuncios:
            break
    if not anuncios:
        return ""
    nombre = anuncios[0]["titulo"].split(" · ")[0].strip() or consulta.upper()
    cab = [f"【{nombre}】 · localizada en el BORME oficial (boe.es)",
           f"{len(anuncios)} anuncio(s) publicados en la Sección C del BORME "
           "(convocatorias de junta, fusiones, escisiones, reducciones de capital…):"]
    for a in anuncios[:12]:
        t = a["titulo"]
        if len(t) > 150:
            t = t[:150] + "…"
        cab.append(f"  · {a['fecha']} · {t} · Ref. {a['ref']}")
    if len(anuncios) > 12:
        cab.append(f"  (+{len(anuncios) - 12} anuncios más antiguos)")
    cab.append("\nPara leer el texto íntegro de un anuncio usa leer_boe con su "
               "referencia (p.ej. " + (anuncios[0]["ref"] or "BORME-C-…") + "). "
               "Este listado NO incluye los actos inscritos de la Sección A "
               "(nombramientos/ceses, capital, domicilio): para acreditarlos, "
               "nota oficial del Registro Mercantil. Fuente: BORME oficial (boe.es).")
    return "\n".join(cab)


def buscar_empresa(consulta: str, maximo_actos: int = 12) -> str:
    """Busca una sociedad por nombre o CIF en el índice del BORME y devuelve su
    ficha: datos registrales + administradores + últimos actos inscritos."""
    consulta = (consulta or "").strip()
    if len(consulta) < 2:
        return "Indica el nombre o el CIF de la empresa."
    # OJO: la API solo admite q/limit/offset (parámetros extra => {"error": ...}).
    d = _get_json(f"/search?q={urllib.parse.quote(consulta)}&limit=15")
    if d is None:
        # Reintento mínimo (solo q) por si cambian de nuevo los parámetros admitidos.
        d = _get_json(f"/search?q={urllib.parse.quote(consulta)}")
    items = (d or {}).get("items") or []
    if not items:
        # No está en el índice principal → empresia.es (Sección A: pymes) y
        # después el buscador OFICIAL del BORME en boe.es (Sección C).
        fb = _ficha_empresia(consulta) or _ficha_borme_oficial(consulta)
        if fb:
            return fb
        return (f"No encuentro ninguna sociedad para «{consulta}» en ninguna de las "
                "tres fuentes (índice del BORME, índice mercantil y buscador oficial "
                "de boe.es). Puede ser muy reciente/sin actos publicados, operar con "
                "otra razón social, o no ser una sociedad mercantil. " + _FUENTE)

    nq = _norm(consulta)
    nqe = _norm_emp(consulta)
    best = None
    if _es_cif(consulta):
        cif = _cif_norm(consulta)
        best = next((it for it in items if _cif_norm(it.get("cif", "")) == cif), None)
    if not best:
        best = next((it for it in items if _norm_emp(it.get("name", "")) == nqe
                     or nqe in [_norm_emp(a) for a in it.get("aliases", [])]), None)
    if not best:
        conword = [it for it in items
                   if all(w in _norm(it.get("name", "")) for w in nq.split())]
        # una sola candidata razonable -> úsala; varias -> pide que afine
        if len(conword) == 1:
            best = conword[0]
        elif conword:
            # Si la consulta trae forma societaria explícita («Telefónica, S.A.»)
            # y NINGUNA candidata es esa sociedad exacta, probablemente la matriz
            # no está en el índice → probar el BORME oficial antes de rendirse.
            if _forma_societaria(consulta):
                fb = _ficha_empresia(consulta) or _ficha_borme_oficial(consulta)
                if fb:
                    return fb
            lista = "\n".join(
                f"  · {it['name']}" + (f" · CIF {it['cif']}" if it.get("cif") else "")
                + f" · {it.get('acts_count', 0)} actos" for it in conword[:8])
            return (f"Varias sociedades coinciden con «{consulta}». Afina con el nombre "
                    f"exacto o el CIF:\n{lista}\n" + _FUENTE)
    if not best:
        # Sin coincidencia clara en el índice → probar el BORME oficial antes de
        # devolver solo candidatas próximas.
        oficial = _ficha_borme_oficial(consulta)
        if oficial:
            return oficial
        lista = "\n".join(
            f"  · {it['name']}" + (f" · CIF {it['cif']}" if it.get("cif") else "")
            for it in items[:8])
        return (f"No hay una coincidencia clara para «{consulta}». Candidatas próximas:\n"
                f"{lista}\nProbable que la sociedad exacta no esté indexada. " + _FUENTE)

    prof = _get_json(f"/company/{best['slug']}")
    if not prof or not prof.get("company"):
        prof = _get_json(f"/company/{best['slug']}", timeout=12)  # reintento único
    if not prof or not prof.get("company"):
        nom = best.get("name") or consulta
        fb = _ficha_empresia(nom) or _ficha_borme_oficial(nom)
        if fb:
            return fb
        return (f"Localicé «{best.get('name')}» pero no pude cargar su ficha ahora mismo. "
                "Reinténtalo en unos segundos. " + _FUENTE)

    c = prof["company"]
    kpis = prof.get("kpis", {}) or {}
    cab = [f"【{c.get('name', '?')}】"
           + (f" · CIF {c['cif']}" if c.get("cif") else "")
           + (f" · {c['company_type']}" if c.get("company_type") else "")
           + (f" · {c['status']}" if c.get("status") else "")]
    prov = ", ".join(p.get("province", "") for p in (prof.get("top_provinces") or [])[:2] if p.get("province"))
    meta = []
    if kpis.get("acts_count") is not None:
        meta.append(f"{kpis['acts_count']} actos")
    if kpis.get("first_seen") and kpis.get("last_seen"):
        meta.append(f"de {kpis['first_seen']} a {kpis['last_seen']}")
    if prov:
        meta.append(prov)
    if meta:
        cab.append(" · ".join(meta))

    off = prof.get("officers", {}) or {}
    cur = off.get("current") or []
    if cur:
        cab.append("\nCargos vigentes:")
        for o in cur[:10]:
            since = f" (desde {o['since']})" if o.get("since") else ""
            cab.append(f"  · {o.get('name', '?')} — {o.get('role', '')}{since}")
        if off.get("historical"):
            cab.append(f"  (+{len(off['historical'])} cargos históricos)")

    events = prof.get("events") or []
    if events:
        cab.append(f"\nÚltimos actos inscritos ({min(maximo_actos, len(events))} de {len(events)}):")
        for e in events[:maximo_actos]:
            det = re.sub(r"\s+", " ", (e.get("details") or e.get("type") or "")).strip()
            if len(det) > 150:
                det = det[:150] + "…"
            cab.append(f"  · {e.get('date', '?')} · {e.get('type', '')} — {det}")

    cab.append("\nNo incluye el depósito de cuentas anuales ni su contenido financiero "
               "(de pago en el Registro Mercantil). " + _FUENTE)
    return "\n".join(cab)
