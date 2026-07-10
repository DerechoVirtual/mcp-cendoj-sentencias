# -*- coding: utf-8 -*-
"""Banco TOLEDO (BOP familia SOLR) — offline _*. 34 casos (30 debe-encontrar en 15
municipios + 4 honesto), ordenanza EXACTA + texto real + < 5 s."""
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
    ("Toledo", "convivencia", r"convivencia", ""),
    ("Talavera de la Reina", "limpieza urbana", r"limpieza urbana", "1"),
    ("Talavera de la Reina", "terrazas y veladores", r"mesas y veladores|veladores", ""),
    ("Talavera de la Reina", "contaminación acústica", r"contaminaci[oó]n ac[uú]stica", ""),
    ("Illescas", "residuos urbanos", r"residuos urbanos|residuos", ""),
    ("Torrijos", "vertidos de aguas residuales", r"vertidos de aguas residuales", ""),
    ("Torrijos", "instalaciones deportivas", r"instalaciones deportivas", ""),
    ("Seseña", "vehículos de movilidad personal", r"veh[ií]culos de movilidad|movilidad personal", ""),
    ("Seseña", "convivencia y ocio", r"convivencia y ocio", ""),
    ("Seseña", "ciclo integral del agua", r"ciclo integral", ""),
    ("Ocaña", "administración electrónica", r"administraci[oó]n electr[oó]nica", ""),
    ("Ocaña", "patinetes VMP", r"veh[ií]culos de movilidad|movilidad personal", ""),
    ("Consuegra", "caminos públicos", r"caminos", "1"),
    ("Madridejos", "impuesto vehículos tracción mecánica", r"veh[ií]culos de tracci[oó]n", ""),
    ("Madridejos", "alcantarillado", r"alcantarillado", ""),
    ("Sonseca", "vehículos de movilidad", r"veh[ií]culos de m", ""),
    ("Sonseca", "protección civil", r"protecci[oó]n civil", ""),
    ("Sonseca", "plusvalía incremento de valor", r"incremento de valor", ""),
    ("Quintanar de la Orden", "comercio ambulante", r"comercio ambulante", ""),
    ("Quintanar de la Orden", "caminos públicos", r"caminos p[uú]blicos|caminos", ""),
    ("Quintanar de la Orden", "piscinas", r"piscinas", ""),
    ("Mora", "abastecimiento de agua potable", r"abastecimiento de agua", ""),
    ("Bargas", "estaciones de recarga vehículos eléctricos", r"estaciones de recarga", ""),
    ("Bargas", "circuitos caninos perros", r"circuitos caninos|caninos", ""),
    ("Fuensalida", "escuela municipal de música", r"escuela.{0,8}m[uú]sica", ""),
    ("Fuensalida", "instalaciones y servicios deportivos", r"instalaciones y servicios deportivos|deportiv", ""),
    ("Yuncos", "aparcamiento subterráneo", r"aparcamiento subterr[aá]neo", ""),
    ("Yuncos", "registro de entidades ciudadanas", r"entidades ciudadanas", ""),
    ("Villacañas", "cementerio", r"cementerio", ""),
    ("Ocaña", "movilidad urbana sostenible", r"veh[ií]culos de movilidad|movilidad", ""),
]

HONESTO = [
    ("Villacañas", "aeropuerto"),
    ("Consuegra", "metro subterráneo"),
    ("Mora", "puerto marítimo"),
    ("Bargas", "telesilla de montaña"),
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
        return ("LENTO", f"{dt:.1f}s {cab[:48]}", dt)
    return ("OK", cab[:60], dt)


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
    print("== TOLEDO debe-encontrar ==")
    for muni, consulta, rx, art in ENCONTRAR:
        tot += 1
        estado, det, dt = probar_encontrar(muni, consulta, rx, art)
        if estado == "OK":
            ok += 1
        if dt >= LIMITE:
            lentos.append((muni, consulta, round(dt, 1)))
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:24} {consulta:38} -> {det}")
    print("\n== honesto ==")
    for muni, consulta in HONESTO:
        tot += 1
        estado, det, dt = probar_honesto(muni, consulta)
        if estado == "OK":
            ok += 1
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:24} {consulta:38} -> {det}")
    print(f"\nRESULTADO TOLEDO: {ok}/{tot}  (límite {LIMITE:.0f}s)")
    if lentos:
        print("LENTOS:", lentos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
