# -*- coding: utf-8 -*-
"""¿Qué municipios ESPAÑOLES de más de 50.000 habitantes cubre hoy el conector?

Objetivo de Carlos (26-jul-2026): todos funcionando. Este script mide el avance
sin tocar la red: pregunta al enrutado real (ordenanzas_engine) si cada municipio
se resuelve a un adaptador de ciudad o al BOP de su provincia.

Uso:  ./.venv/Scripts/python.exe _cobertura_50k.py [-v]
"""
import sys

import ordenanzas_engine as OE

try:
    import bop_engine as BOP
except Exception:  # noqa: BLE001
    BOP = None

# Municipios de España > 50.000 hab. (INE 2024, redondeado; agrupados por provincia)
MUNICIPIOS = {
    "Madrid": ["Madrid", "Móstoles", "Alcalá de Henares", "Fuenlabrada", "Leganés", "Getafe",
               "Alcorcón", "Torrejón de Ardoz", "Parla", "Alcobendas", "Las Rozas de Madrid",
               "San Sebastián de los Reyes", "Pozuelo de Alarcón", "Coslada", "Rivas-Vaciamadrid",
               "Valdemoro", "Majadahonda", "Collado Villalba", "Aranjuez", "Arganda del Rey",
               "Boadilla del Monte", "Pinto", "Colmenar Viejo", "Tres Cantos",
               "San Fernando de Henares", "Galapagar", "Villaviciosa de Odón"],
    "Barcelona": ["Barcelona", "L'Hospitalet de Llobregat", "Badalona", "Terrassa", "Sabadell",
                  "Mataró", "Santa Coloma de Gramenet", "Cornellà de Llobregat", "Sant Cugat del Vallès",
                  "Sant Boi de Llobregat", "Manresa", "Rubí", "Vilanova i la Geltrú", "Viladecans",
                  "Castelldefels", "El Prat de Llobregat", "Granollers", "Cerdanyola del Vallès",
                  "Mollet del Vallès", "Esplugues de Llobregat", "Gavà", "Igualada", "Vic",
                  "Ripollet", "Sant Feliu de Llobregat", "Barberà del Vallès", "Sant Adrià de Besòs"],
    "Valencia": ["Valencia", "Torrent", "Gandia", "Paterna", "Sagunto", "Alzira", "Mislata",
                 "Burjassot", "Ontinyent", "Xirivella", "Manises", "Quart de Poblet", "Aldaia",
                 "Catarroja", "Alaquàs", "Sueca"],
    "Alicante": ["Alicante", "Elche", "Torrevieja", "Orihuela", "Benidorm", "Alcoy", "Elda",
                 "San Vicente del Raspeig", "Denia", "Villena", "Petrer", "Santa Pola",
                 "Villajoyosa", "Crevillente", "Novelda", "Ibi"],
    "Sevilla": ["Sevilla", "Dos Hermanas", "Alcalá de Guadaíra", "Utrera", "Mairena del Aljarafe",
                "Écija", "La Rinconada", "Los Palacios y Villafranca", "Morón de la Frontera",
                "Lebrija", "Coria del Río", "Camas", "Carmona"],
    "Málaga": ["Málaga", "Marbella", "Vélez-Málaga", "Mijas", "Fuengirola", "Torremolinos",
               "Estepona", "Benalmádena", "Antequera", "Rincón de la Victoria", "Alhaurín de la Torre",
               "Ronda", "Cártama", "Alhaurín el Grande"],
    "Murcia": ["Murcia", "Cartagena", "Lorca", "Molina de Segura", "Alcantarilla", "Cieza",
               "Águilas", "Yecla", "San Javier", "Totana", "Mazarrón", "Torre-Pacheco",
               "San Pedro del Pinatar", "Caravaca de la Cruz", "Jumilla", "Las Torres de Cotillas"],
    "Cádiz": ["Jerez de la Frontera", "Algeciras", "Cádiz", "San Fernando", "El Puerto de Santa María",
              "Chiclana de la Frontera", "Sanlúcar de Barrameda", "La Línea de la Concepción",
              "Puerto Real", "Rota", "Arcos de la Frontera", "Los Barrios", "Barbate", "San Roque"],
    "Granada": ["Granada", "Motril", "Almuñécar", "Armilla", "Baza", "Maracena", "Loja"],
    "Jaén": ["Jaén", "Linares", "Andújar", "Úbeda", "Martos", "Alcalá la Real"],
    "Huelva": ["Huelva", "Lepe", "Almonte", "Ayamonte", "Isla Cristina", "Moguer"],
    "León": ["León", "Ponferrada", "San Andrés del Rabanedo"],
    "Toledo": ["Toledo", "Talavera de la Reina", "Illescas", "Seseña"],
    "Cáceres": ["Cáceres", "Plasencia"],
    "Huesca": ["Huesca"],
    # ---- pendientes (provincias aún no cubiertas) ----
    "Vizcaya": ["Bilbao", "Barakaldo", "Getxo", "Portugalete", "Santurtzi", "Basauri", "Leioa",
                "Galdakao", "Durango", "Sestao", "Erandio", "Amorebieta-Etxano"],
    "A Coruña": ["A Coruña", "Santiago de Compostela", "Ferrol", "Narón", "Oleiros", "Arteixo",
                 "Culleredo", "Ames", "Carballo", "Ribeira"],
    "Pontevedra": ["Vigo", "Pontevedra", "Vilagarcía de Arousa", "Redondela", "Cangas", "Marín",
                   "Ponteareas", "Nigrán", "Moaña", "Poio"],
    "Asturias": ["Gijón", "Oviedo", "Avilés", "Siero", "Langreo", "Mieres", "Castrillón"],
    "Baleares": ["Palma", "Calvià", "Ibiza", "Manacor", "Marratxí", "Llucmajor", "Santa Eulària des Riu"],
    "Las Palmas": ["Las Palmas de Gran Canaria", "Telde", "Santa Lucía de Tirajana", "Arrecife",
                   "San Bartolomé de Tirajana", "Arucas", "Puerto del Rosario", "Ingenio", "Agüimes"],
    "Sta Cruz Tenerife": ["Santa Cruz de Tenerife", "San Cristóbal de La Laguna", "Arona", "Adeje",
                          "Granadilla de Abona", "La Orotava", "Los Realejos", "Puerto de la Cruz",
                          "Candelaria", "Icod de los Vinos", "Los Llanos de Aridane"],
    "Zaragoza": ["Zaragoza"],
    "Tarragona": ["Tarragona", "Reus", "El Vendrell", "Cambrils"],
    "Girona": ["Girona", "Figueres", "Blanes", "Lloret de Mar", "Olot", "Salt"],
    "Lleida": ["Lleida"],
    "Almería": ["Almería", "Roquetas de Mar", "El Ejido", "Níjar", "Vícar", "Adra"],
    "Córdoba": ["Córdoba", "Lucena", "Puente Genil"],
    "Gipuzkoa": ["San Sebastián", "Irún", "Errenteria", "Eibar", "Zarautz"],
    "Álava": ["Vitoria-Gasteiz"],
    "Navarra": ["Pamplona", "Tudela", "Barañáin"],
    "Cantabria": ["Santander", "Torrelavega", "Castro-Urdiales", "Camargo"],
    "Valladolid": ["Valladolid"],
    "Badajoz": ["Badajoz", "Mérida", "Almendralejo"],
    "Castellón": ["Castellón de la Plana", "Vila-real", "Burriana"],
    "Burgos": ["Burgos", "Miranda de Ebro"],
    "Salamanca": ["Salamanca"],
    "Albacete": ["Albacete", "Hellín"],
    "Ciudad Real": ["Ciudad Real", "Puertollano", "Tomelloso", "Valdepeñas", "Alcázar de San Juan"],
    "Guadalajara": ["Guadalajara"],
    "Cuenca": ["Cuenca"],
    "La Rioja": ["Logroño"],
    "Lugo": ["Lugo"],
    "Ourense": ["Ourense"],
    "Palencia": ["Palencia"],
    "Zamora": ["Zamora"],
    "Ávila": ["Ávila"],
    "Segovia": ["Segovia"],
    "Melilla": ["Melilla"],
    "Ceuta": ["Ceuta"],
}


def estado(muni):
    ad = OE._resolver_municipio(muni)
    if ad is not None:
        return "ciudad", ad.nombre
    if BOP is not None:
        p = BOP.provincia_de(muni)
        if p:
            return "bop", p
    return "NO", ""


if __name__ == "__main__":
    verbose = "-v" in sys.argv
    tot = cub = 0
    faltan_por_prov = {}
    for prov, munis in MUNICIPIOS.items():
        n_ok = []
        n_no = []
        for m in munis:
            tot += 1
            via, det = estado(m)
            if via == "NO":
                n_no.append(m)
            else:
                cub += 1
                n_ok.append(f"{m} [{via}:{det}]")
        if n_no:
            faltan_por_prov[prov] = n_no
        marca = "OK  " if not n_no else f"{len(n_ok):2d}/{len(munis):2d}"
        print(f"[{marca}] {prov:20s} cubiertos {len(n_ok):2d}/{len(munis):2d}"
              + (f"   faltan: {', '.join(n_no[:6])}{'…' if len(n_no) > 6 else ''}" if n_no else ""))
        if verbose:
            for x in n_ok:
                print("        ", x)
    print("=" * 90)
    print(f"COBERTURA >50k hab.: {cub}/{tot} municipios ({100*cub/tot:.0f}%)")
    pend = sum(len(v) for v in faltan_por_prov.values())
    print(f"Pendientes: {pend} municipios en {len(faltan_por_prov)} provincias")
