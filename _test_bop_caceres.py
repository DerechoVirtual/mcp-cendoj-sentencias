# -*- coding: utf-8 -*-
"""Banco CÁCERES (BOP familia REST-JSON) — offline _*.
34 casos (30 debe-encontrar repartidos por 13 municipios + 4 honesto), cada uno
verificando la ordenanza EXACTA + texto real + < 5 s. Estilo Bormujos/León."""
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

# (municipio, consulta, regex_en_cabecera, articulo)
ENCONTRAR = [
    ("Plasencia", "ruidos", r"ordenanza municipal de ruidos", "1"),
    ("Plasencia", "contaminación acústica", r"ruidos", ""),
    ("Cáceres", "zona de bajas emisiones", r"zonas? de baja[s]? emisiones", ""),
    ("Cáceres", "ZBE", r"baja[s]? emisiones", ""),
    ("Cáceres", "piscinas municipales", r"piscinas municipales", ""),
    ("Trujillo", "residuos", r"residuos s[oó]lidos urbanos|limpieza de espacios", ""),
    ("Trujillo", "limpieza viaria", r"limpieza de espacios|residuos", ""),
    ("Navalmoral de la Mata", "terrazas", r"terrazas y veladores|instalaci[oó]n de terrazas", ""),
    ("Coria", "punto limpio", r"punto limpio", "1"),
    ("Miajadas", "venta ambulante", r"venta ambulante", ""),
    ("Miajadas", "mercadillo", r"venta ambulante|mercado", ""),
    ("Jaraíz de la Vera", "saneamiento y depuración", r"saneamiento", ""),
    ("Jaraíz de la Vera", "abastecimiento de agua", r"abastecimiento", ""),
    ("Moraleja", "festejos taurinos", r"festejos taurinos", ""),
    ("Moraleja", "ayuda a domicilio", r"ayuda a domicilio", ""),
    ("Casar de Cáceres", "incendios forestales", r"incendios forestales", "1"),
    ("Casar de Cáceres", "alcantarillado", r"alcantarillado|abastecimiento", ""),
    ("Valencia de Alcántara", "residencia de mayores", r"residencia de mayores", ""),
    ("Valencia de Alcántara", "desinfección", r"desinfecci[oó]n", ""),
    ("Malpartida de Cáceres", "segunda actividad policía", r"segunda actividad", ""),
    ("Talayuela", "residuos de construcción y derribo", r"residuos de derribo|derribos y construccion", ""),
    ("Talayuela", "matrimonio civil", r"matrimonio civil", ""),
    ("Talayuela", "escuela de música", r"escuela municipal de m[uú]sica", ""),
    ("Talayuela", "honores y distinciones", r"honores y distinciones", ""),
    ("Montehermoso", "utilización privativa dominio público", r"utilizaci[oó]n privativa", ""),
    ("Miajadas", "suministro de agua precio público", r"suministro", ""),
    ("Coria", "carrera profesional", r"carrera profesional", ""),
    ("Casar de Cáceres", "archivo municipal", r"archivo municipal", ""),
    ("Plasencia", "reglamento orgánico", r"reglamento org[aá]nico", ""),
    ("Valencia de Alcántara", "reglamento residencia", r"residencia de mayores", ""),
]

HONESTO = [
    ("Trujillo", "zona de bajas emisiones"),   # ZBE es de la capital, no de Trujillo
    ("Coria", "prostitución"),
    ("Montehermoso", "aeropuerto"),
    ("Miajadas", "puerto deportivo"),
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
    print("== CÁCERES debe-encontrar ==")
    for muni, consulta, rx, art in ENCONTRAR:
        tot += 1
        estado, det, dt = probar_encontrar(muni, consulta, rx, art)
        if estado == "OK":
            ok += 1
        if dt >= LIMITE:
            lentos.append((muni, consulta, round(dt, 1)))
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:22} {consulta:32} -> {det}")
    print("\n== honesto (materia ausente) ==")
    for muni, consulta in HONESTO:
        tot += 1
        estado, det, dt = probar_honesto(muni, consulta)
        if estado == "OK":
            ok += 1
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:22} {consulta:32} -> {det}")
    print(f"\nRESULTADO CÁCERES: {ok}/{tot}  (límite {LIMITE:.0f}s)")
    if lentos:
        print("LENTOS:", lentos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
