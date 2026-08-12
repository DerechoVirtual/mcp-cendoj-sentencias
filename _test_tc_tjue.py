# -*- coding: utf-8 -*-
"""Banco de pruebas TC + TJUE (>=30 casos): busqueda, cita exacta y lectura con
parrafos clave. Cada caso mide el tiempo y comprueba que el resultado contiene
el FRAGMENTO literal esperado. Objetivo: <3 s por operacion.

Uso: .venv/Scripts/python.exe _test_tc_tjue.py
"""
import os, sys, time, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("DOWNLOAD_DIR", os.path.join(os.environ.get("TEMP", "/tmp"), "sent"))

import server_http as sh

LIMITE = 3.0
resultados = []


def caso(nombre, fn, *frags, limite=LIMITE):
    t0 = time.time()
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001
        resultados.append((nombre, 0.0, False, f"EXCEPCION: {e}"))
        print(f"  FAIL {nombre}: EXCEPCION {e}")
        return
    dt = time.time() - t0
    out_low = (out or "").lower()
    faltan = [f for f in frags if f.lower() not in out_low]
    ok = not faltan and dt <= limite
    motivo = ""
    if faltan:
        motivo = "faltan: " + "; ".join(repr(f)[:60] for f in faltan)
    if dt > limite:
        motivo += f" LENTO {dt:.2f}s>{limite}s"
    resultados.append((nombre, dt, ok, motivo))
    print(f"  {'ok  ' if ok else 'FAIL'} {dt:5.2f}s  {nombre}" + (f"  [{motivo}]" if motivo else ""))


print("=== TRIBUNAL CONSTITUCIONAL ===")
# --- busquedas por materia ---
caso("TC buscar eutanasia", lambda: sh.buscar_sentencias("eutanasia", base="TC", maximo=8), "STC 19/2023")
caso("TC buscar derecho al olvido", lambda: sh.buscar_sentencias("derecho al olvido", base="TC", maximo=8), "TC ")
caso("TC buscar prision permanente revisable", lambda: sh.buscar_sentencias("prisión permanente revisable", base="TC", maximo=8), "STC 169/2021")
caso("TC buscar matrimonio mismo sexo (barrido 25)", lambda: sh.buscar_sentencias("matrimonio entre personas del mismo sexo", base="TC", maximo=25), "STC 198/2012", limite=4.5)
caso("TC buscar amnistia", lambda: sh.buscar_sentencias("ley de amnistía", base="TC", maximo=8), "ECLI:ES:TC")
caso("TC buscar solo AUTOS recusacion", lambda: sh.buscar_sentencias("recusación", base="TC", maximo=8, tipo_resolucion="AUTO"), "ATC ")
caso("TC buscar con rango de fechas", lambda: sh.buscar_sentencias("vivienda", base="TC", maximo=8, fecha_desde="01/01/2020", fecha_hasta="31/12/2023"), "ECLI:ES:TC:202")
# --- citas exactas ---
caso("TC cita STC 31/2010 (Estatut)", lambda: sh.buscar_por_cita("STC 31/2010"), "STC 31/2010", "ECLI:ES:TC:2010:31")
caso("TC cita ATC con ECLI sufijo A", lambda: sh.buscar_por_cita("ECLI:ES:TC:2016:105A"), "ATC 105/2016")
caso("TC cita ECLI STC 169/2021", lambda: sh.buscar_por_cita("ECLI:ES:TC:2021:169"), "STC 169/2021")
caso("TC cita STC 1/1981 (la primera)", lambda: sh.buscar_por_cita("STC 1/1981"), "STC 1/1981")
# --- lecturas con parrafos clave (fragmento literal) ---
caso("TC leer STC 53/1985 nasciturus", lambda: sh.leer_sentencias("STC 53/1985", parrafos=3, terminos="vida nasciturus proteccion"), "nasciturus")
caso("TC leer STC 76/2019 datos partidos", lambda: sh.leer_sentencias("STC 76/2019", parrafos=3, terminos="opiniones políticas datos personales"), "datos", "PARRAFOS CLAVE")
caso("TC leer STC 19/2023 eutanasia", lambda: sh.leer_sentencias("STC 19/2023", parrafos=3, terminos="eutanasia contexto eutanásico"), "eutanas")
caso("TC leer ATC 105/2016 (auto, no sentencia)", lambda: sh.leer_sentencias("ATC 105/2016", parrafos=2, terminos="recurso"), "ECLI:ES:TC:2016:105A")
caso("TC leer integro recortado STC 198/2012", lambda: sh.leer_sentencias("STC 198/2012", max_chars=3000), "matrimonio")
caso("TC verificacion anti-mismatch (STC vs ATC)", lambda: sh.leer_sentencias("ECLI:ES:TC:2010:31A", parrafos=2, terminos="admision"), "ATC 31/2010")

print("=== TJUE / TRIBUNAL GENERAL ===")
# --- busquedas por materia ---
caso("TJUE buscar clausulas abusivas hipoteca", lambda: sh.buscar_sentencias("cláusulas abusivas hipoteca", base="TJUE", maximo=8), "ECLI:EU:C")
caso("TJUE buscar proteccion de datos", lambda: sh.buscar_sentencias("protección de datos personales", base="TJUE", maximo=8), "ECLI:EU:C")
caso("TJUE buscar despido colectivo", lambda: sh.buscar_sentencias("despido colectivo", base="TJUE", maximo=8), "ECLI:EU:C")
caso("TJUE buscar Schrems (partes)", lambda: sh.buscar_sentencias("Schrems Facebook", base="TJUE", maximo=8), "C-311/18")
caso("TJUE buscar tiempo de trabajo", lambda: sh.buscar_sentencias("tiempo de trabajo registro jornada", base="TJUE", maximo=8), "ECLI:EU:C")
caso("TJUE buscar solo sentencias IVA", lambda: sh.buscar_sentencias("IVA deducción", base="TJUE", maximo=8, tipo_resolucion="SENTENCIA"), "ECLI:EU:C"),
caso("TJUE buscar con fechas", lambda: sh.buscar_sentencias("consumidores", base="TJUE", maximo=8, fecha_desde="01/01/2024", fecha_hasta="31/12/2025"), "202")
# --- citas exactas ---
caso("TJUE cita C-311/19", lambda: sh.buscar_por_cita("C-311/19"), "C-311/19", "ECLI:EU:C:2020:981")
caso("TJUE cita ECLI Schrems II", lambda: sh.buscar_por_cita("ECLI:EU:C:2020:559"), "C-311/18")
caso("TJUE cita T-778/16 (Apple, Trib. General)", lambda: sh.buscar_por_cita("T-778/16"), "T-778/16")
caso("TJUE cita Google Spain C-131/12", lambda: sh.buscar_por_cita("C-131/12"), "C-131/12")
# --- lecturas con parrafos clave (fragmento literal) ---
caso("TJUE leer Schrems II Escudo privacidad", lambda: sh.leer_sentencias("ECLI:EU:C:2020:559", parrafos=3, terminos="Escudo de la privacidad decisión de adecuación"), "Escudo de la privacidad")
caso("TJUE leer Aziz C-415/11 ejecucion hipotecaria", lambda: sh.leer_sentencias("C-415/11", parrafos=3, terminos="ejecución hipotecaria cláusulas abusivas"), "ejecución hipotecaria")
caso("TJUE leer Gutierrez Naranjo C-154/15 clausulas suelo", lambda: sh.leer_sentencias("C-154/15", parrafos=3, terminos="cláusula suelo efectos restitutorios"), "suelo")
caso("TJUE leer Google Spain derecho al olvido", lambda: sh.leer_sentencias("C-131/12", parrafos=3, terminos="motor de búsqueda datos personales"), "motor de búsqueda")
caso("TJUE leer sentencia antigua Van Gend en Loos", lambda: sh.leer_sentencias("ECLI:EU:C:1963:1", parrafos=0, max_chars=2500), "26/62", limite=4.0)
caso("TJUE leer conclusiones AG C-311/19", lambda: sh.leer_sentencias("C-311/19 (Conclusiones del AG)", parrafos=2, terminos="libre prestación de servicios"), "ECLI:EU:C:2020:640")
caso("TJUE leer Tribunal General T-778/16", lambda: sh.leer_sentencias("T-778/16", parrafos=3, terminos="ayudas de Estado ventaja selectiva"), "ayuda", limite=4.0)

print()
oks = sum(1 for _, _, ok, _ in resultados if ok)
lentas = [(n, d) for n, d, ok, m in resultados if "LENTO" in m]
print(f"RESULTADO: {oks}/{len(resultados)} OK")
for n, d, ok, m in resultados:
    if not ok:
        print(f"  - {n}: {m} ({d:.2f}s)")
tiempos = sorted(d for _, d, _, _ in resultados)
print(f"tiempos: p50={tiempos[len(tiempos)//2]:.2f}s max={tiempos[-1]:.2f}s")
sys.exit(0 if oks == len(resultados) else 1)
