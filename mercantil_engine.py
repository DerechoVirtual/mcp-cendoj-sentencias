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
        # No está en el índice enriquecido → buscador OFICIAL del BORME (boe.es)
        oficial = _ficha_borme_oficial(consulta)
        if oficial:
            return oficial
        return (f"No encuentro ninguna sociedad para «{consulta}» ni en el índice del "
                "BORME ni en el buscador oficial de boe.es. Puede ser muy reciente/sin "
                "actos publicados, operar con otra razón social, o no ser una sociedad "
                "mercantil. " + _FUENTE)

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
                oficial = _ficha_borme_oficial(consulta)
                if oficial:
                    return oficial
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
        oficial = _ficha_borme_oficial(best.get("name") or consulta)
        if oficial:
            return oficial
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
