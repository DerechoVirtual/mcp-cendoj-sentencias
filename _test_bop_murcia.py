# -*- coding: utf-8 -*-
"""Banco MURCIA / BORM (familia REST-JSON, /txt sin OCR) — offline _*.
34 casos (30 debe-encontrar en 13 municipios + 4 honesto), ordenanza EXACTA + < 5 s.
(Murcia CIUDAD va por el catálogo curado de las 9 ciudades, no aquí.)"""
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
    ("Cartagena", "participación ciudadana", r"participaci[oó]n ciudadana", ""),
    ("Cartagena", "ayuda a domicilio", r"ayuda a domicilio", ""),
    ("Cartagena", "quioscos", r"quioscos", ""),
    ("Lorca", "zona de bajas emisiones", r"zona de bajas emisiones", ""),
    ("Lorca", "ZBE", r"bajas emisiones", ""),
    ("Lorca", "mujer e igualdad", r"mujer e igualdad", ""),
    ("Molina de Segura", "zona de bajas emisiones", r"bajas emisiones", ""),
    ("Molina de Segura", "ayudas económicas municipales", r"ayudas econ[oó]micas", ""),
    ("Águilas", "vehículos de movilidad personal", r"veh[ií]culos de movilidad", ""),
    ("Águilas", "patinetes VMP", r"veh[ií]culos de movilidad|movilidad", ""),
    ("Águilas", "residuos y limpieza viaria", r"residuos|limpieza vi", ""),
    ("Cieza", "tráfico y movilidad", r"tr[aá]fico y movilidad", "1"),
    ("Cieza", "protección animal", r"protecci[oó]n animal", ""),
    ("Yecla", "vehículos de movilidad", r"veh[ií]culos de m|circulaci[oó]n de veh", ""),
    ("Yecla", "precio público", r"precio p[uú]blico", ""),
    ("Jumilla", "medio ambiente ruidos", r"medio ambiente contra la emisi[oó]n|ruidos", ""),
    ("Jumilla", "contaminación acústica", r"medio ambiente contra la emisi[oó]n", ""),
    ("Totana", "actividades ganaderas", r"actividades ganaderas", ""),
    ("Totana", "caminos rurales", r"caminos rurales", ""),
    ("Mazarrón", "convivencia ciudadana", r"convivencia ciudadana", ""),
    ("Mazarrón", "protección del medio ambiente", r"medio ambiente|medioamb", ""),
    ("Caravaca de la Cruz", "transparencia", r"transparencia", ""),
    ("Caravaca de la Cruz", "instalaciones deportivas", r"instalaciones deportiv", ""),
    ("La Unión", "animales de compañía", r"animales de compa[ñn][ií]a|tenencia de los animales", ""),
    ("La Unión", "retirada de vehículos grúa", r"retira", ""),
    ("Alhama de Murcia", "plazas y mercados", r"plazas y mercados|mercado", ""),
    ("Cieza", "limpieza viaria", r"limpieza viaria|tr[aá]fico y movilidad|residuos", ""),
    ("Jumilla", "tarifas servicios", r"tarifas|servicios", ""),
    ("Caravaca de la Cruz", "utilización instalaciones deportivas", r"instalaciones deportiv", ""),
    ("Cartagena", "medio ambiente", r"medio ambiente|convivencia|participaci", ""),
]

HONESTO = [
    ("Cartagena", "aeropuerto internacional"),
    ("Yecla", "puerto marítimo"),
    ("Jumilla", "metro subterráneo"),
    ("Totana", "telesilla de montaña"),
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
    print("== MURCIA/BORM debe-encontrar ==")
    for muni, consulta, rx, art in ENCONTRAR:
        tot += 1
        estado, det, dt = probar_encontrar(muni, consulta, rx, art)
        if estado == "OK":
            ok += 1
        if dt >= LIMITE:
            lentos.append((muni, consulta, round(dt, 1)))
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:22} {consulta:38} -> {det}")
    print("\n== honesto ==")
    for muni, consulta in HONESTO:
        tot += 1
        estado, det, dt = probar_honesto(muni, consulta)
        if estado == "OK":
            ok += 1
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:22} {consulta:38} -> {det}")
    print(f"\nRESULTADO MURCIA: {ok}/{tot}  (límite {LIMITE:.0f}s)")
    if lentos:
        print("LENTOS:", lentos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
