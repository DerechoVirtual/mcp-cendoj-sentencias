# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/salamanca_indice.json: índice empaquetado de los
anuncios NORMATIVOS de los ayuntamientos (y mancomunidades) publicados en el BOP
de Salamanca desde 2012.

Es UNA sola petición al BOP (texto vacío, ventana 2012→hoy): ~35 MB y ~10 s. Se
filtra en local con los mismos criterios del motor (_es_ordenanza / _NORMA_AMPLIA)
y unas familias extra (bandos, estatutos, normas, convivencia). Volver a
ejecutarlo cuando el índice envejezca (la fecha va en meta.hasta):

    python -X utf8 _gen_bop_salamanca_indice.py            # regenera
    python -X utf8 _gen_bop_salamanca_indice.py --html X   # desde un volcado guardado
"""
import json
import os
import re
import sys
import time

import bop_engine as B
import bop_salamanca as S

_EXTRA = re.compile(r"ordenan|reglament|\btasas?\b|precio p|\bbando\b|estatuto|\bnormas?\b|"
                    r"plan (?:general|especial|parcial)|regulad|convivencia|civismo", re.I)
_RUIDO = re.compile(r"^(?:SUMARIO|EDICTO|DECRETO|ANUNCIO)\.?$", re.I)


def _keep(t):
    # fuera lo que nunca es una norma: ruido administrativo (sanciones, padrones,
    # licencias) y lo que el propio motor descarta (_NO_NORMA: convocatorias,
    # nombramientos, planes urbanísticos, expropiaciones...)
    if len(t) < 8 or _RUIDO.match(t) or S.RUIDO.search(t) or B._NO_NORMA.search(t):
        return False
    return bool(B._es_ordenanza(t) or B._NORMA_AMPLIA.search(t) or _EXTRA.search(t))


def main():
    cfg = B.PROVINCIAS.get("salamanca") or json.load(open(os.path.join(B._DATA, "bop_salamanca_config.json"), encoding="utf-8"))
    hoy = time.strftime("%d/%m/%Y")
    desde = f"01/01/{cfg.get('indice_desde', 2012)}"
    t0 = time.time()
    if "--html" in sys.argv:
        h = open(sys.argv[sys.argv.index("--html") + 1], encoding="utf-8", errors="replace").read()
        filas = S.parse_listado(h)
    else:
        filas = S.consulta_viva(cfg, "", desde, hoy, timeout=180)
    print(f"volcado: {len(filas)} anuncios en {time.time()-t0:.1f}s")
    out, munis, manc = [], set(), 0
    for r in filas:
        g = r["grupo"]
        if g == "Ayuntamientos":
            code = "A"
        elif g == "Ayuntamiento de Salamanca":
            code = "C"
        elif g == "Mancomunidades":
            code = "M"
        else:
            continue
        if not r["entidad"] or not _keep(r["titulo"]):
            continue
        out.append([r["cve"], r["entidad"], r["titulo"], code])
        if code == "M":
            manc += 1
        else:
            munis.add(r["entidad"])
    out.sort(key=lambda x: x[0], reverse=True)
    meta = {"fuente": "Boletín Oficial de la Provincia de Salamanca (sede.diputaciondesalamanca.gob.es)",
            "desde": desde, "hasta": hoy, "generado": time.strftime("%Y-%m-%d"), "n": len(out),
            "municipios": len(munis), "mancomunidades": manc,
            "regenerar": "python -X utf8 _gen_bop_salamanca_indice.py"}
    ruta = os.path.join(B._DATA, "salamanca_indice.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "filas": out}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"índice: {len(out)} filas ({len(munis)} municipios, {manc} de mancomunidades) -> {ruta} "
          f"({os.path.getsize(ruta)/1e6:.2f} MB)")
    # municipios vistos en el BOP que no están en el mapa empaquetado (informativo)
    try:
        mapa = json.load(open(os.path.join(B._DATA, cfg["mapa"]), encoding="utf-8"))
        conocidos = {B._norm(k) for k in mapa}
        nuevos = sorted(m for m in munis if B._norm(m) not in conocidos)
        print(f"entidades del listado que NO están en el mapa ({len(nuevos)}): {nuevos}")
    except Exception as e:  # noqa: BLE001
        print("mapa:", e)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
