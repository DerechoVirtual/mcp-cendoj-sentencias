# -*- coding: utf-8 -*-
"""Casos que fallaban en el banco de los 300 (2-sep-2026) y su arreglo."""
import os, re, sys, time
_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
for ln in open(_ENV, encoding="utf-8"):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1); os.environ[k.strip()] = v.strip().strip('"').strip("'")
import ordenanzas_engine as OE
import bop_engine as B

CASOS = [
    # (municipio, consulta, regex esperado en la lectura)   -- buscar + leer(parrafos=3)
    ("Crevillente", "terrazas", r"terraza|velador|ocupaci"),
    ("La Coruña", "terrazas", r"terraza|velador|dominio|ocupaci"),
    ("Las Rozas", "terrazas veladores", r"terraza|velador"),
    ("Hospitalet", "residuos limpieza", r"residu|neteja|limpieza"),
    ("Jerez de la Frontera", "terrazas veladores", r"terraza|velador"),
    ("Cádiz", "residuos limpieza", r"residu|limpieza|basura"),
    ("San Fernando", "ruido contaminacion acustica", r"ruido|ac[uú]stic"),
    ("La Línea de la Concepción", "tenencia de animales", r"animal|perro"),
    ("Almuñécar", "terrazas veladores", r"terraza|velador|ocupaci"),
    ("Dos Hermanas", "zona de bajas emisiones", r"emision|zbe"),
    ("Fuengirola", "residuos limpieza", r"residu|limpieza"),
    ("Torremolinos", "terrazas veladores", r"velador|mesas"),
    ("Sevilla", "pisos turísticos", r"tur[ií]stic"),
    ("Sevilla", "terrazas veladores", r"terraza|velador"),
    ("Sevilla", "residuos limpieza", r"residu|limpieza"),
]

if __name__ == "__main__":
    solo = sys.argv[1:]
    ok = 0
    for muni, q, rx in CASOS:
        if solo and not any(s.lower() in muni.lower() for s in solo):
            continue
        t0 = time.time()
        b = OE.buscar(muni, q, 6)
        ids = re.findall(r"\bid: (\S+)", b)
        ref = ids[0] if ids else q
        l = OE.leer(muni, ref, "", 3, q, 0)
        dt = time.time() - t0
        bien = ("【" in l[:600]) and bool(re.search(rx, l, re.I)) and not l.startswith("Error")
        ok += bien
        print(f"[{'OK ' if bien else 'BAD'}] {muni:26s} «{q[:24]:24s}» {dt:5.1f}s | B={re.sub(r'\s+',' ',b)[:90]}")
        print(f"      L={re.sub(r'\s+',' ',l)[:230]}")
    print(f"\n{ok} correctos")
