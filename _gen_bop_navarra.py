# -*- coding: utf-8 -*-
"""Mapa de municipios de NAVARRA para el BON. Su buscador NO tiene índice de
municipios: el filtro `organoSolicitante` es el NOMBRE DE LA LOCALIDAD en texto
('TUDELA' funciona; 'AYUNTAMIENTO DE TUDELA' devuelve 0). Por eso el mapa es
nombre -> nombre, con una lista curada de los municipios con peso poblacional."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

MUNICIPIOS = [
    "Pamplona", "Tudela", "Barañáin", "Burlada", "Estella-Lizarra", "Zizur Mayor",
    "Tafalla", "Villava", "Ansoáin", "Valle de Egüés", "Berriozar", "Noáin",
    "Huarte", "Cintruénigo", "Corella", "San Adrián", "Peralta", "Sangüesa",
    "Aoiz", "Alsasua", "Altsasu", "Lodosa", "Milagro", "Castejón", "Cadreita",
    "Valtierra", "Arguedas", "Cascante", "Murchante", "Fitero", "Buñuel", "Cortes",
    "Ribaforada", "Marcilla", "Falces", "Funes", "Azagra", "Andosilla", "Cárcar",
    "Sartaguda", "Viana", "Los Arcos", "Puente la Reina", "Artajona", "Olite",
    "Caparroso", "Carcastillo", "Villafranca", "Mendavia", "Allo", "Larraga",
    "Baztan", "Lesaka", "Bera", "Etxarri-Aranatz", "Leitza", "Cabanillas",
    "Mélida", "Beriáin", "Orkoien", "Cendea de Olza", "Aranguren", "Galar",
    "Esteribar", "Lekunberri", "Irurtzun", "Puente la Reina-Gares", "Obanos",
    "Tiebas", "Ujué", "Santacara", "Murillo el Fruto", "Rada", "Fustiñana",
    "Monteagudo", "Barillas", "Tulebras", "Ablitas", "Cascante de Navarra",
]

if __name__ == "__main__":
    mapa = {m: m.upper() for m in sorted(set(MUNICIPIOS))}
    dest = os.path.join(HERE, "ordenanzas_data")
    with open(os.path.join(dest, "bop_navarra_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1)
    cfg = {"id": "navarra", "base": "https://bon.navarra.es",
           "mapa": "bop_navarra_municipios.json", "nombre": "Navarra",
           "familia": "navarra", "indice_desde": 2004,
           "verifica_texto": True, "fulltext": True}
    with open(os.path.join(dest, "bop_navarra_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"municipios: {len(mapa)}")
