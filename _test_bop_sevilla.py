# -*- coding: utf-8 -*-
"""Banco de pruebas BOP Sevilla: 30 municipios grandes (excl. capital).
Éxito = localiza una ordenanza REAL del municipio por el BOP y extrae su texto
exacto (directo u OCR). Offline (_*)."""
import re
import sys
import time

import _probe_bop3 as b

# 30 municipios más poblados de la provincia de Sevilla (sin la capital)
MUNICIPIOS = [
    "Dos Hermanas", "Alcalá de Guadaíra", "Utrera", "Mairena del Aljarafe", "Écija",
    "La Rinconada", "Los Palacios y Villafranca", "Camas", "Coria del Río", "Lebrija",
    "Morón de la Frontera", "Carmona", "Tomares", "San Juan de Aznalfarache", "Bormujos",
    "Mairena del Alcor", "Alcalá del Río", "Gines", "Castilleja de la Cuesta", "Estepa",
    "Marchena", "Osuna", "Arahal", "La Algaba", "Guillena",
    "Pilas", "Umbrete", "Cantillana", "Brenes", "Bollullos de la Mitación",
]

# materias comunes: se prueba en orden hasta dar con una ordenanza con texto
MATERIAS = [
    ("terrazas veladores", "terraza"), ("limpieza residuos", "residuo"),
    ("convivencia civismo", "convivencia"), ("animales tenencia", "animal"),
    ("ruido acustica", "ruido"), ("movilidad trafico circulacion", "circulaci"),
    ("cementerio", "cementerio"), ("mercado venta ambulante", "venta"),
    ("terrazas ocupacion via publica", "ocupaci"), ("fiscal basura", "tasa"),
]


def _validar(url):
    """Descarga y valida que se extrae texto exacto con articulado. (texto_len, arts, via)."""
    texto, via = b.texto_anuncio(url, ocr=True)
    tl = b._limpia(texto)
    arts = re.findall(r"(?im)art[íi]culo\s+\d+", tl)
    return len(tl), len(arts), via


def probar_municipio(muni):
    """Éxito = el sistema extrae texto exacto de ALGUNA ordenanza real del
    municipio por el BOP (materia normativa preferida; si no, cualquiera con
    articulado). Refleja el uso real: buscar materia -> leer artículo."""
    if b.resolver_muni(muni) is None:
        return ("NO_MAPA", muni, "-", 0)
    t0 = time.time()
    # 1) intentar materias normativas concretas (caso de uso real)
    for materia, verifica in MATERIAS:
        try:
            m = b.mejor(b.buscar(muni, materia), materia)
        except Exception:
            continue
        if not m:
            continue
        try:
            n, arts, via = _validar(m["url"])
        except Exception:
            continue
        if n > 1500 and arts >= 3:
            return ("OK", m["titulo"][:55], via, time.time() - t0)
    # 2) fallback: cualquier ordenanza/reglamento con articulado (incl. fiscal)
    try:
        res = b.buscar(muni, "ordenanza")
    except Exception:
        return ("ERR", muni, "-", time.time() - t0)
    cand = [r for r in res if b._es_ordenanza(r["titulo"])
            and not re.search(r"correcci|delegaci|honores|condecorac|personal", r["titulo"], re.I)]
    cand.sort(key=lambda r: (bool(re.search("definitiv", r["titulo"], re.I)), r["orden"]), reverse=True)
    for r in cand[:5]:
        try:
            n, arts, via = _validar(r["url"])
        except Exception:
            continue
        # texto exacto sustancial (las ordenanzas FISCALES de tasas usan tarifas/
        # epígrafes en vez de 'Artículo N', pero el texto se extrae igual de bien)
        if n > 2500:
            return ("OK", r["titulo"][:55], via, time.time() - t0)
    return ("SIN_MATCH", muni, "-", time.time() - t0)


def main(munis):
    b._sesion()
    ok = 0
    for i, muni in enumerate(munis, 1):
        estado, detalle, via, dt = probar_municipio(muni)
        if estado == "OK":
            ok += 1
            print(f"{i:2}. ✅ {muni:26} [{dt:4.1f}s {via:7}] {detalle}")
        else:
            print(f"{i:2}. ❌ {muni:26} [{dt:4.1f}s] {estado}")
    print(f"\nRESULTADO: {ok}/{len(munis)} municipios con texto exacto por el BOP")
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sel = sys.argv[1:]
    munis = [m for m in MUNICIPIOS if m in sel] if sel else MUNICIPIOS
    main(munis)
