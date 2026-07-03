# -*- coding: utf-8 -*-
"""
Motor DOCTRINA TRIBUTARIA — consultas de la Dirección General de Tributos (DGT)
desde el buscador oficial PETETE (petete.tributos.hacienda.gob.es/consultas).

Fuente oficial del Ministerio de Hacienda (consultas vinculantes y generales,
1997-hoy). HTML server-rendered (motor KnoSys), sin captcha ni login. Clave: el
endpoint de LECTURA exige cabeceras AJAX (X-Requested-With + Referer) o devuelve
401; con ellas basta httpx normal (no hace falta impersonar navegador). La
lectura solo autoriza IDs del último result-set de esa JSESSIONID, así que
búsqueda y lectura van en la MISMA sesión (httpx.Client mantiene la cookie).
"""
import os
import re
import html as _html
import urllib.parse as _up
import httpx

BASE = "https://petete.tributos.hacienda.gob.es/consultas"
_AJAX = {"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/",
         "Accept": "text/html, */*; q=0.01"}
# petete usa una CA del sector público español (FNMT) que NO está en el bundle
# certifi (ni en Windows ni en el runtime de Vercel) -> la verificación TLS falla
# con CERTIFICATE_VERIFY_FAILED. Es un sitio PÚBLICO y de SOLO LECTURA, así que
# desactivamos la verificación por defecto (se puede forzar con DGT_TLS_VERIFY=1).
_VERIFY = os.environ.get("DGT_TLS_VERIFY", "0") != "0"


def _session():
    """httpx.Client con las cabeceras AJAX y la cookie JSESSIONID sembrada."""
    c = httpx.Client(verify=_VERIFY, timeout=25, headers=_AJAX, follow_redirects=True)
    c.get(BASE + "/")  # siembra la cookie de sesión
    return c


def _limpiar(fragmento_html: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", fragmento_html or "")
    return re.sub(r"\s+", " ", _html.unescape(txt)).strip()


def _fila(html_doc: str, cls: str) -> str:
    """Valor de una fila <tr class="cls">, sin la etiqueta <th> (label)."""
    m = re.search(r'<tr class="' + re.escape(cls) + r'">(.*?)</tr>', html_doc, re.S)
    if not m:
        return ""
    bloque = m.group(1)
    val = re.search(r'<td class="value">(.*?)</td>', bloque, re.S)
    return _limpiar(val.group(1) if val else re.sub(r"<th\b.*?</th>", "", bloque, flags=re.S))


def _params_busqueda(texto, numero, desde, hasta, normativa, tipo):
    p = {"page": "1", "cmpOrder": "FECHA-SALIDA", "dirOrder": "1"}  # recientes primero
    t = (tipo or "vinculantes").lower()
    generales = t.startswith("gen") or t in ("ambas", "todas", "todos")
    vinculantes = not t.startswith("gen") or t in ("ambas", "todas", "todos")
    if vinculantes:
        p["type2"] = "on"
    if generales:
        p["type1"] = "on"
    p["tab"] = "1" if (generales and not t.startswith("vinc") and not vinculantes) else "2"
    n = 1
    if numero:
        p[f"NMCMP_{n}"] = "NUM-CONSULTA"; p[f"VLCMP_{n}"] = numero.strip().upper(); p[f"OPCMP_{n}"] = ".Y"; n += 1
    if desde or hasta:
        p["NMCMP_2"] = "FECHA-SALIDA"; p["VLCMP_2"] = ""
        p["dateIni_2"] = desde or ""; p["dateEnd_2"] = hasta or ""; p["OPCMP_2"] = ".Y"
    if normativa:
        p["NMCMP_3"] = "NORMATIVA"; p["VLCMP_3"] = normativa.strip(); p["OPCMP_3"] = ".Y"
    if texto:
        p["NMCMP_6"] = "FreeText"; p["VLCMP_6"] = texto.strip(); p["OPCMP_6"] = ".Y"
    return p


def _parse_listado(htmltxt: str):
    total = None
    mt = re.search(r'updateNumResults\("?\d+"?,\s*"?(\d+)"?\)', htmltxt)
    if mt:
        total = mt.group(1)
    filas = re.findall(r'<td id="doc_(\d+)"[^>]*viewDocument\((\d+),\s*(\d+)\).*?</td>',
                       htmltxt, re.S)
    docs = []
    for bloque in re.findall(r'(<td id="doc_\d+".*?</td>)', htmltxt, re.S):
        did = re.search(r'id="doc_(\d+)"', bloque)
        num = re.search(r'class="NUM-CONSULTA">(.*?)</span>', bloque, re.S)
        hec = re.search(r'class="DESCRIPCION-HECHOS">(.*?)</span>', bloque, re.S)
        cue = re.search(r'class="CUESTION-PLANTEADA">(.*?)</span>', bloque, re.S)
        docs.append({"docid": did.group(1) if did else "",
                     "num": _limpiar(num.group(1)) if num else "?",
                     "hechos": _limpiar(hec.group(1)) if hec else "",
                     "cuestion": _limpiar(cue.group(1)) if cue else ""})
    return total, docs


def buscar(texto: str = "", numero: str = "", desde: str = "", hasta: str = "",
           normativa: str = "", tipo: str = "vinculantes", limite: int = 15) -> str:
    """Lista consultas de la DGT (doctrina tributaria) por texto libre, número,
    normativa citada o rango de fechas. Ordena por más reciente."""
    if not any([texto, numero, normativa, desde, hasta]):
        return "Indica qué buscar: un texto (p.ej. 'exención IVA enseñanza online'), un número (V0282-26), una normativa o un rango de fechas."
    try:
        s = _session()
        # URL montada a mano (ASCII vía urlencode): httpx en el runtime peta al
        # codificar params no-ASCII (tildes/ñ); así el crash desaparece.
        qs = _up.urlencode(_params_busqueda(texto, numero, desde, hasta, normativa, tipo))
        r = s.get(f"{BASE}/do/search?{qs}")
        if getattr(r, "status_code", 0) != 200:
            return f"El buscador de la DGT respondió HTTP {getattr(r, 'status_code', '?')}."
        total, docs = _parse_listado(r.text)
    except Exception as e:  # noqa: BLE001
        return f"No pude consultar la doctrina de la DGT ahora mismo ({str(e)[:80]})."
    if not docs:
        return (f"Sin consultas de la DGT para esa búsqueda"
                + (f" ({texto!r})" if texto else "") + ". Prueba otros términos o quita filtros.")
    docs = docs[:max(1, int(limite))]
    tt = "vinculantes" if (tipo or "vinculantes").lower().startswith("vinc") else (tipo or "")
    out = [f"{len(docs)} consultas de la DGT (total: {total or '?'}) {tt}, más recientes primero. "
           "Para el texto íntegro de una: leer_consulta_hacienda con su número.\n"]
    for i, d in enumerate(docs, 1):
        linea = f"{i}. Consulta {d['num']}"
        if d["hechos"]:
            linea += f"\n   HECHOS: " + (d["hechos"][:240] + " […]" if len(d["hechos"]) > 240 else d["hechos"])
        if d["cuestion"]:
            linea += f"\n   CUESTIÓN: " + (d["cuestion"][:240] + " […]" if len(d["cuestion"]) > 240 else d["cuestion"])
        out.append(linea)
    return "\n".join(out)


def leer(numero: str, max_chars: int = 12000) -> str:
    """Texto ÍNTEGRO de una consulta de la DGT por su número (p.ej. V0282-26):
    órgano, fecha, normativa, hechos, cuestión y contestación completa."""
    numero = (numero or "").strip().upper()
    if not re.match(r"[VC]?\d{3,4}-\d{2}", numero):
        return "Indica el número de la consulta, p.ej. V0282-26."
    try:
        s = _session()
        # 1) buscar por número (misma sesión) para obtener el ID interno + autorizar la lectura
        r = s.get(f"{BASE}/do/search?" + _up.urlencode(_params_busqueda("", numero, "", "", "", "vinculantes")))
        _, docs = _parse_listado(r.text)
        if not docs:  # probar como consulta general
            r = s.get(f"{BASE}/do/search?" + _up.urlencode(_params_busqueda("", numero, "", "", "", "generales")))
            _, docs = _parse_listado(r.text)
        if not docs or not docs[0]["docid"]:
            return f"No encuentro la consulta {numero} en la DGT."
        docid = docs[0]["docid"]
        tab = "2" if numero.startswith("V") else "1"
        d = s.get(f"{BASE}/do/document?" + _up.urlencode({"doc": docid, "tab": tab}))
        if getattr(d, "status_code", 0) != 200:
            return f"La DGT respondió HTTP {getattr(d, 'status_code', '?')} al leer {numero}."
        t = d.text
    except Exception as e:  # noqa: BLE001
        return f"No pude leer la consulta {numero} de la DGT ahora mismo ({str(e)[:80]})."
    num = _fila(t, "NUM-CONSULTA") or numero
    organo = _fila(t, "ORGANO")
    fecha = _fila(t, "FECHA-SALIDA")
    normativa = _fila(t, "NORMATIVA")
    hechos = _fila(t, "DESCRIPCION-HECHOS")
    cuestion = _fila(t, "CUESTION-PLANTEADA")
    contestacion = _fila(t, "CONTESTACION-COMPL")
    if not contestacion:
        return f"Localicé {numero} pero no pude extraer su contestación."
    if len(contestacion) > max_chars:
        contestacion = contestacion[:max_chars].rstrip() + " […]"
    partes = [f"【Consulta {num}】"]
    meta = " · ".join(x for x in [organo, fecha] if x)
    if meta:
        partes.append(meta)
    if normativa:
        partes.append(normativa if normativa.lower().startswith("normativa") else "Normativa: " + normativa)
    if hechos:
        partes.append("\n" + hechos)
    if cuestion:
        partes.append("\n" + cuestion)
    partes.append("\n" + contestacion)
    partes.append("\nFuente: consultas de la DGT (Ministerio de Hacienda).")
    return "\n".join(partes)
