# -*- coding: utf-8 -*-
"""Banco DURO (orden de Carlos 12-ago-2026): 30 consultas ULTRA-especificas con
respuesta canonica conocida. Si el sistema no la encuentra, se arregla el motor
y se repite hasta verde. Tiempo objetivo <3 s por operacion.
"""
import os, sys, time, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("DOWNLOAD_DIR", os.path.join(os.environ.get("TEMP", "/tmp"), "sent"))

import server_http as sh

resultados = []


def caso(nombre, fn, *frags, limite=3.0):
    t0 = time.time()
    try:
        out = fn() or ""
    except Exception as e:  # noqa: BLE001
        resultados.append((nombre, 0.0, False, f"EXCEPCION: {e}"))
        print(f"  FAIL {nombre}: EXCEPCION {e}")
        return
    dt = time.time() - t0
    low = out.lower()
    faltan = [f for f in frags if f.lower() not in low]
    ok = not faltan and dt <= limite
    motivo = ("faltan: " + "; ".join(repr(f)[:60] for f in faltan)) if faltan else ""
    if dt > limite:
        motivo += f" LENTO {dt:.2f}s>{limite}s"
    resultados.append((nombre, dt, ok, motivo))
    print(f"  {'ok  ' if ok else 'FAIL'} {dt:5.2f}s  {nombre}" + (f"  [{motivo}]" if motivo else ""))


print("=== NORMATIVA UE (10) ===")
# La consulta REAL de Carlos: no existe tal directiva; la mas cercana es la de
# datos abiertos. El sistema debe (a) encontrar la 2019/1024 con la consulta
# bien formulada y (b) responder UTIL (no vacio) a la formulacion cruda.
caso("UE dir. datos abiertos (consulta bien formulada)",
     lambda: sh.buscar_boe("directiva datos abiertos reutilización información sector público"),
     "32019L1024")
caso("UE consulta cruda de Carlos (respuesta util, no muda)",
     lambda: sh.buscar_boe("directiva acceso público gratuito jurisprudencia"),
     "normas UE")  # debe orientar hacia la busqueda por titulo, no callar
caso("UE RGPD art. 83 (multas)",
     lambda: sh.buscar_articulo("RGPD", "83"), "multas administrativas")
caso("UE Directiva 93/13 art. 6 (no vincularan)",
     lambda: sh.buscar_articulo("Directiva 93/13/CEE", "6"), "no vincular")
caso("UE euroorden art. 2 (ambito)",
     lambda: sh.buscar_articulo("euroorden", "2"), "entrega")
caso("UE Reglamento 261/2004 art. 7 (250 EUR)",
     lambda: sh.buscar_articulo("Reglamento (CE) nº 261/2004", "7"), "250")
caso("UE Reglamento IA art. 6 (alto riesgo)",
     lambda: sh.buscar_articulo("Reglamento de IA", "6"), "alto riesgo")
caso("UE tiempo de trabajo art. 7 (4 semanas vacaciones)",
     lambda: sh.buscar_articulo("Directiva de tiempo de trabajo", "7"), "cuatro semanas")
caso("UE buscar reglamento IA por materia",
     lambda: sh.buscar_boe("reglamento europeo inteligencia artificial"), "32024R1689")
caso("UE leer_boe por CELEX",
     lambda: sh.leer_boe("32019L1024"), "datos abiertos")

print("=== TJUE (10 duras) ===")
caso("TJUE centimo sanitario (C-82/12)",
     lambda: sh.buscar_sentencias("impuesto ventas minoristas hidrocarburos", base="TJUE", maximo=8),
     "C-82/12")
caso("TJUE registro de jornada (C-55/18)",
     lambda: sh.buscar_sentencias("registro tiempo de trabajo duración jornada", base="TJUE", maximo=8),
     "C-55/18")
caso("TJUE IRPH (C-125/18)",
     lambda: sh.buscar_sentencias("índice de referencia préstamos hipotecarios", base="TJUE", maximo=8),
     "C-125/18")
caso("TJUE vencimiento anticipado (C-70/17)",
     lambda: sh.buscar_sentencias("vencimiento anticipado", base="TJUE", maximo=8),
     "C-70/17")
caso("TJUE de Diego Porras (C-596/14)",
     lambda: sh.buscar_sentencias("Diego Porras", base="TJUE", maximo=8), "C-596/14")
caso("TJUE Sumal cartel camiones (C-882/19)",
     lambda: sh.buscar_sentencias("Sumal", base="TJUE", maximo=8), "C-882/19")
caso("TJUE Google CNIL olvido mundial (C-507/17)",
     lambda: sh.buscar_sentencias("Google CNIL", base="TJUE", maximo=8), "C-507/17")
caso("TJUE Achmea arbitraje intra-UE (C-284/16)",
     lambda: sh.buscar_sentencias("Achmea", base="TJUE", maximo=8), "C-284/16")
caso("TJUE leer C-55/18 parrafo del fallo",
     lambda: sh.leer_sentencias("C-55/18", parrafos=3, terminos="sistema computar jornada laboral diaria"),
     "jornada laboral diaria")
caso("TJUE leer centimo sanitario parrafos",
     lambda: sh.leer_sentencias("C-82/12", parrafos=3, terminos="efectos en el tiempo devolución"),
     "C-82/12")

print("=== TRIBUNAL CONSTITUCIONAL (10 duras) ===")
caso("TC plusvalia municipal (STC 182/2021)",
     lambda: sh.buscar_sentencias("incremento de valor de los terrenos de naturaleza urbana", base="TC", maximo=10),
     "STC 182/2021", limite=4.5)
caso("TC tasas judiciales (STC 140/2016)",
     lambda: sh.buscar_sentencias("tasas judiciales", base="TC", maximo=10), "STC 140/2016", limite=4.5)
caso("TC despido embarazada (STC 92/2008)",
     lambda: sh.buscar_sentencias("despido trabajadora embarazada", base="TC", maximo=10),
     "STC 92/2008", limite=4.5)
caso("TC estado de alarma covid (STC 148/2021)",
     lambda: sh.buscar_sentencias("estado de alarma", base="TC", maximo=10), "STC 148/2021", limite=4.5)
caso("TC pension viudedad parejas de hecho (STC 40/2014)",
     lambda: sh.buscar_sentencias("pensión de viudedad parejas de hecho", base="TC", maximo=10),
     "STC 40/2014", limite=4.5)
caso("TC aborto 2010 (STC 44/2023)",
     lambda: sh.buscar_sentencias("interrupción voluntaria del embarazo", base="TC", maximo=10),
     "STC 44/2023", limite=4.5)
caso("TC Sortu (STC 138/2012)",
     lambda: sh.buscar_sentencias("Sortu", base="TC", maximo=10), "STC 138/2012", limite=4.5)
caso("TC correo electronico trabajador (STC 170/2013)",
     lambda: sh.buscar_sentencias("correo electrónico del trabajador secreto", base="TC", maximo=10),
     "STC 170/2013", limite=4.5)
caso("TC leer ATC 26/2007 (recusacion Perez Tremps)",
     lambda: sh.leer_sentencias("ATC 26/2007", parrafos=2, terminos="recusación causa"),
     "recusaci")
caso("TC leer STC 31/2010 'nacion' (Estatut, doc gigante)",
     lambda: sh.leer_sentencias("STC 31/2010", parrafos=2, terminos="nación realidad nacional"),
     "nación", limite=10.0)

print()
oks = sum(1 for _, _, ok, _ in resultados if ok)
print(f"RESULTADO: {oks}/{len(resultados)} OK")
for n, d, ok, m in resultados:
    if not ok:
        print(f"  - {n}: {m} ({d:.2f}s)")
tiempos = sorted(d for _, d, _, _ in resultados)
print(f"tiempos: p50={tiempos[len(tiempos)//2]:.2f}s max={tiempos[-1]:.2f}s")
sys.exit(0 if oks == len(resultados) else 1)
