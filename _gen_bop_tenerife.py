# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/bop_tenerife_{municipios,config}.json.

Cruza la lista oficial INE de los 54 municipios de la provincia de Santa Cruz de
Tenerife (Tenerife, La Palma, La Gomera, El Hierro) con los ORGANISMOS reales que
devuelve el buscador del BOP (cosechados con _probe_tenerife3.py) y deja como valor
el nombre canónico del organismo (la variante más frecuente en el buscador).
"""
import json, os, re, unicodedata, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")

# 54 municipios oficiales (INE) de la provincia de Santa Cruz de Tenerife
MUNICIPIOS = [
    # --- Tenerife (31) ---
    "Adeje", "Arafo", "Arico", "Arona", "Buenavista del Norte", "Candelaria",
    "Fasnia", "Garachico", "Granadilla de Abona", "La Guancha", "Guía de Isora",
    "Güímar", "Icod de los Vinos", "La Matanza de Acentejo", "La Orotava",
    "Puerto de la Cruz", "Los Realejos", "El Rosario", "San Cristóbal de La Laguna",
    "San Juan de la Rambla", "San Miguel de Abona", "Santa Cruz de Tenerife",
    "Santa Úrsula", "Santiago del Teide", "El Sauzal", "Los Silos", "Tacoronte",
    "El Tanque", "Tegueste", "La Victoria de Acentejo", "Vilaflor de Chasna",
    # --- La Palma (14) ---
    "Barlovento", "Breña Alta", "Breña Baja", "Fuencaliente de la Palma",
    "Garafía", "Los Llanos de Aridane", "El Paso", "Puntagorda", "Puntallana",
    "San Andrés y Sauces", "Santa Cruz de la Palma", "Tazacorte", "Tijarafe",
    "Villa de Mazo",
    # --- La Gomera (6) ---
    "Agulo", "Alajeró", "Hermigua", "San Sebastián de la Gomera", "Valle Gran Rey",
    "Vallehermoso",
    # --- El Hierro (3) ---
    "Frontera", "El Pinar de El Hierro", "Valverde",
]

# prefijos de entidad tal cual aparecen en el BOP (incluidas erratas reales:
# AAYUNTAMIENTO, AYAYUNTAMIENTO, YUNTAMIENTO, M.I. AYUNTAMIENTO)
PREF = re.compile(r"^\s*(?:M\.?\s*I\.?\s*)?[AY]*UNTAMIENTO\b\s*(?:DE\s+)?", re.I)
VILLA = re.compile(r"^\s*(?:LA\s+)?VILLA\s+DE\s+", re.I)
ART = re.compile(r"^\s*(?:EL|LA|LOS|LAS)\s+", re.I)


def sinac(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def clave(s):
    """Clave de comparación: sin entidad, sin 'Villa de', sin artículo, sin tildes."""
    s = sinac(s or "").upper()
    s = PREF.sub("", s)
    s = VILLA.sub("", s)
    s = re.sub(r"^\s*DE\s+", "", s)      # errata "VILLA DE DE TEGUESTE"
    s = ART.sub("", s)
    s = re.sub(r"\bY\s+SAUCE\b", "Y SAUCES", s)          # errata "SAN ANDRÉS Y SAUCE"
    s = re.sub(r"\bBUENA\s+VISTA\b", "BUENAVISTA", s)     # errata "BUENA VISTA DEL NORTE"
    s = re.sub(r"\bVILAFOR\b", "VILAFLOR", s)             # errata "VILAFOR DE CHASNA"
    s = re.sub(r"\bGUIMAR\b", "GUIMAR", s)
    s = re.sub(r"\bDE\s+DE\b", "DE", s)                   # errata "VILLA DE DE TEGUESTE"
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s


# variantes de nombre corto admitidas (municipio -> claves extra que valen)
EQUIV = {
    "Fuencaliente de la Palma": ["FUENCALIENTE"],
    "Villa de Mazo": ["MAZO"],
    "Frontera": ["FRONTERA"],
    "El Pinar de El Hierro": ["PINARDEELHIERRO"],
    "Vilaflor de Chasna": ["VILAFLOR"],
    "Santa Cruz de la Palma": ["SANTACRUZDELAPALMA"],
}


def main():
    org = json.load(open(os.path.join(HERE, "_tmp_tfe_organismos.json"), encoding="utf-8"))
    # solo AYUNTAMIENTOS (fuera cabildos, consorcios, mancomunidades, consejos, fundaciones...)
    ayto = {k: v for k, v in org.items() if PREF.match(k)}
    porclave = collections.defaultdict(collections.Counter)
    for k, v in ayto.items():
        porclave[clave(k)][k] += v

    mapa, faltan, sobran = {}, [], dict(porclave)
    for m in MUNICIPIOS:
        claves = [clave(m)] + [clave(x) for x in EQUIV.get(m, [])]
        cand = collections.Counter()
        for c in claves:
            if c in porclave:
                cand.update(porclave[c])
                sobran.pop(c, None)
        if not cand:
            faltan.append(m)
            continue
        canon = max(cand.items(), key=lambda kv: (kv[1], kv[0].isupper()))[0]
        mapa[m] = canon.upper()

    print(f"municipios mapeados: {len(mapa)}/{len(MUNICIPIOS)}")
    if faltan:
        print("SIN ORGANISMO EN EL BUSCADOR:", faltan)
    if sobran:
        print("AYUNTAMIENTOS DEL BUSCADOR NO ASIGNADOS (revisar: ajenos a la provincia):")
        for c, cc in sorted(sobran.items()):
            print("   ", c, dict(cc))

    json.dump(mapa, open(os.path.join(DATA, "bop_tenerife_municipios.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, sort_keys=True)

    cfg = {
        "id": "tenerife",
        "base": "https://www.bopsantacruzdetenerife.es",
        "mapa": "bop_tenerife_municipios.json",
        "nombre": "Santa Cruz de Tenerife",
        "familia": "tenerife",
        # el desplegable del buscador ofrece 1992-2026, pero la BBDD solo devuelve
        # resultados desde 2002 (2001 y anteriores = 0 en vivo, verificado 26-jul-2026)
        "indice_desde": 2002,
        # OJO: sin backend `familia == "tenerife"` en bop_engine._buscar_raw/_texto,
        # registrar la provincia la enrutaria al backend Saga y romperia los 54
        # municipios. Quitar `activo` SOLO al aterrizar el backend.
        "activo": False,
        "nota": "pendiente de backend en bop_engine (26-jul-2026)",
    }
    json.dump(cfg, open(os.path.join(DATA, "bop_tenerife_config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("escritos bop_tenerife_municipios.json y bop_tenerife_config.json")


if __name__ == "__main__":
    main()
