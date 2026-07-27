# -*- coding: utf-8 -*-
"""Mapa de municipios de CANTABRIA para el BOC. Su buscador NO tiene filtro por
municipio (la lista de entidades vuelve vacía), así que el nombre del municipio
se mete como término de la búsqueda de cuerpo y se confirma con el campo
'organismo' del resultado. El mapa es, por tanto, nombre -> nombre.
Se omite 'Cieza' a propósito: colisiona con Cieza (Murcia, 35.000 hab.)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

MUNICIPIOS = [
    "Santander", "Torrelavega", "Castro-Urdiales", "Camargo", "Piélagos",
    "El Astillero", "Laredo", "Santa Cruz de Bezana", "Reinosa",
    "Los Corrales de Buelna", "Santoña", "Colindres", "Suances",
    "Cabezón de la Sal", "Noja", "Ampuero", "Bárcena de Cicero", "Medio Cudeyo",
    "Villaescusa", "Marina de Cudeyo", "Ribamontán al Mar", "Ribamontán al Monte",
    "Val de San Vicente", "San Vicente de la Barquera", "Comillas", "Potes",
    "Ramales de la Victoria", "Guriezo", "Liendo", "Limpias", "Voto", "Solórzano",
    "Escalante", "Argoños", "Arnuero", "Meruelo", "Bareyo", "Miengo", "Polanco",
    "Reocín", "Alfoz de Lloredo", "Udías", "Ruiloba", "Valdáliga", "Herrerías",
    "Rionansa", "Cillorigo de Liébana", "Camaleño", "Vega de Liébana", "Molledo",
    "Arenas de Iguña", "Bárcena de Pie de Concha", "Valderredible", "Valdeolea",
    "Campoo de Yuso", "Santiurde de Toranzo", "Corvera de Toranzo", "Luena",
    "Puente Viesgo", "San Felices de Buelna", "Cartes", "Mazcuerras", "Ruente",
    "Santillana del Mar", "Selaya", "Villacarriedo", "Santa María de Cayón",
    "Entrambasaguas", "Liérganes", "Penagos", "Hazas de Cesto", "Valle de Villaverde",
]

if __name__ == "__main__":
    mapa = {m: m for m in sorted(set(MUNICIPIOS))}
    dest = os.path.join(HERE, "ordenanzas_data")
    with open(os.path.join(dest, "bop_cantabria_municipios.json"), "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1)
    cfg = {"id": "cantabria", "base": "https://boc.cantabria.es",
           "mapa": "bop_cantabria_municipios.json", "nombre": "Cantabria",
           "familia": "cantabria", "indice_desde": 2010,
           "verifica_texto": True, "fulltext": True}
    with open(os.path.join(dest, "bop_cantabria_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"municipios: {len(mapa)}")
