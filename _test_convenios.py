# -*- coding: utf-8 -*-
"""Banco de pruebas de convenios colectivos.

Regla dura del encargo: identificar el convenio correcto en MENOS DE 2 SEGUNDOS,
sea de la comunidad que sea. Se comprueban las dos cosas: acierto y tiempo.
"""
import sys, io, time, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
import convenios_engine as ce

LIMITE = 2.0

# (pregunta tal cual la haria un abogado, palabras que DEBEN salir en el 1er resultado,
#  territorio que debe reconocerse)
CASOS = [
    ("convenio de hosteleria de la Comunidad de Madrid", ["hosteler"], "Madrid"),
    ("convenio colectivo del metal de Valencia", ["metal"], "Valencia"),
    ("convenio de la construccion de Barcelona", ["construc"], "Barcelona"),
    ("convenio de comercio de Sevilla", ["comerc"], "Sevilla"),
    ("convenio de limpieza de edificios y locales de Zaragoza", ["limpieza"], "Zaragoza"),
    ("convenio de oficinas y despachos de Bizkaia", ["oficina", "despacho"], "Bizkaia"),
    ("convenio de hosteleria de Baleares", ["hosteler"], "Balears"),
    ("convenio del campo de Almeria", ["campo", "agr"], "Almeria"),
    ("convenio de transporte de mercancias por carretera de Murcia", ["transporte", "mercanc"], "Murcia"),
    ("convenio de la madera de Galicia", ["madera"], "Galicia"),
    ("convenio de siderometalurgia de A Coruna", ["metal", "sidero"], "Coruna"),
    ("convenio de hosteleria de Canarias", ["hosteler"], "Palmas"),
    ("convenio de comercio de Cantabria", ["comerc"], "Cantabria"),
    ("convenio de la construccion de Navarra", ["construc"], "Navarra"),
    ("convenio de peluquerias de Andalucia", ["peluquer"], "Andaluc"),
    ("convenio de oficinas y despachos de Castilla y Leon", ["oficina", "despacho"], "Castilla"),
    ("convenio de hosteleria de Asturias", ["hosteler"], "Asturias"),
    ("convenio de limpieza de Castilla-La Mancha", ["limpieza"], "Castilla"),
    ("convenio de comercio de La Rioja", ["comerc"], "Rioja"),
    ("convenio de la construccion de Extremadura", ["construc"], "Extremadura"),
    ("convenio de hosteleria de Aragon", ["hosteler"], "Arag"),
    ("convenio de dependencia estatal", ["dependencia", "atencion"], "Estatal"),
    ("convenio estatal de empresas de seguridad", ["seguridad"], "Estatal"),
    ("convenio colectivo estatal de banca", ["banca"], "Estatal"),
    ("convenio de ensenanza privada concertada estatal", ["ensenanza", "enseñanza"], "Estatal"),
    ("convenio de la industria quimica", ["quimic", "químic"], None),
    ("convenio de artes graficas estatal", ["graficas", "gráficas"], "Estatal"),
    ("convenio del calzado de Alicante", ["calzado"], "Alicante"),
    ("convenio de hosteleria de Tenerife", ["hosteler"], "Tenerife"),
    ("convenio de comercio de Gipuzkoa", ["comerc"], "Gipuzkoa"),
    ("convenio de transporte de viajeros de Malaga", ["viajeros", "transporte"], "Malaga"),
    ("convenio de panaderias de Ceuta", ["panader"], "Ceuta"),
    ("convenio de oficinas y despachos de Melilla", ["oficina", "despacho"], "Melilla"),
    ("convenio de la vid de Cadiz", ["vid", "vinicola", "vino"], "Cadiz"),
    ("convenio de derivados del cemento de Madrid", ["cemento"], "Madrid"),
]

def norm(s):
    import unicodedata
    s = unicodedata.normalize('NFD', s)
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()

ok = fallo = lento = 0
tiempos = []
print(f"{'CASO':<58} {'seg':>6}  RESULTADO")
print("-" * 118)
for pregunta, esperadas, territorio in CASOS:
    t0 = time.time()
    try:
        r = ce.buscar(pregunta)
    except Exception as e:
        r = f"EXCEPCION {type(e).__name__}: {e}"
    dt = time.time() - t0
    tiempos.append(dt)
    prim = ""
    m = re.search(r"^1\. (.+)$", r, re.M)
    if m:
        prim = m.group(1)
    nprim = norm(prim)
    acierta = any(norm(e) in nprim for e in esperadas)
    terr_ok = (territorio is None) or (norm(territorio) in norm(r[:400]))
    marca = "OK " if (acierta and terr_ok) else "MAL"
    if dt > LIMITE:
        marca = "LENTO"; lento += 1
    if acierta and terr_ok and dt <= LIMITE:
        ok += 1
    else:
        fallo += 1
    print(f"{pregunta[:57]:<58} {dt:>6.3f}  {marca} {prim[:52]}")

tiempos.sort()
print("-" * 118)
print(f"ACIERTOS {ok}/{len(CASOS)}   fallos={fallo}  fuera de tiempo={lento}")
print(f"tiempos: min={tiempos[0]:.3f}s  mediana={tiempos[len(tiempos)//2]:.3f}s  "
      f"p95={tiempos[int(len(tiempos)*0.95)]:.3f}s  max={tiempos[-1]:.3f}s")
sys.exit(0 if fallo == 0 else 1)
