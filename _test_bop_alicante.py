# -*- coding: utf-8 -*-
"""Banco ALICANTE (familia eConsulta webservice + PDF) — offline _*.
34 casos (30 debe-encontrar en 14 municipios + 4 honesto), ordenanza EXACTA + < 5 s.
(Nombres valencianos: el filtro `publicante` usa la forma castellana; el mapa lo
resuelve. Alicante CIUDAD no está en las 9 ciudades, va por aquí si se mapea.)"""
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
    ("Elche", "residuos", r"recogi|residuos", ""),
    ("Alcoy", "terrazas", r"terr|mesas|ocupaci", ""),
    ("Santa Pola", "huertos urbanos", r"huertos urbanos", ""),
    ("Orihuela", "venta ambulante", r"venta no sedentaria|ambulante", ""),
    ("Orihuela", "transporte público discrecional", r"transporte p[uú]blico|discrecional", ""),
    ("Mutxamel", "circulación", r"circulaci[oó]n", ""),
    ("Crevillent", "convivencia ciudadana", r"convivencia ciudadana", ""),
    ("Crevillent", "celebraciones civiles", r"celebraciones civiles", ""),
    ("Elda", "cementerios", r"cementerios", ""),
    ("Elda", "utilización privativa dominio público", r"utilizaci[oó]n privativa", ""),
    ("Villena", "menjar a casa", r"menjar a casa", ""),
    ("Villena", "recogida de residuos", r"recogida|residuos", ""),
    ("Dénia", "alcantarillado", r"alcantarillado", ""),
    ("Torrevieja", "derechos de examen", r"derechos de examen", ""),
    ("Torrevieja", "prestaciones patrimoniales", r"prestaciones patrimoniales", ""),
    ("Benidorm", "movilidad", r"movilidad", ""),
    ("Ibi", "convivencia ciudadana", r"convivencia ciudadana", ""),
    ("Ibi", "ayuda a domicilio", r"ayuda a domicilio", ""),
    ("Novelda", "grúa retirada de vehículos", r"gr[uú]a|recogida veh[ií]culos|veh[ií]culos con gr", ""),
    ("Aspe", "menjar a casa", r"menjar a casa", ""),
    ("Guardamar del Segura", "venta ambulante", r"venta no sedentaria|ambulante", ""),
    ("La Nucía", "ayuda a domicilio", r"ayuda a domicilio", ""),
    ("San Vicente del Raspeig", "vivienda compartida", r"vivienda compartida", ""),
    ("Petrer", "recogida de basura", r"recogida|residuos", ""),
    ("Santa Pola", "utilización privativa", r"utilizaci[oó]n privativa", ""),
    ("Elche", "basura", r"recogi|residuos", ""),
    ("Orihuela", "mercadillo", r"venta no sedentaria|ambulante", ""),
    ("Crevillent", "bodas civiles", r"celebraciones civiles", ""),
    ("La Nucía", "recogida de residuos sólidos urbanos", r"trsu|recogida|residuos", ""),
    ("Elda", "recogida de basura", r"recogida|residuos|basura", ""),
]

HONESTO = [
    ("Elche", "aeropuerto internacional"),
    ("Benidorm", "telesilla de montaña"),
    ("Orihuela", "metro subterráneo"),
    ("Villena", "puerto marítimo"),
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
    return ("OK", cab[:56], dt)


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
    print("== ALICANTE debe-encontrar ==")
    for muni, consulta, rx, art in ENCONTRAR:
        tot += 1
        estado, det, dt = probar_encontrar(muni, consulta, rx, art)
        if estado == "OK":
            ok += 1
        if dt >= LIMITE:
            lentos.append((muni, consulta, round(dt, 1)))
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:26} {consulta:38} -> {det}")
    print("\n== honesto ==")
    for muni, consulta in HONESTO:
        tot += 1
        estado, det, dt = probar_honesto(muni, consulta)
        if estado == "OK":
            ok += 1
        print(f"{'✅' if estado=='OK' else '❌'} [{dt:4.1f}s {estado:9}] {muni:26} {consulta:38} -> {det}")
    print(f"\nRESULTADO ALICANTE: {ok}/{tot}  (límite {LIMITE:.0f}s)")
    if lentos:
        print("LENTOS:", lentos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
