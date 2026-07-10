# -*- coding: utf-8 -*-
"""Banco JAÉN (BOP Digit@l, índice por municipio + PDF) — offline _*.
Verificación REDUCIDA (orden Carlos, 11-jul): ~8 casos + honesto, < 5 s."""
import os
import re
import sys
import time

_env = open(os.path.expanduser("~/.claude/.env"), encoding="utf-8", errors="replace").read()
for _k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
    _m = re.search(rf"^{_k}=(.+)$", _env, re.M)
    if _m:
        os.environ[_k] = _m.group(1).strip().strip('"')

import bop_engine as b  # noqa: E402

LIMITE = 5.0

ENCONTRAR = [
    ("Bailén", "comercio ambulante", r"comercio ambulante", ""),
    ("Bailén", "vehículos de movilidad personal", r"veh[ií]culos de movilidad", "1"),
    ("Úbeda", "terrazas", r"terrazas|ocupaci[oó]n|mesas|veladores", ""),
    ("Linares", "residuos", r"residuos|basura|limpieza", ""),
    ("Andújar", "agua abastecimiento", r"agua|abastecimiento|saneamiento|alcantarillado", ""),
    ("Martos", "cementerio", r"cementerio", ""),
    ("Alcalá la Real", "registro de entidades ciudadanas", r"entidades|registro", ""),
    ("Jaén", "estacionamiento zona azul", r"estacionamiento|aparcamiento|movilidad|circulaci", ""),
    ("Torredonjimeno", "residuos basura", r"residuos|basura|limpieza|tasa", ""),
    ("Villacarrillo", "ordenanza fiscal", r"ordenanza|reglamento|tasa|impuesto", ""),
]

HONESTO = [
    ("Bailén", "aeropuerto internacional"),
    ("Martos", "puerto marítimo"),
]


def probar_encontrar(muni, consulta, rx, art):
    t0 = time.time()
    try:
        r = b.leer(muni, consulta, articulo=art, parrafos=(0 if art else 2), terminos=consulta)
    except Exception as e:  # noqa: BLE001
        return ("EXC", str(e)[:60], time.time() - t0)
    dt = time.time() - t0
    cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
    if not cab or not re.search(rx, cab, re.I):
        return ("MAL_ORD", (cab or (r or "")[:70])[:70], dt)
    if len(r) < 400 or "No encuentro" in r[:60] or "no pude leer" in r or "no tiene texto" in r:
        return ("SIN_TEXTO", cab[:58], dt)
    if art and not re.search(r"Art[íi]cul[oe]\s*" + re.escape(art) + r"(?![\d])", r, re.I):
        return ("SIN_ART", f"art {art}? " + cab[:50], dt)
    if dt >= LIMITE:
        return ("LENTO", f"{dt:.1f}s {cab[:46]}", dt)
    return ("OK", cab[:58], dt)


def probar_honesto(muni, consulta):
    t0 = time.time()
    try:
        r = b.leer(muni, consulta, parrafos=2, terminos=consulta)
    except Exception as e:  # noqa: BLE001
        return ("EXC", str(e)[:60], time.time() - t0)
    dt = time.time() - t0
    if r and r.startswith("No encuentro"):
        return ("OK", "honesto", dt)
    cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
    return ("FALSO_POS", (cab or (r or "")[:60])[:60], dt)


def main():
    ok = tot = 0
    lentos = []
    for muni, consulta, rx, art in ENCONTRAR:
        tot += 1
        estado, det, dt = probar_encontrar(muni, consulta, rx, art)
        if estado == "OK":
            ok += 1
        if dt >= LIMITE:
            lentos.append((muni, consulta, round(dt, 1)))
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:18} {consulta:34} -> {det}")
    for muni, consulta in HONESTO:
        tot += 1
        estado, det, dt = probar_honesto(muni, consulta)
        if estado == "OK":
            ok += 1
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:18} {consulta:34} -> {det}")
    print(f"\nRESULTADO JAÉN: {ok}/{tot}  (límite {LIMITE:.0f}s)")
    if lentos:
        print("LENTOS:", lentos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
