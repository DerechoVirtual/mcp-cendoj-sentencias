# -*- coding: utf-8 -*-
"""Verificación EN PRODUCCIÓN del conector desplegado (lo único que cuenta).
Llama por la URL personal (JSON-RPC del MCP) y comprueba texto + latencia.

Uso: ./.venv/Scripts/python.exe _verif_prod.py [madrid|acoruna|pontevedra|tenerife|bizkaia|todo]
"""
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.request

URL = ("https://mcp.jurisprudenciator.lexiaipro.org/u/"
       "v1.ZGVyZWNob3ZpcnR1YWxncHRAZ21haWwuY29t.gBktSpIaBDjm4c81/mcp")

CASOS = {
    "madrid": [
        ("Getafe", "tenencia de animales", r"animal|perro|censo"),
        ("Móstoles", "ordenanzas fiscales IBI", r"bienes inmuebles|IBI|gravamen"),
        ("Alcalá de Henares", "terrazas y veladores", r"terraza|velador|v[íi]a p[úu]blica"),
        ("Alcobendas", "ruido contaminación acústica", r"ruido|ac[úu]stic|decibel"),
        ("Rivas-Vaciamadrid", "residuos y limpieza viaria", r"residuo|limpieza|basura"),
        ("Fuenlabrada", "impuesto construcciones ICIO", r"construcciones|instalaciones y obras|ICIO"),
        ("Leganés", "venta ambulante mercadillo", r"venta ambulante|mercadillo|puesto"),
        ("Torrejón de Ardoz", "ocupación de la vía pública", r"v[íi]a p[úu]blica|ocupaci"),
    ],
    "acoruna": [
        ("A Coruña", "terrazas", r"terraza|velador|dominio p[úu]blico|ocupaci"),
        ("Santiago de Compostela", "residuos", r"residuo|lixo|basura|limpeza"),
        ("Ferrol", "contaminación acústica ruido", r"ac[úu]stic|ru[íi]do|son"),
        ("Arteixo", "vertidos y saneamiento", r"vertedur|vertido|saneament|augas"),
        ("Carballo", "gestión de residuos", r"residuo|lixo|xesti[óo]n|recollida"),
    ],
    "pontevedra": [
        ("Vigo", "procedimiento administrativo electrónico", r"electr[óo]nic|telem[áa]tic|sede"),
        ("Pontevedra", "movilidad", r"mobilidade|movilidad|circulaci|tr[áa]fico"),
        ("Marín", "terrazas", r"terraza|mesas|v[íi]a p[úu]blica|ocupaci"),
        ("Poio", "furanchos", r"furanch|loureiro|viño|vino"),
        ("Sanxenxo", "contaminación acústica", r"ac[úu]stic|ru[íi]do|son|decibel"),
    ],
    "tenerife": [
        ("Santa Cruz de Tenerife", "zona de bajas emisiones", r"emisiones|ZBE|veh[íi]culo|circulaci"),
        ("San Cristóbal de La Laguna", "terrazas", r"terraza|velador|v[íi]a p[úu]blica"),
        ("Arona", "ordenanza fiscal", r"tasa|tarifa|cuota|imposto|impuesto"),
        ("Adeje", "residuos", r"residuo|basura|limpieza"),
        ("La Orotava", "ordenanza", r"ordenanza|reglamento|tasa"),
    ],
    "bizkaia": [
        ("Bilbao", "terrazas", r"terraza|velador|v[íi]a p[úu]blica|ocupaci"),
        ("Barakaldo", "residuos", r"residuo|basura|limpieza"),
        ("Getxo", "ordenanza fiscal", r"tasa|tarifa|cuota|impuesto"),
        ("Portugalete", "ordenanza", r"ordenanza|reglamento|tasa"),
        ("Durango", "circulación", r"circulaci|tr[áa]fico|veh[íi]culo"),
    ],
}


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
    m = re.search(r'"text"\s*:\s*"(.*?)"\s*}', b, re.S)
    if not m:
        return time.time() - t0, b[:200]
    txt = m.group(1)
    for a, c in (("\\n", "\n"), ('\\"', '"'), ("\\\\", "\\")):
        txt = txt.replace(a, c)
    try:                       # el JSON viene con \uXXXX
        txt = json.loads('"' + m.group(1) + '"')
    except Exception:  # noqa: BLE001
        pass
    return time.time() - t0, txt


def uno(c):
    muni, consulta, esperado = c
    dt, txt = llamar(muni, consulta)
    bien = (f"Ayuntamiento de {muni}" in txt) and bool(re.search(esperado, txt, re.I))
    return muni, consulta, bien, dt, txt


if __name__ == "__main__":
    quien = (sys.argv[1] if len(sys.argv) > 1 else "todo").lower()
    grupos = CASOS if quien == "todo" else {quien: CASOS[quien]}
    total_ok = total = 0
    for prov, casos in grupos.items():
        print(f"\n=== {prov.upper()} ({len(casos)} casos en producción) " + "=" * 30)
        tiempos = []
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            for muni, consulta, bien, dt, txt in ex.map(uno, casos):
                tiempos.append(dt)
                total += 1
                total_ok += bien
                print(f"[{'OK  ' if bien else 'FAIL'}] {muni:24s} «{consulta[:28]:28s}» {dt:6.1f}s")
                if not bien:
                    print("        ", re.sub(r"\s+", " ", txt)[:190])
        print(f"  → media {sum(tiempos)/len(tiempos):.1f}s · máx {max(tiempos):.1f}s")
    print("\n" + "=" * 74)
    print(f"PRODUCCIÓN: {total_ok}/{total} correctos")
    sys.exit(0 if total_ok == total else 1)
