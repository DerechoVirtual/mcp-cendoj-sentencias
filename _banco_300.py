# -*- coding: utf-8 -*-
"""Banco de los 300 municipios >50k: el MISMO uso que hace el chat/Claude.

Por municipio y materia: buscar_ordenanzas -> leer_ordenanza (parrafos=3) y se
exige TEXTO LITERAL (no un error, no un "no se pudo descargar"). Un municipio
esta OK si al menos una materia comun devuelve texto; ademas se sondea la
materia "viviendas de uso turistico" (caso de Carlos) y se clasifica.

Uso: python _banco_300.py [salida.jsonl] [--prov Sevilla,Madrid] [--workers 8]
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import threading

# claves para OCR (bop_engine): SIEMPRE las del .env global (el shell trae una vieja)
_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
try:
    for ln in open(_ENV, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")
except Exception:  # noqa: BLE001
    pass

import ordenanzas_engine as OE  # noqa: E402
from _cobertura_50k import MUNICIPIOS  # noqa: E402

MATERIAS = [
    ("terrazas veladores", r"terraza|velador|mesas|ocupaci|taula|cadires"),
    ("residuos limpieza", r"residu|basura|limpieza|lixo|escombr|limpeza|neteja|hondakin"),
    ("ruido contaminacion acustica", r"ruido|ac[uú]stic|soroll|ru[ií]do|vibraci|decibel|sonor"),
    ("tenencia de animales", r"animal|perro|gat|mascota|can[ií]n|gos"),
]
VUT = ("viviendas de uso turistico", "turistic")

_LOCK = threading.Lock()


def _clasificar_busqueda(b: str) -> str:
    if not b:
        return "VACIO"
    if b.startswith("Municipio no cubierto"):
        return "NOCUB"
    if b.startswith("Error"):
        return "ERROR"
    if b.startswith("Sin resultados") or b.startswith("No encuentro"):
        return "HONESTO"
    if "【" in b:
        return "LISTA"
    return "RARO"


def _clasificar_lectura(l: str, rx: str) -> str:
    if not l:
        return "VACIO"
    if l.startswith("Municipio no cubierto"):
        return "NOCUB"
    if l.startswith("Error") or l.startswith("Localicé") or "no pude leer" in l[:200]:
        return "ERROR"
    if l.startswith("No encuentro") or l.startswith("Sin resultados") or l.startswith("No identifico"):
        return "HONESTO"
    if "【" not in l[:400]:
        return "RARO"
    # cuerpo tras la cabecera
    cuerpo = l.split("\n\n", 1)[1] if "\n\n" in l else ""
    cuerpo = re.sub(r"\(v[ií]a [^)]*\)\s*$", "", cuerpo).strip()
    cuerpo = re.sub(r"Fuente:.*$", "", cuerpo, flags=re.S).strip()
    if len(cuerpo) < 250:
        return "CORTO"
    if "sin pasajes" in l.lower()[:600]:
        return "SINPASAJES"
    if "no tiene texto legible" in l:
        return "ERROR"
    return "TEXTO"


def probar(prov, muni):
    out = {"prov": prov, "muni": muni, "casos": [], "estado": "FAIL", "t_total": 0.0}
    t00 = time.time()
    for materia, rx in MATERIAS:
        caso = {"materia": materia}
        t0 = time.time()
        try:
            b = OE.buscar(muni, materia, 6)
        except Exception as e:  # noqa: BLE001
            b = f"Error EXC {e}"
        caso["t_buscar"] = round(time.time() - t0, 1)
        caso["buscar"] = _clasificar_busqueda(b)
        caso["buscar_txt"] = re.sub(r"\s+", " ", b)[:260]
        if caso["buscar"] == "NOCUB":
            out["casos"].append(caso)
            out["estado"] = "NOCUB"
            break
        ids = re.findall(r"\bid: (\S+)", b)
        ref = ids[0] if ids else materia
        caso["ref"] = ref
        t0 = time.time()
        try:
            l = OE.leer(muni, ref, "", 3, materia, 0)
        except Exception as e:  # noqa: BLE001
            l = f"Error EXC {e}"
        caso["t_leer"] = round(time.time() - t0, 1)
        caso["leer"] = _clasificar_lectura(l, rx)
        caso["leer_txt"] = re.sub(r"\s+", " ", l)[:260]
        caso["match_rx"] = bool(re.search(rx, l, re.I))
        out["casos"].append(caso)
        if caso["leer"] == "TEXTO":
            out["estado"] = "OK"
            out["materia_ok"] = materia
            out["t_ok"] = round(caso["t_buscar"] + caso["t_leer"], 1)
            break
    if out["estado"] == "FAIL":
        clases = {c.get("leer") for c in out["casos"]} | {c.get("buscar") for c in out["casos"]}
        if "ERROR" not in clases and "RARO" not in clases and "VACIO" not in clases:
            out["estado"] = "HONESTO"      # ninguna materia da texto pero responde honesto
    # sonda VUT (caso Carlos): solo se clasifica
    t0 = time.time()
    try:
        b = OE.buscar(muni, VUT[0], 6)
        ids = re.findall(r"\bid: (\S+)", b)
        l = OE.leer(muni, ids[0] if ids else VUT[0], "", 3, "turistico alojamiento", 0)
        out["vut"] = {"buscar": _clasificar_busqueda(b), "leer": _clasificar_lectura(l, VUT[1]),
                      "t": round(time.time() - t0, 1), "txt": re.sub(r"\s+", " ", l)[:200]}
    except Exception as e:  # noqa: BLE001
        out["vut"] = {"buscar": "EXC", "leer": "EXC", "t": round(time.time() - t0, 1), "txt": str(e)[:200]}
    out["t_total"] = round(time.time() - t00, 1)
    return out


def main():
    args = sys.argv[1:]
    salida = next((a for a in args if a.endswith(".jsonl")), "_banco_300.jsonl")
    workers = 8
    provs = None
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    if "--prov" in args:
        provs = [p.strip() for p in args[args.index("--prov") + 1].split(",")]
    pausa = float(args[args.index("--pausa") + 1]) if "--pausa" in args else 0.0
    plan = [(p, ms) for p, ms in MUNICIPIOS.items() if not provs or p in provs]
    hechos = set()
    if os.path.exists(salida):
        for ln in open(salida, encoding="utf-8"):
            try:
                hechos.add(json.loads(ln)["muni"])
            except Exception:  # noqa: BLE001
                pass
    fh = open(salida, "a", encoding="utf-8")

    def provincia(item):
        prov, munis = item
        for m in munis:
            if m in hechos:
                continue
            r = probar(prov, m)
            if pausa:
                time.sleep(pausa)      # boletines con límite de peticiones/minuto (Málaga: Turnstile)
            with _LOCK:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
                extra = r.get("materia_ok", "") or (r["casos"][-1].get("leer") if r["casos"] else "")
                print(f"[{r['estado']:7s}] {prov:18s} {m:30s} {r['t_total']:6.1f}s  {extra}  VUT={r.get('vut',{}).get('leer')}",
                      flush=True)
        return prov

    # las provincias grandes primero (tardan mas)
    plan.sort(key=lambda x: -len(x[1]))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(provincia, plan))
    fh.close()
    # resumen
    tot = {}
    for ln in open(salida, encoding="utf-8"):
        r = json.loads(ln)
        tot[r["estado"]] = tot.get(r["estado"], 0) + 1
    print("=" * 80)
    print("RESUMEN:", tot)


if __name__ == "__main__":
    main()
