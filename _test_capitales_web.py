# -*- coding: utf-8 -*-
"""Banco CAPITALES VÍA SU WEB PROPIA (offline _*): Cuenca, Guadalajara y Ceuta con
catálogo consolidado + texto empaquetado (ordenanzas_data/<codigo>.json + <codigo>_textos/).

Mismo flujo que el chat: buscar_ordenanzas(muni, materia, 6) -> primer id ->
leer_ordenanza(muni, id, parrafos=3, terminos=materia). Éxito = la lectura empieza por
【, tiene texto real (≥500 chars), NO es un error y el CUERPO (no solo la cabecera)
contiene literalmente la materia. Con el texto empaquetado cada caso debe ir en < 1 s
(LIMITE_S) y la lectura no debe tocar la red.

Además comprueba el ENRUTADO: «Cuenca», «Guadalajara» y «Ceuta» están en los nombres
PROTEGIDOS del motor BOP (solo casan exactos) y, con el catálogo registrado por
ADAPTADORES, resuelven ANTES que cualquier BOP. Se informa (sin puntuar) del caso
«Cuenca de Campos» (Valladolid): la 2ª pasada de _resolver_municipio casa por palabra
y lo lleva al catálogo de Cuenca capital (limitación del fichero compartido).

Uso:  python -X utf8 _test_capitales_web.py [cuenca|guadalajara|ceuta]
"""
import os
import re
import statistics
import sys
import time

_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
try:
    for ln in open(_ENV, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ordenanzas_engine as OE  # noqa: E402

LIMITE_S = 1.0

# (municipio tal como lo escribiría el abogado, materia, regex que debe aparecer en el CUERPO)
CASOS = {
    "cuenca": [
        ("Cuenca", "terrazas", r"terraza"),
        ("Cuenca", "ruido", r"ruido"),
        ("Cuenca", "residuos basura", r"residuo|basura"),
        ("Cuenca", "animales", r"animal"),
        ("Cuenca", "convivencia ciudadana", r"convivencia"),
        ("Cuenca", "IBI", r"bienes inmuebles"),
        ("Cuenca", "venta ambulante", r"ambulante|puestos|barracas"),
        ("Cuenca", "tráfico", r"tr[aá]fico|circulaci"),
        ("Cuenca", "plusvalía", r"incremento de valor|plusval"),
        ("Cuenca", "botellón", r"botell[oó]n|alcohol"),
        ("Cuenca", "taxi", r"taxi"),
        ("Cuenca", "patinetes", r"movilidad personal|patinete"),
        ("Ayuntamiento de Cuenca", "vados", r"vado|entrada de veh"),
        ("Cuenca", "ORA zona azul", r"estacionamiento|O\.?R\.?A"),
        ("Cuenca capital", "agua", r"agua"),
        ("Cuenca", "medio ambiente", r"medio ambiente|ambiental"),
    ],
    "guadalajara": [
        ("Guadalajara", "terrazas", r"terraza"),
        ("Guadalajara", "ruido", r"ruido|ac[uú]stic"),
        ("Guadalajara", "limpieza viaria", r"limpieza"),
        ("Guadalajara", "animales", r"animal"),
        ("Guadalajara", "convivencia ciudadana", r"convivencia"),
        ("Guadalajara", "IBI", r"bienes inmuebles"),
        ("Guadalajara", "venta ambulante", r"ambulante"),
        ("Guadalajara", "movilidad", r"movilidad|circulaci|tr[aá]fico"),
        ("Guadalajara", "zona de bajas emisiones", r"bajas emisiones|ZBE"),
        ("Guadalajara", "estacionamiento regulado", r"estacionamiento"),
        ("Guadalajara", "taxi", r"taxi"),
        ("Guadalajara", "plusvalía", r"incremento de valor|plusval"),
        ("Guadalajara", "parques y jardines", r"parque|jard[ií]n"),
        ("Guadalajara", "subvenciones", r"subvenci"),
        ("Guadalajara", "parejas de hecho", r"uniones|de hecho"),
        ("Ayuntamiento de Guadalajara", "ayuda a domicilio", r"ayuda a domicilio"),
        ("Guadalajara", "tasa de basura", r"basura|residuo"),
        ("Guadalajara capital", "inspección técnica de edificios", r"inspecci[oó]n t[eé]cnica"),
    ],
    "ceuta": [
        ("Ceuta", "terrazas veladores", r"terraza|velador"),
        ("Ceuta", "ruido", r"ruido"),
        ("Ceuta", "limpieza residuos", r"limpieza|residuo"),
        ("Ceuta", "animales", r"animal"),
        ("Ceuta", "espacios públicos convivencia", r"espacios? p[uú]blico|convivencia"),
        ("Ceuta", "IBI", r"bienes inmuebles"),
        ("Ceuta", "IPSI", r"IPSI|producci[oó]n"),
        ("Ceuta", "venta ambulante", r"ambulante|fuera de establecimiento"),
        ("Ceuta", "circulación tráfico", r"circulaci|tr[aá]fico"),
        ("Ceuta", "taxi", r"taxi"),
        ("Ciudad Autónoma de Ceuta", "vados", r"vado|acceso de veh"),
        ("Ceuta", "playas", r"playa"),
        ("Ceuta", "plusvalía", r"incremento de valor|plusval"),
        ("Ceuta", "apuestas", r"apuesta"),
        ("Ceuta", "policía local", r"polic[ií]a"),
        ("Ceuta", "quioscos", r"kiosk|quiosc"),
        ("Ayuntamiento de Ceuta", "cementerio", r"cementerio"),
        ("Ceuta", "disciplina urbanística", r"urban[ií]stic"),
        ("Ceuta", "tarjeta de estacionamiento discapacidad", r"discapacidad|movilidad reducida"),
    ],
}

# (entrada, codigo esperado o None)
ENRUTADO = [
    ("Cuenca", "cuenca"), ("cuenca", "cuenca"), ("Ayuntamiento de Cuenca", "cuenca"),
    ("Cuenca, Cuenca", "cuenca"), ("ordenanzas de Cuenca", "cuenca"),
    ("Guadalajara", "guadalajara"), ("Ayuntamiento de Guadalajara", "guadalajara"),
    ("Guadalajara capital", "guadalajara"), ("Guadalajara, Guadalajara", "guadalajara"),
    ("Ceuta", "ceuta"), ("Ciudad Autónoma de Ceuta", "ceuta"), ("Ciudad de Ceuta", "ceuta"),
    ("Ayuntamiento de Ceuta", "ceuta"),
    # no deben caer en la capital
    ("Tarancón", None), ("Azuqueca de Henares", None), ("Cuenca de Campos, Valladolid", None),
]
ENRUTADO_INFO = [("Cuenca de Campos", None)]      # limitación conocida del motor (no puntúa)


def _cuerpo(r: str) -> str:
    """Lo que sigue a la cabecera 【…】 y su línea de metadatos (para no dar por buena
    una lectura solo porque el TÍTULO contiene la materia)."""
    partes = r.split("\n\n", 1)
    return partes[1] if len(partes) > 1 else ""


def caso(muni, materia, rx):
    t0 = time.perf_counter()
    r1 = OE.buscar(muni, materia, 6)
    m = re.search(r"id:\s*(\S+)", r1 or "")
    if not (r1 or "").startswith("【") or not m:
        return "SIN_BUSQUEDA", (r1 or "")[:90].replace("\n", " "), time.perf_counter() - t0, ""
    nid = m.group(1)
    r2 = OE.leer(muni, nid, "", 3, materia, 0)
    dt = time.perf_counter() - t0
    cab = (re.search(r"【([^】]+)】", r2 or "") or [None, ""])[1]
    if not (r2 or "").startswith("【"):
        return "ERROR", (r2 or "")[:90].replace("\n", " "), dt, nid
    if re.match(r"(?i)^(Error|No )", r2) or "Error leyendo" in r2[:200]:
        return "ERROR", r2[:90].replace("\n", " "), dt, nid
    if len(r2) < 500:
        return "CORTO", cab[:70], dt, nid
    if not re.search(rx, _cuerpo(r2), re.I):
        return "SIN_MATERIA", cab[:70], dt, nid
    if dt >= LIMITE_S:
        return "LENTO", cab[:70], dt, nid
    return "OK", cab[:70], dt, nid


def main():
    solo = [a for a in sys.argv[1:] if not a.startswith("-")]
    ciudades = solo or list(CASOS)
    total_ok = total = 0
    todos_dt = []
    for codigo in ciudades:
        ad = OE.ADAPTADORES.get(codigo)
        n_normas = len(ad.catalogo()["normas"]) if ad else 0
        con_texto = sum(1 for n in ad.catalogo()["normas"] if n.get("texto")) if ad else 0
        print(f"\n== {codigo.upper()} ({n_normas} normas, {con_texto} con texto empaquetado) ==")
        ok, dts = 0, []
        for muni, materia, rx in CASOS[codigo]:
            estado, det, dt, nid = caso(muni, materia, rx)
            dts.append(dt)
            if estado == "OK":
                ok += 1
            print(f"{'✅' if estado == 'OK' else '❌'} [{dt*1000:5.0f} ms {estado:12}] {materia:38} -> {nid:16} {det}")
        n = len(CASOS[codigo])
        total_ok += ok
        total += n
        todos_dt += dts
        print(f"-- {codigo}: {ok}/{n} OK · mediana {statistics.median(dts)*1000:.0f} ms · máx {max(dts)*1000:.0f} ms")

    print("\n== ENRUTADO ==")
    ok_r = 0
    for entrada, esperado in ENRUTADO:
        ad = OE._resolver_municipio(entrada)
        got = ad.codigo if ad else None
        bien = got == esperado
        ok_r += bien
        print(f"{'✅' if bien else '❌'} {entrada:34} -> {got}")
    for entrada, esperado in ENRUTADO_INFO:
        ad = OE._resolver_municipio(entrada)
        got = ad.codigo if ad else None
        print(f"{'ℹ️ ' if got != esperado else '✅'} {entrada:34} -> {got}   (informativo: 2ª pasada por palabra de _resolver_municipio)")
    print(f"-- enrutado: {ok_r}/{len(ENRUTADO)}")

    print(f"\nRESULTADO CAPITALES WEB: {total_ok}/{total} OK ({100*total_ok/max(1,total):.0f} %) · "
          f"mediana {statistics.median(todos_dt)*1000:.0f} ms · máx {max(todos_dt)*1000:.0f} ms · límite {LIMITE_S:.0f} s")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
