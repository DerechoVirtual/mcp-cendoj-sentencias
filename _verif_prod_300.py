# -*- coding: utf-8 -*-
"""Verificación EN PRODUCCIÓN (o en un preview) de los 300 municipios >50k:
el MISMO flujo que hace Claude/el chat (buscar_ordenanzas -> leer_ordenanza con
parrafos=3) por JSON-RPC contra la URL personal del conector. Lo único que cuenta
es lo que devuelve Vercel, no este PC.

Uso: python -X utf8 _verif_prod_300.py [host] [--workers 4] [--prov Sevilla,Cádiz] [--materias 2] [salida.jsonl]
     host por defecto: mcp.jurisprudenciator.lexiaipro.org (producción); para un
     preview pasa su hostname (p.ej. jurisprudenciator-mcp-git-xxx.vercel.app).
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time
import urllib.request

from _cobertura_50k import MUNICIPIOS

TOKEN = "v1.ZGVyZWNob3ZpcnR1YWxncHRAZ21haWwuY29t.gBktSpIaBDjm4c81"
MATERIAS = [
    ("terrazas veladores", r"terraza|velador|mesas|ocupaci|taula|cadires"),
    ("residuos limpieza", r"residu|basura|limpieza|lixo|escombr|limpeza|neteja|hondakin"),
    ("ruido contaminacion acustica", r"ruido|ac[uú]stic|soroll|ru[ií]do|vibraci|decibel|sonor"),
    ("tenencia de animales", r"animal|perro|gat|mascota|can[ií]n|gos"),
]
_LOCK = threading.Lock()


def llamar(url, tool, args, timeout=120):
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": tool, "arguments": args}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=cuerpo, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/event-stream"})
    t0 = time.time()
    try:
        b = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return time.time() - t0, f"[ERROR HTTP] {type(e).__name__}: {str(e)[:120]}"
    dt = time.time() - t0
    # respuesta SSE o JSON: extraer el primer "text"
    m = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', b, re.S)
    if not m:
        return dt, b[:300]
    try:
        txt = json.loads('"' + m.group(1) + '"')
    except Exception:  # noqa: BLE001
        txt = m.group(1)
    return dt, txt


def clasificar(l):
    if not l:
        return "VACIO"
    if l.startswith("[ERROR HTTP]"):
        return "HTTP"
    if l.startswith("Municipio no cubierto"):
        return "NOCUB"
    if l.startswith("Error") or l.startswith("Localicé") or "no pude leer" in l[:200]:
        return "ERROR"
    if l.startswith("No encuentro") or l.startswith("Sin resultados") or l.startswith("No identifico"):
        return "HONESTO"
    if "【" not in l[:600]:
        return "RARO"
    cuerpo = l.split("\n\n", 1)[1] if "\n\n" in l else ""
    cuerpo = re.sub(r"Fuente:.*$", "", cuerpo, flags=re.S).strip()
    return "TEXTO" if len(cuerpo) >= 250 else "CORTO"


def probar(url, prov, muni, n_materias):
    out = {"prov": prov, "muni": muni, "casos": [], "estado": "FAIL"}
    t00 = time.time()
    for materia, rx in MATERIAS[:n_materias]:
        dtb, b = llamar(url, "buscar_ordenanzas", {"municipio": muni, "consulta": materia, "limite": 6})
        ids = re.findall(r"\bid: (\S+)", b)
        ref = ids[0] if ids else materia
        dtl, l = llamar(url, "leer_ordenanza", {"municipio": muni, "ordenanza": ref, "parrafos": 3, "terminos": materia})
        c = {"materia": materia, "t_buscar": round(dtb, 1), "buscar": clasificar(b), "t_leer": round(dtl, 1),
             "leer": clasificar(l), "leer_txt": re.sub(r"\s+", " ", l)[:220], "buscar_txt": re.sub(r"\s+", " ", b)[:160]}
        out["casos"].append(c)
        if c["buscar"] == "NOCUB":
            out["estado"] = "NOCUB"
            break
        if c["leer"] == "TEXTO":
            out["estado"] = "OK"
            out["materia_ok"] = materia
            out["t_ok"] = round(dtb + dtl, 1)
            break
    if out["estado"] == "FAIL":
        clases = {c["leer"] for c in out["casos"]} | {c["buscar"] for c in out["casos"]}
        if not clases & {"ERROR", "RARO", "VACIO", "HTTP"}:
            out["estado"] = "HONESTO"
    out["t_total"] = round(time.time() - t00, 1)
    return out


def main():
    args = sys.argv[1:]
    host = next((a for a in args if "." in a and not a.endswith(".jsonl") and not a.startswith("--")), "mcp.jurisprudenciator.lexiaipro.org")
    url = f"https://{host}/u/{TOKEN}/mcp"
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 4
    n_mat = int(args[args.index("--materias") + 1]) if "--materias" in args else 2
    provs = [p.strip() for p in args[args.index("--prov") + 1].split(",")] if "--prov" in args else None
    salida = next((a for a in args if a.endswith(".jsonl")), f"_verif_prod_300_{re.sub(r'[^a-z0-9]+', '_', host)[:40]}.jsonl")
    plan = [(p, m) for p, ms in MUNICIPIOS.items() for m in ms if not provs or p in provs]
    hechos = set()
    if os.path.exists(salida):
        for ln in open(salida, encoding="utf-8"):
            try:
                hechos.add(json.loads(ln)["muni"])
            except Exception:  # noqa: BLE001
                pass
    plan = [(p, m) for p, m in plan if m not in hechos]
    fh = open(salida, "a", encoding="utf-8")
    print(f"{url}\n{len(plan)} municipios · {workers} hilos · {n_mat} materias\n" + "=" * 80)

    def uno(pm):
        p, m = pm
        r = probar(url, p, m, n_mat)
        with _LOCK:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            extra = r.get("materia_ok", "") or (r["casos"][-1]["leer"] if r["casos"] else "")
            print(f"[{r['estado']:7s}] {p:18s} {m:30s} {r['t_total']:6.1f}s  {extra}", flush=True)
            if r["estado"] in ("FAIL",):
                for c in r["casos"]:
                    print("          ", c["materia"][:18], c["buscar"], c["leer"], "|", c["leer_txt"][:150], flush=True)
        return r

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(uno, plan))
    fh.close()
    tot, tiempos = {}, []
    for ln in open(salida, encoding="utf-8"):
        r = json.loads(ln)
        tot[r["estado"]] = tot.get(r["estado"], 0) + 1
        if r["estado"] == "OK":
            tiempos.append(r["t_ok"])
    tiempos.sort()
    print("=" * 80)
    print("RESUMEN:", tot, "· t_ok mediana", tiempos[len(tiempos) // 2] if tiempos else None,
          "p90", tiempos[int(len(tiempos) * 0.9)] if tiempos else None, "max", tiempos[-1] if tiempos else None)


if __name__ == "__main__":
    main()
