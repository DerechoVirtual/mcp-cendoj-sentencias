# -*- coding: utf-8 -*-
"""Actualiza ordenanzas_data/ceuta.json (índice curado de normativa de la Ciudad
Autónoma de CEUTA, generado el 27-jul-2026 desde ceuta.es/ceuta/la-institucion/normativa)
para que el motor lo registre solo y la búsqueda funcione con alias de verdad.
Offline/_gen (no se despliega). NO vuelve a rastrear la web: parte del json existente.

Qué hace:
  1. meta.aliases (sin ellos `_registrar_catalogos_auto` no registra la ciudad) y
     meta.recorte (regex que aísla el texto en las páginas HTML del portal Joomla:
     <div class="item-page">…hasta el siguiente módulo «ja-»/paginación).
  2. alias: los del 27-jul eran las PALABRAS DEL TÍTULO («enero», «general»…), que el
     ranking ya puntúa por el propio título. Se sustituyen por el tesauro común
     (_gen_comun.alias_para) + EXTRAS curados por materia (IPSI, juego, taxi, vados…).
     Los alias por CONTENIDO (--enriquecer) se añaden tras empaquetar el texto.
  3. `mod`: fecha de la última modificación listada en `modificaciones`.

Uso:  python -X utf8 _gen_catalogo_ceuta.py
      python -X utf8 _fill_ceuta.py --workers 2          (texto empaquetado; recorta los BOCCE)
      python -X utf8 _gen_catalogo_ceuta.py --enriquecer
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _gen_capital_web as W  # noqa: E402
from _gen_comun import alias_para, norm  # noqa: E402

CODIGO = "ceuta"
FP = os.path.join(W.DATA_DIR, CODIGO + ".json")
ALIASES = ["ceuta", "ciudad autonoma de ceuta", "ciudad de ceuta", "ayuntamiento de ceuta",
           "ciudad autónoma de ceuta", "asamblea de ceuta"]
RECORTE = r'(?s)<div class="item-page">(.*?)<div class="(?:ja-|pagination|item-separator)'

EXTRAS = [
    (r"asamblea de la ciudad", ["pleno", "asamblea", "reglamento organico", "diputados", "mociones", "sesiones"]),
    (r"gobierno y servicios", ["reglamento organico", "consejo de gobierno", "consejeros", "organizacion"]),
    (r"registro general", ["registro de entrada", "registro general", "presentacion de documentos"]),
    (r"inventario general", ["inventario", "bienes", "patrimonio municipal"]),
    (r"padron", ["padron", "empadronamiento", "sancionador", "baja en el padron"]),
    (r"puestos de trabajo", ["rpt", "relacion de puestos de trabajo", "retribuciones", "funcionarios",
                             "empleados publicos"]),
    (r"servicios administrativos", ["organizacion administrativa", "funcionarios", "empleados publicos"]),
    (r"mesa general de negociacion", ["negociacion colectiva", "sindicatos", "mesa de negociacion"]),
    (r"ceremonial|protocolo", ["protocolo", "actos oficiales", "ceremonial"]),
    (r"distinciones honorificas", ["honores", "distinciones", "medallas", "hijo predilecto"]),
    (r"ipsi|produccion los servicios y la importacion", ["ipsi", "impuesto sobre la produccion los servicios y la importacion",
                                                        "iva", "impuesto indirecto", "importacion", "tipo impositivo"]),
    (r"licencias de apertura", ["licencia de apertura", "tasa de apertura", "licencia de actividad"]),
    (r"autotaxis", ["taxi", "taxis", "licencia de taxi", "autotaxi", "vtc", "alquiler con conductor"]),
    (r"boletin oficial", ["bocce", "boletin oficial", "tasa de publicacion", "anuncios"]),
    (r"vehiculos abandonados", ["grua", "retirada de vehiculos", "deposito de vehiculos", "vehiculos abandonados",
                                "tasa de grua"]),
    (r"casinos", ["casino", "juego", "casinos de juego"]),
    (r"catalogo de juegos", ["juego", "juegos de azar", "juegos"]),
    (r"maquinas recreativas", ["maquinas recreativas", "tragaperras", "juego", "salones recreativos"]),
    (r"apuestas", ["apuestas", "casas de apuestas", "juego"]),
    (r"bingo", ["bingo", "salas de bingo", "juego"]),
    (r"acceso al juego", ["ludopatia", "autoprohibicion", "prohibidos", "juego", "interdiccion"]),
    (r"museos", ["museos", "museo"]),
    (r"patrimonio cultural|patrimonio documental", ["patrimonio", "archivo", "patrimonio historico", "archivos"]),
    (r"murallas reales", ["murallas reales", "monumentos", "conjunto monumental"]),
    (r"guarderias", ["guarderia", "guarderias", "escuelas infantiles", "primer ciclo"]),
    (r"policia local", ["policia local", "policia", "agentes", "policia municipal"]),
    (r"regimen disciplinario", ["disciplinario", "sanciones disciplinarias", "faltas"]),
    (r"academia", ["academia de policia", "formacion"]),
    (r"extincion de incendios", ["bomberos", "incendios", "salvamento"]),
    (r"parque movil", ["parque movil", "vehiculos oficiales", "coches oficiales"]),
    (r"circulacion", ["trafico", "seguridad vial", "multas de trafico", "estacionamiento", "aparcamiento",
                      "vados", "zona azul", "ora", "carga y descarga", "velocidad", "movilidad"]),
    (r"transporte urbano de viajeros", ["taxi", "taxis", "autotaxi", "licencia de taxi", "vtc",
                                        "transporte de viajeros"]),
    (r"proteccion civil", ["proteccion civil", "emergencias"]),
    (r"autoproteccion", ["planes de autoproteccion", "autoproteccion", "emergencias"]),
    (r"instrucci", ["proteccion civil", "emergencias", "incendios forestales"]),
    (r"juventud", ["juventud", "jovenes"]),
    (r"carnet joven", ["carnet joven", "carne joven", "juventud"]),
    (r"informacion juvenil", ["informacion juvenil", "juventud"]),
    (r"manipuladores de alimentos", ["manipulador de alimentos", "seguridad alimentaria", "carnet de manipulador"]),
    (r"consumidores|consumo", ["consumo", "consumidores", "omic"]),
    (r"vigilancia epidemiologica", ["epidemiologia", "salud publica", "enfermedades"]),
    (r"establecimientos sanitarios", ["centros sanitarios", "clinicas", "autorizacion sanitaria"]),
    (r"sanidad mortuoria", ["sanidad mortuoria", "tanatorio", "funeraria", "cadaveres", "inhumacion", "cementerio"]),
    (r"farmacia", ["farmacias", "horarios de farmacia", "guardias de farmacia"]),
    (r"piscinas", ["piscinas", "piscina", "socorrista"]),
    (r"animales de compania", ["bienestar animal", "ppp", "perros peligrosos", "excrementos"]),
    (r"turismo", ["turismo", "alojamientos turisticos", "viviendas turisticas", "establecimientos turisticos"]),
    (r"establecimientos turisticos", ["horarios", "bares", "restaurantes", "hoteles", "horario de cierre",
                                      "hosteleria"]),
    (r"licencias de instalacion y de apertura|licencias de instalacion y apertura",
     ["licencia de apertura", "licencia de actividad", "declaracion responsable", "actividades", "apertura"]),
    (r"actividades comerciales", ["comercio", "horarios comerciales", "apertura comercial", "actividades comerciales",
                                  "declaracion responsable"]),
    (r"venta fuera de establecimiento", ["venta ambulante", "mercadillo", "venta no sedentaria", "puestos ambulantes"]),
    (r"vertidos", ["vertidos", "aguas residuales"]),
    (r"playas", ["playa", "banistas", "perros en la playa", "chiringuitos"]),
    (r"gestion y auditor", ["emas", "auditoria ambiental", "gestion ambiental"]),
    (r"acuicultura|pesca", ["pesca", "acuicultura", "marisqueo"]),
    (r"energia solar", ["energia solar", "placas solares", "agua caliente sanitaria", "paneles solares"]),
    (r"federaciones deportivas|asociaciones deportivas", ["deporte", "federaciones", "clubes deportivos"]),
    (r"deportistas", ["deporte", "ayudas a deportistas", "becas deportivas"]),
    (r"pesca maritima de recreo", ["pesca recreativa", "licencia de pesca"]),
    (r"servicios sociales", ["servicios sociales", "ayudas sociales", "prestaciones sociales"]),
    (r"prestaciones economicas", ["ayudas economicas", "prestaciones", "emergencia social"]),
    (r"ingreso minimo", ["ingreso minimo", "imi", "renta minima"]),
    (r"ayudas al alquiler", ["alquiler", "ayudas al alquiler", "vivienda"]),
    (r"discapacidad", ["discapacidad", "personas con discapacidad"]),
    (r"personas mayores|tercera edad", ["mayores", "tercera edad", "personas mayores"]),
    (r"menores|infancia", ["menores", "infancia"]),
    (r"violencia de genero|mujeres maltratadas", ["violencia de genero", "mujeres", "casa de acogida",
                                                  "pisos tutelados"]),
    (r"punto de encuentro familiar", ["punto de encuentro familiar", "regimen de visitas", "familia"]),
    (r"uniones de hecho", ["parejas de hecho", "registro de parejas de hecho", "uniones de hecho"]),
    (r"mobiliario urbano", ["mobiliario urbano", "bancos", "papeleras", "marquesinas"]),
    (r"buen uso de los espacios publicos", ["convivencia", "civismo", "espacios publicos", "botellon",
                                            "conductas incivicas", "pintadas", "vandalismo"]),
    (r"kioskos", ["quiosco", "quioscos", "kiosco", "kioscos"]),
    (r"estacionamientos reservados", ["discapacidad", "movilidad reducida", "estacionamiento reservado", "pmr",
                                      "aparcamiento discapacitados"]),
    (r"tarjeta de estacionamiento", ["tarjeta de estacionamiento", "discapacidad", "movilidad reducida", "pmr"]),
    (r"acceso de vehiculos a inmuebles", ["vado", "vados", "entrada de vehiculos", "paso de carruajes", "aceras"]),
    (r"nomenclatura y rotulacion", ["nombres de calles", "callejero", "rotulacion", "numeracion de edificios"]),
    (r"almacen general", ["almacen", "suministros"]),
    (r"disciplina urbanistica", ["disciplina urbanistica", "infracciones urbanisticas", "obras sin licencia",
                                 "orden de demolicion", "legalizacion"]),
    (r"fichas urbanisticas", ["pgou", "plan general", "normas urbanisticas", "fichas urbanisticas",
                              "catalogo de elementos protegidos", "edificabilidad"]),
    (r"accesibilidad", ["accesibilidad", "barreras arquitectonicas", "discapacidad", "movilidad reducida"]),
    (r"materia de vivienda", ["vivienda publica", "adjudicacion de viviendas", "vpo", "emvicesa"]),
    (r"cedula de habitabilidad", ["cedula de habitabilidad", "habitabilidad", "primera ocupacion"]),
    (r"terrazas de veladores", ["hosteleria", "ocupacion de via publica"]),
    (r"limpieza publica", ["limpieza viaria", "recogida de basura", "contenedores"]),
]


def mod_de(norma: dict) -> str:
    mods = norma.get("modificaciones") or []
    fechas = [m.get("pub", "") for m in mods if m.get("pub")]
    return fechas[-1] if fechas else ""


def main(enriquecer=False):
    if enriquecer:
        n = W.enriquecer_catalogo(CODIGO)
        print(f"{n} normas enriquecidas con alias por contenido")
        return
    cat = json.load(open(FP, encoding="utf-8"))
    meta = cat["meta"]
    meta["aliases"] = ALIASES
    meta["recorte"] = RECORTE
    meta.setdefault("actualizado", "2026-07")
    for n in cat["normas"]:
        alias = alias_para(n["titulo"])
        tn = norm(n["titulo"])
        for rx, extra in EXTRAS:
            if re.search(rx, tn):
                alias.extend(extra)
        alias.extend(n.get("alias_contenido", []))
        n["alias"] = W.uniq(alias)
        n.setdefault("ref", "")
        n["mod"] = mod_de(n)
    with open(FP, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    con_alias = sum(1 for n in cat["normas"] if n["alias"])
    print(f"{len(cat['normas'])} normas · {con_alias} con alias · meta.aliases={ALIASES[:3]}… · recorte OK")


if __name__ == "__main__":
    main(enriquecer="--enriquecer" in sys.argv)
