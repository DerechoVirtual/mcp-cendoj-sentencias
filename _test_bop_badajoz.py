# -*- coding: utf-8 -*-
"""Banco BADAJOZ (BOP de Badajoz, backend externo bop_badajoz.py) — 2-sep-2026.

Mismo flujo que el chat: ordenanzas_engine.buscar(muni, materia, 6) y después
ordenanzas_engine.leer(muni, materia, "", 3, materia, 0). Éxito = el listado y la
lectura empiezan por 【, la cabecera es la ordenanza esperada, el texto trae la
materia literal y no hay «Error»/«PDF sin texto». Los casos HONESTO exigen
«No encuentro…». Objetivo del brief: ≥ 90 % OK, mediana < 5 s y máximo < 15 s.

Uso: .venv/Scripts/python.exe -X utf8 _test_bop_badajoz.py
"""
import os
import re
import statistics
import sys
import time

_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
try:
    for _ln in open(_ENV, encoding="utf-8", errors="replace"):
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except Exception:  # noqa: BLE001
    pass

import ordenanzas_engine as OE  # noqa: E402

# (municipio, materia, regex esperado en la CABECERA 【…】, regex esperado en el TEXTO)
ENCONTRAR = [
    # > 50.000 habitantes
    ("Badajoz", "residuos", r"residuos", r"residuo"),
    ("Badajoz", "venta ambulante", r"venta ambulante", r"ambulante"),
    ("Badajoz", "IBI", r"bienes inmuebles", r"inmueble"),
    ("Badajoz", "convivencia ciudadana", r"convivencia", r"convivencia"),
    ("Badajoz", "agua", r"agua|abastecimiento", r"agua"),
    ("Mérida", "contaminación acústica", r"ac[uú]stica|ruido", r"ruido|ac[uú]stic"),
    ("Mérida", "ordenanza fiscal", r"ordenanza fiscal", r"impuesto|tasa|tarifa"),
    ("Mérida", "taxi", r"taxi", r"taxi"),
    ("Almendralejo", "basura", r"basura|residuos", r"basura|residuo"),
    ("Almendralejo", "ICIO", r"construcciones", r"construcci"),
    # resto del banco
    ("Don Benito", "IBI", r"bienes inmuebles", r"inmueble"),
    ("Don Benito", "terrazas", r"aprovechamiento especial|terraza|mesas|velador", r"terraza|mesas|velador"),
    ("Villanueva de la Serena", "tenencia de animales", r"animales", r"animal"),
    ("Zafra", "terrazas veladores", r"terrazas", r"terraza|velador"),
    ("Zafra", "agua", r"agua", r"agua"),
    ("Montijo", "patinetes", r"patinetes", r"patinete|movilidad personal|vmp|veh[ií]culo"),
    ("Olivenza", "residuos", r"residuos", r"residuo"),
    ("Jerez de los Caballeros", "cementerio", r"cementerio", r"cementerio"),
    ("Villafranca de los Barros", "cementerio", r"cementerio", r"cementerio"),
    # Mérida no titula «terrazas»: la tasa «por ocupación de terrenos de uso público
    # con mesas y sillas» sale por la 2ª oleada del tesauro
    ("Mérida", "terrazas", r"terraza|mesas|velador|ocupaci[oó]n de terrenos", r"terraza|mesas|velador|ocupaci[oó]n"),
]

HONESTO = [
    ("Zafra", "aeropuerto"),
    ("Montijo", "puerto deportivo"),
]

_ERR = re.compile(r"^Error|no pude leer|no tiene texto legible|anti-robots", re.I)


def encontrar(muni, materia, rx_cab, rx_txt):
    t0 = time.time()
    try:
        b = OE.buscar(muni, materia, 6)
    except Exception as e:  # noqa: BLE001
        return "EXC", f"buscar: {e}"[:70], 0.0, time.time() - t0
    t1 = time.time()
    try:
        r = OE.leer(muni, materia, "", 3, materia, 0) or ""
    except Exception as e:  # noqa: BLE001
        return "EXC", f"leer: {e}"[:70], t1 - t0, time.time() - t1
    t2 = time.time()
    dtb, dtl = t1 - t0, t2 - t1
    if _ERR.search(r):
        return "ERROR", r[:70], dtb, dtl
    if not (b or "").startswith("【"):
        return "SIN_LISTA", (b or "")[:70], dtb, dtl
    m = re.search(r"【([^】]+)】", r)
    if not r.startswith("【") or not m:
        return "SIN_CAB", r[:70], dtb, dtl
    cab = m.group(1)
    if not re.search(rx_cab, cab, re.I):
        return "MAL_ORD", cab[:70], dtb, dtl
    cuerpo = r[m.end():]
    if len(cuerpo) < 300 or not re.search(rx_txt, cuerpo, re.I):
        return "SIN_MATERIA", cab[:70], dtb, dtl
    return "OK", cab[:70], dtb, dtl


def honesto(muni, materia):
    t0 = time.time()
    try:
        b = OE.buscar(muni, materia, 6) or ""
        t1 = time.time()
        r = OE.leer(muni, materia, "", 3, materia, 0) or ""
    except Exception as e:  # noqa: BLE001
        return "EXC", str(e)[:70], 0.0, time.time() - t0
    t2 = time.time()
    if _ERR.search(r) or _ERR.search(b):
        return "ERROR", r[:70], t1 - t0, t2 - t1
    if r.startswith("No encuentro") and b.startswith("No encuentro"):
        return "OK", "honesto", t1 - t0, t2 - t1
    m = re.search(r"【([^】]+)】", r)
    return "FALSO_POS", (m.group(1) if m else r)[:70], t1 - t0, t2 - t1


def main():
    ok = tot = 0
    totales, lecturas = [], []
    print("== BADAJOZ debe-encontrar (buscar -> leer parrafos=3) ==")
    for muni, materia, rx_cab, rx_txt in ENCONTRAR:
        tot += 1
        estado, det, dtb, dtl = encontrar(muni, materia, rx_cab, rx_txt)
        ok += estado == "OK"
        totales.append(dtb + dtl)
        lecturas.append(dtl)
        print(f"{'✅' if estado == 'OK' else '❌'} [{dtb + dtl:4.1f}s = b{dtb:3.1f}+l{dtl:3.1f} {estado:11}] "
              f"{muni:26} {materia:24} -> {det}")
    print("\n== honesto (materia inexistente) ==")
    for muni, materia in HONESTO:
        tot += 1
        estado, det, dtb, dtl = honesto(muni, materia)
        ok += estado == "OK"
        totales.append(dtb + dtl)
        lecturas.append(dtl)
        print(f"{'✅' if estado == 'OK' else '❌'} [{dtb + dtl:4.1f}s = b{dtb:3.1f}+l{dtl:3.1f} {estado:11}] "
              f"{muni:26} {materia:24} -> {det}")
    med, mx = statistics.median(totales), max(totales)
    medl, mxl = statistics.median(lecturas), max(lecturas)
    print(f"\nRESULTADO BADAJOZ: {ok}/{tot} OK ({100 * ok / tot:.0f} %) · buscar+leer mediana {med:.1f} s, "
          f"máximo {mx:.1f} s · solo leer mediana {medl:.1f} s, máximo {mxl:.1f} s")
    cumple = ok / tot >= 0.9 and med < 5 and mx < 15
    print("CUMPLE el brief (≥90 %, mediana <5 s, máx <15 s)" if cumple else "NO CUMPLE el brief")
    return 0 if cumple else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
