# -*- coding: utf-8 -*-
"""Banco de pruebas del BOP de MADRID (BOCM). Regla de Carlos: no se integra
hasta que TODAS las pruebas den resultado correcto (ordenanza real del municipio
pedido, con texto exacto) — y "correcto" incluye DECIR QUE NO EXISTE cuando de
verdad no está publicada, sin inventar articulado.

  * POSITIVOS: materia muy concreta -> ordenanza real de ESE municipio.
  * HONESTOS : materia que ese ayuntamiento NO tiene en el BOCM -> lo dice.
  * BUSCAR   : el listado devuelve ordenanzas de ese municipio.

Uso:  ./.venv/Scripts/python.exe _test_bop_madrid.py [-v]
"""
import concurrent.futures as cf
import re
import sys
import time

import bop_engine as B

VERBOSE = "-v" in sys.argv

# (municipio, consulta, términos que DEBEN aparecer en el texto devuelto)
POSITIVOS = [
    ("Getafe",                "tenencia de animales",         r"animal|perro|censo"),
    ("Móstoles",              "ordenanzas fiscales IBI",      r"bienes inmuebles|IBI|tipo de gravamen"),
    ("Alcalá de Henares",     "terrazas y veladores",         r"terraza|velador|v[íi]a p[úu]blica"),
    ("Alcobendas",            "ruido contaminación acústica", r"ruido|ac[úu]stic|decibel|dBA"),
    ("Las Rozas de Madrid",   "circulación y movilidad",      r"circulaci[óo]n|tr[áa]fico|veh[íi]culo|movilidad"),
    ("Rivas-Vaciamadrid",     "residuos y limpieza viaria",   r"residuo|limpieza|basura"),
    ("Torrejón de Ardoz",     "ocupación de la vía pública",  r"v[íi]a p[úu]blica|ocupaci[óo]n"),
    ("Fuenlabrada",           "impuesto construcciones ICIO", r"construcciones|instalaciones y obras|ICIO"),
    ("Leganés",               "venta ambulante mercadillo",   r"venta ambulante|mercadillo|puesto"),
    ("Pozuelo de Alarcón",    "convivencia ciudadana",        r"convivencia|civismo|espacio p[úu]blico"),
    ("Aranjuez",              "ordenanza fiscal tasa",        r"tasa|cuota|tarifa"),
    ("Majadahonda",           "aparcamiento estacionamiento", r"aparcamiento|estacionamiento|v[íi]a p[úu]blica"),
    ("Collado Villalba",      "terrazas de hostelería",       r"terraza|hosteler|velador"),
]

# materias que ESE ayuntamiento no tiene publicadas en el BOCM (verificado a mano:
# la búsqueda del propio BOCM devuelve 0 ordenanzas para el término distintivo)
HONESTOS = [
    ("Boadilla del Monte",    "vados entrada de vehículos"),
    ("Robledo de Chavela",    "zona de bajas emisiones ZBE"),
]

BUSCAR = [("Getafe", "residuos"), ("Móstoles", "ordenanza")]


def caso_positivo(c):
    muni, consulta, esperado = c
    t0 = time.time()
    try:
        r = B.leer(muni, consulta, parrafos=2, terminos=consulta) or ""
    except Exception as e:  # noqa: BLE001
        return (f"POS {muni}", False, f"EXCEPCIÓN {type(e).__name__}: {e}", time.time() - t0, "")
    dt, fallos = time.time() - t0, []
    if not r or "No encuentro" in r[:200]:
        fallos.append("sin resultado")
    else:
        if not re.search(rf"Ayuntamiento de {re.escape(muni)}", r, re.I):
            fallos.append("cabecera de OTRO municipio")
        cuerpo = r.split("\n\n", 1)[-1]
        if not re.search(esperado, cuerpo, re.I):
            fallos.append(f"no aparece /{esperado}/")
        if len(cuerpo) < 250:
            fallos.append(f"texto muy corto ({len(cuerpo)}c)")
    return (f"POS {muni} «{consulta[:26]}»", not fallos, "; ".join(fallos) or "OK", dt, r)


def caso_honesto(c):
    muni, consulta = c
    t0 = time.time()
    try:
        r = B.leer(muni, consulta, parrafos=2, terminos=consulta) or ""
    except Exception as e:  # noqa: BLE001
        return (f"HON {muni}", False, f"EXCEPCIÓN {type(e).__name__}: {e}", time.time() - t0, "")
    dt, fallos = time.time() - t0, []
    if "No encuentro" not in r[:200]:
        fallos.append("DEVUELVE ALGO en vez de reconocer que no hay")
    else:
        if "sede electrónica" not in r and "sede electr" not in r:
            fallos.append("no orienta a la sede del ayuntamiento")
        if re.search(r"Art[íi]culo\s+\d+\s*[.\-–]", r):
            fallos.append("¡INVENTA ARTICULADO!")
    return (f"HON {muni} «{consulta[:26]}»", not fallos, "; ".join(fallos) or "OK", dt, r)


def caso_buscar(c):
    muni, consulta = c
    t0 = time.time()
    try:
        r = B.buscar(muni, consulta) or ""
    except Exception as e:  # noqa: BLE001
        return (f"BUS {muni}", False, f"EXCEPCIÓN {type(e).__name__}: {e}", time.time() - t0, "")
    dt, fallos = time.time() - t0, []
    if "No encuentro" in r[:200] or "Error" in r[:40]:
        fallos.append("sin listado")
    else:
        if not re.search(r"BOCM-\d{8}-\d+", r):
            fallos.append("sin identificadores BOCM")
        if len(re.findall(r"\n\d+\. ", r)) < 2:
            fallos.append("menos de 2 resultados")
    return (f"BUS {muni} «{consulta[:26]}»", not fallos, "; ".join(fallos) or "OK", dt, r)


if __name__ == "__main__":
    trabajos = ([(caso_positivo, c) for c in POSITIVOS]
                + [(caso_honesto, c) for c in HONESTOS]
                + [(caso_buscar, c) for c in BUSCAR])
    print(f"BANCO BOP MADRID — {len(trabajos)} pruebas "
          f"({len(POSITIVOS)} positivas, {len(HONESTOS)} honestas, {len(BUSCAR)} listados)")
    print("=" * 86)
    res = []
    with cf.ThreadPoolExecutor(max_workers=3) as ex:      # suave con el BOCM
        for r in ex.map(lambda t: t[0](t[1]), trabajos):
            res.append(r)
            etiq, ok, det, dt, txt = r
            print(f"[{'OK ' if ok else 'FAIL'}] {etiq:52s} {dt:5.1f}s  {det}")
            if VERBOSE or not ok:
                print("        ", re.sub(r"\s+", " ", (txt or ""))[:300])
    n = sum(1 for x in res if x[1])
    lent = [(x[0].split()[1], round(x[3], 1)) for x in res if x[3] > 6]
    print("=" * 86)
    print(f"RESULTADO: {n}/{len(res)} correctos")
    print(f"latencia: media {sum(x[3] for x in res)/len(res):.1f}s · máx {max(x[3] for x in res):.1f}s"
          + (f" · >6s: {lent}" if lent else ""))
    sys.exit(0 if n == len(res) else 1)
