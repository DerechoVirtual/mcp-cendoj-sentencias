# -*- coding: utf-8 -*-
"""Verificación EN PRODUCCIÓN (conector desplegado) de la cobertura de Madrid.
Mide lo único que importa: qué devuelve y cuánto tarda desde Vercel, no desde
este PC. Se llama por la URL personal (JSON-RPC del MCP)."""
import json
import re
import sys
import time
import urllib.request

URL = ("https://mcp.jurisprudenciator.lexiaipro.org/u/"
       "v1.ZGVyZWNob3ZpcnR1YWxncHRAZ21haWwuY29t.gBktSpIaBDjm4c81/mcp")

CASOS = [
    ("Getafe", "tenencia de animales", r"animal|perro|censo"),
    ("Móstoles", "ordenanzas fiscales IBI", r"bienes inmuebles|IBI|gravamen"),
    ("Alcalá de Henares", "terrazas y veladores", r"terraza|velador|v[íi]a p[úu]blica"),
    ("Alcobendas", "ruido contaminación acústica", r"ruido|ac[úu]stic|decibel"),
    ("Las Rozas de Madrid", "circulación y movilidad", r"circulaci|tr[áa]fico|veh[íi]culo|movilidad"),
    ("Rivas-Vaciamadrid", "residuos y limpieza viaria", r"residuo|limpieza|basura"),
    ("Torrejón de Ardoz", "ocupación de la vía pública", r"v[íi]a p[úu]blica|ocupaci"),
    ("Fuenlabrada", "impuesto construcciones ICIO", r"construcciones|instalaciones y obras|ICIO"),
    ("Leganés", "venta ambulante mercadillo", r"venta ambulante|mercadillo|puesto"),
    ("Pozuelo de Alarcón", "convivencia ciudadana", r"convivencia|civismo|espacio p[úu]blico"),
]


def llamar(muni, consulta, timeout=150):
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "leer_ordenanza",
                                    "arguments": {"municipio": muni, "ordenanza": consulta,
                                                  "parrafos": 2, "terminos": consulta}}},
                        ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=cuerpo, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/event-stream"})
    t0 = time.time()
    try:
        b = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return time.time() - t0, f"[ERROR HTTP] {type(e).__name__}: {e}"
    dt = time.time() - t0
    m = re.search(r'"text"\s*:\s*"(.*?)"\s*}', b, re.S)
    txt = m.group(1).encode().decode("unicode_escape") if m else b[:200]
    return dt, txt


if __name__ == "__main__":
    print(f"VERIFICACIÓN EN PRODUCCIÓN — {len(CASOS)} casos\n" + "=" * 84)
    ok_n, tiempos = 0, []
    for muni, consulta, esperado in CASOS:
        dt, txt = llamar(muni, consulta)
        tiempos.append(dt)
        bien = (f"Ayuntamiento de {muni}" in txt) and bool(re.search(esperado, txt, re.I))
        ok_n += bien
        estado = "OK  " if bien else "FAIL"
        print(f"[{estado}] {muni:22s} «{consulta[:26]:26s}» {dt:6.1f}s")
        if not bien:
            print("        ", re.sub(r"\s+", " ", txt)[:180])
    print("=" * 84)
    print(f"PRODUCCIÓN: {ok_n}/{len(CASOS)} correctos · media {sum(tiempos)/len(tiempos):.1f}s "
          f"· máx {max(tiempos):.1f}s")
    sys.exit(0 if ok_n == len(CASOS) else 1)
