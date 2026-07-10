# -*- coding: utf-8 -*-
"""Banco HUELVA (BOP familia bope_web, POST Solr + PDF) — offline _*.
34 casos (30 debe-encontrar en 13 municipios + 4 honesto), ordenanza EXACTA + < 5 s."""
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
    ("Huelva", "protección civil", r"protecci[oó]n civil", ""),
    ("Huelva", "zona de bajas emisiones", r"bajas emisiones", ""),
    ("Lepe", "huella de carbono", r"huella de carbono", ""),
    ("Lepe", "movilidad sostenible", r"movilidad sosten|circulaci[oó]n", ""),
    ("Almonte", "reglamento orgánico", r"reglamento org[aá]nico", ""),
    ("Almonte", "honores policía", r"honores|condecoraciones", ""),
    ("Ayamonte", "transparencia", r"transparencia", ""),
    ("Isla Cristina", "protección civil", r"protecci[oó]n civil", ""),
    ("Isla Cristina", "precio público", r"precio p[uú]blico", ""),
    ("Moguer", "administración electrónica", r"administraci[oó]n electr[oó]nica", ""),
    ("Moguer", "prestación patrimonial", r"prestaci[oó]n patrimonial", ""),
    ("Cartaya", "recogida de residuos", r"recogida y transporte de residuos|residuos", ""),
    ("Cartaya", "zonas de acceso restringido", r"accesos? restringid", ""),
    ("Cartaya", "alojamiento turístico", r"alojamiento", ""),
    ("Cartaya", "basura", r"residuos|recogida", ""),
    ("Bollullos Par del Condado", "emisora municipal", r"emisora municipal", ""),
    ("Bollullos Par del Condado", "honores y distinciones", r"honores", ""),
    ("Aljaraque", "aeronaves no tripuladas drones", r"aeronaves no tripuladas", ""),
    ("Aljaraque", "protección civil", r"protecci[oó]n civil", ""),
    ("Aljaraque", "suministro de agua", r"suministro", ""),
    ("Punta Umbría", "columbario", r"columbario", ""),
    ("Valverde del Camino", "residuos de construcción y demolición", r"residuos de (la )?construcci", ""),
    ("Valverde del Camino", "actuaciones urbanísticas", r"actuaciones urban[ií]sticas", ""),
    ("Gibraleón", "vehículos de movilidad personal", r"veh[ií]culos de movilidad", ""),
    ("Gibraleón", "patinetes VMP", r"veh[ií]culos de movilidad|movilidad", ""),
    ("Palos de la Frontera", "registro electrónico", r"registro electr[oó]nico", ""),
    ("Bonares", "utilización privativa dominio público", r"utilizaci[oó]n privativa", ""),
    ("Ayamonte", "reglamento de honores policía local", r"honores", ""),
    ("Almonte", "precio público servicio", r"precio p[uú]blico", ""),
    ("Punta Umbría", "declaración responsable licencia", r"declaraci[oó]n responsable|licencia", ""),
]

HONESTO = [
    ("Bonares", "aeropuerto internacional"),
    ("Isla Cristina", "metro subterráneo"),
    ("Almonte", "puerto espacial"),
    ("Moguer", "telesilla de montaña"),
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
    print("== HUELVA debe-encontrar ==")
    for muni, consulta, rx, art in ENCONTRAR:
        tot += 1
        estado, det, dt = probar_encontrar(muni, consulta, rx, art)
        if estado == "OK":
            ok += 1
        if dt >= LIMITE:
            lentos.append((muni, consulta, round(dt, 1)))
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:26} {consulta:36} -> {det}")
    print("\n== honesto ==")
    for muni, consulta in HONESTO:
        tot += 1
        estado, det, dt = probar_honesto(muni, consulta)
        if estado == "OK":
            ok += 1
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:26} {consulta:36} -> {det}")
    print(f"\nRESULTADO HUELVA: {ok}/{tot}  (límite {LIMITE:.0f}s)")
    if lentos:
        print("LENTOS:", lentos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
