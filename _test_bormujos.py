# -*- coding: utf-8 -*-
"""Banco BORMUJOS: 30 materias al detalle contra bop_engine (offline _*).
24 DEBE-ENCONTRAR (con sinónimos difíciles) + 6 HONESTO-ÚTIL (no están en el
índice: el motor debe decirlo sin inventar y sin colar una ordenanza errónea).
Éxito ENCONTRAR = leer() devuelve la ordenanza CORRECTA (cabecera machea la
regex) con texto real. Éxito HONESTO = leer() devuelve el mensaje honesto.
"""
import os
import re
import sys
import time

# claves para el OCR (visión) desde el .env global
_env = open(os.path.expanduser("~/.claude/.env"), encoding="utf-8", errors="replace").read()
for k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
    m = re.search(rf"^{k}=(.+)$", _env, re.M)
    if m:
        os.environ[k] = m.group(1).strip().strip('"')  # override (el shell puede traer claves viejas)

import bop_engine as b  # noqa: E402

MUNI = "Bormujos"

ENCONTRAR = [
    ("patinetes eléctricos", r"movilidad personal"),
    ("tenencia de animales", r"tenencia responsable de animales"),
    ("perros", r"animales"),
    ("mosquitos", r"mosquitos"),
    ("control de plagas", r"mosquitos"),
    ("terrazas y veladores", r"terrazas y veladores|ocupaci[oó]n del espacio p[uú]blico"),
    ("ocupación del espacio público", r"ocupaci[oó]n del espacio p[uú]blico"),
    ("mesas y sillas", r"mesas, sillas"),
    ("feria", r"eria de"),
    ("comercio ambulante", r"comercio ambulante"),
    ("mercadillo", r"comercio ambulante"),
    ("IBI", r"bienes inmuebles"),
    ("impuesto sobre construcciones instalaciones y obras", r"construcciones, instalaciones y obras"),
    ("impuesto de circulación", r"tracci[oó]n mec[aá]nica"),
    ("grúa retirada de vehículos", r"retirada y recogida de veh[ií]culos"),
    ("publicidad exterior vallas", r"publicidad exterior"),
    ("tasa por actividades publicitarias", r"actividades publicitarias"),
    ("patrocinios", r"patrocinios privados"),
    ("ayuda a domicilio", r"ayuda a domicilio"),
    ("derechos de examen", r"derechos de examen"),
    ("honores y distinciones", r"protocolo|honores"),
    ("consejo de infancia", r"nfancia y"),
    ("registro de entidades urbanísticas colaboradoras", r"entidades urban[ií]sticas"),
    ("rescate de animales", r"rescate de animales"),
]

HONESTO = [
    "recogida de basuras",
    "ruido",
    "convivencia ciudadana",
    "cementerio",
    "alcantarillado",
    "vados",
]


def main(solo=None):
    ok = 0
    total = 0
    fallos = []
    for consulta, rx in ENCONTRAR:
        if solo and consulta not in solo:
            continue
        total += 1
        t0 = time.time()
        try:
            r = b.leer(MUNI, consulta, parrafos=2, terminos=consulta)
        except Exception as e:  # noqa: BLE001
            r = f"EXC {e}"
        dt = time.time() - t0
        cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
        cuerpo_ok = bool(r) and len(r) > 600 and "No encuentro" not in r[:80]
        if cab and re.search(rx, cab, re.I) and cuerpo_ok:
            ok += 1
            print(f"✅ [{dt:4.1f}s] {consulta:44} -> {cab[:70]}")
        else:
            fallos.append(consulta)
            print(f"❌ [{dt:4.1f}s] {consulta:44} -> {(cab or (r or '')[:90])[:90]}")
    for consulta in HONESTO:
        if solo and consulta not in solo:
            continue
        total += 1
        t0 = time.time()
        try:
            r = b.leer(MUNI, consulta, parrafos=2, terminos=consulta)
        except Exception as e:  # noqa: BLE001
            r = f"EXC {e}"
        dt = time.time() - t0
        if r and r.startswith("No encuentro"):
            extra = "supra✓" if "SUPRAMUNICIPAL" in r else ""
            ok += 1
            print(f"✅ [{dt:4.1f}s] {consulta:44} -> honesto {extra}")
        else:
            fallos.append(consulta)
            cab = (re.search(r"【([^】]+)】", r or "") or [None, ""])[1]
            print(f"❌ [{dt:4.1f}s] {consulta:44} -> DEVOLVIÓ: {(cab or (r or '')[:90])[:90]}")
    print(f"\nRESULTADO BORMUJOS: {ok}/{total}")
    if fallos:
        print("FALLOS:", fallos)
    return ok, total


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:] or None)
