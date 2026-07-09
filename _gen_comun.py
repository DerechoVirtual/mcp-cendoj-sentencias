# -*- coding: utf-8 -*-
"""
Utilidades comunes de los generadores de catálogos de ordenanzas municipales
(_gen_catalogo_*.py). Offline: excluido del deploy por el patrón `_*`.

Pieza clave: TESAURO materia→alias. A partir del TÍTULO de cada norma deriva
los alias coloquiales que un abogado escribiría ("plusvalía", "ZBE", "vado",
"botellón"...), para que el scoring de buscar_ordenanzas acierte sin curar
alias a mano ciudad a ciudad. Cada generador puede añadir extras por id.
"""
import re
import unicodedata


def norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# (regex sobre el título NORMALIZADO, alias que añade)
TESAURO = [
    (r"ruido|acustic|vibracion", ["ruido", "ruidos", "contaminacion acustica",
                                  "decibelios", "insonorizacion", "molestias por ruido"]),
    (r"terraza|velador", ["terraza", "terrazas", "veladores", "mesas y sillas",
                          "terraza de bar", "horario de terrazas", "hosteleria"]),
    (r"limpieza|residuo|basura", ["limpieza", "residuos", "basura", "basuras",
                                  "contenedores", "recogida de residuos", "escombros",
                                  "pintadas", "grafitis", "punto limpio"]),
    (r"movilidad|circulacion|trafico", ["movilidad", "trafico", "circulacion",
                                        "estacionamiento", "aparcamiento", "bicicleta",
                                        "patinete", "vmp", "peatones",
                                        "multa de trafico", "grua", "carril bici"]),
    (r"mesas y sillas|mesas, sillas", ["terraza", "terrazas", "veladores",
                                       "mesas y sillas", "tasa de terrazas"]),
    (r"estacionamiento regulado|\bora\b|zona azul", ["ora", "zona azul", "zona verde",
                                                     "estacionamiento regulado", "ser",
                                                     "parquimetro"]),
    (r"zonas? de bajas emisiones|\bzbe\b", ["zbe", "zona de bajas emisiones",
                                            "distintivo ambiental",
                                            "restriccion de trafico",
                                            "etiqueta ambiental"]),
    (r"\btaxi\b", ["taxi", "autotaxi", "eurotaxi", "licencia de taxi", "taximetro"]),
    (r"vado|paso de vehiculo|paso de carruaje|entrada de vehiculo",
     ["vado", "vados", "paso de vehiculos", "entrada de vehiculos"]),
    (r"animal", ["animales", "perros", "gatos", "mascotas", "ppp",
                 "perros potencialmente peligrosos", "tenencia de animales",
                 "colonias felinas"]),
    (r"venta ambulante|mercadillo|no sedentaria", ["venta ambulante", "mercadillo",
                                                   "mercadillos", "puestos ambulantes",
                                                   "food truck"]),
    (r"mercado", ["mercados municipales", "mercado municipal"]),
    (r"bienes inmuebles", ["ibi", "impuesto sobre bienes inmuebles", "contribucion"]),
    (r"vehiculos de traccion mecanica", ["ivtm", "impuesto de vehiculos",
                                         "impuesto de circulacion"]),
    (r"incremento de[l]? valor|plusvalia", ["plusvalia", "plusvalia municipal", "iivtnu"]),
    (r"actividades economicas", ["iae", "impuesto de actividades economicas"]),
    (r"construcciones instalaciones y obras", ["icio", "impuesto de obras"]),
    (r"\bagua\b|abastecimiento|saneamiento|alcantarillado",
     ["agua", "abastecimiento", "saneamiento", "alcantarillado"]),
    (r"alcohol|botellon", ["botellon", "consumo de alcohol en la via publica",
                           "beber en la calle"]),
    (r"convivencia|civismo|espacio publico", ["convivencia", "civismo", "botellon",
                                              "vandalismo", "conductas incivicas"]),
    (r"publicidad", ["publicidad exterior", "carteles", "rotulos", "vallas publicitarias",
                     "lonas"]),
    (r"urbanistic|licencias|declaracion responsable|obras y actividades",
     ["licencia urbanistica", "licencia de obras", "declaracion responsable",
      "licencia de actividad", "licencia de apertura", "obras"]),
    (r"inspeccion tecnica de edificios|conservacion|ruina|rehabilitacion|\bite\b",
     ["ite", "inspeccion tecnica de edificios", "ruina", "rehabilitacion",
      "conservacion de edificios"]),
    (r"transparencia", ["transparencia", "acceso a la informacion publica"]),
    (r"administracion electronica|atencion a la ciudadania|sede electronica",
     ["administracion electronica", "sede electronica", "registro electronico"]),
    (r"participacion", ["participacion ciudadana", "consultas ciudadanas"]),
    (r"subvencion", ["subvenciones", "bases reguladoras de subvenciones", "ayudas"]),
    (r"fiscal general|gestion.*recaudacion|recaudacion|inspeccion tributaria",
     ["ordenanza fiscal general", "recaudacion", "aplazamiento", "fraccionamiento",
      "inspeccion tributaria", "tributos municipales"]),
    (r"zonas verdes|parques|arbolado|jardines", ["parques", "jardines", "arbolado",
                                                 "zonas verdes", "poda"]),
    (r"salubridad|salud publica|higiene", ["salubridad", "salud publica", "plagas",
                                           "control sanitario"]),
    (r"incendio", ["incendios", "proteccion contra incendios", "pci"]),
    (r"quiosco|kiosco", ["quiosco", "quioscos", "kiosco"]),
    (r"medio ambiente", ["medio ambiente", "proteccion ambiental"]),
    (r"contaminacion atmosferica|calidad del aire|emisiones",
     ["calidad del aire", "emisiones", "contaminacion atmosferica", "calderas"]),
    (r"tenencia de armas|armas", ["armas"]),
    (r"prostitucion", ["prostitucion"]),
    (r"velocidad", ["velocidad", "limite de velocidad"]),
    (r"fuegos|pirotecnia|petardos", ["pirotecnia", "petardos", "fuegos artificiales"]),
    (r"playas?\b", ["playa", "playas", "costa"]),
    (r"huertos", ["huertos urbanos"]),
    (r"cementerio|funerari", ["cementerio", "cementerios", "servicios funerarios",
                              "tanatorio"]),
    (r"padron|empadronamiento", ["padron", "empadronamiento"]),
    (r"vivienda", ["vivienda"]),
    (r"tasa", ["tasa", "tasas"]),
    (r"precio publico", ["precio publico", "precios publicos"]),
]


def alias_para(titulo: str, extras=None) -> list:
    t = norm(titulo)
    alias = []
    for pat, al in TESAURO:
        if re.search(pat, t):
            alias.extend(al)
    if extras:
        alias.extend(extras)
    # únicos conservando orden
    vistos, out = set(), []
    for a in alias:
        if a not in vistos:
            vistos.add(a)
            out.append(a)
    return out


def fecha_iso(s: str) -> str:
    """'2010-11-03T00:00:00' -> '03/11/2010' (o '' si no parsea)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""
