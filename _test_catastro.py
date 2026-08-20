# -*- coding: utf-8 -*-
"""Banco de pruebas del motor CATASTRO. Uso: python _test_catastro.py [-v]"""
import sys, time, re
sys.stdout.reconfigure(encoding="utf-8")
import catastro_engine as C

VERBOSE = "-v" in sys.argv
SOLO = [a for a in sys.argv[1:] if a.isdigit()]

FICHA = lambda t: "Referencia catastral" in t and "Localización" in t
LISTA = lambda t: "| `" in t


def casos():
    return [
        ("RC urbana 20 dígitos (oficinas C/ Alcalá 45)",
         lambda: C.consultar(referencia_catastral="1047206VK4714G0001ZH"),
         lambda t: FICHA(t) and "Oficinas" in t and "1933" in t and "15.786" in t),
        ("RC con espacios y minúsculas",
         lambda: C.consultar(referencia_catastral="1047206 vk4714g 0001 zh"),
         lambda t: FICHA(t) and "1047206VK4714G0001ZH" in t),
        ("RC de parcela (14) → lista de inmuebles",
         lambda: C.consultar(referencia_catastral="0847106VK4704F"),
         lambda t: LISTA(t) and "0847106VK4704F" in t),
        ("RC inexistente → error claro",
         lambda: C.consultar(referencia_catastral="9999999XX9999X9999XX"),
         lambda t: "Catastro:" in t and ("no existe" in t.lower() or "dígitos de control" in t)),
        ("RC mal formada → explica el formato",
         lambda: C.consultar(referencia_catastral="ABC123"),
         lambda t: "20 caracteres" in t or "no está correctamente formada" in t),
        ("Dirección simple (Alcalá 45, Madrid)",
         lambda: C.consultar(direccion="Calle Alcalá 45", municipio="Madrid"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Dirección con abreviatura C/ y coma",
         lambda: C.consultar(direccion="C/ Alcalá, 45", municipio="Madrid"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Avenida con artículos (Paseo de la Castellana 100)",
         lambda: C.consultar(direccion="Paseo de la Castellana 100", municipio="Madrid"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Gran Vía 1 (edificio con varios inmuebles) → lista",
         lambda: C.consultar(direccion="Gran Vía 1", municipio="Madrid"),
         lambda t: LISTA(t) and "GRAN VIA" in t.upper()),
        ("Piso concreto: Gran Vía 1, planta 3",
         lambda: C.consultar(direccion="Gran Vía 1", municipio="Madrid", planta="3"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Planta/puerta dentro del texto (3º B)",
         lambda: C.consultar(direccion="Calle Serrano 100, 3º B", municipio="Madrid"),
         lambda t: FICHA(t) or LISTA(t) or "Números que sí existen" in t),
        ("Municipio sin provincia (Getafe)",
         lambda: C.consultar(direccion="Calle Madrid 1", municipio="Getafe"),
         lambda t: FICHA(t) or LISTA(t) or "Vías parecidas" in t or "Números que sí existen" in t),
        ("Barcelona (Passeig de Gràcia 92)",
         lambda: C.consultar(direccion="Paseo de Gracia 92", municipio="Barcelona"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Valencia (Calle Colón 1)",
         lambda: C.consultar(direccion="Calle Colón 1", municipio="Valencia"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Sevilla (Avenida de la Constitución 21)",
         lambda: C.consultar(direccion="Avenida de la Constitución 21", municipio="Sevilla"),
         lambda t: FICHA(t) or LISTA(t)),
        ("A Coruña con artículo (La Coruña)",
         lambda: C.consultar(direccion="Calle Real 1", municipio="La Coruña"),
         lambda t: FICHA(t) or LISTA(t) or "Números que sí existen" in t),
        ("Municipio con acento (Cáceres)",
         lambda: C.consultar(direccion="Plaza Mayor 1", municipio="Cáceres"),
         lambda t: FICHA(t) or LISTA(t) or "Números que sí existen" in t),
        ("Vía mal escrita: la corrige pero lo dice",
         lambda: C.consultar(direccion="Calle Alcalaaa 45", municipio="Madrid"),
         lambda t: ("Se ha entendido" in t and (FICHA(t) or LISTA(t)))
                   or "Vías parecidas" in t or "No encuentro" in t),
        ("Vía que no existe ni parecida",
         lambda: C.consultar(direccion="Calle Zzyxwv 3", municipio="Getafe"),
         lambda t: "No encuentro" in t or "Catastro:" in t),
        ("Número inexistente → sugiere números reales",
         lambda: C.consultar(direccion="Calle Alcalá 9998", municipio="Madrid"),
         lambda t: "pero no consta el número" in t),
        ("Municipio inexistente",
         lambda: C.consultar(direccion="Calle Mayor 1", municipio="Villaquenoexiste"),
         lambda t: "No encuentro el municipio" in t),
        ("Territorio foral por municipio (Bilbao)",
         lambda: C.consultar(direccion="Gran Vía 1", municipio="Bilbao"),
         lambda t: "foral" in t and "bizkaia.eus" in t),
        ("Territorio foral por provincia (Navarra)",
         lambda: C.consultar(direccion="Calle Mayor 1", municipio="Tudela", provincia="Navarra"),
         lambda t: "foral" in t and "catastro.navarra.es" in t),
        ("Rústica por polígono/parcela + cultivos",
         lambda: C.consultar(municipio="Santa Cruz de Mudela", poligono="10", parcela="25"),
         lambda t: FICHA(t) and "Rústico" in t and "Cultivos" in t and "OLIVOS" in t.upper()),
        ("Rústica: parcela inexistente",
         lambda: C.consultar(municipio="Santa Cruz de Mudela", poligono="10", parcela="99999"),
         lambda t: "Catastro:" in t),
        ("Coordenadas exactas (lat,lon)",
         lambda: C.consultar(coordenadas="40.419372,-3.696322"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Coordenadas con radio 60 m",
         lambda: C.consultar(coordenadas="40.42028,-3.70256", radio=60),
         lambda t: "Parcelas a menos de" in t and LISTA(t)),
        ("Coordenadas en vía pública (Puerta del Sol) → aviso claro",
         lambda: C.consultar(coordenadas="40.4168,-3.7038"),
         lambda t: "no hay ninguna parcela catastral" in t.lower()
                   or "Parcelas a menos de" in t or FICHA(t)),
        ("Coordenadas invertidas (lon,lat) se reordenan solas",
         lambda: C.consultar(coordenadas="-3.696322,40.419372"),
         lambda t: FICHA(t) or LISTA(t)),
        ("Número con más de 4 dígitos → lo explica",
         lambda: C.consultar(direccion="Calle Alcalá 123456", municipio="Madrid"),
         lambda t: "4 dígitos" in t),
        ("Coordenadas no válidas",
         lambda: C.consultar(coordenadas="hola"),
         lambda t: "no válidas" in t),
        ("Sin parámetros → explica cómo se usa",
         lambda: C.consultar(),
         lambda t: "referencia_catastral" in t and "coordenadas" in t),
        ("Callejero: vías con «Mayor» en Getafe",
         lambda: C.callejero(municipio="Getafe", via="Mayor"),
         lambda t: "Vías con" in t or "No hay ninguna vía" in t),
        ("Callejero: municipios de una provincia",
         lambda: C.callejero(provincia="Segovia"),
         lambda t: "Municipios de" in t and "Segovia" in t),
        ("Calle acabada en N (Colón): se parsea bien a la primera",
         lambda: C.consultar(direccion="Calle Colón 1", municipio="Valencia"),
         lambda t: (FICHA(t) or LISTA(t)) and "Se ha entendido" not in t),
        ("Calle con acento y artículos (Constitución, Sevilla) sin fallback",
         lambda: C.consultar(direccion="Avenida de la Constitución 21", municipio="Sevilla"),
         lambda t: (FICHA(t) or LISTA(t)) and "Se ha entendido" not in t),
        ("Escalera + planta + puerta juntos (Aragón 208)",
         lambda: C.consultar(direccion="Calle Aragón 208, esc 2, 4º C", municipio="Barcelona"),
         lambda t: FICHA(t) or LISTA(t) or "pero no consta el número" in t),
        ("Aviso de datos protegidos en las fichas",
         lambda: C.consultar(referencia_catastral="1047206VK4714G0001ZH"),
         lambda t: "no son datos públicos" in t and "51-53" in t),
    ]


def main():
    cs = casos()
    ok = 0
    tiempos = []
    for i, (nombre, fn, check) in enumerate(cs, 1):
        if SOLO and str(i) not in SOLO:
            continue
        t0 = time.time()
        try:
            out = fn()
            bien = bool(check(out))
        except Exception as e:  # noqa: BLE001
            out, bien = "EXCEPCION %r" % e, False
        dt = time.time() - t0
        tiempos.append(dt)
        ok += bien
        print("%s %2d. %-52s %5.2fs" % ("OK  " if bien else "FALLA", i, nombre[:52], dt))
        if not bien or VERBOSE:
            print("     " + re.sub(r"\n", "\n     ", out[:1200]))
            print()
    n = len(SOLO) if SOLO else len(cs)
    print("\n=== %d/%d OK · media %.2fs · p95 %.2fs · máx %.2fs ===" % (
        ok, n, sum(tiempos) / max(len(tiempos), 1),
        sorted(tiempos)[int(len(tiempos) * 0.95) - 1] if tiempos else 0,
        max(tiempos) if tiempos else 0))
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
