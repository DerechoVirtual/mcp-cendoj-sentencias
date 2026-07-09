# -*- coding: utf-8 -*-
"""
Recalcula los ALIAS de los catálogos ya generados (ordenanzas_data/*.json)
aplicando el TESAURO vigente de _gen_comun.py + los EXTRAS por ciudad, SIN red.
Útil cuando se mejora el tesauro y no hace falta re-scrapear la fuente.

    python _recalcular_alias.py valencia zaragoza ...

(madrid se salta por defecto: sus alias son curados a mano en su generador.)
"""
import importlib
import json
import os
import re
import sys

from _gen_comun import alias_para, norm

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(_HERE, "ordenanzas_data")

# ciudad -> módulo generador con lista EXTRAS opcional
GENERADORES = {
    "valencia": "_gen_catalogo_valencia",
    "zaragoza": "_gen_catalogo_zaragoza",
    "barcelona": "_gen_catalogo_barcelona",
}


def extras_de(ciudad):
    mod = GENERADORES.get(ciudad)
    if not mod:
        return []
    try:
        return getattr(importlib.import_module(mod), "EXTRAS", [])
    except Exception:  # noqa: BLE001
        return []


def recalc(ciudad):
    ruta = os.path.join(DATA, ciudad + ".json")
    d = json.load(open(ruta, encoding="utf-8"))
    extras = extras_de(ciudad)
    cambiadas = 0
    for n in d["normas"]:
        ex = []
        for pat, al in extras:
            if re.search(pat, norm(n["titulo"])):
                ex.extend(al)
        nuevos = alias_para(n["titulo"], ex)
        if nuevos != n.get("alias"):
            n["alias"] = nuevos
            cambiadas += 1
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    print(f"{ciudad}: alias recalculados ({cambiadas} normas cambiaron)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ciudades = sys.argv[1:] or [c for c in GENERADORES if c != "madrid"]
    for c in ciudades:
        recalc(c)
