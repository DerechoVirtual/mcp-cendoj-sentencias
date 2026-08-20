# -*- coding: utf-8 -*-
"""
Territorios del registro de convenios (REGCON): las 64 "autoridades laborales"
del Ministerio de Trabajo — 52 provincias + Ceuta/Melilla + 10 comunidades
pluriprovinciales + Estatal + Tierras del Ebro.

Sirve para dos cosas:
  1) Reconocer el territorio dentro de una pregunta en lenguaje natural
     ("el convenio de hosteleria de la Comunidad de Madrid").
  2) Expandir una COMUNIDAD a sus provincias: quien pregunta por "la Comunidad
     Valenciana" quiere tambien el convenio provincial de Valencia, Alicante y
     Castellon, porque la mayoria de los convenios de sector se registran en la
     provincia, no en la comunidad.
"""

# id REGCON -> (nombre oficial, tipo)   tipo: P provincia, C comunidad, E estatal
AUTORIDADES = {
    "1": ("Alava", "P"), "2": ("Albacete", "P"), "3": ("Alicante/Alacant", "P"),
    "4": ("Almeria", "P"), "5": ("Avila", "P"), "6": ("Badajoz", "P"),
    "7": ("Illes Balears", "P"), "8": ("Barcelona", "P"), "9": ("Burgos", "P"),
    "10": ("Caceres", "P"), "11": ("Cadiz", "P"), "12": ("Castellon/Castello", "P"),
    "13": ("Ciudad Real", "P"), "14": ("Cordoba", "P"), "15": ("A Coruna", "P"),
    "16": ("Cuenca", "P"), "17": ("Girona", "P"), "18": ("Granada", "P"),
    "19": ("Guadalajara", "P"), "20": ("Gipuzkoa", "P"), "21": ("Huelva", "P"),
    "22": ("Huesca", "P"), "23": ("Jaen", "P"), "24": ("Leon", "P"),
    "25": ("Lleida", "P"), "26": ("La Rioja", "P"), "27": ("Lugo", "P"),
    "28": ("Madrid", "P"), "29": ("Malaga", "P"), "30": ("Murcia", "P"),
    "31": ("Navarra", "P"), "32": ("Ourense", "P"), "33": ("Asturias", "P"),
    "34": ("Palencia", "P"), "35": ("Las Palmas", "P"), "36": ("Pontevedra", "P"),
    "37": ("Salamanca", "P"), "38": ("Santa Cruz de Tenerife", "P"),
    "39": ("Cantabria", "P"), "40": ("Segovia", "P"), "41": ("Sevilla", "P"),
    "42": ("Soria", "P"), "43": ("Tarragona", "P"), "44": ("Teruel", "P"),
    "45": ("Toledo", "P"), "46": ("Valencia/Valencia", "P"), "47": ("Valladolid", "P"),
    "48": ("Bizkaia", "P"), "49": ("Zamora", "P"), "50": ("Zaragoza", "P"),
    "51": ("Ceuta", "P"), "52": ("Melilla", "P"),
    "65": ("Tierras del Ebro", "P"),
    "53": ("Andalucia", "C"), "54": ("Aragon", "C"), "55": ("Canarias", "C"),
    "56": ("Castilla y Leon", "C"), "57": ("Castilla-La Mancha", "C"),
    "58": ("Cataluna", "C"), "59": ("Comunitat Valenciana", "C"),
    "60": ("Extremadura", "C"), "61": ("Galicia", "C"), "62": ("Pais Vasco", "C"),
    "63": ("Estatal", "E"),
}

# comunidad -> provincias que la integran (para ampliar la busqueda)
COMUNIDAD_PROVINCIAS = {
    "53": ["4", "11", "14", "18", "21", "23", "29", "41"],          # Andalucia
    "54": ["22", "44", "50"],                                       # Aragon
    "55": ["35", "38"],                                             # Canarias
    "56": ["5", "9", "24", "34", "37", "40", "42", "47", "49"],     # Castilla y Leon
    "57": ["2", "13", "16", "19", "45"],                            # Castilla-La Mancha
    "58": ["8", "17", "25", "43", "65"],                            # Cataluna
    "59": ["3", "12", "46"],                                        # Comunitat Valenciana
    "60": ["6", "10"],                                              # Extremadura
    "61": ["15", "27", "32", "36"],                                 # Galicia
    "62": ["1", "20", "48"],                                        # Pais Vasco
}

# Comunidades UNIPROVINCIALES: su autoridad ES la provincia (no hay entrada de
# comunidad en REGCON). Preguntar por "la Comunidad de Madrid" = autoridad 28.
UNIPROVINCIALES = {"7", "26", "28", "30", "31", "33", "39", "51", "52"}

# provincia -> comunidad a la que pertenece (para ampliar hacia arriba: quien
# pregunta por Alicante tambien quiere el convenio autonomico valenciano).
PROVINCIA_COMUNIDAD = {}
for _c, _provs in COMUNIDAD_PROVINCIAS.items():
    for _p in _provs:
        PROVINCIA_COMUNIDAD[_p] = _c

# Alias reconocibles en lenguaje natural -> id de autoridad. Se buscan por
# coincidencia de palabra completa, del mas largo al mas corto.
ALIAS = {
    # --- provincias, variantes linguisticas y ciudades principales ---
    "alava": "1", "araba": "1", "vitoria": "1", "gasteiz": "1",
    "albacete": "2",
    "alicante": "3", "alacant": "3", "elche": "3", "elx": "3", "benidorm": "3",
    "almeria": "4", "roquetas": "4",
    "avila": "5",
    "badajoz": "6", "merida": "6",
    "baleares": "7", "balears": "7", "illes balears": "7", "islas baleares": "7",
    "mallorca": "7", "menorca": "7", "ibiza": "7", "eivissa": "7",
    "palma de mallorca": "7",
    "barcelona": "8", "bcn": "8", "hospitalet": "8", "sabadell": "8",
    "terrassa": "8", "badalona": "8",
    "burgos": "9",
    "caceres": "10", "plasencia": "10",
    "cadiz": "11", "jerez": "11", "algeciras": "11",
    "castellon": "12", "castello": "12", "castellon de la plana": "12",
    "ciudad real": "13", "puertollano": "13",
    "cordoba": "14",
    "coruna": "15", "a coruna": "15", "la coruna": "15",
    "santiago de compostela": "15", "ferrol": "15",
    "cuenca": "16",
    "girona": "17", "gerona": "17",
    "granada": "18", "motril": "18",
    "guadalajara": "19",
    "guipuzcoa": "20", "gipuzkoa": "20", "san sebastian": "20", "donostia": "20",
    "huelva": "21",
    "huesca": "22", "jaca": "22",
    "jaen": "23", "linares": "23",
    "leon": "24", "ponferrada": "24",
    "lleida": "25", "lerida": "25",
    "rioja": "26", "la rioja": "26", "logrono": "26",
    "lugo": "27",
    "madrid": "28", "mostoles": "28", "alcala de henares": "28", "getafe": "28",
    "leganes": "28", "fuenlabrada": "28", "alcorcon": "28",
    "malaga": "29", "marbella": "29", "torremolinos": "29", "benalmadena": "29",
    "murcia": "30", "cartagena": "30", "lorca": "30",
    "navarra": "31", "nafarroa": "31", "pamplona": "31", "irunea": "31",
    "ourense": "32", "orense": "32",
    "asturias": "33", "oviedo": "33", "gijon": "33", "aviles": "33",
    "principado de asturias": "33",
    "palencia": "34",
    "las palmas": "35", "gran canaria": "35", "lanzarote": "35",
    "fuerteventura": "35", "las palmas de gran canaria": "35",
    "pontevedra": "36", "vigo": "36",
    "salamanca": "37",
    "santa cruz de tenerife": "38", "tenerife": "38", "la gomera": "38",
    "el hierro": "38", "santa cruz": "38",
    "cantabria": "39", "santander": "39", "torrelavega": "39",
    "segovia": "40",
    "sevilla": "41", "dos hermanas": "41",
    "soria": "42",
    "tarragona": "43", "reus": "43",
    "tierras del ebro": "65", "tortosa": "65",
    "teruel": "44",
    "toledo": "45", "talavera de la reina": "45", "illescas": "45",
    "valencia": "46", "gandia": "46", "torrent": "46", "paterna": "46",
    "valladolid": "47",
    "vizcaya": "48", "bizkaia": "48", "bilbao": "48", "barakaldo": "48",
    "zamora": "49",
    "zaragoza": "50",
    "ceuta": "51",
    "melilla": "52",
    # --- comunidades pluriprovinciales ---
    "andalucia": "53", "junta de andalucia": "53",
    "aragon": "54",
    "canarias": "55", "islas canarias": "55",
    "castilla y leon": "56", "castilla leon": "56",
    "castilla la mancha": "57", "castilla-la mancha": "57",
    "cataluna": "58", "catalunya": "58",
    "comunitat valenciana": "59", "comunidad valenciana": "59",
    "c valenciana": "59", "pais valenciano": "59",
    "generalitat valenciana": "59",
    "extremadura": "60",
    "galicia": "61", "galiza": "61",
    "pais vasco": "62", "euskadi": "62",
    # --- estatal ---
    "estatal": "63", "nacional": "63", "espana": "63", "todo el estado": "63",
    "ambito estatal": "63", "ambito nacional": "63", "sectorial estatal": "63",
    "interprovincial": "63", "de todo el pais": "63",
}
