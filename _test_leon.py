# -*- coding: utf-8 -*-
"""Banco LEÓN (offline _*): 30+ consultas estilo Carlos.
- CAPITAL (aytoleon.es, catálogo consolidado): materia -> ordenanza correcta +
  artículo exacto, TODO en < 5 s.
- PROVINCIA (BOP): municipios de León vía bop_engine + regla anti-secuestro
  ('Astorga, León' NO debe caer en el catálogo capital).
Éxito = la cabecera 【...】 machea la regex esperada, hay texto real y dt < LIMITE.
"""
import os
import re
import sys
import time

_env = open(os.path.expanduser("~/.claude/.env"), encoding="utf-8", errors="replace").read()
for _k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
    _m = re.search(rf"^{_k}=(.+)$", _env, re.M)
    if _m:
        os.environ[_k] = _m.group(1).strip().strip('"')

import ordenanzas_engine as oe  # noqa: E402

LIMITE = 5.0  # segundos (exigencia de Carlos)

# (municipio, consulta_materia, regex_esperada_en_cabecera, articulo_para_leer)
CAPITAL = [
    ("León", "medio ambiente", r"protecci[oó]n del medio ambiente contra la emisi[oó]n de ruidos", "1"),
    ("León", "ruidos y vibraciones", r"ruidos y vibraciones", "1"),
    ("León", "contaminación acústica", r"ruidos y vibraciones", ""),
    ("León", "limpieza y residuos", r"limpieza en espacios p[uú]blicos", ""),
    ("León", "recogida de basura", r"limpieza en espacios p[uú]blicos|recogida de basuras", ""),
    ("León", "economía circular", r"limpieza en espacios p[uú]blicos", ""),
    ("León", "parques y jardines", r"parques y jardines", "1"),
    ("León", "zonas verdes", r"parques y jardines", ""),
    ("León", "antenas de telefonía", r"radiocomunicaci[oó]n", ""),
    ("León", "publicidad exterior vallas", r"publicidad exterior", ""),
    ("León", "movilidad y ZBE", r"movilidad", ""),
    ("León", "zona de bajas emisiones", r"movilidad", ""),
    ("León", "patinetes VMP", r"movilidad", ""),
    ("León", "terrazas y veladores", r"terrazas|ocupaci[oó]n de terrenos de uso p[uú]blico", ""),
    ("León", "vados", r"vados", ""),
    ("León", "convivencia ciudadana", r"convivencia ciudadana", "1"),
    ("León", "taxi", r"servicio de taxi", ""),
    ("León", "ORA zona azul", r"ora|zona azul", ""),
    ("León", "inspección técnica de edificios", r"inspecci[oó]n t[eé]cnica de edificios", ""),
    ("León", "tráfico y seguridad vial", r"tr[aá]fico y seguridad vial", ""),
    ("León", "casco histórico", r"casco hist[oó]rico", ""),
    ("León", "estacionamiento discapacidad", r"discapacidad", ""),
    ("León", "huertos urbanos", r"huertos", ""),
    ("León", "transparencia", r"transparencia", ""),
    ("León", "IBI bienes inmuebles", r"bienes inmuebles", "1"),
    ("León", "ICIO obras", r"construcciones, instalaciones y obras", ""),
    ("León", "plusvalía", r"incremento de valor de los terrenos", ""),
    ("León", "impuesto de circulación IVTM", r"veh[ií]culos de tracci[oó]n mec[aá]nica", ""),
    ("León", "IAE actividades económicas", r"actividades econ[oó]micas", ""),
    ("León", "grúa retirada de vehículos", r"retirada de veh[ií]culos", ""),
    ("León", "recogida de perros", r"recogida de perros", ""),
    ("León", "ayuda a domicilio", r"ayuda a domicilio", ""),
    ("León", "participación ciudadana", r"participaci[oó]n ciudadana", ""),
]

# provincia (BOP) + anti-secuestro
PROVINCIA = [
    ("Ponferrada", "ordenanza", r"PONFERRADA", False),
    ("Astorga, León", "ordenanza", r"ASTORGA", False),   # NO debe ser León capital
    ("San Andrés del Rabanedo", "tasa", r"SAN ANDR[EÉ]S", False),
]


def _dur(r):
    m = re.search(r"(\d+)\s*ms\)?\s*$", r or "")
    return int(m.group(1)) / 1000 if m else None


def probar_capital(muni, consulta, rx, art):
    t0 = time.time()
    try:
        r = oe.leer(muni, consulta, articulo=art, parrafos=(0 if art else 2), terminos=consulta)
    except Exception as e:  # noqa: BLE001
        return ("EXC", str(e)[:70], time.time() - t0)
    dt = time.time() - t0
    cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
    if not cab or not re.search(rx, cab, re.I):
        return ("MAL_ORD", cab[:70] if cab else (r or "")[:70], dt)
    if len(r) < 500 or "No encuentro" in r[:60] or "Sin pasajes" in r or "no tiene texto" in r:
        return ("SIN_TEXTO", cab[:60], dt)
    if art and not re.search(r"Art[íi]cul[oe]\s*" + re.escape(art) + r"(?![\d])", r, re.I):
        return ("SIN_ART", f"art {art}? " + cab[:55], dt)
    if dt >= LIMITE:
        return ("LENTO", f"{dt:.1f}s {cab[:50]}", dt)
    return ("OK", cab[:62], dt)


def probar_provincia(muni, consulta, rx, _):
    t0 = time.time()
    try:
        r = oe.buscar(muni, consulta, 5)
    except Exception as e:  # noqa: BLE001
        return ("EXC", str(e)[:70], time.time() - t0)
    dt = time.time() - t0
    cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
    if cab and re.search(rx, cab, re.I):
        return ("OK", cab[:62], dt)
    return ("MAL_RUTA", (cab or (r or "")[:70])[:70], dt)


def main():
    ok = tot = 0
    lentos = []
    print("== CAPITAL (aytoleon.es) ==")
    for muni, consulta, rx, art in CAPITAL:
        tot += 1
        estado, det, dt = probar_capital(muni, consulta, rx, art)
        flag = "✅" if estado == "OK" else "❌"
        if estado == "OK":
            ok += 1
        if estado == "LENTO" or dt >= LIMITE:
            lentos.append((consulta, dt))
        print(f"{flag} [{dt:4.1f}s {estado:9}] {consulta:32} -> {det}")
    print("\n== PROVINCIA (BOP) + anti-secuestro ==")
    for muni, consulta, rx, _ in PROVINCIA:
        tot += 1
        estado, det, dt = probar_provincia(muni, consulta, rx, _)
        flag = "✅" if estado == "OK" else "❌"
        if estado == "OK":
            ok += 1
        print(f"{flag} [{dt:4.1f}s {estado:9}] {muni:26} -> {det}")
    print(f"\nRESULTADO LEÓN: {ok}/{tot}  (límite {LIMITE:.0f}s)")
    if lentos:
        print("LENTOS (>=5s):", [(c, round(d, 1)) for c, d in lentos])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
