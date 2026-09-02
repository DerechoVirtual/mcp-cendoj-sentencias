# -*- coding: utf-8 -*-
"""Auditoría anti-secuestro: comprueba que cada municipio >50.000 hab. se resuelve
con el BOP de SU provincia y no con el de otra.

Los listados de entidades de los BOP vienen sucios (publican edictos de
notificación de ayuntamientos de toda España), así que un municipio ajeno colado
en un mapa se lleva las consultas a la provincia equivocada. Esto ya pasó con
Lepe, Majadahonda, Pájara, Alfafar, Santander, Badajoz, Mérida, Vila-real,
Ciudad Real y Alcázar de San Juan.

Uso: ./.venv/Scripts/python.exe _audit_enrutado.py
"""
import sys

import bop_engine as B
import ordenanzas_engine as OE
import _cobertura_50k as C

ESPERADO = {
    "Madrid": "madrid", "Barcelona": "barcelona", "Valencia": "valencia",
    "Alicante": "alicante", "Sevilla": "sevilla", "Málaga": "malaga_prov",
    "Murcia": "murcia_prov", "Cádiz": "cadiz", "Granada": "granada", "Jaén": "jaen",
    "Huelva": "huelva", "León": "leon", "Toledo": "toledo", "Cáceres": "caceres",
    "Huesca": "huesca", "Vizcaya": "bizkaia", "A Coruña": "acoruna",
    "Pontevedra": "pontevedra", "Asturias": "asturias", "Las Palmas": "laspalmas",
    "Sta Cruz Tenerife": "tenerife", "Tarragona": "tarragona", "Gipuzkoa": "gipuzkoa",
    "Baleares": "baleares", "Girona": "girona", "Almería": "almeria", "Córdoba": "cordoba",
    "Navarra": "navarra", "Cantabria": "cantabria", "Ciudad Real": "ciudadreal",
    "Castellón": "castellon", "Burgos": "burgos", "Albacete": "albacete",
    "Badajoz": "badajoz", "Valladolid": "valladolid", "Zaragoza": "zaragoza",
    "La Rioja": "larioja", "Ourense": "ourense", "Álava": "alava", "Lugo": "lugo",
    "Melilla": "melilla",
}

if __name__ == "__main__":
    malos = []
    for prov, munis in C.MUNICIPIOS.items():
        for m in munis:
            if OE._resolver_municipio(m):       # adaptador de ciudad: correcto
                continue
            p = B.provincia_de(m)
            if p and p != ESPERADO.get(prov):
                malos.append((m, prov, p))
    if malos:
        print(f"❌ {len(malos)} SECUESTROS DE ENRUTADO:")
        for m, prov, p in malos:
            print(f"   {m:28s} es de {prov:20s} pero se resuelve con el BOP de {p}")
        print("\nArreglo: añadir el nombre al `excluir` del config de esa provincia.")
    else:
        print("✅ Sin secuestros: cada municipio >50k se resuelve con el BOP de su provincia.")
    sys.exit(1 if malos else 0)
