# -*- coding: utf-8 -*-
"""Banco DURO de 30 pruebas del motor TEAC (DYCTEA) — orden de Carlos 17-ago-2026:
"ejecutas hasta un total de 30 pruebas distintas y te aseguras de que devuelve
el resultado lo suficientemente pertinente y desarrollado".

Criterios de aprobado por prueba:
  - BUSCAR: >=1 resultado con RG + fecha + resumen; salida >=200 chars; y
    PERTINENCIA: alguna palabra clave sustantiva de la consulta aparece en los
    resúmenes (comparación sin tildes ni mayúsculas).
  - LEER: >=3000 chars, con cabecera del RG, bloque CRITERIO y TEXTO ÍNTEGRO.
  - EDGE: el error se explica en UNA llamada (mensaje claro, sin excepción).
"""
import re
import sys
import time
import unicodedata

sys.path.insert(0, ".")
import teac_engine as t


def _norm(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def ok_buscar(res, claves, min_hits=1):
    if not re.search(r"^1\. RG \d{2}/\d{5}/\d{4} · \d{2}/\d{2}/\d{4} · ", res, re.M):
        return False, "sin fila 1 con RG·fecha·órgano"
    if len(res) < 200:
        return False, f"salida corta ({len(res)})"
    cuerpo = _norm(res)
    hits = [k for k in claves if _norm(k) in cuerpo]
    if len(hits) < min_hits:
        return False, f"pertinencia dudosa (solo {hits})"
    return True, f"{res.count(chr(10)+chr(32)+chr(32)+chr(32))} resúmenes; claves {hits[:4]}"


def ok_leer(res, rg_frag, min_len=3000):
    if len(res) < min_len:
        return False, f"corto ({len(res)})"
    if rg_frag not in res.split("\n", 1)[0]:
        return False, "cabecera sin el RG pedido"
    if "CRITERIO" not in res:
        return False, "sin bloque CRITERIO"
    if "TEXTO ÍNTEGRO" not in res:
        return False, "sin TEXTO ÍNTEGRO"
    return True, f"{len(res)} chars"


PRUEBAS = []  # (nombre, funcion)

# ---------- BÚSQUEDAS POR MATERIA (1-15) ----------
MATERIAS = [
    ("01 comprobación de valores / TPC",
     dict(consulta="comprobacion de valores tasacion pericial contradictoria"),
     ["tasacion pericial", "comprobacion de valores", "valor de referencia"]),
    ("02 derivación responsabilidad 42.2 LGT",
     dict(consulta="derivacion responsabilidad solidaria ocultacion 42.2"),
     ["responsabilidad", "ocultacion", "42.2"]),
    ("03 plusvalía municipal IIVTNU",
     dict(consulta="plusvalia municipal incremento valor terrenos"),
     ["incremento de valor", "IIVTNU", "plusval"]),
    ("04 IVA deducción facturas falsas",
     dict(consulta="IVA deduccion cuotas facturas falsas"),
     ["IVA", "deduc", "factura"]),
    ("05 IRPF exención por reinversión vivienda",
     dict(consulta="IRPF ganancia patrimonial reinversion vivienda habitual"),
     ["vivienda habitual", "reinversi", "ganancia"]),
    ("06 sanción art. 203 LGT",
     dict(consulta="sancion resistencia obstruccion excusa negativa"),
     ["resistencia", "obstruc", "sancion"]),
    ("07 notificaciones electrónicas",
     dict(consulta="notificacion electronica direccion electronica habilitada"),
     ["notificacion", "electronic"]),
    ("08 prescripción del derecho a liquidar",
     dict(consulta="prescripcion derecho liquidar interrupcion"),
     ["prescripcion", "interrup"]),
    ("09 IS operaciones vinculadas",
     dict(consulta="impuesto sociedades operaciones vinculadas valoracion mercado"),
     ["vinculada", "sociedades", "valor"]),
    ("10 recargos por extemporaneidad",
     dict(consulta="recargo declaracion extemporanea requerimiento previo"),
     ["extempor", "recargo", "requerimiento"]),
    ("11 frase exacta 'unificación de criterio'",
     dict(frase="unificacion de criterio"),
     # la frase machea server-side en el criterio COMPLETO; el resumen de 300
     # chars puede truncarla, así que la pertinencia se mide con sus términos
     ["unificacion", "criterio", "alzada"]),
    ("12 modelo 720 bienes en el extranjero",
     dict(consulta="modelo 720 bienes derechos extranjero"),
     ["720", "extranjero"]),
    ("13 embargo de cuentas",
     dict(consulta="embargo cuentas bancarias diligencia"),
     ["embargo", "diligencia", "cuenta"]),
    ("14 aplazamiento y fraccionamiento",
     dict(consulta="aplazamiento fraccionamiento pago garantia"),
     ["aplazamiento", "fraccionamiento", "garantia"]),
    ("15 comprobación limitada alcance",
     dict(consulta="comprobacion limitada alcance actuaciones"),
     ["comprobacion limitada", "alcance"]),
]
for nombre, kw, claves in MATERIAS:
    PRUEBAS.append((nombre, lambda kw=kw, claves=claves:
                    ok_buscar(t.buscar(**kw), claves)))

# ---------- FILTROS Y VARIANTES (16-25) ----------
PRUEBAS.append(("16 TEAR de Madrid",
                lambda: ok_buscar(t.buscar("IRPF deduccion", organo="Madrid"),
                                  ["TEAR de Madrid"])))
PRUEBAS.append(("17 TEAR de Andalucía (ITP)",
                lambda: ok_buscar(t.buscar("transmisiones patrimoniales", organo="Andalucia"),
                                  ["TEAR de Andaluc"])))
PRUEBAS.append(("18 todos los órganos",
                lambda: ok_buscar(t.buscar("comprobacion limitada", organo="todos"),
                                  ["comprobacion"])))
PRUEBAS.append(("19 solo vinculantes (doctrina)",
                lambda: ok_buscar(t.buscar("IVA", vinculantes="vinculantes"), ["IVA"])))
PRUEBAS.append(("20 fechas 2026",
                lambda: ok_buscar(t.buscar("IVA", desde="01/01/2026", hasta="17/08/2026"),
                                  ["2026"])))
PRUEBAS.append(("21 fechas 2020 (histórico)",
                lambda: ok_buscar(t.buscar("prescripcion", desde="01/01/2020", hasta="31/12/2020"),
                                  ["2020"])))
def _t22():
    # En modo texto íntegro las palabras machean DENTRO de la resolución, no en
    # el resumen: la pertinencia se demuestra leyendo la primera y buscándolas.
    res = t.buscar("simulacion negocio juridico", ambito="resoluciones", maximo=5)
    okv, msg = ok_buscar(res, [""], min_hits=0)
    if not okv:
        return False, msg
    m = re.search(r"RG (\d{2}/\d{5}/\d{4})", res)
    texto = t.leer(m.group(1), max_chars=500000)  # sin recorte: el match puede ir al fondo
    if "simulaci" not in _norm(texto):
        return False, f"'simulacion' no aparece en el texto de {m.group(1)}"
    return True, f"pertinencia verificada leyendo {m.group(1)} ({len(texto)} chars)"


PRUEBAS.append(("22 ámbito resoluciones (texto íntegro)", _t22))
PRUEBAS.append(("23 búsqueda por RG largo",
                lambda: ok_buscar(t.buscar(numero_rg="00/06291/2024"), ["06291"])))
PRUEBAS.append(("24 búsqueda por RG corto 'RG 2283-2022'",
                lambda: ok_buscar(t.buscar(numero_rg="RG 2283-2022"), ["02283"])))


def _t25():
    res = t.buscar("IVA", maximo=20)
    filas = len(re.findall(r"^\d+\. RG ", res, re.M))
    if filas < 15:
        return False, f"paginación corta: {filas} filas"
    return ok_buscar(res, ["IVA"])


PRUEBAS.append(("25 máximo 20 (paginación >10)", _t25))

# ---------- LECTURAS ÍNTEGRAS (26-29) ----------
PRUEBAS.append(("26 leer 00/06291/2024 (TEAC 2026)",
                lambda: ok_leer(t.leer("00/06291/2024"), "00/06291/2024", 20000)))


def _t27():
    res = t.leer("00/02211/2024")
    okv, msg = ok_leer(res, "00/02211/2024", 8000)
    if okv and res.count("CRITERIO") < 2:
        return False, "esperaba varios criterios en esta resolución"
    return okv, msg + f" · criterios={res.count('CRITERIO ')}"


PRUEBAS.append(("27 leer 00/02211/2024 (multi-criterio)", _t27))


def _t28():
    lst = t.buscar("IRPF", organo="Madrid", maximo=3)
    m = re.search(r"RG (\d{2}/\d{5}/\d{4})", lst)
    if not m:
        return False, "no hay RG del TEAR Madrid que leer"
    return ok_leer(t.leer(m.group(1)), m.group(1), 2500)


PRUEBAS.append(("28 leer resolución de un TEAR (Madrid)", _t28))
PRUEBAS.append(("29 leer por id interno directo",
                lambda: ok_leer(t.leer("00/06291/2024/00/0/1"), "00/06291/2024", 20000)))


def _t30():
    res = t.leer("00/99998/2019")
    if "No encuentro" in res and "99998" in res:
        return True, "mensaje claro en UNA llamada"
    return False, f"respuesta inesperada: {res[:120]}"


PRUEBAS.append(("30 RG inexistente → aviso claro", _t30))


def main():
    ok = 0
    fallos = []
    for nombre, fn in PRUEBAS:
        t0 = time.time()
        try:
            res, msg = fn()
        except Exception as e:  # noqa: BLE001
            res, msg = False, f"EXCEPCIÓN {e!r}"
        dt = time.time() - t0
        estado = "OK " if res else "FALLO"
        print(f"[{estado}] {nombre}  ({dt:.1f}s)  {msg}")
        if res:
            ok += 1
        else:
            fallos.append(nombre)
        time.sleep(0.4)
    print(f"\nRESULTADO: {ok}/{len(PRUEBAS)}")
    if fallos:
        print("FALLAN:", "; ".join(fallos))
        sys.exit(1)


if __name__ == "__main__":
    main()
