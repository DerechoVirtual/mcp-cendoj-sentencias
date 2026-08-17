# -*- coding: utf-8 -*-
"""
Motor DOCTRINA TEAC — resoluciones y criterios de los Tribunales
Económico-Administrativos (TEAC y TEAR) desde el buscador oficial DYCTEA
(serviciostelematicosext.hacienda.gob.es/TEAC/DYCTEA).

Fuente oficial del Ministerio de Hacienda. Aunque la portada es ASP.NET
WebForms (VIEWSTATE), la búsqueda es 100% GET sin estado: el POST del
formulario redirige a Criterios.aspx con todos los filtros en la query
(s=1&u=&tc=&tr=&tp=&tf=&c=&rs=&rn=&ra=&fd=&fh=&pg=). El detalle
(criterio.aspx?id=...) y el texto ÍNTEGRO de la resolución
(textoresolucion.aspx?id=...) también son GET planos. Sin captcha, sin
login, sin cookies obligatorias. Trampas conocidas: el número de
reclamación exige 5 dígitos con ceros (06291, no 6291) y el orden llega
ya por fecha descendente (recientes primero), 10 filas por página.
"""
import os
import re
import html as _html
import urllib.parse as _up
import httpx

BASE = "https://serviciostelematicosext.hacienda.gob.es/TEAC/DYCTEA/"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
# Igual que petete (DGT): CA del sector público español fuera del bundle certifi
# -> verificación TLS desactivada por defecto (sitio público de SOLO LECTURA).
_VERIFY = os.environ.get("TEAC_TLS_VERIFY", "0") != "0"

# Unidades resolutorias del formulario oficial (ddlUnidad)
_UNIDADES = {
    "": "", "TODOS": "", "TODAS": "",
    "TEAC": "00",
    "ANDALUCIA": "12", "ARAGON": "15", "ASTURIAS": "16", "BALEARES": "17",
    "CANARIAS": "18", "CANTABRIA": "20", "CASTILLA Y LEON": "22",
    "CASTILLA-LA MANCHA": "21", "CASTILLA LA MANCHA": "21", "CATALUNA": "24",
    "EXTREMADURA": "25", "GALICIA": "26", "LA RIOJA": "27", "RIOJA": "27",
    "MADRID": "28", "MURCIA": "29", "NAVARRA": "30", "VALENCIA": "32",
    "PAIS VASCO": "31", "EUSKADI": "31",
    "ALICANTE": "35", "GRANADA": "13", "TENERIFE": "19",
}
_NOMBRE_UNIDAD = {v: k.title() for k, v in _UNIDADES.items() if v}
_NOMBRE_UNIDAD["00"] = "TEAC"


def _cliente(timeout: int = 30):
    return httpx.Client(verify=_VERIFY, timeout=timeout, headers=_UA,
                        follow_redirects=True)


def _limpiar(fragmento_html: str) -> str:
    txt = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", fragmento_html or "", flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = _html.unescape(txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r" ?\n ?", "\n", txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


def _sin_tildes(s: str) -> str:
    tabla = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return (s or "").translate(tabla)


def _unidad_a_codigo(organo: str) -> str:
    o = _sin_tildes((organo or "").strip().upper())
    o = re.sub(r"^(TEAR|SALA( DESCONCENTRADA)?( DESC\.?)?)( DE L?A?S?)? ?", "", o).strip()
    if o in ("", "TODOS", "TODAS"):
        return ""
    return _UNIDADES.get(o, _UNIDADES.get("TEAC") if "TEAC" in o else None)


def _parse_rg(numero: str):
    """'00/06291/2024', 'RG 2283-2022', '6291/2024', '00-06291-2024-00' ->
    (sede, numero5, año). Sede por defecto: 00 (TEAC)."""
    s = _sin_tildes((numero or "").strip().upper())
    s = re.sub(r"^(RG|R\.G\.|RECLAMACION|RESOLUCION|RES\.?)[:\s]*", "", s).strip()
    partes = [p for p in re.split(r"[/\-\s]+", s) if p.isdigit()]
    if not partes:
        return None
    anio = num = sede = None
    for p in partes:
        if len(p) == 4 and p.startswith(("19", "20")) and anio is None:
            anio = p
    resto = [p for p in partes if p != anio]
    if anio is None or not resto:
        return None
    if len(resto) >= 2 and len(resto[0]) <= 2 and int(resto[0]) <= 35:
        sede, num = resto[0].zfill(2), resto[1]
    else:
        sede, num = "00", resto[0]
    return sede, num.zfill(5), anio


def _url_busqueda(texto="", frase="", rg=None, unidad="", desde="", hasta="",
                  criterios=True, resoluciones=False, vinculantes="", pagina=1):
    p = {"s": "1", "rs": "", "rn": "", "ra": "", "fd": desde or "", "fh": hasta or "",
         "u": unidad or "", "n": "", "p": "", "c1": "", "c2": "", "c3": "",
         "tc": "1" if criterios else "", "tr": "1" if resoluciones else "",
         "tp": texto or "", "tf": frase or "", "c": vinculantes or "2",
         "pg": str(pagina) if pagina and int(pagina) > 1 else ""}
    if rg:
        p["rs"], p["rn"], p["ra"] = rg
    return BASE + "Criterios.aspx?" + _up.urlencode(p)


def _parse_filas(htmltxt: str):
    m = re.search(r"Se han obtenido\s+([\d.]+)\s+resultados", htmltxt)
    total = m.group(1).replace(".", "") if m else None
    filas = []
    for li in re.findall(r"<li class='resultado(?:Criterio|Resolucion)'>(.*?)</li>",
                         htmltxt, re.S):
        href = re.search(r"href='(criterio\.aspx\?id=([^&']+)[^']*)'", li)
        tit = re.search(r"resultadoCriterioTitulo'>(.*?)</span>", li, re.S)
        txt = re.search(r"resultadoCriterioTexto'>(.*?)</span>", li, re.S)
        titulo = _limpiar(tit.group(1)) if tit else ""
        dm = re.search(r"[Cc]riterio\s+(\d+)\s+de la resoluci.n\s+([\d/]+)\s+del\s+"
                       r"(\d{2}/\d{2}/\d{4})\s*-\s*(.+)$", titulo)
        filas.append({
            "id": _html.unescape(href.group(2)) if href else "",
            "criterio_n": dm.group(1) if dm else "",
            "rg": dm.group(2) if dm else "",
            "fecha": dm.group(3) if dm else "",
            "unidad": dm.group(4).strip() if dm else "",
            "titulo": titulo,
            "resumen": _limpiar(txt.group(1)) if txt else "",
        })
    return total, filas


def _rg_corto(rg: str) -> str:
    """00/06291/2024/00/00 -> 00/06291/2024"""
    partes = (rg or "").split("/")
    return "/".join(partes[:3]) if len(partes) >= 3 else rg


def buscar(consulta: str = "", frase: str = "", numero_rg: str = "",
           organo: str = "TEAC", vinculantes: str = "", desde: str = "",
           hasta: str = "", ambito: str = "criterios", maximo: int = 10) -> str:
    """Busca doctrina y criterios del TEAC/TEAR en DYCTEA (Ministerio de
    Hacienda). Devuelve lista con RG, fecha, órgano y resumen del criterio."""
    if not any([consulta, frase, numero_rg, desde, hasta]):
        return ("Indica qué buscar: un texto (p.ej. 'comprobacion de valores "
                "tasacion pericial'), una frase exacta, un número de resolución "
                "(RG 00/06291/2024) o un rango de fechas.")
    rg = _parse_rg(numero_rg) if numero_rg else None
    if numero_rg and not rg:
        return f"No entiendo el número de reclamación {numero_rg!r}. Formato: 00/06291/2024 (sede/número/año)."
    unidad = _unidad_a_codigo(organo)
    if unidad is None:
        validos = "TEAC, " + ", ".join(sorted(k.title() for k, v in _UNIDADES.items()
                                              if v and k not in ("EUSKADI", "CASTILLA LA MANCHA", "RIOJA")))
        return f"Órgano {organo!r} no reconocido. Valores: {validos} o 'todos'."
    amb = _sin_tildes((ambito or "criterios").lower())
    en_criterios = amb.startswith(("criterio", "ambos", "todo"))
    en_resoluciones = amb.startswith(("resolucion", "ambos", "todo"))
    vinc = {"vinculantes": "0", "si": "0", "no vinculantes": "1", "no": "1"}.get(
        _sin_tildes((vinculantes or "").strip().lower()), "2")
    maximo = max(1, min(int(maximo or 10), 30))
    # La búsqueda en el texto ÍNTEGRO (tr=1) tarda 30-45 s en el servidor de
    # Hacienda; solo se permite si se pide expresamente, con margen de timeout.
    nota = ""
    try:
        with _cliente(55 if en_resoluciones else 30) as c:
            total, filas = None, []
            pagina = 1
            while len(filas) < maximo and pagina <= 5:
                r = c.get(_url_busqueda(consulta, frase, rg, unidad, desde, hasta,
                                        en_criterios, en_resoluciones, vinc, pagina))
                if r.status_code != 200:
                    return f"El buscador DYCTEA respondió HTTP {r.status_code}."
                total, nuevas = _parse_filas(r.text)
                filas.extend(nuevas)
                if not nuevas or len(nuevas) < 10:
                    break
                pagina += 1
            # Fallback rápido: sin resultados -> relajar la consulta a sus
            # términos más distintivos (los largos), siempre dentro de criterios
            # (el modo texto íntegro es demasiado lento para reintentos a ciegas).
            if not filas and en_criterios and not en_resoluciones and consulta and not frase:
                stop = {"de", "del", "la", "el", "los", "las", "un", "una", "en",
                        "por", "para", "con", "sobre", "al", "a", "y", "o", "u",
                        "que", "se", "su", "sus", "no", "si"}
                cand = [w for w in re.split(r"\s+", consulta.strip())
                        if w and _sin_tildes(w.lower()) not in stop]
                cand.sort(key=len, reverse=True)
                intentos = []
                if len(cand) > 3:
                    intentos.append(" ".join(cand[:3]))
                if len(cand) > 2:
                    intentos.append(" ".join(cand[:2]))
                for sub in intentos:
                    r = c.get(_url_busqueda(sub, "", rg, unidad, desde, hasta,
                                            True, False, vinc, 1))
                    total, filas = _parse_filas(r.text)
                    if filas:
                        nota = (f" Nota: la consulta completa no daba resultados; "
                                f"se muestran los de {sub!r}.")
                        break
    except Exception as e:  # noqa: BLE001
        return f"No pude consultar la doctrina del TEAC ahora mismo ({str(e)[:80]})."
    if not filas:
        return ("Sin resultados en la doctrina del TEAC para esa búsqueda"
                + (f" ({consulta!r})" if consulta else "")
                + ". Prueba con menos palabras o, para rastrear el texto íntegro "
                  "de las resoluciones (lento, ~30-45 s), repite con "
                  "ambito='resoluciones'.")
    filas = filas[:maximo]
    org_txt = _NOMBRE_UNIDAD.get(unidad, "todos los tribunales económico-administrativos")
    out = [f"{len(filas)} criterios ({org_txt}; total: {total or '?'}), más recientes "
           "primero. Para el texto íntegro de una resolución: leer_resolucion_teac "
           f"con su RG.{nota}\n"]
    for i, f in enumerate(filas, 1):
        linea = f"{i}. RG {_rg_corto(f['rg'])} · {f['fecha']} · {f['unidad']}"
        if f["criterio_n"] and f["criterio_n"] != "1":
            linea += f" · criterio {f['criterio_n']}"
        if f["resumen"]:
            resumen = re.sub(r"\s+", " ", f["resumen"])
            linea += "\n   " + (resumen[:300].rstrip() + " […]" if len(resumen) > 300 else resumen)
        out.append(linea)
    return "\n".join(out)


def _ficha_criterio(html_doc: str) -> dict:
    def bloque(id_):
        m = re.search(r"<div id='" + id_ + r"'[^>]*>(.*?)</div>", html_doc, re.S)
        return m.group(1) if m else ""
    def dato(id_):
        return _limpiar(re.sub(r"^[^:]*:", "", _limpiar(bloque(id_)), count=1))
    ficha = {
        "n": "", "de": "", "rg": "", "calificacion": dato("criterioDatosCalificacion"),
        "unidad": dato("criterioDatosUnidad"), "fecha": dato("criterioDatosFecha"),
    }
    tit = _limpiar(bloque("criterioDatosTitulo"))
    m = re.search(r"Criterio\s+(\d+)\s+de\s+(\d+)\s+de la resoluci.n:\s*([\d/]+)", tit)
    if m:
        ficha["n"], ficha["de"], ficha["rg"] = m.group(1), m.group(2), m.group(3)
    m = re.search(r"<div id='criterioDatosAsunto'[^>]*>\s*<span[^>]*>Asunto:\s*</span>(.*?)</div>",
                  html_doc, re.S)
    ficha["asunto"] = _limpiar(m.group(1)) if m else ""
    m = re.search(r"<div id='criterioDatosContenido'[^>]*>\s*<span[^>]*>Criterio:\s*</span>(.*?)</div>",
                  html_doc, re.S)
    ficha["criterio"] = _limpiar(m.group(1)) if m else ""
    m = re.search(r"Referencias normativas:\s*</span>(.*?)</div>", html_doc, re.S)
    ficha["normas"] = re.sub(r"\n+", "; ", _limpiar(m.group(1))) if m else ""
    m = re.search(r"<span class='criterioNegrita'>Conceptos:\s*</span>(.*?)</div>", html_doc, re.S)
    ficha["conceptos"] = re.sub(r"\n+", " · ", _limpiar(m.group(1))) if m else ""
    return ficha


def leer(numero_rg: str, max_chars: int = 60000) -> str:
    """Ficha del criterio + TEXTO ÍNTEGRO de una resolución del TEAC/TEAR por su
    número de reclamación RG (p.ej. 00/06291/2024)."""
    numero_rg = (numero_rg or "").strip()
    directo = re.match(r"^(\d{2}/\d{5}/\d{4}/\d{2}/\d+/\d+)$", numero_rg)
    rg = _parse_rg(numero_rg)
    if not directo and not rg:
        return "Indica el número de reclamación, p.ej. 00/06291/2024 (o RG 6291/2024)."
    try:
        with _cliente() as c:
            if directo:
                ids = [directo.group(1)]
                sede, num, anio = numero_rg.split("/")[:3]
            else:
                sede, num, anio = rg
                r = c.get(_url_busqueda(rg=rg, criterios=True, resoluciones=True))
                _, filas = _parse_filas(r.text)
                ids = [f["id"] for f in filas if f["id"]]
                if not ids:
                    return (f"No encuentro la resolución {sede}/{num}/{anio} en la doctrina "
                            "del TEAC (DYCTEA solo recoge resoluciones con criterio publicado).")
            fichas = []
            for i in ids[:4]:
                d = c.get(BASE + "criterio.aspx?id=" + i)
                if d.status_code == 200:
                    fichas.append(_ficha_criterio(d.text))
            t = c.get(BASE + "textoresolucion.aspx?id=" + ids[0])
            texto = _limpiar(t.text) if t.status_code == 200 else ""
            # quitar chrome de la página si lo hubiera
            texto = re.sub(r"^.*?(Tribunal Econ)", r"\1", texto, count=1, flags=re.S) or texto
    except Exception as e:  # noqa: BLE001
        return f"No pude leer la resolución {numero_rg} del TEAC ahora mismo ({str(e)[:80]})."
    if not fichas and not texto:
        return f"Localicé {numero_rg} pero no pude extraer su contenido."
    f0 = fichas[0] if fichas else {}
    cab = f"【Resolución {f0.get('rg') or f'{sede}/{num}/{anio}'}】"
    meta = " · ".join(x for x in [f0.get("unidad"), f0.get("fecha"),
                                  ("Calificación: " + f0["calificacion"]) if f0.get("calificacion") else ""] if x)
    partes = [cab]
    if meta:
        partes.append(meta)
    for f in fichas:
        etiqueta = f" {f['n']} de {f['de']}" if f.get("de") and f["de"] != "1" else ""
        if f.get("asunto"):
            partes.append(f"\nASUNTO{etiqueta}: {f['asunto']}")
        if f.get("criterio"):
            partes.append(f"CRITERIO{etiqueta}:\n{f['criterio']}")
        if f.get("normas"):
            partes.append("Referencias normativas: " + f["normas"])
        if f.get("conceptos"):
            partes.append("Conceptos: " + f["conceptos"])
    if texto:
        if len(texto) > max_chars:
            texto = texto[:max_chars].rstrip() + " […]"
        partes.append("\nTEXTO ÍNTEGRO DE LA RESOLUCIÓN:\n" + texto)
    partes.append("\nFuente: doctrina de los tribunales económico-administrativos "
                  "(Ministerio de Hacienda).")
    return "\n".join(partes)
