# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/bop_barcelona_{municipios,config}.json (BOPB, Diputació de Barcelona).

Fuentes (todo EN VIVO, sin datos a mano):
  1. https://bop.diba.cat/cercador-butlletins  -> <option value="ID" data-parent-id="177">
     (177 = "Ajuntaments Província de Barcelona"). Los ids son NUMERICOS ("714") o
     con prefijo ("org-557"); los dos valen para bopb_cerca[tipologiaAnunciantBase].
  2. es.wikipedia "Anexo:Municipios de la provincia de Barcelona" -> los 311
     municipios oficiales con su nombre catalán oficial y su nombre en castellano.

Reglas:
  * SOLO ayuntamientos de la provincia de Barcelona. Se excluyen consorcios,
    organismos autónomos, EMD y el municipio intruso "Riudoms" (es de Tarragona).
  * Claves: nombre oficial catalán (artículo delante) + nombre castellano +
    variantes sin artículo, SIEMPRE que no colisionen con otro municipio de este
    mapa ni con los mapas de las demás provincias ya empaquetadas.

Uso:  ./.venv/Scripts/python.exe _gen_bop_barcelona.py [--dry]
"""
import glob
import html as H
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
AQUI = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(AQUI, "ordenanzas_data")
CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# entidades del select bajo 177 que NO son ayuntamientos de la provincia
EXCLUIR_IDS = {
    "org-2992907",   # "Organisme Demo" (pruebas)
    "org-527",       # "Riudoms" -> municipio de TARRAGONA (0 anuncios; secuestraría el enrutado)
    "org-3872919",   # Palau-solità i Plegamans. Serveis Municipals ... SL
    "org-3398275",   # Pobla de Lillet, La. Promoció Econòmica Lillet, SL
    "org-3782692",   # Santa Margarida de Montbui. Patronat Municipal d'Escoles Bressols
    "org-272",       # Bellaterra              -> EMD de Cerdanyola del Vallès
    "org-693",       # Valldoreix              -> EMD de Sant Cugat del Vallès
    "org-596",       # Sant Miquel de Balenyà  -> EMD de Seva/Balenyà
    "org-591",       # Sant Martí de Torroella -> EMD de Sant Joan de Vilatorrada
    "org-3127788",   # Bigues i Riells del Fai -> mismo municipio que org-275 (ver NOTA_BIGUES)
}
NOTA_BIGUES = ("org-275 cubre 1998-2021 (134 anuncios de Normativa) y org-3127788, "
               "creado tras el cambio de nombre a 'Bigues i Riells del Fai', cubre "
               "2024-2026 (31). El mapa usa org-275; para cobertura total el backend "
               "debería consultar los dos.")

# nombre oficial (wikipedia) -> etiqueta con la que aparece en el select del BOPB
ALIAS_SELECT = {
    "Bigues i Riells del Fai": "Bigues i Riells",   # el select conserva el nombre anterior a 2021
}
# alias que NO se generan aunque salgan de quitar el artículo: son el nombre de un
# municipio de OTRA provincia (aún no empaquetada) y secuestrarían su enrutado.
ALIAS_VETADOS = {"quart"}                           # Quart (Gironès), de "La Quar" -> "Quart"


def _op():
    o = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
    o.addheaders = [("User-Agent", UA), ("Accept-Language", "ca,es;q=0.9")]
    return o


def _norm(s):
    """Misma normalización que bop_engine._norm."""
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def _art(s):
    """'Ametlla del Vallès, l'' -> \"L'Ametlla del Vallès\";  'Bruc, el' -> 'El Bruc'."""
    m = re.match(r"^(.*),\s*(l'|el|els|la|les|es)$", s.strip(), re.I)
    if not m:
        return s.strip()
    base, a = m.group(1).strip(), m.group(2).lower()
    return (a.capitalize() + base) if a == "l'" else (a.capitalize() + " " + base)


def _sin_art(s):
    """\"L'Ametlla del Vallès\" -> 'Ametlla del Vallès';  'El Bruc' -> 'Bruc'."""
    return re.sub(r"^(l'|els?\s+|les?\s+|la\s+)", "", s.strip(), flags=re.I).strip()


def _link(t):
    t = t.strip()
    m = re.match(r"^\[\[([^\]|]+)\|([^\]]+)\]\]$", t) or re.match(r"^\[\[([^\]]+)\]\]$", t)
    if not m:
        return t
    return (m.group(2) if m.lastindex == 2 else m.group(1)).strip()


# ---------------------------------------------------------------- 1. select BOPB
def opciones_bopb():
    h = _op().open("https://bop.diba.cat/cercador-butlletins", timeout=60).read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(r"<option\b([^>]*)>(.*?)</option>", h, re.S):
        a, t = m.group(1), m.group(2)
        v = re.search(r'value="([^"]*)"', a)
        p = re.search(r'data-parent-id="([^"]*)"', a)
        if v and p and p.group(1) == "177":
            out.append((v.group(1), re.sub(r"\s+", " ", H.unescape(re.sub("<[^>]+>", "", t))).strip()))
    return out


# ------------------------------------------------------- 2. los 311 oficiales
def municipios_oficiales():
    u = ("https://es.wikipedia.org/w/api.php?action=parse&format=json&formatversion=2&prop=wikitext"
         "&page=" + urllib.parse.quote("Anexo:Municipios de la provincia de Barcelona"))
    w = json.loads(_op().open(u, timeout=60).read().decode("utf-8"))["parse"]["wikitext"]
    filas = re.findall(r"\n\|-\n\| ?([^\n]*)\n\| ?([^\n]*)\n\| align=\"right\" ?\| ?([\d.]+)\n", w)
    return [{"oficial": _link(o), "castellano": _link(c), "pob": int(p.replace(".", ""))}
            for o, c, p in filas]


# ------------------------------------------------------- 3. colisiones externas
def claves_otras_provincias():
    """{clave_normalizada: 'fichero'} de todos los mapas ya empaquetados."""
    fuera = {}
    for f in sorted(glob.glob(os.path.join(DATA, "bop_*_municipios.json"))):
        if os.path.basename(f) == "bop_barcelona_municipios.json":
            continue
        try:
            m = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for k in m:
            limpio = re.sub(r"^(?:EXCMO\.?\s+)?(?:AYUNTAMIENTO|AYTO\.?|CONCELLO|CONCEJO|AJUNTAMENT|UDALA)"
                            r"\s+(?:DE\s+LA\s+|DE\s+L'|DEL?\s+|D')?", "", k.strip(), flags=re.I)
            fuera.setdefault(_norm(limpio), os.path.basename(f))
    return fuera


def main(dry=False):
    opts = opciones_bopb()
    print("opciones del select (parent 177): %d" % len(opts))
    ofi = municipios_oficiales()
    print("municipios oficiales (wikipedia): %d" % len(ofi))

    idx = {}
    for oid, txt in opts:
        if oid in EXCLUIR_IDS:
            continue
        for k in (txt, _art(txt)):
            idx.setdefault(_norm(k), (oid, _art(txt)))

    mapa, sin_id, usados = {}, [], set()
    fuera = claves_otras_provincias()
    colisiones = []

    def poner(clave, oid):
        n = _norm(clave)
        if not n or n in ALIAS_VETADOS:
            return
        if n in mapa_norm:                      # ya la tenemos en este mapa
            if mapa_norm[n] != oid:
                colisiones.append(("interna", clave, oid, mapa_norm[n]))
            return
        if n in fuera:                          # colisión con otra provincia
            colisiones.append(("externa", clave, oid, fuera[n]))
            return
        mapa[clave] = oid
        mapa_norm[n] = oid

    mapa_norm = {}
    for m in sorted(ofi, key=lambda x: -x["pob"]):
        cand = None
        for k in (m["oficial"], _art(m["oficial"]), _sin_art(m["oficial"]), m["castellano"],
                  ALIAS_SELECT.get(m["oficial"], "")):
            if _norm(k) in idx:
                cand = idx[_norm(k)]
                break
        if not cand:
            sin_id.append(m)
            continue
        oid = cand[0]
        usados.add(oid)
        poner(m["oficial"], oid)                                  # nombre oficial catalán
        if _norm(m["castellano"]) != _norm(m["oficial"]):
            poner(m["castellano"], oid)                           # nombre en castellano
        for v in (_sin_art(m["oficial"]), _sin_art(m["castellano"]), ALIAS_SELECT.get(m["oficial"], "")):
            if v and _norm(v) != _norm(m["oficial"]):
                poner(v, oid)                                     # sin artículo / nombre anterior

    print("\nmunicipios mapeados: %d  (claves totales: %d)" % (len(usados), len(mapa)))
    if sin_id:
        print("SIN id en el select (%d):" % len(sin_id))
        for m in sin_id:
            print("   ", m["oficial"], "/", m["castellano"], m["pob"])
    sobran = [(o, t) for o, t in opts if o not in usados and o not in EXCLUIR_IDS]
    if sobran:
        print("opciones del select NO usadas (%d):" % len(sobran))
        for o, t in sobran:
            print("   ", o, "|", t)
    if colisiones:
        print("\nCOLISIONES descartadas (%d):" % len(colisiones))
        for tipo, clave, oid, contra in colisiones:
            print("   [%s] %-38s (%s)  choca con %s" % (tipo, clave, oid, contra))

    cfg = {
        "id": "barcelona",
        "base": "https://bop.diba.cat",
        "mapa": "bop_barcelona_municipios.json",
        "nombre": "Barcelona",
        "familia": "barcelona",
        "indice_desde": 1998,
        "idioma": "ca",
        "activo": False,
        "nota": ("BOPB (app Symfony de la Diputació). Búsqueda RÁPIDA = filtro por ayuntamiento "
                 "+ tipo de anuncio, SIN texto libre ni fechas: GET /resultats-cerca?"
                 "bopb_cerca[tipologiaAnunciantBase]=<id>&bopb_cerca[tipusAnunciBase]=40 "
                 "(40=Normativa; 41=ordenances fiscals, 42=ordenances reguladores i reglaments); "
                 "20 resultados/página, orden ASCENDENTE por fecha, paginación /resultats-cerca/<n>. "
                 "SIN cookies (una sesión compartida serializa las peticiones). Texto libre "
                 "(paraulaClau) 19-38 s y rango de fechas ~7 s: NO usarlos. Títulos en CATALÁN. "
                 "PDF del anuncio en /anunci/descarrega-pdf/<id>: desde 2011 es el anuncio suelto "
                 "(94 % con capa de texto, sin OCR); ANTES de 2011 devuelve el BUTLLETÍ ENTERO "
                 "(96-144 págs) y hay que recortar por título. " + NOTA_BIGUES),
    }
    if dry:
        print("\n[dry] no se escribe nada")
        return
    with open(os.path.join(DATA, "bop_barcelona_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(mapa.items(), key=lambda kv: _norm(kv[0]))), f, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA, "bop_barcelona_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print("\nescritos ordenanzas_data/bop_barcelona_municipios.json y bop_barcelona_config.json")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
