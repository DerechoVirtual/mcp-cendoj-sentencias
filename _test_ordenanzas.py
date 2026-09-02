# -*- coding: utf-8 -*-
"""
Banco de pruebas del motor de ordenanzas municipales (offline, excluido del
deploy por `_*`). 20-30 pruebas REALES por municipio: cada una exige que la
salida contenga fragmentos EXACTOS verificados contra la fuente oficial.

    python _test_ordenanzas.py            # todos los municipios
    python _test_ordenanzas.py zaragoza   # solo uno

Convenciones: B = buscar_ordenanzas, L = leer_ordenanza. `espera` son
subcadenas obligatorias (comparación normalizada: sin tildes/punt.).
"""
import sys
import time

import ordenanzas_engine as oe
from ordenanzas_engine import _norm


def B(q, espera, municipio=None, desc=""):
    return {"fn": "buscar", "q": q, "espera": espera, "municipio": municipio,
            "desc": desc or f"buscar «{q}»"}


def L(ordenanza, espera, articulo="", parrafos=0, terminos="", municipio=None, desc=""):
    return {"fn": "leer", "ordenanza": ordenanza, "articulo": articulo,
            "parrafos": parrafos, "terminos": terminos, "espera": espera,
            "municipio": municipio,
            "desc": desc or f"leer {ordenanza}" + (f" art.{articulo}" if articulo else f" parr={parrafos} «{terminos}»")}


TESTS = {
    "madrid": [
        B("terrazas", ["terrazas y quioscos de hosteleria", "conso-66304"]),
        B("ruido", ["contaminacion acustica", "conso-38610"]),
        B("ibi", ["conso-38641", "bienes inmuebles"]),
        B("plusvalia", ["incremento de valor", "conso-38640"]),
        B("zbe", ["movilidad sostenible"]),
        B("perros", ["tenencia y proteccion de animales"]),
        B("vados", ["pasos de vehiculos"]),
        B("mercadillo", ["venta ambulante"]),
        B("licencia de obras", ["licencias y declaraciones responsables"]),
        B("icio", ["construcciones instalaciones y obras"]),
        B("taxi", ["conso-38605"]),
        B("basuras", ["limpieza de los espacios publicos"]),
        B("botellon", ["sin resultados"], desc="botellon: honesto (no hay ordenanza; es ley autonomica)"),
        L("taxi", ["numero maximo de licencias", "normativa autonomica"], articulo="5"),
        L("terrazas", ["horarios", "01 00", "periodo estacional"], articulo="18"),
        L("movilidad", ["madrid zona de bajas emisiones", "ordenacion de trafico"], articulo="21"),
        L("conso-38635", ["perros podran permanecer sueltos"], parrafos=3, terminos="perros correa via publica"),
        L("conso-38641", ["cuota integra", "tipo de gravamen"], parrafos=2, terminos="tipo de gravamen"),
        L("conso-61411", ["pintadas, grafitis"], parrafos=2, terminos="pintadas grafitis prohibido"),
        L("conso-38610", ["limites de niveles sonoros"], parrafos=2, terminos="valores limite dormitorios decibelios"),
        L("conso-38606", ["regulacion del regimen juridico"], articulo="1"),
        L("conso-63367", ["cuota del impuesto de construcciones"], parrafos=2, terminos="tipo gravamen cuota"),
        L("BOCM-m-2012-90266", ["regular el regimen juridico aplicable al servicio de taxi"], articulo="1",
          desc="resolver por referencia oficial BOCM-m"),
        L("taxi", ["no encuentro el articulo", "indice"], articulo="999",
          desc="articulo inexistente -> indice como pista"),
        B("ruido", ["no cubierto"], municipio="Teruel", desc="municipio no cubierto (anti-atasco)"),
    ],
    "zaragoza": [
        B("terrazas", ["terrazas de veladores", "zgz-3723"]),
        B("botellon", ["bebidas alcoholicas", "zgz-12063"]),
        B("zbe", ["zona de bajas emisiones", "zgz-13277"]),
        B("ruido", ["ruidos y vibraciones", "zgz-247"]),
        B("ibi", ["zgz-3444"]),
        B("plusvalia", ["zgz-3465"]),
        B("iae", ["zgz-3463"]),
        B("taxi", ["zgz-154"]),
        B("patinete", ["movilidad urbana"]),
        B("animales", ["zgz-4523"]),
        B("basuras", ["limpieza viaria"]),
        B("zona azul", ["estacionamiento regulado"]),
        B("convivencia", ["zgz-12063"]),
        L("zgz-13277", ["distintivo ambiental"], parrafos=2, terminos="distintivo ambiental acceso vehiculos"),
        L("zgz-247", ["regular el ejercicio de las competencias"], articulo="1"),
        L("zgz-247", ["valores limite"], parrafos=2, terminos="valores limite decibelios"),
        L("zgz-3444", ["cuota integra", "tipo de gravamen"], parrafos=2, terminos="tipo de gravamen"),
        L("zgz-4523", ["prohibido"], parrafos=2, terminos="correa suelto via publica"),
        L("zgz-154", ["transporte de viajeros en taxi"], articulo="1"),
        L("zgz-13296", ["movilidad personal"], parrafos=2, terminos="vehiculos de movilidad personal casco"),
        L("zgz-3723", ["horario"], parrafos=2, terminos="horario funcionamiento"),
        L("zgz-12063", ["bebidas alcoholicas"], parrafos=3, terminos="prohibido consumo bebidas alcoholicas via publica"),
        L("zgz-247", ["no encuentro el articulo"], articulo="999"),
        L("zgz-3466", ["gravamen"], parrafos=2, terminos="tipo de gravamen"),
    ],
    "valencia": [
        B("terrazas", ["ocupacion de dominio publico", "val-170"]),
        B("zbe", ["zona de bajas emisiones"]),
        B("movilidad", ["ordenanza de movilidad"]),
        B("botellon", ["convivencia y civismo"]),
        B("ibi", ["bienes inmuebles"]),
        B("plusvalia", ["incremento de valor"]),
        B("ruido", ["contaminacion acustica"]),
        B("taxi", ["auto taxis"]),
        B("limpieza", ["limpieza urbana"]),
        B("mercados", ["mercados de distrito"]),
        B("iae", ["actividades economicas"]),
        B("venta ambulante", ["venta no sedentaria"]),
        L("impuesto sobre bienes inmuebles", ["tipo de gravamen"], parrafos=2, terminos="tipo de gravamen"),
        L("val-170", ["mesas"], parrafos=2, terminos="mesas sillas terrazas homologacion"),
        L("contaminacion acustica", ["niveles sonoros"], parrafos=2, terminos="valores limite niveles sonoros"),
        L("ordenanza de movilidad", ["movilidad personal"], parrafos=2, terminos="vehiculos de movilidad personal aceras"),
        L("zona de bajas emisiones", ["acces"], parrafos=2, terminos="restriccions acces vehicles"),
        L("auto-taxis", ["transportes de viajeros"], articulo="1"),
        L("ordenanza fiscal general", ["hacienda municipal"], articulo="2"),
        L("auto-taxis", ["no encuentro el articulo"], articulo="999"),
    ],
    "barcelona": [
        B("botellon", ["convivencia"]),
        B("civismo", ["convivencia"]),
        B("terrazas", ["ordenanza de terrazas"]),
        B("ruido", ["medio ambiente"]),
        B("ibi", ["bienes inmuebles"]),
        B("plusvalia", ["incremento"]),
        B("patinete", ["circulacion de peatones"]),
        B("animales", ["tenencia"]),
        B("mercados", ["mercados"]),
        B("residuos", ["medio ambiente"]),
        L("convivencia", ["prohibit el consum de begudes alcoholiques"], parrafos=3,
          terminos="begudes alcoholiques espais publics"),
        L("impuesto sobre bienes inmuebles", ["tipus de gravamen", "quota integra"], articulo="7"),
        L("medio ambiente", ["valors limit"], parrafos=2, terminos="soroll valors limit immissio"),
        L("tenencia y venta de animales", ["corretges"], parrafos=2, terminos="gossos corretja via publica"),
        L("ordenanza de terrazas", ["terrass"], parrafos=2, terminos="horari de les terrasses"),
        L("restriccion de la circulacion", ["calidad del aire"], parrafos=2,
          terminos="zona de bajas emisiones distintivo ambiental"),
        L("impuesto sobre bienes inmuebles", ["no encuentro el articulo"], articulo="999"),
    ],
    "sevilla": [
        B("terrazas", ["terrazas de veladores"]),
        B("ruido", ["contaminacion acustica"]),
        B("botellon", ["convivencia"]),
        B("taxi", ["taxi"]),
        B("limpieza", ["limpieza publica"]),
        B("animales", ["tenencia responsable"]),
        B("circulacion", ["ordenanza de circulacion de sevilla"]),
        B("veladores", ["terrazas de veladores"]),
        B("arbolado", ["arbolado, parques y jardines"]),
        B("mercadillo", ["comercio ambulante"]),
        L("instituto del taxi", ["crea el instituto del taxi"], articulo="1"),
        L("instituto del taxi", ["competencias municipales en materia de taxi"], articulo="5"),
        L("contaminacion acustica", ["dba"], parrafos=2, terminos="valores limite ruido dBA"),
        L("ordenanza de circulacion de sevilla", ["circulacion de vehiculos y de personas"], articulo="1"),
        L("tenencia responsable", ["perros potencialmente peligrosos"], parrafos=2,
          terminos="perros correa via publica"),
        L("terrazas de veladores", ["veladores"], parrafos=2, terminos="horario maximo veladores"),
        L("instituto del taxi", ["no encuentro el articulo"], articulo="999"),
    ],
    "malaga": [
        B("terrazas", ["ocupacion de la via publica"]),
        B("botellon", ["convivencia"]),
        B("ruido", ["ruido y vibraciones"]),
        B("movilidad", ["ordenanza de movilidad"]),
        B("zbe", ["movilidad"]),
        B("ibi", ["bienes inmuebles"]),
        B("playas", ["playas"]),
        B("publicidad", ["publicitari"]),
        B("ite", ["inspeccion tecnica"]),
        B("subvenciones", ["subvenciones"]),
        L("convivencia", ["bebidas alcoholicas"], parrafos=2,
          terminos="bebidas alcoholicas via publica"),
        L("ordenanza de movilidad", ["zona de bajas emisiones"], parrafos=2,
          terminos="zona de bajas emisiones distintivo ambiental"),
        L("ruido y vibraciones", ["limite"], parrafos=2, terminos="valores limite niveles sonoros dBA"),
        L("impuesto sobre bienes inmuebles", ["gravamen"], parrafos=2, terminos="tipo de gravamen"),
        L("playas", ["playa"], parrafos=2, terminos="animales perros prohibido playa"),
        L("ordenanza de movilidad", ["no encuentro el articulo"], articulo="9999"),
    ],
    "murcia": [
        B("botellon", ["conductas en el espacio publico", "bebidas alcoholicas"]),
        B("alcohol", ["bebidas alcoholicas"]),
        B("ibi", ["bienes inmuebles"]),
        B("ruido", ["medio ambiente"]),
        B("movilidad", ["ordenanza de movilidad"]),
        B("terrazas", ["mesas y sillas"]),
        B("plusvalia", ["impuesto plusvalia"]),
        B("icio", ["impuesto icio"]),
        B("vados", ["vados"]),
        B("mercados", ["mercado"]),
        L("venta dispensacion y suministro de bebidas alcoholicas",
          ["prohibido el consumo de bebidas alcoholicas en las vias y espacios publicos"],
          parrafos=2, terminos="prohibido consumo bebidas alcoholicas via publica"),
        L("impuesto sobre bienes inmuebles", ["tipo de gravamen"], parrafos=2,
          terminos="tipo de gravamen"),
        L("ordenanza de movilidad", ["movilidad"], parrafos=2,
          terminos="vehiculos de movilidad personal patinete"),
        L("impuesto sobre bienes inmuebles", ["no encuentro el articulo"], articulo="999"),
    ],
    "palma": [
        B("civismo", ["convivencia"]),
        B("botellon", ["convivencia"]),
        B("terrazas", ["ocupacion de via publica"]),
        B("ruido", ["ruido"]),
        B("ibi", ["fiscales"]),
        B("residuos", ["limpieza"]),
        B("taxi", ["taxi"]),
        B("mercados", ["mercados"]),
        L("convivencia", ["consumo de bebidas alcoholicas en los espacios publicos"],
          parrafos=3, terminos="queda prohibido el consumo de bebidas alcoholicas en los espacios publicos"),
        L("ordenanzas fiscales", ["tipo de gravamen"], parrafos=2,
          terminos="bienes inmuebles tipo de gravamen"),
        L("ruido", ["ruido"], parrafos=2, terminos="valores limite niveles sonoros"),
        L("ordenanzas fiscales", ["no encuentro el articulo"], articulo="9999"),
    ],
    "laspalmas": [
        B("convivencia", ["convivencia ciudadana"], municipio="Las Palmas"),
        B("ruido", ["ruidos y vibraciones"], municipio="Las Palmas"),
        B("trafico", ["trafico"], municipio="Las Palmas de Gran Canaria"),
        B("ibi", ["bienes inmuebles"], municipio="Las Palmas"),
        B("icio", ["construcciones"], municipio="Las Palmas"),
        B("plusvalia", ["incremento"], municipio="Las Palmas"),
        B("edificacion", ["edificacion"], municipio="Las Palmas"),
        L("convivencia", ["pintadas", "quedan prohibidos"], parrafos=2,
          terminos="via publica prohibido ensuciar", municipio="Las Palmas"),
        L("ruidos y vibraciones", ["niveles maximos admisibles"], parrafos=2,
          terminos="niveles maximos admisibles", municipio="Las Palmas"),
        L("impuesto sobre bienes inmuebles", ["tipo de gravamen", "0,62"], parrafos=2,
          terminos="tipo de gravamen", municipio="Las Palmas"),
        L("trafico", ["no encuentro el articulo"], articulo="9999", municipio="Las Palmas"),
    ],
}


def run(municipios):
    total_fail = 0
    for muni in municipios:
        tests = TESTS.get(muni)
        if not tests:
            print(f"(sin tests para {muni})")
            continue
        fallos = []
        t0 = time.perf_counter()
        for t in tests:
            m = t["municipio"] or muni
            if t["fn"] == "buscar":
                out = oe.buscar(m, t["q"])
            else:
                out = oe.leer(m, t["ordenanza"], articulo=t["articulo"],
                              parrafos=t["parrafos"], terminos=t["terminos"])
            outn = _norm(out)
            faltan = [e for e in t["espera"] if _norm(e) not in outn]
            if faltan:
                fallos.append((t["desc"], faltan, out[:260].replace("\n", " ")))
        dt = time.perf_counter() - t0
        print(f"{muni.upper()}: {len(tests) - len(fallos)}/{len(tests)} PASS ({dt:.1f}s)")
        for desc, faltan, ctx in fallos:
            print(f"  FAIL {desc}\n       falta: {faltan}\n       out: {ctx}")
        total_fail += len(fallos)
    return total_fail


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    munis = sys.argv[1:] or list(TESTS)
    sys.exit(1 if run(munis) else 0)
