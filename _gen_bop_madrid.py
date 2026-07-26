# -*- coding: utf-8 -*-
"""Genera el mapa municipio->tid y la config del BOP de MADRID (BOCM, Drupal 7).

El BOCM es el boletín de la Comunidad de Madrid y hace de BOP (uniprovincial).
Mapa = <select name="field_orden_organo_y_organismo_3"> de /advanced-search:
las opciones top-level son entidades (ayuntamientos y otros); las que empiezan
por '-' son organismos dependientes (patronatos, empresas municipales...).
"""
import html as _html
import json
import os
import re
import sys
import urllib.request

BASE = "https://www.bocm.es"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")

# entidades que NO son ayuntamientos (se excluyen del mapa: no son municipios)
NO_MUNI = re.compile(r"mancomunidad|consorcio|comunidad de madrid|federaci|"
                     r"universidad|c[aá]mara|colegio|confederaci|canal de isabel|"
                     r"empresa municipal|patronato|instituto|fundaci|agencia|"
                     r"entidad urban[ií]stica|junta de compensaci|servicio de emergencias", re.I)

# Entidades de OTRAS provincias que aparecen en el <select> del BOCM (publicaron
# alguna vez ahí). Si se cuelan, secuestran el enrutado nacional de municipios.
NO_MADRID = {"arjonilla", "cenicero", "aguarda", "alicante", "barcelona", "burgos",
             "albacete", "guadalajara", "astorga", "consuegra", "carranque", "chipiona",
             "fuengirola", "inca", "alameda", "alburquerque", "alcazardesanjuan",
             "beniarbeig", "bejar", "cabanes", "cabezarados", "cadreita",
             "campodecriptana", "canals", "castellgali", "ciudadrodrigo",
             "concellodecuntis", "corveradeasturias", "cudillero", "deltebre",
             "forallac", "granjadetorrehermosa", "guijuelo", "hinojos",
             "jerezdeloscaballeros", "burguillosdelcerro", "campodesanpedro",
             "carrascaldelrio", "cilleruelodesanmames", "ayuntamientos"}


def get(url, timeout=90):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-ES,es"}),
        timeout=timeout).read().decode("utf-8", "replace")


# Municipios de la Comunidad de Madrid que SOLO existen en el vocabulario heredado
# del <select> (no tienen entrada en MAYÚSCULAS). Curada a mano: todo lo demás del
# heredado es de otras provincias y se descarta.
MADRID_EXTRA = [
    "Aranjuez", "Arganda del Rey", "Ajalvir", "Algete", "Alpedrete", "Aldea del Fresno",
    "Ambite", "Anchuelo", "Berzosa de Lozoya", "Braojos", "Brea del Tajo",
    "Buitrago del Lozoya", "Buitrago de Lozoya", "Cervera de Buitrago", "El Vellón",
    "Fuentidueña del Tajo", "Horcajo de la Sierra-Aoslos", "Campo Real", "Casarrubuelos",
    "Cenicientos", "Chapinería", "Colmenar de Oreja", "Corpa", "Cubas de la Sagra",
    "Daganzo de Arriba", "Fresnedillas de la Oliva", "Fuente el Saz de Jarama",
    "Garganta de los Montes", "Gargantilla del Lozoya y Pinilla de Buitrago",
    "Gascones", "Griñón", "Guadalix de la Sierra", "Hoyo de Manzanares",
    "Loeches", "Lozoya", "Lozoyuela-Navas-Sieteiglesias", "Madarcos", "Manzanares el Real",
    "Meco", "Mejorada del Campo", "Miraflores de la Sierra", "Montejo de la Sierra",
    "Moraleja de Enmedio", "Moralzarzal", "Morata de Tajuña", "Navacerrada",
    "Navalafuente", "Navarredonda y San Mamés", "Nuevo Baztán", "Olmeda de las Fuentes",
    "Orusco de Tajuña", "Paracuellos de Jarama", "Patones", "Pedrezuela",
    "Pelayos de la Presa", "Perales de Tajuña", "Pezuela de las Torres",
    "Pinilla del Valle", "Piñuécar-Gandullas", "Prádena del Rincón", "Puebla de la Sierra",
    "Puentes Viejas", "Quijorna", "Rascafría", "Redueña", "Ribatejada", "Robledillo de la Jara",
    "Robledo de Chavela", "Robregordo", "Rozas de Puerto Real", "San Agustín del Guadalix",
    "San Martín de la Vega", "San Martín de Valdeiglesias", "Santa María de la Alameda",
    "Santorcaz", "Los Santos de la Humosa", "La Serna del Monte", "Serranillos del Valle",
    "Sevilla la Nueva", "Somosierra", "Soto del Real", "Talamanca de Jarama",
    "Tielmes", "Titulcia", "Torrelaguna", "Torrelodones", "Torremocha de Jarama",
    "Torres de la Alameda", "Valdaracete", "Valdeavero", "Valdelaguna", "Valdemanco",
    "Valdemaqueda", "Valdeolmos-Alalpardo", "Valdepiélagos", "Valdetorres de Jarama",
    "Valdilecha", "Valverde de Alcalá", "Velilla de San Antonio", "El Berrueco",
    "El Boalo", "El Escorial", "El Molar", "Los Molinos", "Villa del Prado",
    "Villaconejos", "Villalbilla", "Villamanrique de Tajo", "Villamanta",
    "Villamantilla", "Villanueva de la Cañada", "Villanueva del Pardillo",
    "Villanueva de Perales", "Villar del Olmo", "Villarejo de Salvanés",
    "Villavieja del Lozoya", "Zarzalejo", "Entidad Local Menor Belvis de Jarama",
    "Entidad Local Menor Real Cortijo de San Isidro",
]


def _norm(s):
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower())
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def _titulo(s):
    """'ALCALÁ DE HENARES' -> 'Alcalá de Henares' (respeta partículas)."""
    men = {"de", "del", "la", "las", "los", "el", "y", "en", "a", "al"}
    ps = [p for p in re.split(r"\s+", s.strip().lower()) if p]
    out = []
    for i, p in enumerate(ps):
        out.append(p if (i and p in men) else "-".join(x.capitalize() for x in p.split("-")))
    return " ".join(out)


def mapa():
    """El <select> trae DOS vocabularios para lo mismo:
      * MAYÚSCULAS + tid 12xxx  -> taxonomía viva del buscador (la que filtra bien)
      * 'Ayuntamiento de X' + tid 8xxx -> heredada
    Nos quedamos con la de MAYÚSCULAS y solo caemos a la otra si no existe."""
    h = get(BASE + "/advanced-search")
    m = re.search(r'<select[^>]*name="field_orden_organo_y_organismo_3"[^>]*>(.*?)</select>', h, re.S)
    if not m:
        raise SystemExit("No encuentro el <select> de organismos")
    mayus, legado, saltadas = {}, {}, []
    for tid, txt in re.findall(r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', m.group(1), re.S):
        t = _html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
        if not t or t.startswith("-"):        # organismo dependiente
            continue
        nom = re.sub(r"^Ayuntamiento de\s+", "", t, flags=re.I).strip()
        if NO_MUNI.search(nom) or _norm(nom) in NO_MADRID:
            saltadas.append(nom)
            continue
        if t.upper() == t and re.search(r"[A-ZÁÉÍÓÚÜÑ]", t):
            mayus.setdefault(_titulo(t), tid)
        else:
            legado.setdefault(nom, tid)
    # ⚠️ El vocabulario HEREDADO (8xxx/1xxxx) contiene entidades de TODA ESPAÑA que
    # alguna vez publicaron en el BOCM (Barcelona, Alicante, Burgos, Fuengirola,
    # Chipiona, Inca...). Meterlas secuestraría el enrutado nacional de municipios.
    # Por eso: base = taxonomía viva (MAYÚSCULAS/12xxx) y del heredado SOLO se
    # admiten municipios de la Comunidad de Madrid explícitamente listados.
    out = dict(mayus)
    for k, v in legado.items():
        if _norm(k) in {_norm(x) for x in MADRID_EXTRA}:
            out.setdefault(k, v)
    return out, saltadas


if __name__ == "__main__":
    mp, sk = mapa()
    print(f"municipios: {len(mp)}   (descartadas {len(sk)} entidades no municipales)")
    print("  ejemplos:", list(mp.items())[:6])
    print("  descartadas:", sk[:12])
    for k in ("Móstoles", "Getafe", "Alcalá de Henares", "Madrid", "Pozuelo de Alarcón",
              "Rivas-Vaciamadrid", "San Sebastián de los Reyes", "Torrejón de Ardoz"):
        print(f"    {k:32s} -> {mp.get(k)}")
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "bop_madrid_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(mp.items())), f, ensure_ascii=False, indent=1)
    cfg = {"id": "madrid", "base": BASE, "mapa": "bop_madrid_municipios.json",
           "nombre": "Madrid", "familia": "madrid", "indice_desde": 2010, "fulltext": True, "verifica_texto": True,
           "seccion": "8387"}
    with open(os.path.join(DATA, "bop_madrid_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print("escritos bop_madrid_municipios.json + bop_madrid_config.json")
